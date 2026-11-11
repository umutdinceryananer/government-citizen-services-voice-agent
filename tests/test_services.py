"""
Tests for government services API endpoints and LangGraph node integration.

Unit tests: API endpoints via TestClient (no real server needed).
Integration tests: LangGraph nodes calling real API via TestClient.
"""

import pytest
from fastapi.testclient import TestClient

from api.server import app
from api.models import Citizen, Appointment, DocumentRequest
from tests.conftest import TEST_ADMIN_KEY, TEST_INTERNAL_KEY, TestSession


# Acts as the LangGraph nodes do: every request carries the internal key.
client = TestClient(app, headers={"X-Internal-Key": TEST_INTERNAL_KEY})
ADMIN = {"X-Admin-Key": TEST_ADMIN_KEY}


class TestApplicationStatus:
    """Test GET /applications/{ref_number}."""

    def test_existing_application(self):
        r = client.get("/applications/2024-TR-0001")
        assert r.status_code == 200
        data = r.json()
        assert data["application_ref"] == "2024-TR-0001"
        assert data["service_type"] == "passport"
        assert data["status"] == "in_review"
        assert data["first_name"] == "Ahmet"
        assert data["last_name_initial"] == "Y***"
        assert data["estimated_completion"] is not None

    def test_unknown_application_404(self):
        r = client.get("/applications/9999-XX-0000")
        assert r.status_code == 404

    def test_case_insensitive_ref(self):
        r = client.get("/applications/2024-tr-0001")
        assert r.status_code == 200

    def test_profile_has_no_full_lastname(self):
        r = client.get("/applications/2024-TR-0001")
        assert "Yilmaz" not in str(r.json())


class TestListCitizenApplications:
    """Test GET /applications?citizen_id=X."""

    def test_list_applications_for_citizen_with_multiple(self):
        r = client.get("/applications", params={"citizen_id": 1})
        assert r.status_code == 200
        apps = r.json()
        assert len(apps) == 2
        refs = [a["application_ref"] for a in apps]
        assert "2024-TR-0001" in refs
        assert "2024-TR-0024" in refs

    def test_list_applications_includes_service_type(self):
        r = client.get("/applications", params={"citizen_id": 1})
        types = {a["service_type"] for a in r.json()}
        assert "passport" in types
        assert "id_card" in types

    def test_list_applications_unknown_citizen_404(self):
        r = client.get("/applications", params={"citizen_id": 99999})
        assert r.status_code == 404

    def test_list_applications_single(self):
        r = client.get("/applications", params={"citizen_id": 2})
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestAppointmentBooking:
    """Test POST /appointments and GET /appointments/{citizen_id}."""

    def _future_date(self, days_ahead=1):
        """Return a future business day string for testing (skips weekends)."""
        from datetime import date, timedelta
        d = date.today() + timedelta(days=days_ahead)
        while d.weekday() >= 5:  # Skip Saturday/Sunday
            d += timedelta(days=1)
        return str(d)

    def test_book_appointment(self):
        future = self._future_date(1)
        r = client.post("/appointments", json={
            "citizen_id": 1,
            "service_type": "passport",
            "preferred_date": future,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "confirmed"
        assert data["office"] != ""
        assert data["appointment_date"] == future

    def test_book_without_preferred_date(self):
        r = client.post("/appointments", json={
            "citizen_id": 2,
            "service_type": "id_card",
            "preferred_date": "",
        })
        assert r.status_code == 200

    def test_unknown_citizen_404(self):
        future = self._future_date(1)
        r = client.post("/appointments", json={
            "citizen_id": 99999,
            "service_type": "passport",
            "preferred_date": future,
        })
        assert r.status_code == 404

    def test_get_appointments(self):
        future = self._future_date(1)
        client.post("/appointments", json={
            "citizen_id": 1,
            "service_type": "passport",
            "preferred_date": future,
        })
        r = client.get("/appointments/1")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_get_appointments_empty(self):
        r = client.get("/appointments/99999")
        assert r.status_code == 200
        assert r.json() == []

    def test_past_date_rejected(self):
        r = client.post("/appointments", json={
            "citizen_id": 1,
            "service_type": "passport",
            "preferred_date": "2020-01-01",
        })
        assert r.status_code == 400
        assert "past" in r.json()["detail"].lower()

    def test_duplicate_appointment_rejected(self):
        future = self._future_date(2)
        # Book first
        client.post("/appointments", json={
            "citizen_id": 2,
            "service_type": "id_card",
            "preferred_date": future,
        })
        # Try same again
        r = client.post("/appointments", json={
            "citizen_id": 2,
            "service_type": "id_card",
            "preferred_date": future,
        })
        assert r.status_code == 409
        assert "already" in r.json()["detail"].lower()


class TestDocumentRequest:
    """Test POST /documents/request."""

    def test_request_document(self):
        r = client.post("/documents/request", json={
            "citizen_id": 1,
            "document_type": "birth_certificate",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["request_ref"].startswith("DOC-2026-")
        assert data["status"] == "processing"
        assert data["estimated_days"] == 3

    def test_unknown_citizen_404(self):
        r = client.post("/documents/request", json={
            "citizen_id": 99999,
            "document_type": "birth_certificate",
        })
        assert r.status_code == 404

    def test_different_doc_types_different_estimates(self):
        r1 = client.post("/documents/request", json={"citizen_id": 1, "document_type": "birth_certificate"})
        r2 = client.post("/documents/request", json={"citizen_id": 1, "document_type": "criminal_record"})
        assert r1.json()["estimated_days"] == 3
        assert r2.json()["estimated_days"] == 7


class TestServicesCatalog:
    """Test GET /services."""

    def test_returns_services(self):
        r = client.get("/services")
        assert r.status_code == 200
        services = r.json()
        assert len(services) == 5

    def test_services_have_bilingual_names(self):
        r = client.get("/services")
        for svc in r.json():
            assert "name_tr" in svc
            assert "name_en" in svc

    def test_services_have_auth_flag(self):
        r = client.get("/services")
        general = [s for s in r.json() if s["id"] == "general_inquiry"][0]
        passport = [s for s in r.json() if s["id"] == "passport"][0]
        assert general["requires_auth"] is False
        assert passport["requires_auth"] is True


class TestGDPRErasure:
    """Test DELETE /citizens/{citizen_id} — GDPR Article 17."""

    def test_delete_citizen_removes_all_data(self):
        # Citizen 2 (Fatma) exists in test DB
        r = client.delete("/citizens/2", headers=ADMIN)
        assert r.status_code == 200
        data = r.json()
        assert data["citizen_id"] == 2
        assert "deleted_records" in data

        # Verify citizen is gone
        r2 = client.get("/applications/2024-TR-0002")
        assert r2.status_code == 404

    def test_delete_nonexistent_citizen_404(self):
        r = client.delete("/citizens/99999", headers=ADMIN)
        assert r.status_code == 404
