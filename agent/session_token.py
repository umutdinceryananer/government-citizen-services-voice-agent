"""
Signed session tokens for authenticated calls.

verify_webhook (api/auth.py) issues a token when a caller passes identity
verification. ElevenLabs stores it in the {{session_token}} dynamic variable;
custom LLM node prompts carry it as a `SESSION_TOKEN=<token>` line, and the
Custom LLM server (agent/server.py) verifies it instead of trusting names and
IDs that appear in the prompt.

Format:  <citizen_id>.<expires_unix>.<hex HMAC-SHA256>
  The HMAC covers "<citizen_id>.<expires_unix>" under SESSION_TOKEN_SECRET.

The token holds only the internal citizen id and an expiry — no personal
data. It is signed, not encrypted, and is not bound to a conversation, so it
stays valid for its TTL wherever it is presented.
"""

import hashlib
import hmac
import os
import re
import time

MIN_SECRET_LENGTH = 32
DEFAULT_TTL_SECONDS = 900  # 15 minutes

_TOKEN_RE = re.compile(r"(\d{1,18})\.(\d{1,12})\.([0-9a-f]{64})", re.ASCII)


def _secret() -> bytes:
    """Read SESSION_TOKEN_SECRET per call; refuse to sign or verify with a missing or weak secret."""
    secret = os.getenv("SESSION_TOKEN_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"SESSION_TOKEN_SECRET must be set and at least {MIN_SECRET_LENGTH} characters long"
        )
    return secret.encode("utf-8")


def _signature(body: str) -> str:
    return hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()


def issue(subject_id: int, ttl_seconds: int = DEFAULT_TTL_SECONDS, now: float | None = None) -> str:
    """Return a signed token for `subject_id` (citizen id), valid for `ttl_seconds`.

    Raises RuntimeError if SESSION_TOKEN_SECRET is missing or too short.
    """
    expires = int(time.time() if now is None else now) + ttl_seconds
    body = f"{int(subject_id)}.{expires}"
    return f"{body}.{_signature(body)}"


def verify(token: str, now: float | None = None) -> int | None:
    """Return the citizen id for a genuine, unexpired token, else None.

    Raises RuntimeError if SESSION_TOKEN_SECRET is missing or too short — a
    misconfiguration should be loud, not look like a bad token.
    """
    _secret()  # fail loudly on misconfiguration before inspecting the token

    if not isinstance(token, str):
        return None
    match = _TOKEN_RE.fullmatch(token.strip())
    if not match:
        return None
    subject, expires, signature = match.groups()

    expected = _signature(f"{subject}.{expires}")
    if not hmac.compare_digest(expected, signature):
        return None
    current = time.time() if now is None else now
    if int(expires) <= current:
        return None
    return int(subject)
