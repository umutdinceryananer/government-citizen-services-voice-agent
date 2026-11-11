"""
Document request status node.

Calls the government API to list all document requests for a citizen.
"""

import httpx
from langchain_core.messages import AIMessage

from agent.state import AgentState
from agent.logging_config import get_logger
from agent.nodes.utils import mark_completed, get_honorific, gov_api_request

logger = get_logger(__name__)


def _fetch_document_requests(citizen_id: int) -> list[dict]:
    """Call government API for citizen's document requests."""
    try:
        r = gov_api_request("GET", f"/documents/{citizen_id}")
        if r.status_code == 200:
            return r.json()
        return []
    except httpx.RequestError as e:
        logger.error(f"Documents API unreachable: {e}")
        return []


def document_status(state: AgentState) -> dict:
    """List all document requests for the authenticated citizen."""
    profile = state.get("citizen_profile")
    language = state.get("language", "tr")

    if not profile:
        msg = ("I need to verify your identity before checking document requests."
               if language == "en" else
               "Belge taleplerinizi kontrol etmek icin kimlik dogrulamasi gerekiyor.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "document_status"),
            "service_node_name": "document_status",
            "api_calls_count": 0,
        }

    citizen_id = profile.get("citizen_id")
    first_name = get_honorific(profile, language)

    docs = _fetch_document_requests(citizen_id) if citizen_id else []

    if not docs:
        msg = (f"{first_name}, you have no document requests on record."
               if language == "en" else
               f"{first_name}, sistemde kayitli belge talebiniz bulunmuyor.")
    elif len(docs) == 1:
        d = docs[0]
        if language == "en":
            msg = (f"{first_name}, your {d['document_type']} request ({d['request_ref']}) "
                   f"is {d['status']}. Estimated: {d['estimated_days']} business days.")
        else:
            msg = (f"{first_name}, {d['document_type']} talebiniz ({d['request_ref']}) "
                   f"{d['status']} durumunda. Tahmini sure: {d['estimated_days']} is gunu.")
    else:
        if language == "en":
            msg = f"{first_name}, you have {len(docs)} document requests. "
            for d in docs:
                msg += f"{d['document_type']} ({d['request_ref']}): {d['status']}. "
        else:
            msg = f"{first_name}, {len(docs)} belge talebiniz var. "
            for d in docs:
                msg += f"{d['document_type']} ({d['request_ref']}): {d['status']}. "

    logger.info(f"Document status | citizen={first_name} | count={len(docs)}")

    return {
        "messages": [AIMessage(content=msg)],
        "completed_intents": mark_completed(state, "document_status"),
        "service_node_name": "document_status",
        "api_calls_count": 1 if citizen_id else 0,
    }
