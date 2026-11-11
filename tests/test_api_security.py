"""
Tests for API key protection on government routes (api/security.py).

- Citizen-data routes refuse requests without a valid X-Internal-Key.
- DELETE /citizens/{id} needs X-Admin-Key; the internal key is not enough.
- Read-only public routes still answer without a key.
- The public app (agent/server.py, exposed via ngrok) enforces the same rules.
- Every LangGraph node helper that calls the API gets through with the key.
"""

import importlib
import os

import pytest
from fastapi.testclient import TestClient

from api.models import AuthAuditLog
from api.server import app
from tests.conftest import TEST_ADMIN_KEY, TEST_INTERNAL_KEY, TestSession


anon = TestClient(app)  # no default headers

INTERNAL = {"X-Internal-Key": TEST_INTERNAL_KEY}
ADMIN = {"X-Admin-Key": TEST_ADMIN_KEY}

# (method, path, request kwargs)
PROTECTED_ROUTES = [
    ("GET", "/applications", {"params": {"citizen_id": 1}}),
    ("GET", "/applications/2024-TR-0001", {}),
    ("GET", "/appointments/1", {}),
    ("POST", "/appointments", {"json": {"citizen_id": 1, "service_type": "passport", "preferred_date": ""}}),
    ("DELETE", "/appointments/1", {}),
    ("GET", "/documents/1", {}),
    ("POST", "/documents/request", {"json": {"citizen_id": 1, "document_type": "birth_certificate"}}),
    ("POST", "/handoff", {"json": {"session_id": "sec-test", "reason": "caller_request"}}),
]

PUBLIC_ROUTES = [
    ("GET", "/services", {}),
    ("GET", "/appointments/slots/passport", {}),
    ("POST", "/guest/info", {"json": {"question": "Calisma saatleri nedir?", "language": "tr"}}),
]


# agent.server and the escalate node build a ChatOpenAI client at import time,
# which raises without OPENAI_API_KEY. The checks themselves never call OpenAI.
requires_openai_key = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="importing agent.server / escalate needs OPENAI_API_KEY",
)


def _ids(routes):
    return [f"{method} {path}" for method, path, _ in routes]


class TestInternalKeyRequired:
    """Citizen-data routes refuse callers without the internal key."""

    @pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES, ids=_ids(PROTECTED_ROUTES))
    def test_missing_key_403(self, method, path, kwargs):
        r = anon.request(method, path, **kwargs)
        assert r.status_code == 403

    @pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES, ids=_ids(PROTECTED_ROUTES))
    def test_wrong_key_403(self, method, path, kwargs):
        r = anon.request(method, path, headers={"X-Internal-Key": "wrong-key"}, **kwargs)
        assert r.status_code == 403

    @pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES, ids=_ids(PROTECTED_ROUTES))
    def test_admin_key_is_not_an_internal_key(self, method, path, kwargs):
        r = anon.request(method, path, headers={"X-Internal-Key": TEST_ADMIN_KEY}, **kwargs)
        assert r.status_code == 403

    def test_valid_key_reads_data(self):
        r = anon.get("/applications/2024-TR-0001", headers=INTERNAL)
        assert r.status_code == 200
        assert r.json()["application_ref"] == "2024-TR-0001"

    def test_rejected_handoff_writes_no_audit_row(self):
        anon.post("/handoff", json={"session_id": "sec-fake", "reason": "caller_request"})
        db = TestSession()
        try:
            assert db.query(AuthAuditLog).filter(AuthAuditLog.session_id == "sec-fake").count() == 0
        finally:
            db.close()


class TestFailClosed:
    """When INTERNAL_API_KEY is unset, every protected request is refused."""

    @pytest.mark.parametrize("header_value", [TEST_INTERNAL_KEY, ""], ids=["old-key", "empty-key"])
    def test_unset_internal_key_refuses_all(self, monkeypatch, header_value):
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        r = anon.get("/applications/2024-TR-0001", headers={"X-Internal-Key": header_value})
        assert r.status_code == 403

    def test_unset_admin_key_refuses_erasure(self, monkeypatch):
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        r = anon.delete("/citizens/99999", headers=ADMIN)
        assert r.status_code == 403


class TestAdminKeyForErasure:
    """DELETE /citizens/{id} is admin-only — the voice flow's key cannot erase."""

    def test_no_key_403(self):
        assert anon.delete("/citizens/1").status_code == 403

    def test_internal_key_only_403(self):
        assert anon.delete("/citizens/1", headers=INTERNAL).status_code == 403

    def test_wrong_admin_key_403(self):
        assert anon.delete("/citizens/1", headers={"X-Admin-Key": "wrong-key"}).status_code == 403

    def test_admin_key_passes_guard(self):
        # Unknown citizen → 404 proves the request got past the key check
        # without deleting seed data (the 200 path is covered in test_services).
        r = anon.delete("/citizens/99999", headers=ADMIN)
        assert r.status_code == 404


class TestPublicRoutes:
    """Read-only routes used before authentication stay open."""

    @pytest.mark.parametrize("method,path,kwargs", PUBLIC_ROUTES, ids=_ids(PUBLIC_ROUTES))
    def test_answers_without_key(self, method, path, kwargs):
        r = anon.request(method, path, **kwargs)
        assert r.status_code == 200


@requires_openai_key
class TestPublicAppEnforcesKeys:
    """agent/server.py is the app ngrok exposes — it must mount the guarded routers."""

    @pytest.fixture(autouse=True)
    def public_client(self):
        from agent.server import app as public_app
        self.client = TestClient(public_app)

    @pytest.mark.parametrize("method,path,kwargs", PROTECTED_ROUTES, ids=_ids(PROTECTED_ROUTES))
    def test_protected_route_403_without_key(self, method, path, kwargs):
        assert self.client.request(method, path, **kwargs).status_code == 403

    def test_erasure_403_with_internal_key(self):
        assert self.client.delete("/citizens/1", headers=INTERNAL).status_code == 403

    def test_service_catalog_open(self):
        assert self.client.get("/services").status_code == 200


# --- LangGraph nodes → API ---

# (module, function, args) for every node helper that calls the government API
NODE_API_CALLS = [
    ("status_check", "_fetch_all_applications", (1,)),
    ("status_check", "_fetch_status", ("2024-TR-0001",)),
    ("appointment_book", "_fetch_appointments", (1,)),
    ("appointment_book", "_fetch_available_slots", ("passport",)),
    ("appointment_book", "_book_via_api", (99999, "passport", None)),       # unknown citizen → 404, no write
    ("appointment_list", "_fetch_appointments", (1,)),
    ("appointment_cancel", "_fetch_appointments", (1,)),
    ("appointment_cancel", "_cancel_appointment", (99999,)),                # unknown appointment → 404, no write
    ("document_request", "_request_via_api", (99999, "birth_certificate")),  # unknown citizen → 404, no write
    ("document_status", "_fetch_document_requests", (1,)),
    pytest.param("escalate", "_log_handoff", ("sec-node", "caller_request", "", "tr"),
                 marks=requires_openai_key, id="escalate._log_handoff"),
]


@pytest.fixture
def api_calls(monkeypatch):
    """Route the nodes' outbound HTTP calls into the in-process API app.

    The client has no default headers, so a call only passes the key check
    if the node helper attached X-Internal-Key itself. Returns the list of
    (method, path, status_code) for every call made.
    """
    import httpx
    from agent.nodes import utils

    client = TestClient(app)
    calls = []

    def fake_request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        path = url.removeprefix(utils.GOV_API_BASE)
        r = client.request(method, path, **kwargs)
        calls.append((method, path, r.status_code))
        return r

    monkeypatch.setattr(httpx, "request", fake_request)
    return calls


class TestNodesSendInternalKey:

    @pytest.mark.parametrize(
        "module,func,args", NODE_API_CALLS,
        ids=[None if hasattr(c, "marks") else f"{c[0]}.{c[1]}" for c in NODE_API_CALLS],
    )
    def test_node_helper_passes_key_check(self, api_calls, module, func, args):
        node = importlib.import_module(f"agent.nodes.{module}")
        getattr(node, func)(*args)

        assert api_calls, f"{module}.{func} made no API call"
        for method, path, status in api_calls:
            assert status != 403, f"{method} {path} was refused"

    def test_status_check_gets_real_data(self, api_calls):
        from agent.nodes import status_check
        apps = status_check._fetch_all_applications(1)
        assert {a["application_ref"] for a in apps} == {"2024-TR-0001", "2024-TR-0024"}

    @requires_openai_key
    def test_escalate_handoff_is_audited(self, api_calls):
        from agent.nodes import escalate
        escalate._log_handoff("sec-node-audit", "caller_request", "", "tr")
        db = TestSession()
        try:
            assert db.query(AuthAuditLog).filter(AuthAuditLog.session_id == "sec-node-audit").count() == 1
        finally:
            db.close()

    def test_missing_key_degrades_gracefully(self, api_calls, monkeypatch):
        """A misconfigured key must not crash the voice turn — node returns empty."""
        from agent.nodes import status_check
        monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
        assert status_check._fetch_all_applications(1) == []
        assert api_calls[-1][2] == 403
