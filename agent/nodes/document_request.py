"""
Document request node.

Asks the citizen which document type they need, then calls the
government API to initiate preparation. Detects document type
from conversation context when possible.
"""

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from agent.state import AgentState
from agent.logging_config import get_logger
from agent.nodes.utils import mark_completed, get_honorific, gov_api_request

logger = get_logger(__name__)

DOCUMENT_TYPES = {
    "birth_certificate": {
        "tr": "dogum belgesi",
        "en": "birth certificate",
        "keywords_tr": ["dogum", "dogum belgesi"],
        "keywords_en": ["birth", "birth certificate"],
    },
    "residence_cert": {
        "tr": "ikametgah belgesi",
        "en": "residence certificate",
        "keywords_tr": ["ikametgah", "yerlesim yeri"],
        "keywords_en": ["residence", "address"],
    },
    "marriage_cert": {
        "tr": "evlilik cuzdani",
        "en": "marriage certificate",
        "keywords_tr": ["evlilik", "evlilik cuzdani", "nikah"],
        "keywords_en": ["marriage"],
    },
    "criminal_record": {
        "tr": "sabika kaydi",
        "en": "criminal record",
        "keywords_tr": ["sabika", "adli sicil"],
        "keywords_en": ["criminal", "criminal record"],
    },
}


def _normalize(text: str) -> str:
    """Normalize Turkish characters for matching."""
    return (text.replace("ı", "i").replace("İ", "i")
            .replace("ş", "s").replace("Ş", "s")
            .replace("ğ", "g").replace("Ğ", "g")
            .replace("ü", "u").replace("Ü", "u")
            .replace("ö", "o").replace("Ö", "o")
            .replace("ç", "c").replace("Ç", "c")
            .lower())


def _detect_document_type(messages: list, language: str) -> str | None:
    """Try to detect document type from conversation history."""
    # Check last 4 messages for document type keywords
    for msg in reversed(messages[-4:]):
        if not isinstance(msg, HumanMessage) and getattr(msg, "type", "") != "human":
            continue
        normalized = _normalize(msg.content)
        for doc_type, info in DOCUMENT_TYPES.items():
            key = "keywords_tr" if language != "en" else "keywords_en"
            for keyword in info[key]:
                if _normalize(keyword) in normalized:
                    return doc_type
    return None


def _request_via_api(citizen_id: int, document_type: str) -> dict | None:
    """Call government API to request document."""
    try:
        payload = {
            "citizen_id": citizen_id,
            "document_type": document_type,
        }
        r = gov_api_request("POST", "/documents/request", json=payload)
        if r.status_code == 200:
            return r.json()
        logger.warning(f"Document API returned {r.status_code}: {r.text}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Document API unreachable: {e}")
        return None


def document_request(state: AgentState) -> dict:
    """Initiate a document request via government API."""
    profile = state.get("citizen_profile")
    language = state.get("language", "tr")
    messages = state.get("messages", [])

    if not profile:
        msg = ("I need to verify your identity before processing a document request."
               if language == "en" else
               "Belge talebi icin kimlik dogrulamasi gerekiyor.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "document_request"),
            "service_node_name": "document_request",
            "api_calls_count": 0,
        }

    first_name = get_honorific(profile, language)
    citizen_id = profile.get("citizen_id")

    # Try to detect document type from conversation
    doc_type = _detect_document_type(messages, language)

    if not doc_type:
        # Ask which document type
        if language == "en":
            msg = (f"{first_name}, which type of document would you like to request? "
                   f"I can help with birth certificate, residence certificate, "
                   f"marriage certificate, or criminal record.")
        else:
            msg = (f"{first_name}, hangi tur belge talep etmek istiyorsunuz? "
                   f"Dogum belgesi, ikametgah belgesi, evlilik cuzdani "
                   f"veya sabika kaydi konusunda yardimci olabilirim.")
        return {"messages": [AIMessage(content=msg)], "service_node_name": "document_request", "api_calls_count": 0}

    # Call government API with detected type
    result = _request_via_api(citizen_id, doc_type)
    doc_name = DOCUMENT_TYPES[doc_type]["tr" if language != "en" else "en"]

    if result:
        ref = result.get("request_ref", "")
        days = result.get("estimated_days", 5)

        if language == "en":
            msg = (f"{first_name}, your {doc_name} request has been submitted. "
                   f"Reference number: {ref}. "
                   f"Estimated preparation time: {days} business days. "
                   f"You can check the status anytime by asking me.")
        else:
            msg = (f"{first_name}, {doc_name} talebiniz olusturuldu. "
                   f"Referans numaraniz: {ref}. "
                   f"Tahmini hazirlama suresi: {days} is gunu. "
                   f"Durumunu istediginiz zaman bana sorarak ogrenebilirsiniz.")
    else:
        if language == "en":
            msg = (f"{first_name}, I wasn't able to process your {doc_name} request right now. "
                   f"Please try again later or contact us at ALO 181.")
        else:
            msg = (f"{first_name}, su anda {doc_name} talebinizi isleyemedim. "
                   f"Lutfen daha sonra tekrar deneyin veya ALO 181'i arayin.")

    logger.info(f"Document request | citizen_id={citizen_id} | type={doc_type} | success={bool(result)}")

    return {
        "messages": [AIMessage(content=msg)],
        "completed_intents": mark_completed(state, "document_request"),
        "service_node_name": "document_request",
        "api_calls_count": 1,
    }
