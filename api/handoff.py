"""
Human handoff and guest mode endpoints.

POST /handoff    — logs transfer event, returns handoff confirmation (X-Internal-Key)
POST /guest/info — limited FAQ access without authentication (public)
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agent.logging_config import get_logger
from api.models import AuthAuditLog, get_db
from api.security import require_internal_key

logger = get_logger(__name__)

# /handoff writes to the audit log — LangGraph nodes only.
router = APIRouter(tags=["handoff"], dependencies=[Depends(require_internal_key)])
# /guest/info is static FAQ text — open to unauthenticated callers.
guest_router = APIRouter(tags=["handoff"])


# --- Request / Response models ---

class HandoffRequest(BaseModel):
    """Request to transfer caller to human operator."""
    session_id: str
    reason: str  # "auth_failure", "caller_request", "out_of_scope", "frustration"
    auth_attempts: int = 0
    language: str = "tr"


class HandoffResponse(BaseModel):
    """Handoff confirmation."""
    transferred: bool
    message: str
    queue_position: int | None = None


class GuestInfoRequest(BaseModel):
    """Guest mode query — no auth required."""
    question: str
    language: str = "tr"


class GuestInfoResponse(BaseModel):
    """Guest mode response — limited to general info."""
    answer: str
    source: str
    requires_auth: bool = False


# --- Handoff reasons for analytics ---

HANDOFF_REASONS = {
    "auth_failure": "Kimlik dogrulama basarisiz (3 deneme)",
    "caller_request": "Arayan operatore baglanmak istedi",
    "out_of_scope": "Kapsam disi talep",
    "frustration": "Arayan memnuniyetsizligi tespit edildi",
}

# --- Guest mode FAQ — no auth required ---

GUEST_FAQ = {
    "tr": [
        {
            "keywords": ["calisma", "saat", "mesai"],
            "answer": "Vatandas Hizmetleri hafta ici 09:00-17:00 arasinda hizmet vermektedir.",
            "source": "Genel Bilgi",
        },
        {
            "keywords": ["adres", "nerede", "konum"],
            "answer": "En yakin vatandas hizmetleri ofisi icin e-Devlet uygulamasindan veya ALO 181'den bilgi alabilirsiniz.",
            "source": "Genel Bilgi",
        },
        {
            "keywords": ["belge", "gerekli", "evrak", "dokuman"],
            "answer": "Basvuru icin gerekli belgeler: nufus cuzdani fotokopisi, ikametgah belgesi ve 2 adet vesikalik fotograf. Detayli bilgi icin kimlik dogrulamasi gereklidir.",
            "source": "Genel Bilgi",
        },
        {
            "keywords": ["ucret", "fiyat", "harc", "masraf"],
            "answer": "Islem ucretleri hizmet turune gore degismektedir. Detayli ucret bilgisi icin kimlik dogrulamasi yapmaniz veya e-Devlet uzerinden kontrol etmeniz gerekmektedir.",
            "source": "Genel Bilgi",
        },
        {
            "keywords": ["randevu", "randevu almak", "randevu alma"],
            "answer": "Randevu almak icin kimlik dogrulamasi gerekmektedir. Kimliginizi dogruladiktan sonra size uygun tarih ve saati belirleyebiliriz.",
            "source": "Genel Bilgi",
            "requires_auth": True,
        },
        {
            "keywords": ["basvuru", "durum", "sorgula"],
            "answer": "Basvuru durumu sorgulamak icin kimlik dogrulamasi gerekmektedir. TC Kimlik numaraniz veya basvuru numaraniz ile dogrulama yapabiliriz.",
            "source": "Genel Bilgi",
            "requires_auth": True,
        },
    ],
    "en": [
        {
            "keywords": ["hours", "open", "working"],
            "answer": "Citizen Services operates Monday to Friday, 09:00-17:00.",
            "source": "General Info",
        },
        {
            "keywords": ["address", "location", "where"],
            "answer": "For the nearest citizen services office, please check the e-Government portal or call ALO 181.",
            "source": "General Info",
        },
        {
            "keywords": ["document", "required", "need"],
            "answer": "Required documents: ID card copy, proof of address, and 2 passport photos. For detailed information, identity verification is required.",
            "source": "General Info",
        },
        {
            "keywords": ["fee", "cost", "price", "charge"],
            "answer": "Processing fees vary by service type. For detailed fee information, please verify your identity or check the e-Government portal.",
            "source": "General Info",
        },
        {
            "keywords": ["appointment", "book", "schedule"],
            "answer": "Booking an appointment requires identity verification. Once verified, we can find a suitable date and time for you.",
            "source": "General Info",
            "requires_auth": True,
        },
        {
            "keywords": ["application", "status", "check"],
            "answer": "Checking application status requires identity verification. You can verify using your TC ID number or application reference number.",
            "source": "General Info",
            "requires_auth": True,
        },
    ],
}


def _find_faq_answer(question: str, language: str) -> GuestInfoResponse | None:
    """Simple keyword matching for guest FAQ."""
    question_lower = question.lower()
    faqs = GUEST_FAQ.get(language, GUEST_FAQ["tr"])

    for faq in faqs:
        if any(kw in question_lower for kw in faq["keywords"]):
            return GuestInfoResponse(
                answer=faq["answer"],
                source=faq.get("source", "Genel Bilgi"),
                requires_auth=faq.get("requires_auth", False),
            )

    return None


# --- Endpoints ---

@router.post("/handoff", response_model=HandoffResponse)
def request_handoff(
    request: HandoffRequest,
    db: Session = Depends(get_db),
):
    """Transfer caller to human operator."""
    reason_text = HANDOFF_REASONS.get(request.reason, request.reason)
    logger.info(f"Handoff requested | session={request.session_id} | reason={request.reason}")

    # Log handoff event in audit trail
    audit = AuthAuditLog(
        timestamp=datetime.now(UTC),
        session_id=request.session_id,
        method="handoff",
        attempt_number=request.auth_attempts,
        result="transferred",
        failure_reason=request.reason,
    )
    db.add(audit)
    db.commit()

    # Simulate queue position (mock)
    queue_position = 3

    if request.language == "en":
        message = f"Transferring you to a human operator. Reason: {reason_text}. You are number {queue_position} in queue."
    else:
        message = f"Sizi bir operatore bagliyorum. Sebep: {reason_text}. Sirada {queue_position}. siradasiniz."

    return HandoffResponse(
        transferred=True,
        message=message,
        queue_position=queue_position,
    )


@guest_router.post("/guest/info", response_model=GuestInfoResponse)
def guest_info(request: GuestInfoRequest):
    """Answer general questions without authentication."""
    logger.info(f"Guest query | language={request.language}")

    result = _find_faq_answer(request.question, request.language)

    if result:
        return result

    # No match found
    if request.language == "en":
        return GuestInfoResponse(
            answer="I couldn't find specific information about your question. For detailed assistance, identity verification may be required. Would you like to verify your identity or speak with a human operator?",
            source="System",
        )
    return GuestInfoResponse(
        answer="Sorunuzla ilgili belirli bir bilgi bulamadim. Detayli yardim icin kimlik dogrulamasi gerekebilir. Kimliginizi dogrulamak veya bir operatorle gorusmek ister misiniz?",
        source="Sistem",
    )
