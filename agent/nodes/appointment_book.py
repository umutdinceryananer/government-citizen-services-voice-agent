"""
Appointment booking node.

Multi-turn flow:
1. Detect service type from conversation (or ask)
2. Check for existing appointment conflicts
3. Fetch available slots and present options
4. User selects a slot → book it

Uses the same pattern as status_check multi-app listing:
- Turn 1: list slots (don't mark completed → LangGraph returns here)
- Turn 2: user selects → book and mark completed
"""

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from agent.state import AgentState
from agent.logging_config import get_logger
from agent.nodes.utils import mark_completed, get_honorific, format_date_spoken, format_time_spoken, gov_api_request

logger = get_logger(__name__)

SERVICE_TYPES = {
    "passport": {
        "tr": "pasaport",
        "en": "passport",
        "keywords_tr": ["pasaport"],
        "keywords_en": ["passport"],
    },
    "id_card": {
        "tr": "kimlik karti",
        "en": "ID card",
        "keywords_tr": ["kimlik", "kimlik karti", "nufus cuzdani"],
        "keywords_en": ["id card", "identity card", "id"],
    },
    "driver_license": {
        "tr": "ehliyet",
        "en": "driver's license",
        "keywords_tr": ["ehliyet", "surucu belgesi"],
        "keywords_en": ["driver", "license", "driving"],
    },
    "civil_registry": {
        "tr": "nufus islemi",
        "en": "civil registry",
        "keywords_tr": ["nufus", "dogum belgesi", "ikametgah", "evlilik"],
        "keywords_en": ["civil", "birth certificate", "residence", "marriage"],
    },
}

# Slot selection keywords
SLOT_KEYWORDS = {
    "1": 0, "one": 0, "first": 0, "bir": 0, "ilk": 0, "birinci": 0,
    "2": 1, "two": 1, "second": 1, "iki": 1, "ikinci": 1,
    "3": 2, "three": 2, "third": 2, "uc": 2, "ucuncu": 2,
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


def _detect_service_type(messages: list, language: str) -> str | None:
    """Try to detect service type from conversation history."""
    for msg in reversed(messages[-4:]):
        if not isinstance(msg, HumanMessage) and getattr(msg, "type", "") != "human":
            continue
        normalized = _normalize(msg.content)
        for svc_type, info in SERVICE_TYPES.items():
            key = "keywords_tr" if language != "en" else "keywords_en"
            for keyword in info[key]:
                if _normalize(keyword) in normalized:
                    return svc_type
    return None


def _fetch_appointments(citizen_id: int) -> list[dict]:
    """Call government API to get existing appointments."""
    try:
        r = gov_api_request("GET", f"/appointments/{citizen_id}")
        if r.status_code == 200:
            return r.json()
        return []
    except httpx.RequestError:
        return []


def _fetch_available_slots(service_type: str) -> list[dict]:
    """Fetch available appointment slots from API."""
    try:
        r = gov_api_request("GET", f"/appointments/slots/{service_type}")
        if r.status_code == 200:
            return r.json()
        return []
    except httpx.RequestError:
        return []


def _book_via_api(citizen_id: int, service_type: str, preferred_date: str | None) -> tuple[dict | None, str | None]:
    """Call government API to book appointment."""
    try:
        payload = {
            "citizen_id": citizen_id,
            "service_type": service_type,
            "preferred_date": preferred_date,
        }
        r = gov_api_request("POST", "/appointments", json=payload)
        if r.status_code == 200:
            return r.json(), None

        error_detail = ""
        if r.headers.get("content-type", "").startswith("application/json"):
            detail = r.json().get("detail", "")
            error_detail = str(detail) if not isinstance(detail, str) else detail
        logger.warning(f"Appointment API returned {r.status_code}: {error_detail}")
        return None, error_detail

    except httpx.RequestError as e:
        logger.error(f"Appointment API unreachable: {e}")
        return None, "service_unavailable"


def _detect_slot_selection(messages: list, slots: list[dict]) -> dict | None:
    """Check if user's last message selects one of the offered slots."""
    last_msg = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human":
            last_msg = _normalize(m.content)
            break

    if not last_msg or not slots:
        return None

    # Check for number/ordinal keywords ("first", "1", "second", etc.)
    for keyword, idx in SLOT_KEYWORDS.items():
        if keyword in last_msg and idx < len(slots):
            return slots[idx]

    # Check for date/office keywords in the message
    for slot in slots:
        if slot["date"] in last_msg or _normalize(slot["office"]) in last_msg:
            return slot

    return None


def _slots_were_offered(messages: list) -> bool:
    """Check if slots were already listed in a previous turn."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) or getattr(m, "type", "") == "ai":
            content = m.content.lower()
            if "option" in content and ("which" in content or "hangisi" in content):
                return True
    return False


def appointment_book(state: AgentState) -> dict:
    """Book an appointment with slot selection flow."""
    profile = state.get("citizen_profile")
    language = state.get("language", "tr")
    messages = state.get("messages", [])

    if not profile:
        msg = ("I need to verify your identity before booking an appointment."
               if language == "en" else
               "Randevu almak icin kimlik dogrulamasi gerekiyor.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "appointment_book"),
            "service_node_name": "appointment_book",
            "api_calls_count": 0,
        }

    first_name = get_honorific(profile, language)
    citizen_id = profile.get("citizen_id")

    # Detect service type from conversation
    service_type = _detect_service_type(messages, language)

    if not service_type:
        if language == "en":
            msg = (f"{first_name}, which service would you like to book an appointment for? "
                   f"Passport, ID card, driver's license, or civil registry?")
        else:
            msg = (f"{first_name}, hangi hizmet icin randevu almak istiyorsunuz? "
                   f"Pasaport, kimlik karti, ehliyet veya nufus islemi?")
        return {"messages": [AIMessage(content=msg)], "service_node_name": "appointment_book", "api_calls_count": 0}

    svc_name = SERVICE_TYPES[service_type]["tr" if language != "en" else "en"]

    # Check for existing appointment conflict
    _api_calls = 0
    if citizen_id:
        existing = _fetch_appointments(citizen_id)
        _api_calls += 1
        conflicts = [a for a in existing
                     if a.get("service_type") == service_type and a.get("status") == "confirmed"]
        if conflicts:
            a = conflicts[0]
            if language == "en":
                msg = (f"{first_name}, you already have a confirmed {svc_name} appointment "
                       f"on {format_date_spoken(a['appointment_date'])} at {format_time_spoken(a['appointment_time'])}. "
                       f"Would you like to book for a different service?")
            else:
                msg = (f"{first_name}, {svc_name} icin zaten {a['appointment_date']} tarihinde "
                       f"saat {a['appointment_time']} icin onaylanmis bir randevunuz var. "
                       f"Baska bir hizmet icin randevu almak ister misiniz?")
            return {
                "messages": [AIMessage(content=msg)],
                "completed_intents": mark_completed(state, "appointment_book"),
                "service_node_name": "appointment_book",
                "api_calls_count": _api_calls,
            }

    # Fetch available slots
    slots = _fetch_available_slots(service_type)
    _api_calls += 1

    if not slots:
        if language == "en":
            msg = (f"{first_name}, there are no available {svc_name} slots right now. "
                   f"Please try again later or contact us at ALO 181.")
        else:
            msg = (f"{first_name}, su anda {svc_name} icin musait randevu yok. "
                   f"Lutfen daha sonra tekrar deneyin veya ALO 181'i arayin.")
        return {
            "messages": [AIMessage(content=msg)],
            "completed_intents": mark_completed(state, "appointment_book"),
            "service_node_name": "appointment_book",
            "api_calls_count": _api_calls,
        }

    # Check if user is selecting from previously offered slots
    if _slots_were_offered(messages):
        selected = _detect_slot_selection(messages, slots)
        if selected:
            result, error = _book_via_api(citizen_id, service_type, selected["date"])
            _api_calls += 1

            if result:
                date = result.get("appointment_date", "")
                time_str = result.get("appointment_time", "")
                office = result.get("office", "")

                if language == "en":
                    msg = (f"{first_name}, your {svc_name} appointment has been confirmed. "
                           f"{format_date_spoken(date)} at {format_time_spoken(time_str)}, {office}. "
                           f"Please bring your ID card and any required documents.")
                else:
                    msg = (f"{first_name}, {svc_name} randevunuz onaylandi. "
                           f"Tarih: {date}, Saat: {time_str}, Yer: {office}. "
                           f"Lutfen nufus cuzdaninizi ve gerekli belgeleri yaninizda getirin.")
            else:
                if language == "en":
                    msg = (f"{first_name}, I wasn't able to book that slot. "
                           f"Please try again later or contact us at ALO 181.")
                else:
                    msg = (f"{first_name}, bu slotu rezerve edemedim. "
                           f"Lutfen daha sonra tekrar deneyin veya ALO 181'i arayin.")

            logger.info(f"Appointment booked | citizen_id={citizen_id} | type={service_type} | slot={selected} | success={bool(result)}")

            return {
                "messages": [AIMessage(content=msg)],
                "completed_intents": mark_completed(state, "appointment_book"),
                "service_node_name": "appointment_book",
                "api_calls_count": _api_calls,
            }

    # First turn: present available slots — don't mark completed so we come back
    if language == "en":
        msg = f"{first_name}, here are the available {svc_name} slots. "
        for i, slot in enumerate(slots, 1):
            msg += f"Option {i}: {format_date_spoken(slot['date'])} at {format_time_spoken(slot['time'])}, {slot['office']}. "
        msg += "Which one would you prefer?"
    else:
        msg = f"{first_name}, {svc_name} icin musait randevular. "
        for i, slot in enumerate(slots, 1):
            msg += f"Secenek {i}: {slot['date']} saat {slot['time']}, {slot['office']}. "
        msg += "Hangisini tercih edersiniz?"

    logger.info(f"Appointment slots offered | citizen_id={citizen_id} | type={service_type} | count={len(slots)}")

    # Don't mark completed — user needs to select
    return {
        "messages": [AIMessage(content=msg)],
        "service_node_name": "appointment_book",
        "api_calls_count": _api_calls,
    }
