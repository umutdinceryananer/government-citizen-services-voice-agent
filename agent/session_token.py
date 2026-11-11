"""
Signed session tokens for authenticated calls.

verify_webhook (api/auth.py) issues a token when a caller passes identity
verification. ElevenLabs stores it in the {{session_token}} dynamic variable,
and the Custom LLM server verifies it instead of trusting names and IDs that
appear in the prompt.

Format:  v1.<payload>.<signature>
  payload   = base64url(JSON {"citizen_id", "conversation_id", "iat", "exp"})
  signature = base64url(HMAC-SHA256(SESSION_TOKEN_SECRET, "v1." + payload))

The payload is signed, not encrypted. It holds only the internal citizen id
and the conversation id — no personal data. Binding the token to the
conversation id stops a token from one call being replayed in another.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import time

VERSION = "v1"
MIN_SECRET_LENGTH = 32
DEFAULT_TTL_SECONDS = 60 * 60  # longer than any call


def _secret() -> bytes:
    """Read SESSION_TOKEN_SECRET per call; refuse to sign or verify with a missing or weak secret."""
    secret = os.getenv("SESSION_TOKEN_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"SESSION_TOKEN_SECRET must be set and at least {MIN_SECRET_LENGTH} characters long"
        )
    return secret.encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: str) -> str:
    return _b64encode(hmac.new(_secret(), signing_input.encode("ascii"), hashlib.sha256).digest())


def issue(
    citizen_id: int,
    conversation_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Return a signed token for `citizen_id`, bound to `conversation_id`.

    Raises RuntimeError if SESSION_TOKEN_SECRET is missing or too short.
    """
    issued_at = int(time.time() if now is None else now)
    claims = {
        "citizen_id": citizen_id,
        "conversation_id": conversation_id,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    payload = _b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{VERSION}.{payload}"
    return f"{signing_input}.{_sign(signing_input)}"


def verify(token: str, conversation_id: str | None = None, now: float | None = None) -> dict | None:
    """Return the token's claims if it is genuine and unexpired, else None.

    When `conversation_id` is given, the token must also have been issued for
    that conversation. Raises RuntimeError if SESSION_TOKEN_SECRET is missing
    or too short — a misconfiguration should be loud, not look like a bad token.
    """
    _secret()  # fail loudly on misconfiguration before inspecting the token

    if not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        return None

    try:
        expected = _sign(f"{parts[0]}.{parts[1]}")
    except UnicodeEncodeError:  # non-ASCII payload cannot be ours
        return None
    if not hmac.compare_digest(expected.encode("ascii"), parts[2].encode("utf-8")):
        return None

    try:
        claims = json.loads(_b64decode(parts[1]))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int):
        return None

    current = time.time() if now is None else now
    if claims["exp"] <= current:
        return None
    if conversation_id is not None and claims.get("conversation_id") != conversation_id:
        return None
    return claims
