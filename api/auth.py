"""
Caller identity verification for the ElevenLabs Workflow dispatch tool.

POST /auth/verify/webhook — last 4 TC Kimlik digits + date of birth + father's
name initial (STT-friendly, no 11-digit number over voice).

Hardening:
- Only the platform may call /auth: Authorization: Bearer PLATFORM_WEBHOOK_SECRET
- Server-side attempt limit per conversation and per caller number
- One generic failure message per language; the specific reason goes only
  to the audit log
- Every attempt is audited without factor values; success returns a signed
  session token (agent/session_token.py)

The former /auth/verify/tc-kimlik and /auth/verify/app-ref endpoints were
removed — the voice flow never called them.
"""

import threading
import time
from collections import deque
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent import session_token
from agent.logging_config import get_logger
from agent.tools.date_parser import normalize_date
from api.crypto import keyed_hash
from api.models import Application, AuthAuditLog, Citizen, get_db
from api.security import require_platform_secret

logger = get_logger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["authentication"],
    dependencies=[Depends(require_platform_secret)],
)

AUDIT_METHOD = "webhook_last4_dob_father"

FAILURE_MESSAGES = {
    "tr": "Kimlik dogrulama basarisiz. Lutfen bilgilerinizi kontrol edip tekrar deneyin.",
    "en": "Identity verification failed. Please check your details and try again.",
}
LOCKED_MESSAGES = {
    "tr": "Cok fazla basarisiz deneme yapildi. Lutfen daha sonra tekrar deneyin veya bir operatore baglanmak isteyin.",
    "en": "Too many failed attempts. Please try again later or ask to speak with an operator.",
}
WELCOME_MESSAGES = {
    "tr": "Hosgeldiniz, {name}.",
    "en": "Welcome, {name}.",
}


# --- Attempt limiting ---

class AttemptLimiter:
    """Counts failed auth attempts per key in a sliding time window.

    Per process and in memory: fine for the single-process demo server, but
    with several workers or instances each keeps its own counts, and a
    restart clears them. Production needs a shared store (e.g. Redis keys
    with a TTL) so the limit holds across processes.
    """

    MAX_TRACKED_KEYS = 10_000  # sweep expired keys beyond this to bound memory

    def __init__(self, max_failures: int = 3, window_seconds: float = 15 * 60, clock=time.monotonic):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        """Drop expired timestamps for `key` and return what is left. Caller holds the lock."""
        stamps = self._failures.get(key)
        if stamps is None:
            return deque()
        while stamps and stamps[0] <= now - self.window_seconds:
            stamps.popleft()
        if not stamps:
            del self._failures[key]
        return stamps

    def failures(self, key: str) -> int:
        with self._lock:
            return len(self._recent(key, self._clock()))

    def is_locked(self, *keys: str) -> bool:
        with self._lock:
            now = self._clock()
            return any(len(self._recent(k, now)) >= self.max_failures for k in keys)

    def record_failure(self, *keys: str) -> None:
        with self._lock:
            now = self._clock()
            if len(self._failures) > self.MAX_TRACKED_KEYS:
                for k in list(self._failures):
                    self._recent(k, now)
            for k in keys:
                self._failures.setdefault(k, deque()).append(now)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


# 3 failures per conversation and per caller number within 15 minutes
attempt_limiter = AttemptLimiter()


# --- Request / Response models ---

class AuthWebhookRequest(BaseModel):
    """Dispatch tool request.

    The three factors are extracted by the LLM from the conversation;
    conversation_id and caller_number come from ElevenLabs system variables.
    """
    tc_kimlik_last4: str
    date_of_birth: str
    father_initial: str
    conversation_id: str = Field(min_length=1)  # {{system__conversation_id}}
    caller_number: str | None = None             # {{system__caller_id}}, absent for web calls
    language: str = "tr"                         # call language: "tr" or "en"


class AuthResponse(BaseModel):
    """Success response. Failures are HTTP errors with a generic `detail` message."""
    is_error: bool
    message: str
    citizen_profile: dict | None = None
    guidance: str | None = None
    session_token: str | None = None


# --- Helpers ---

def _citizen_to_safe_profile(citizen: Citizen, db: Session) -> dict:
    """Convert citizen record to a safe profile (no raw PII).

    Includes citizen_id for subsequent API calls (appointment, document).
    Returns the most recent application for backward compatibility with
    ElevenLabs dynamic variables ({{application_ref}}, {{application_status}}).
    """
    latest_app = (
        db.query(Application)
        .filter(Application.citizen_id == citizen.id)
        .order_by(Application.id.desc())
        .first()
    )

    profile = {
        "citizen_id": citizen.id,
        "first_name": citizen.first_name,
        "last_name_initial": citizen.last_name[0] + "***",
        "gender": getattr(citizen, "gender", "M"),
        "language_preference": citizen.language_preference,
    }

    if latest_app:
        profile["application_ref"] = latest_app.application_ref
        profile["application_status"] = latest_app.status

    return profile


def _log_audit(
    db: Session,
    conversation_id: str,
    attempt_number: int,
    result: str,
    citizen_id_hash: str | None = None,
    failure_reason: str | None = None,
) -> None:
    """Write an audit record. Never stores the factors (last 4, DOB, father initial)."""
    db.add(AuthAuditLog(
        timestamp=datetime.now(UTC),
        session_id=conversation_id,
        method=AUDIT_METHOD,
        attempt_number=attempt_number,
        result=result,
        citizen_id_hash=citizen_id_hash,
        failure_reason=failure_reason,
    ))
    db.commit()


def _limiter_keys(request: AuthWebhookRequest) -> list[str]:
    """Limit per conversation, and per caller number when the platform sends one."""
    keys = [f"conversation:{request.conversation_id}"]
    caller = (request.caller_number or "").strip()
    if caller:
        keys.append(f"caller:{keyed_hash(caller)}")
    return keys


def _match_citizen(request: AuthWebhookRequest, db: Session) -> tuple[Citizen | None, str | None]:
    """Return (citizen, None) on a unique match, else (None, "format" | "no_match")."""
    last4 = "".join(c for c in request.tc_kimlik_last4 if c.isdigit())
    normalized_dob, date_error = normalize_date(request.date_of_birth)
    father_initial = request.father_initial.strip().upper()[:1]
    if len(last4) != 4 or date_error or not father_initial.isalpha():
        return None, "format"

    # Match keyed hash of last 4 digits + DOB in SQL (indexed), then father
    # initial in Python — str.upper() is Unicode-aware, SQLite's upper() is not.
    candidates = db.query(Citizen).filter(
        Citizen.tc_last4_hash == keyed_hash(last4),
        Citizen.date_of_birth == normalized_dob,
    ).all()
    matches = [c for c in candidates if c.father_name and c.father_name[0].upper() == father_initial]

    # Exactly one citizen must match. Two citizens sharing last 4 + DOB +
    # father initial cannot be told apart, so refuse rather than guess.
    if len(matches) > 1:
        logger.warning(f"Auth webhook ambiguous match | candidates={len(matches)}")
    if len(matches) != 1:
        return None, "no_match"
    return matches[0], None


# --- Endpoint ---

@router.post("/verify/webhook", response_model=AuthResponse)
def verify_webhook(
    request: AuthWebhookRequest,
    db: Session = Depends(get_db),
):
    """Webhook-compatible auth for the ElevenLabs Workflow dispatch tool.

    200 + session_token on success (dispatch tool routes to the authenticated
    subagent); 401 with one generic message on any failure; 429 once the
    attempt limit is reached. Factor values are never logged or audited.
    """
    language = "en" if request.language.strip().lower().startswith("en") else "tr"
    keys = _limiter_keys(request)
    attempt = attempt_limiter.failures(keys[0]) + 1  # attempt number within this conversation
    logger.info(f"Auth webhook attempt | conversation={request.conversation_id} | attempt={attempt}")

    if attempt_limiter.is_locked(*keys):
        _log_audit(db, request.conversation_id, attempt, "failure", failure_reason="locked")
        logger.warning(f"Auth webhook locked | conversation={request.conversation_id}")
        raise HTTPException(status_code=429, detail=LOCKED_MESSAGES[language])

    citizen, reason = _match_citizen(request, db)
    if citizen is None:
        attempt_limiter.record_failure(*keys)
        _log_audit(db, request.conversation_id, attempt, "failure", failure_reason=reason)
        raise HTTPException(status_code=401, detail=FAILURE_MESSAGES[language])

    token = session_token.issue(citizen.id, request.conversation_id)
    _log_audit(db, request.conversation_id, attempt, "success", citizen_id_hash=keyed_hash(str(citizen.id)))
    logger.info(f"Auth webhook success | conversation={request.conversation_id} | citizen_id={citizen.id}")

    return AuthResponse(
        is_error=False,
        message=WELCOME_MESSAGES[language].format(name=citizen.first_name),
        citizen_profile=_citizen_to_safe_profile(citizen, db),
        session_token=token,
    )
