"""
Shared-secret guards for the government API.

The government routers are mounted on the same public app as the Custom LLM
endpoint (agent/server.py, exposed through ngrok). These dependencies make sure
only our own LangGraph nodes (internal key) and operators (admin key) can reach
routes that read or change citizen data.

- require_internal_key:    X-Internal-Key header must match INTERNAL_API_KEY
- require_admin_key:       X-Admin-Key header must match ADMIN_API_KEY
- require_platform_secret: Authorization: Bearer <PLATFORM_WEBHOOK_SECRET>
                           (the ElevenLabs dispatch tool — every /auth route)

Keys are read from the environment on every request. If a key is not
configured, every request to the routes it guards is refused (fail closed).
"""

import hmac
import os

from fastapi import Header, HTTPException, Request

from agent.logging_config import get_logger

logger = get_logger(__name__)

INTERNAL_KEY_HEADER = "X-Internal-Key"
ADMIN_KEY_HEADER = "X-Admin-Key"


def _verify_key(provided: str | None, env_var: str, request: Request) -> None:
    """Raise 403 unless `provided` matches the secret stored in `env_var`."""
    expected = os.getenv(env_var, "")

    if not expected:
        logger.error(f"{env_var} is not set — refusing request | path={request.url.path}")
        raise HTTPException(status_code=403, detail="Forbidden")

    # compare_digest on bytes: constant-time, and safe for non-ASCII header values
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        logger.warning(f"Rejected request: missing or invalid {env_var} | path={request.url.path}")
        raise HTTPException(status_code=403, detail="Forbidden")


def require_internal_key(
    request: Request,
    x_internal_key: str | None = Header(default=None, alias=INTERNAL_KEY_HEADER),
) -> None:
    """Allow only callers holding INTERNAL_API_KEY (LangGraph nodes)."""
    _verify_key(x_internal_key, "INTERNAL_API_KEY", request)


def require_admin_key(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias=ADMIN_KEY_HEADER),
) -> None:
    """Allow only callers holding ADMIN_API_KEY (operators, never the voice flow)."""
    _verify_key(x_admin_key, "ADMIN_API_KEY", request)


def require_platform_secret(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Allow only the ElevenLabs platform: Authorization: Bearer <PLATFORM_WEBHOOK_SECRET>."""
    token = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            token = value.strip()
    _verify_key(token, "PLATFORM_WEBHOOK_SECRET", request)
