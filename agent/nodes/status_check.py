"""
Status check node.

Calls the government API to get all citizen applications, then:
1. If multiple applications → lists them and asks which one to detail
2. If single application → shows detail directly
3. Deterministic tool chaining based on status:
   - "additional_docs_needed" → RAG query for required documents (Pinecone)
   - "rejected" → RAG query for appeal rights + guidance
   - Otherwise → return status with details

This is LangGraph's core differentiator: deterministic tool chaining.
ElevenLabs native tools can't guarantee "status API returned X,
therefore automatically query knowledge base for Y."
"""

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from agent.state import AgentState
from agent.logging_config import get_logger
from agent.nodes.utils import mark_completed, get_honorific, gov_api_request
from rag.retriever import search, format_context_tts

logger = get_logger(__name__)

SERVICE_NAMES_TR = {
    "passport": "pasaport",
    "id_card": "kimlik karti",
    "driver_license": "ehliyet",
    "civil_registry": "nufus islemi",
}

SERVICE_NAMES_EN = {
    "passport": "passport",
    "id_card": "ID card",
    "driver_license": "driver's license",
    "civil_registry": "civil registry",
}

STATUS_NAMES_TR = {
    "pending": "beklemede",
    "in_review": "incelemede",
    "approved": "onaylandi",
    "rejected": "reddedildi",
    "additional_docs_needed": "ek belge gerekli",
}

STATUS_NAMES_EN = {
    "pending": "pending",
    "in_review": "under review",
    "approved": "approved",
    "rejected": "rejected",
    "additional_docs_needed": "additional documents needed",
}


def _rag_lookup(query: str, language: str, category: str | None = None) -> str:
    """Query RAG for supplementary information. Returns TTS-friendly text."""
    results = search(query=query, language=language, category=category, top_k=2)
    if results and results[0].score > 0.3:
        return format_context_tts(results)
    return ""


def _fetch_all_applications(citizen_id: int) -> list[dict]:
    """Call government API for all citizen applications."""
    try:
        r = gov_api_request("GET", "/applications", params={"citizen_id": citizen_id})
        if r.status_code == 200:
            return r.json()
        logger.warning(f"Applications API returned {r.status_code} for citizen {citizen_id}")
        return []
    except httpx.RequestError as e:
        logger.error(f"Applications API unreachable: {e}")
        return []


def _fetch_status(application_ref: str) -> dict | None:
    """Call government API for single application status."""
    try:
        r = gov_api_request("GET", f"/applications/{application_ref}")
        if r.status_code == 200:
            return r.json()
        logger.warning(f"Status API returned {r.status_code} for {application_ref}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Status API unreachable: {e}")
        return None


def _build_detail_message(app: dict, first_name: str, language: str) -> str:
    """Build detailed status message with tool chaining for a single application."""
    status = app.get("status", "unknown")
    ref = app.get("application_ref", "")
    service = app.get("service_type", "")
    notes = app.get("notes", "")
    office = app.get("office", "")
    last_updated = app.get("last_updated", "")
    estimated = app.get("estimated_completion", "")

    svc_name = SERVICE_NAMES_TR.get(service, service) if language != "en" else SERVICE_NAMES_EN.get(service, service)

    if status == "additional_docs_needed":
        svc_en = SERVICE_NAMES_EN.get(service, service)
        rag_query = f"required documents for {svc_en} application" if language == "en" else f"{SERVICE_NAMES_TR.get(service, service)} basvurusu icin gerekli belgeler"
        docs_context = _rag_lookup(rag_query, language, category=service if service else None)
        logger.info(f"Tool chain: status=additional_docs_needed -> RAG docs lookup (found={bool(docs_context)})")

        if language == "en":
            msg = f"{first_name}, your {svc_name} application requires additional documents."
            if notes:
                msg += f" {notes}"
            if office:
                msg += f" Please submit these at {office}."
            if docs_context:
                msg += f" For general requirements: {docs_context}"
        else:
            msg = f"{first_name}, {svc_name} basvurunuz ek belge gerektiriyor."
            if notes:
                msg += f" {notes}"
            if docs_context:
                msg += f" Kayitlarimiza gore gerekli olabilecek belgeler: {docs_context}"
            if office:
                msg += f" Bu belgeleri {office} adresine teslim edebilirsiniz."

    elif status == "rejected":
        rag_query = "appeal rights rejected application" if language == "en" else "itiraz hakki reddedilen basvuru"
        appeal_context = _rag_lookup(rag_query, language, category="general")
        logger.info(f"Tool chain: status=rejected -> RAG appeal lookup (found={bool(appeal_context)})")

        if language == "en":
            msg = f"{first_name}, your {svc_name} application has been rejected."
            if notes:
                msg += f" {notes}"
            if appeal_context:
                msg += f" Appeal information: {appeal_context}"
            else:
                msg += " You may file an appeal within thirty days."
        else:
            msg = f"{first_name}, {svc_name} basvurunuz reddedilmistir."
            if notes:
                msg += f" {notes}"
            if appeal_context:
                msg += f" Itiraz bilgileri: {appeal_context}"
            else:
                msg += " Red tarihinden itibaren otuz gun icinde itiraz basvurusu yapabilirsiniz."

    elif status == "approved":
        if language == "en":
            msg = f"{first_name}, your {svc_name} application has been approved."
            if notes:
                msg += f" {notes}"
        else:
            msg = f"{first_name}, {svc_name} basvurunuz onaylanmistir."
            if notes:
                msg += f" {notes}"

    elif status == "in_review":
        if language == "en":
            msg = f"{first_name}, your {svc_name} application is currently under review."
            if notes:
                msg += f" {notes}"
            if estimated:
                msg += f" Estimated completion: {estimated}."
        else:
            msg = f"{first_name}, {svc_name} basvurunuz inceleme asamasindadir."
            if notes:
                msg += f" {notes}"
            if estimated:
                msg += f" Tahmini tamamlanma suresi: {estimated}."

    else:
        if language == "en":
            msg = f"{first_name}, your {svc_name} application status is {status}."
            if notes:
                msg += f" {notes}"
        else:
            status_tr = STATUS_NAMES_TR.get(status, status)
            msg = f"{first_name}, {svc_name} basvurunuz {status_tr} durumundadir."
            if notes:
                msg += f" {notes}"

    return msg


def status_check(state: AgentState) -> dict:
    """Check application status with multi-application support and tool chaining."""
    profile = state.get("citizen_profile")
    language = state.get("language", "tr")
    _api_calls = 0
    _tool_chain = None

    if not profile:
        msg = ("I need to verify your identity before checking your application status."
               if language == "en" else
               "Basvuru durumunuzu kontrol etmek icin kimlik dogrulamasi gerekiyor.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "status_check"),
            "service_node_name": "status_check",
            "api_calls_count": 0,
        }

    citizen_id = profile.get("citizen_id")
    first_name = get_honorific(profile, language)
    app_ref = profile.get("application_ref", "")

    # If citizen_id missing (post-auth from system prompt), look it up via app_ref
    if not citizen_id and app_ref:
        single = _fetch_status(app_ref)
        _api_calls += 1
        if single:
            citizen_id = single.get("citizen_id")

    # Try to fetch all applications for this citizen
    all_apps = _fetch_all_applications(citizen_id) if citizen_id else []
    if citizen_id:
        _api_calls += 1

    # Check if user is selecting a specific application from a previous listing
    last_user_msg = ""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human":
            last_user_msg = m.content.lower()
            break

    if len(all_apps) > 1 and last_user_msg:
        # Try to match user's selection to a specific application
        # Normalize Turkish characters for matching
        def _normalize(text: str) -> str:
            return (text.replace("ı", "i").replace("İ", "i")
                    .replace("ş", "s").replace("Ş", "s")
                    .replace("ğ", "g").replace("Ğ", "g")
                    .replace("ü", "u").replace("Ü", "u")
                    .replace("ö", "o").replace("Ö", "o")
                    .replace("ç", "c").replace("Ç", "c")
                    .lower())

        normalized_msg = _normalize(last_user_msg)
        for app in all_apps:
            svc = app.get("service_type", "")
            svc_tr = SERVICE_NAMES_TR.get(svc, "")
            svc_en = SERVICE_NAMES_EN.get(svc, "")
            if (_normalize(svc) in normalized_msg or
                _normalize(svc_tr) in normalized_msg or
                _normalize(svc_en) in normalized_msg):
                msg = _build_detail_message(app, first_name, language)
                _tc = None
                if app["status"] == "additional_docs_needed":
                    _tc = "status->rag_docs"
                elif app["status"] == "rejected":
                    _tc = "status->rag_appeal"
                logger.info(f"Status check | citizen={first_name} | selected={svc} | status={app['status']}")
                return {
                    "messages": [AIMessage(content=msg)],
                    "completed_intents": mark_completed(state, "status_check"),
                    "service_node_name": "status_check",
                    "tool_chain_triggered": _tc,
                    "api_calls_count": _api_calls,
                }

    if len(all_apps) > 1:
        # Multiple applications — list them with summary
        if language == "en":
            msg = f"{first_name}, you have {len(all_apps)} applications on file. "
            for app in all_apps:
                svc = SERVICE_NAMES_EN.get(app["service_type"], app["service_type"])
                sts = STATUS_NAMES_EN.get(app["status"], app["status"])
                msg += f"Your {svc} application is {sts}. "
            msg += "Which application would you like more details about?"
        else:
            msg = f"{first_name}, sistemde {len(all_apps)} basvurunuz bulunuyor. "
            for app in all_apps:
                svc = SERVICE_NAMES_TR.get(app["service_type"], app["service_type"])
                sts = STATUS_NAMES_TR.get(app["status"], app["status"])
                msg += f"{svc.capitalize()} basvurunuz {sts}. "
            msg += "Hangisi hakkinda detayli bilgi almak istersiniz?"

        logger.info(f"Status check | citizen={first_name} | apps={len(all_apps)} | listing all")

    elif len(all_apps) == 1:
        # Single application — show details directly
        msg = _build_detail_message(all_apps[0], first_name, language)
        logger.info(f"Status check | citizen={first_name} | single app | status={all_apps[0]['status']}")

    else:
        # No apps from API — fallback to profile data with tool chaining
        fallback_app = {
            "status": profile.get("application_status", "unknown"),
            "application_ref": app_ref,
            "service_type": "",
            "notes": "",
            "office": "",
            "last_updated": "",
            "estimated_completion": "",
        }
        msg = _build_detail_message(fallback_app, first_name, language)
        logger.info(f"Status check | citizen={first_name} | fallback | ref={app_ref}")

    # Detect tool chain from status
    if len(all_apps) == 1:
        status = all_apps[0].get("status", "")
        if status == "additional_docs_needed":
            _tool_chain = "status->rag_docs"
        elif status == "rejected":
            _tool_chain = "status->rag_appeal"
    elif not all_apps:
        fallback_status = profile.get("application_status", "")
        if fallback_status == "additional_docs_needed":
            _tool_chain = "status->rag_docs"
        elif fallback_status == "rejected":
            _tool_chain = "status->rag_appeal"

    return {
        "messages": [AIMessage(content=msg)],
        "completed_intents": mark_completed(state, "status_check"),
        "service_node_name": "status_check",
        "tool_chain_triggered": _tool_chain,
        "api_calls_count": _api_calls,
    }
