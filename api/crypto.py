"""
Keyed hashing for stored identifiers.

TC Kimlik numbers have only ~900 million valid values, so a plain SHA-256
of one can be reversed by hashing every valid number (minutes on one GPU).
HMAC-SHA256 under a secret pepper (ID_HASH_PEPPER, kept outside the
database) makes a stolen database copy useless without the pepper.

Used for Citizen.tc_kimlik_hash, Citizen.tc_last4_hash and every hash
written to AuthAuditLog.
"""

import hashlib
import hmac
import os

MIN_PEPPER_LENGTH = 32


def _pepper() -> bytes:
    """Read ID_HASH_PEPPER per call; refuse to hash with a missing or weak pepper."""
    pepper = os.getenv("ID_HASH_PEPPER", "")
    if len(pepper) < MIN_PEPPER_LENGTH:
        raise RuntimeError(
            f"ID_HASH_PEPPER must be set and at least {MIN_PEPPER_LENGTH} characters long"
        )
    return pepper.encode("utf-8")


def keyed_hash(value: str) -> str:
    """Return the hex HMAC-SHA256 of `value` under ID_HASH_PEPPER.

    Callers normalize the value first (e.g. digits only for TC Kimlik).
    Raises RuntimeError if the pepper is missing or shorter than 32 characters.
    """
    return hmac.new(_pepper(), value.encode("utf-8"), hashlib.sha256).hexdigest()
