"""
Tests for signed session tokens (agent/session_token.py).
"""

import base64
import json

import pytest

from agent import session_token
from agent.session_token import DEFAULT_TTL_SECONDS, MIN_SECRET_LENGTH, issue, verify


def _b64(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class TestRoundTrip:

    def test_issued_token_verifies(self):
        claims = verify(issue(7, "conv-a"))
        assert claims["citizen_id"] == 7
        assert claims["conversation_id"] == "conv-a"
        assert claims["exp"] - claims["iat"] == DEFAULT_TTL_SECONDS

    def test_bound_conversation_accepted(self):
        assert verify(issue(7, "conv-a"), conversation_id="conv-a") is not None

    def test_other_conversation_rejected(self):
        assert verify(issue(7, "conv-a"), conversation_id="conv-b") is None


class TestRejection:

    def test_expired_token_rejected(self):
        token = issue(7, "conv-a", ttl_seconds=60, now=1_000)
        assert verify(token, now=1_059) is not None
        assert verify(token, now=1_060) is None

    def test_tampered_payload_rejected(self):
        version, _, signature = issue(7, "conv-a").split(".")
        forged_payload = _b64({"citizen_id": 8, "conversation_id": "conv-a", "iat": 0, "exp": 2**40})
        assert verify(f"{version}.{forged_payload}.{signature}") is None

    def test_tampered_signature_rejected(self):
        token = issue(7, "conv-a")
        flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
        assert verify(flipped) is None

    def test_token_from_other_secret_rejected(self, monkeypatch):
        token = issue(7, "conv-a")
        monkeypatch.setenv("SESSION_TOKEN_SECRET", "another-secret-" + "x" * MIN_SECRET_LENGTH)
        assert verify(token) is None

    @pytest.mark.parametrize("token", [
        "", "abc", "v1.abc", "v2.abc.def", "v1.abc.def.ghi", "v1.çğ.ş", None, 12345,
    ])
    def test_malformed_tokens_rejected(self, token):
        assert verify(token) is None


class TestSecretRequired:

    @pytest.mark.parametrize("value", [None, "x" * (MIN_SECRET_LENGTH - 1)], ids=["missing", "short"])
    def test_issue_and_verify_refuse_weak_secret(self, monkeypatch, value):
        token = issue(7, "conv-a")
        if value is None:
            monkeypatch.delenv("SESSION_TOKEN_SECRET", raising=False)
        else:
            monkeypatch.setenv("SESSION_TOKEN_SECRET", value)
        with pytest.raises(RuntimeError, match="SESSION_TOKEN_SECRET"):
            issue(7, "conv-a")
        with pytest.raises(RuntimeError, match="SESSION_TOKEN_SECRET"):
            verify(token)

    def test_version_prefix(self):
        assert issue(7, "conv-a").startswith(f"{session_token.VERSION}.")
