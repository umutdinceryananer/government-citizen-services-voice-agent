"""
Tests for handoff and guest mode endpoints.
"""

import pytest
from fastapi.testclient import TestClient

from api.models import AuthAuditLog
from api.server import app
from tests.conftest import TEST_INTERNAL_KEY, TestSession


# Acts as the LangGraph nodes do: every request carries the internal key.
client = TestClient(app, headers={"X-Internal-Key": TEST_INTERNAL_KEY})


class TestHandoff:
    """Test human handoff endpoint."""

    def test_auth_failure_handoff(self):
        r = client.post("/handoff", json={
            "session_id": "h1",
            "reason": "auth_failure",
            "auth_attempts": 3,
            "language": "tr",
        })
        data = r.json()
        assert data["transferred"] is True
        assert data["queue_position"] is not None
        assert "operator" in data["message"].lower()

    def test_caller_request_handoff(self):
        r = client.post("/handoff", json={
            "session_id": "h2",
            "reason": "caller_request",
            "language": "tr",
        })
        assert r.json()["transferred"] is True

    def test_handoff_english(self):
        r = client.post("/handoff", json={
            "session_id": "h3",
            "reason": "out_of_scope",
            "language": "en",
        })
        data = r.json()
        assert "transfer" in data["message"].lower()

    def test_handoff_logged_in_audit(self):
        client.post("/handoff", json={
            "session_id": "h4",
            "reason": "frustration",
            "auth_attempts": 2,
            "language": "tr",
        })
        db = TestSession()
        log = db.query(AuthAuditLog).filter(AuthAuditLog.session_id == "h4").first()
        assert log is not None
        assert log.method == "handoff"
        assert log.result == "transferred"
        assert log.failure_reason == "frustration"
        db.close()


class TestGuestInfo:
    """Test guest mode FAQ endpoint."""

    def test_working_hours_tr(self):
        r = client.post("/guest/info", json={
            "question": "Calisma saatleri nedir?",
            "language": "tr",
        })
        data = r.json()
        assert "09:00" in data["answer"]
        assert data["requires_auth"] is False

    def test_working_hours_en(self):
        r = client.post("/guest/info", json={
            "question": "What are your working hours?",
            "language": "en",
        })
        data = r.json()
        assert "09:00" in data["answer"]

    def test_fee_inquiry_tr(self):
        r = client.post("/guest/info", json={
            "question": "Ucretler ne kadar?",
            "language": "tr",
        })
        data = r.json()
        assert "ucret" in data["answer"].lower()

    def test_auth_required_flagged(self):
        r = client.post("/guest/info", json={
            "question": "Basvuru durumumu sorgulayabilir miyim?",
            "language": "tr",
        })
        data = r.json()
        assert data["requires_auth"] is True

    def test_no_match_fallback(self):
        r = client.post("/guest/info", json={
            "question": "Hava nasil bugun?",
            "language": "tr",
        })
        data = r.json()
        assert data["source"] == "Sistem"

    def test_address_info_en(self):
        r = client.post("/guest/info", json={
            "question": "Where is the nearest office?",
            "language": "en",
        })
        data = r.json()
        assert "office" in data["answer"].lower() or "181" in data["answer"]
