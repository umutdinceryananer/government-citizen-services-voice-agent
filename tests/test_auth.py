"""
Tests for the hardened auth webhook (POST /auth/verify/webhook).

- Only the platform secret gets through (403 otherwise)
- Server-side attempt limit per conversation and per caller number (429)
- One generic failure message — nothing reveals which factor failed
- One audit row per attempt, never containing a factor value
- Success returns a session token that agent.session_token.verify accepts
- Responses carry no raw PII
"""

import json

import pytest
from fastapi.testclient import TestClient

from agent import session_token
from api.auth import AUDIT_METHOD, FAILURE_MESSAGES, LOCKED_MESSAGES, AttemptLimiter, attempt_limiter
from api.crypto import keyed_hash
from api.models import AuthAuditLog, Citizen
from api.seed_data import generate_valid_tc
from api.server import app
from tests.conftest import PLATFORM_AUTH, TEST_PLATFORM_SECRET, TestSession


client = TestClient(app, headers=PLATFORM_AUTH)  # acts as the ElevenLabs dispatch tool
anon = TestClient(app)

TC_AHMET = generate_valid_tc("100000001")
AHMET = {"tc_kimlik_last4": TC_AHMET[-4:], "date_of_birth": "15/03/1990", "father_initial": "M"}
WRONG_DOB = {**AHMET, "date_of_birth": "16/03/1990"}
CALLER = "+905551000001"
OTHER_CALLER = "+905559999999"

# Every way a single factor can be wrong — all must look the same to the caller
FAILURE_CASES = {
    "unknown-last4": {**AHMET, "tc_kimlik_last4": "9999"},
    "wrong-dob": WRONG_DOB,
    "wrong-father-initial": {**AHMET, "father_initial": "K"},
    "bad-format-last4": {**AHMET, "tc_kimlik_last4": "12"},
    "unparseable-dob": {**AHMET, "date_of_birth": "yesterday"},
    "non-letter-initial": {**AHMET, "father_initial": "5"},
}


def _post(factors, conversation_id, caller_number=None, language="tr", c=client):
    body = {**factors, "conversation_id": conversation_id, "language": language}
    if caller_number is not None:
        body["caller_number"] = caller_number
    return c.post("/auth/verify/webhook", json=body)


def _audit_rows(conversation_id):
    db = TestSession()
    try:
        return (
            db.query(AuthAuditLog)
            .filter(AuthAuditLog.session_id == conversation_id)
            .order_by(AuthAuditLog.id)
            .all()
        )
    finally:
        db.close()


def _ahmet_id():
    db = TestSession()
    try:
        return db.query(Citizen).filter_by(first_name="Ahmet", last_name="Yilmaz").one().id
    finally:
        db.close()


class TestPlatformSecret:
    """Only the ElevenLabs dispatch tool (holding the platform secret) may call /auth."""

    def test_correct_secret_passes(self):
        assert _post(AHMET, "conv-secret-ok").status_code == 200

    def test_missing_header_403(self):
        assert _post(AHMET, "conv-secret", c=anon).status_code == 403

    @pytest.mark.parametrize("value", [
        "Bearer wrong-secret",
        f"Basic {TEST_PLATFORM_SECRET}",
        TEST_PLATFORM_SECRET,
        "Bearer ",
    ], ids=["wrong-secret", "wrong-scheme", "no-scheme", "empty-bearer"])
    def test_wrong_header_403(self, value):
        r = anon.post("/auth/verify/webhook", headers={"Authorization": value},
                      json={**AHMET, "conversation_id": "conv-secret"})
        assert r.status_code == 403

    def test_unset_secret_refuses_everyone(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_WEBHOOK_SECRET", raising=False)
        assert _post(AHMET, "conv-secret").status_code == 403

    def test_rejected_calls_are_not_counted_or_audited(self):
        for _ in range(5):
            _post(WRONG_DOB, "conv-secret-spam", c=anon)
        assert _post(AHMET, "conv-secret-spam").status_code == 200
        assert len(_audit_rows("conv-secret-spam")) == 1


class TestAttemptLimit:
    """3 failures per conversation and per caller number within 15 minutes."""

    def test_fourth_attempt_in_conversation_is_429(self):
        for _ in range(3):
            assert _post(WRONG_DOB, "conv-lock").status_code == 401
        # Even the right answer is refused once the conversation is locked
        assert _post(AHMET, "conv-lock").status_code == 429

    def test_new_conversation_from_same_caller_is_429(self):
        for _ in range(3):
            _post(WRONG_DOB, "conv-caller-a", caller_number=CALLER)
        assert _post(AHMET, "conv-caller-b", caller_number=CALLER).status_code == 429

    def test_new_conversation_from_other_caller_allowed(self):
        for _ in range(3):
            _post(WRONG_DOB, "conv-caller-a", caller_number=CALLER)
        assert _post(AHMET, "conv-caller-b", caller_number=OTHER_CALLER).status_code == 200

    def test_without_caller_number_only_conversation_counts(self):
        for _ in range(3):
            _post(WRONG_DOB, "conv-web-a")
        assert _post(AHMET, "conv-web-b").status_code == 200

    def test_successes_do_not_use_up_attempts(self):
        for _ in range(4):
            assert _post(AHMET, "conv-repeat-success").status_code == 200

    def test_locked_message_in_call_language(self):
        for _ in range(3):
            _post(WRONG_DOB, "conv-lock-en", language="en")
        r = _post(AHMET, "conv-lock-en", language="en")
        assert r.status_code == 429
        assert r.json() == {"detail": LOCKED_MESSAGES["en"]}

    def test_caller_number_kept_only_as_keyed_hash(self):
        _post(WRONG_DOB, "conv-caller-hash", caller_number=CALLER)
        keys = list(attempt_limiter._failures)
        assert f"caller:{keyed_hash(CALLER)}" in keys
        assert not any(CALLER in k for k in keys)


class TestAttemptLimiterWindow:
    """Sliding-window behaviour with a controllable clock."""

    def _limiter(self):
        now = [0.0]
        return AttemptLimiter(max_failures=3, window_seconds=900, clock=lambda: now[0]), now

    def test_lock_expires_after_window(self):
        limiter, now = self._limiter()
        for _ in range(3):
            limiter.record_failure("k")
        now[0] = 899
        assert limiter.is_locked("k")
        now[0] = 900
        assert not limiter.is_locked("k")
        assert limiter.failures("k") == 0

    def test_old_failures_drop_out_one_by_one(self):
        limiter, now = self._limiter()
        limiter.record_failure("k")
        now[0] = 500
        limiter.record_failure("k")
        limiter.record_failure("k")
        assert limiter.is_locked("k")
        now[0] = 900  # first failure expired, two remain
        assert limiter.failures("k") == 2
        assert not limiter.is_locked("k")

    def test_keys_are_independent(self):
        limiter, _ = self._limiter()
        for _ in range(3):
            limiter.record_failure("a")
        assert limiter.is_locked("a")
        assert not limiter.is_locked("b")
        assert limiter.is_locked("b", "a")


class TestGenericFailure:
    """No response reveals which factor was wrong, or whether the ID exists."""

    def test_unknown_last4_is_really_unknown(self):
        assert FAILURE_CASES["unknown-last4"]["tc_kimlik_last4"] not in {
            TC_AHMET[-4:], generate_valid_tc("200000002")[-4:],
        }

    @pytest.mark.parametrize("language", ["tr", "en"])
    def test_all_failures_identical(self, language):
        responses = {
            name: _post(factors, f"conv-generic-{name}-{language}", language=language)
            for name, factors in FAILURE_CASES.items()
        }
        for name, r in responses.items():
            assert r.status_code == 401, name
            assert r.json() == {"detail": FAILURE_MESSAGES[language]}, name

    def test_unknown_language_falls_back_to_turkish(self):
        r = _post(WRONG_DOB, "conv-generic-de", language="de")
        assert r.json() == {"detail": FAILURE_MESSAGES["tr"]}

    def test_english_success_message(self):
        assert _post(AHMET, "conv-welcome-en", language="en").json()["message"] == "Welcome, Ahmet."


class TestAuditTrail:
    """One audit row per attempt — outcome and category only, never the factors."""

    def test_one_row_per_attempt(self):
        for _ in range(3):
            _post(WRONG_DOB, "conv-audit")
        _post(AHMET, "conv-audit")  # locked

        rows = _audit_rows("conv-audit")
        assert [r.attempt_number for r in rows] == [1, 2, 3, 4]
        assert [r.result for r in rows] == ["failure"] * 4
        assert [r.failure_reason for r in rows] == ["no_match", "no_match", "no_match", "locked"]
        assert all(r.method == AUDIT_METHOD for r in rows)
        assert all(r.citizen_id_hash is None for r in rows)

    def test_success_row_has_keyed_citizen_id_hash(self):
        _post(AHMET, "conv-audit-ok")
        [row] = _audit_rows("conv-audit-ok")
        assert row.result == "success"
        assert row.failure_reason is None
        assert row.citizen_id_hash == keyed_hash(str(_ahmet_id()))

    def test_format_failure_category(self):
        _post(FAILURE_CASES["bad-format-last4"], "conv-audit-format")
        [row] = _audit_rows("conv-audit-format")
        assert row.failure_reason == "format"

    def test_no_row_contains_a_factor_value(self):
        _post(AHMET, "conv-pii-ok")
        _post(FAILURE_CASES["bad-format-last4"], "conv-pii-format")
        for _ in range(4):
            _post(WRONG_DOB, "conv-pii-lock", caller_number=CALLER)

        db = TestSession()
        rows = db.query(AuthAuditLog).all()
        db.close()
        assert len(rows) == 6

        factor_values = [TC_AHMET, TC_AHMET[-4:], "15/03/1990", "16/03/1990", "1990", "Mehmet", CALLER]
        for row in rows:
            # citizen_id_hash is checked exactly in test_success_row_has_keyed_citizen_id_hash
            text = " ".join(str(v) for v in (
                row.session_id, row.method, row.attempt_number, row.result, row.failure_reason,
            ))
            for value in factor_values:
                assert value not in text
            assert row.failure_reason in {None, "format", "no_match", "locked"}
            assert row.citizen_id_hash in {None, keyed_hash(str(_ahmet_id()))}


class TestSessionTokenIssued:

    def test_success_returns_verifiable_token(self):
        r = _post(AHMET, "conv-token")
        token = r.json()["session_token"]
        claims = session_token.verify(token, conversation_id="conv-token")
        assert claims is not None
        assert claims["citizen_id"] == r.json()["citizen_profile"]["citizen_id"] == _ahmet_id()

    def test_token_is_bound_to_its_conversation(self):
        token = _post(AHMET, "conv-token-a").json()["session_token"]
        assert session_token.verify(token, conversation_id="conv-token-b") is None

    def test_failure_returns_no_token(self):
        assert "session_token" not in _post(WRONG_DOB, "conv-token-fail").json()


class TestPIISafety:
    """Success responses and tokens carry no raw PII."""

    def test_profile_has_no_full_lastname(self):
        profile = _post(AHMET, "conv-pii-profile").json()["citizen_profile"]
        assert profile["last_name_initial"] == "Y***"
        assert "Yilmaz" not in str(profile)

    def test_response_has_no_tc_or_dob(self):
        text = json.dumps(_post(AHMET, "conv-pii-response").json())
        assert TC_AHMET not in text
        assert "15/03/1990" not in text

    def test_token_claims_hold_no_personal_data(self):
        token = _post(AHMET, "conv-pii-token").json()["session_token"]
        claims = session_token.verify(token)
        assert set(claims) == {"citizen_id", "conversation_id", "iat", "exp"}


class TestRemovedEndpoints:
    """The unused tc-kimlik and app-ref endpoints are gone."""

    @pytest.mark.parametrize("path", ["/auth/verify/tc-kimlik", "/auth/verify/app-ref"])
    def test_endpoint_removed(self, path):
        assert client.post(path, json={}).status_code == 404
