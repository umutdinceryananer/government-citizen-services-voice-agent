"""
Appointment listing node.

Calls the government API to list all appointments for a citizen.
"""

import httpx
from langchain_core.messages import AIMessage

from agent.state import AgentState
from agent.logging_config import get_logger
from agent.nodes.utils import mark_completed, get_honorific, format_date_spoken, format_time_spoken, gov_api_request

logger = get_logger(__name__)


def _fetch_appointments(citizen_id: int) -> list[dict]:
    """Call government API for citizen's appointments."""
    try:
        r = gov_api_request("GET", f"/appointments/{citizen_id}")
        if r.status_code == 200:
            return r.json()
        return []
    except httpx.RequestError as e:
        logger.error(f"Appointments API unreachable: {e}")
        return []


def appointment_list(state: AgentState) -> dict:
    """List all appointments for the authenticated citizen."""
    profile = state.get("citizen_profile")
    language = state.get("language", "tr")

    if not profile:
        msg = ("I need to verify your identity before viewing your appointments."
               if language == "en" else
               "Randevularinizi goruntulemek icin kimlik dogrulamasi gerekiyor.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "appointment_list"),
            "service_node_name": "appointment_list",
            "api_calls_count": 0,
        }

    citizen_id = profile.get("citizen_id")
    first_name = get_honorific(profile, language)

    appointments = _fetch_appointments(citizen_id) if citizen_id else []

    if not appointments:
        msg = (f"{first_name}, you have no appointments on record."
               if language == "en" else
               f"{first_name}, sistemde kayitli randevunuz bulunmuyor.")
    elif len(appointments) == 1:
        a = appointments[0]
        if language == "en":
            msg = (f"{first_name}, you have one appointment. "
                   f"{a['service_type']} on {format_date_spoken(a['appointment_date'])} at {format_time_spoken(a['appointment_time'])}, "
                   f"location {a['office']}. Status: {a['status']}.")
        else:
            msg = (f"{first_name}, bir randevunuz var. "
                   f"{a['service_type']} icin {a['appointment_date']} tarihinde saat {a['appointment_time']}, "
                   f"yer {a['office']}. Durum: {a['status']}.")
    else:
        if language == "en":
            msg = f"{first_name}, you have {len(appointments)} appointments. "
            for a in appointments:
                msg += f"{a['service_type']} on {format_date_spoken(a['appointment_date'])} at {format_time_spoken(a['appointment_time'])} ({a['status']}). "
        else:
            msg = f"{first_name}, {len(appointments)} randevunuz var. "
            for a in appointments:
                msg += f"{a['service_type']} icin {a['appointment_date']} saat {a['appointment_time']} ({a['status']}). "

    logger.info(f"Appointment list | citizen={first_name} | count={len(appointments)}")

    return {
        "messages": [AIMessage(content=msg)],
        "completed_intents": mark_completed(state, "appointment_list"),
        "service_node_name": "appointment_list",
        "api_calls_count": 1 if citizen_id else 0,
    }
