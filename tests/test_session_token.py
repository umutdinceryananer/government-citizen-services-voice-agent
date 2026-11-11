"""
Tests for signed session tokens (agent/session_token.py).

Format: <citizen_id>.<expires_unix>.<hex HMAC-SHA256>
"""

import hashlib
import hmac

import pytest

from agent.session_token import DEFAULT_TTL_SECONDS, MIN_SECRET_LENGTH, issue, verify
from tests.conftest import TEST_SESSION_TOKEN_SECRET


def _change_char(token: str, index: int) -> str:
    """Change one character, keeping it in the same character class."""
    old = token[index]
    new = ("1" if old != "1" else "2") if old.isdigit() else ("a" if old != "a" else "b")
    return token[:index] + new + token[index + 1:]


class TestFormat:

    def test_format_is_id_expiry_hex_signature(self):
        citizen_id, expires, signature = issue(7, now=1_000).split(".")
        assert citizen_id == "7"
        assert expires == str(1_000 + DEFAULT_TTL_SECONDS)
        assert len(signature) == 64 and all(c in "0123456789abcdef" for c in signature)

    def test_default_ttl_is_15_minutes(self):
        assert DEFAULT_TTL_SECONDS == 900

    def test_signature_is_hmac_sha256_of_id_and_expiry(self):
        body, _, signature = issue(7, now=1_000).rpartition(".")
        expected = hmac.new(TEST_SESSION_TOKEN_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        assert signature == expected


class TestVerify:

    def test_valid_token_returns_citizen_id(self):
        assert verify(issue(7)) == 7

    def test_surrounding_whitespace_tolerated(self):
        assert verify(f"  {issue(7)}\n") == 7

    def test_expired_token_rejected(self):
        token = issue(7, ttl_seconds=60, now=1_000)
        assert verify(token, now=1_059) == 7
        assert verify(token, now=1_060) is None

    def test_every_single_character_change_rejected(self):
        token = issue(7, now=1_000)
        for i, ch in enumerate(token):
            if ch == ".":
                continue
            assert verify(_change_char(token, i), now=1_000) is None, f"position {i}"

    def test_token_from_other_secret_rejected(self, monkeypatch):
        token = issue(7)
        monkeypatch.setenv("SESSION_TOKEN_SECRET", "another-secret-" + "x" * MIN_SECRET_LENGTH)
        assert verify(token) is None

    def test_extended_expiry_rejected(self):
        citizen_id, expires, signature = issue(7, now=1_000).split(".")
        assert verify(f"{citizen_id}.{int(expires) + 10_000}.{signature}", now=1_000) is None

    @pytest.mark.parametrize("token", [
        "", "abc", "7.123", "7.123.abc", "x.123." + "a" * 64, "7.123." + "A" * 64,
        "7.123." + "a" * 63, "7.123." + "a" * 64 + ".x", "{{session_token}}", None, 12345,
    ])
    def test_malformed_tokens_rejected(self, token):
        assert verify(token) is None


class TestSecretRequired:

    @pytest.mark.parametrize("value", [None, "x" * (MIN_SECRET_LENGTH - 1)], ids=["missing", "short"])
    def test_issue_and_verify_refuse_weak_secret(self, monkeypatch, value):
        token = issue(7)
        if value is None:
            monkeypatch.delenv("SESSION_TOKEN_SECRET", raising=False)
        else:
            monkeypatch.setenv("SESSION_TOKEN_SECRET", value)
        with pytest.raises(RuntimeError, match="SESSION_TOKEN_SECRET"):
            issue(7)
        with pytest.raises(RuntimeError, match="SESSION_TOKEN_SECRET"):
            verify(token)
