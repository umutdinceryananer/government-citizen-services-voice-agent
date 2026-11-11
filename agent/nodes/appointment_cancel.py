"""
Appointment cancellation node.

Cancels a confirmed appointment for the citizen. Detects which
appointment to cancel from conversation context.
"""

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from agent.state import AgentState
from agent.logging_config import get_logger
from agent.nodes.utils import mark_completed, get_honorific, format_date_spoken, format_time_spoken, gov_api_request

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


def _normalize(text: str) -> str:
    """Normalize Turkish characters for matching."""
    return (text.replace("ı", "i").replace("İ", "i")
            .replace("ş", "s").replace("Ş", "s")
            .replace("ğ", "g").replace("Ğ", "g")
            .replace("ü", "u").replace("Ü", "u")
            .replace("ö", "o").replace("Ö", "o")
            .replace("ç", "c").replace("Ç", "c")
            .lower())


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


def _cancel_appointment(appointment_id: int) -> bool:
    """Call government API to cancel an appointment."""
    try:
        r = gov_api_request("DELETE", f"/appointments/{appointment_id}")
        return r.status_code == 200
    except httpx.RequestError as e:
        logger.error(f"Appointment cancel API unreachable: {e}")
        return False


def appointment_cancel(state: AgentState) -> dict:
    """Cancel a confirmed appointment, detecting selection from context."""
    profile = state.get("citizen_profile")
    language = state.get("language", "tr")
    messages = state.get("messages", [])

    if not profile:
        msg = ("I need to verify your identity before cancelling an appointment."
               if language == "en" else
               "Randevu iptal etmek icin kimlik dogrulamasi gerekiyor.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "appointment_cancel"),
            "service_node_name": "appointment_cancel",
            "api_calls_count": 0,
        }

    citizen_id = profile.get("citizen_id")
    first_name = get_honorific(profile, language)

    appointments = _fetch_appointments(citizen_id) if citizen_id else []
    confirmed = [a for a in appointments if a.get("status") == "confirmed"]

    if not confirmed:
        msg = (f"{first_name}, you have no confirmed appointments to cancel."
               if language == "en" else
               f"{first_name}, iptal edilebilecek onaylanmis bir randevunuz bulunmuyor.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "appointment_cancel"),
            "service_node_name": "appointment_cancel",
            "api_calls_count": 1,
        }

    # Try to detect which appointment from conversation context
    last_user_msg = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human":
            last_user_msg = m.content
            break

    if len(confirmed) > 1 and last_user_msg:
        normalized_msg = _normalize(last_user_msg)
        for a in confirmed:
            svc = a.get("service_type", "")
            svc_tr = SERVICE_NAMES_TR.get(svc, "")
            svc_en = SERVICE_NAMES_EN.get(svc, "")
            if (_normalize(svc) in normalized_msg or
                _normalize(svc_tr) in normalized_msg or
                _normalize(svc_en) in normalized_msg):
                # Found match — cancel this one
                success = _cancel_appointment(a["id"])
                if success:
                    svc_name = svc_tr if language != "en" else svc_en
                    if language == "en":
                        msg = (f"{first_name}, your {svc_name} appointment on "
                               f"{format_date_spoken(a['appointment_date'])} at {format_time_spoken(a['appointment_time'])} has been cancelled.")
                    else:
                        msg = (f"{first_name}, {a['appointment_date']} tarihli {svc_name} "
                               f"randevunuz iptal edilmistir.")
                else:
                    msg = (f"{first_name}, randevunuzu su anda iptal edemedim."
                           if language != "en" else
                           f"{first_name}, I couldn't cancel your appointment right now.")

                logger.info(f"Appointment cancel | citizen={first_name} | type={svc} | success={success}")
                return {
                    "messages": [AIMessage(content=msg)],
                    "completed_intents": mark_completed(state, "appointment_cancel"),
                    "service_node_name": "appointment_cancel",
                    "api_calls_count": 2,  # fetch + cancel
                }

    if len(confirmed) == 1:
        a = confirmed[0]
        success = _cancel_appointment(a["id"])
        svc_name = SERVICE_NAMES_TR.get(a["service_type"], a["service_type"]) if language != "en" else SERVICE_NAMES_EN.get(a["service_type"], a["service_type"])
        if success:
            if language == "en":
                msg = (f"{first_name}, your {svc_name} appointment on "
                       f"{format_date_spoken(a['appointment_date'])} at {format_time_spoken(a['appointment_time'])} has been cancelled.")
            else:
                msg = (f"{first_name}, {a['appointment_date']} tarihli {svc_name} "
                       f"randevunuz iptal edilmistir.")
        else:
            msg = (f"{first_name}, randevunuzu su anda iptal edemedim."
                   if language != "en" else
                   f"{first_name}, I couldn't cancel your appointment right now.")

        logger.info(f"Appointment cancel | citizen={first_name} | type={a['service_type']} | success={success}")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "appointment_cancel"),
            "service_node_name": "appointment_cancel",
            "api_calls_count": 2,  # fetch + cancel
        }

    # Multiple appointments, no match — list them
    if language == "en":
        msg = f"{first_name}, you have {len(confirmed)} confirmed appointments. "
        for a in confirmed:
            svc_name = SERVICE_NAMES_EN.get(a["service_type"], a["service_type"])
            msg += f"{svc_name} on {format_date_spoken(a['appointment_date'])} at {format_time_spoken(a['appointment_time'])}. "
        msg += "Which one would you like to cancel?"
    else:
        msg = f"{first_name}, {len(confirmed)} onaylanmis randevunuz var. "
        for a in confirmed:
            svc_name = SERVICE_NAMES_TR.get(a["service_type"], a["service_type"])
            msg += f"{svc_name} icin {a['appointment_date']} saat {a['appointment_time']}. "
        msg += "Hangisini iptal etmek istersiniz?"

    logger.info(f"Appointment cancel | citizen={first_name} | listing {len(confirmed)} appointments")
    return {"messages": [AIMessage(content=msg)], "service_node_name": "appointment_cancel", "api_calls_count": 1}
