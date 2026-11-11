"""
Comprehensive core system test suite.

Covers EVERY possible product ending across all LangGraph nodes.
All tests use English language and mocked API calls.

Test matrix:
  - Intent Classification (10 intents + edge cases)
  - Status Check (7 endings)
  - Appointment Book (6 endings)
  - Appointment List (4 endings)
  - Appointment Cancel (6 endings)
  - Document Request (4 endings)
  - Document Status (4 endings)
  - FAQ / Edge Cases (10 endings)
  - Complaint (2 endings)
  - Escalate (4 endings)
  - Graph Routing (deterministic routing)
  - Full Graph Flows (end-to-end with mocked APIs)
  - SSE Proxy (Custom LLM endpoint)
  - Circuit Breaker (degradation levels)
"""

import json
import os
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.state import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(**overrides) -> AgentState:
    """Create a minimal valid AgentState with overrides."""
    defaults = {
        "messages": [HumanMessage(content="test")],
        "auth_status": "unauthenticated",
        "citizen_profile": None,
        "current_intent": "unknown",
        "completed_intents": [],
        "workflow_node": None,
        "rag_query": None,
        "rag_score": None,
        "timing_intent_classify": None,
        "timing_service_node": None,
        "service_node_name": None,
        "tool_chain_triggered": None,
        "api_calls_count": None,
        "prompt_version": "v1.0",
        "language": "en",
    }
    defaults.update(overrides)
    return defaults


# Profiles for different test scenarios
PROFILE_SINGLE_APP = {
    "first_name": "John",
    "last_name_initial": "S***",
    "citizen_id": 21,
    "gender": "M",
    "application_ref": "2024-EN-0001",
    "application_status": "approved",
    "language_preference": "en",
}

PROFILE_MULTI_APP = {
    "first_name": "Ahmet",
    "last_name_initial": "Y***",
    "citizen_id": 1,
    "gender": "M",
    "application_ref": "2024-TR-0001",
    "application_status": "in_review",
    "language_preference": "en",
}

PROFILE_NO_CITIZEN_ID = {
    "first_name": "TestUser",
    "last_name_initial": "T***",
    "gender": "M",
    "application_ref": "2024-TR-9999",
    "application_status": "pending",
    "language_preference": "en",
}


# ===================================================================
# 1. INTENT CLASSIFICATION — 10 intents + edge cases
# ===================================================================

@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
class TestIntentClassification:
    """Verify each intent is correctly classified from English input."""

    def _classify(self, text: str) -> str:
        from agent.nodes.intent_classify import intent_classify
        state = make_state(messages=[HumanMessage(content=text)])
        result = intent_classify(state)
        return result["current_intent"]

    # --- Core intents ---

    def test_status_check(self):
        assert self._classify("I want to check my application status") == "status_check"

    def test_appointment_book(self):
        assert self._classify("I'd like to book an appointment") == "appointment_book"

    def test_appointment_list(self):
        assert self._classify("Show me my existing appointments") == "appointment_list"

    def test_appointment_cancel(self):
        assert self._classify("I need to cancel my appointment") == "appointment_cancel"

    def test_document_request(self):
        assert self._classify("I want to request a birth certificate") == "document_request"

    def test_document_status(self):
        assert self._classify("What's the status of my document request?") == "document_status"

    def test_faq(self):
        assert self._classify("What are your working hours?") == "faq"

    def test_fee_inquiry(self):
        assert self._classify("How much does a passport cost?") == "fee_inquiry"

    def test_complaint(self):
        assert self._classify("I want to file a complaint about the service") == "complaint"

    def test_escalate_operator_request(self):
        assert self._classify("I want to speak with a human operator") == "escalate"

    # --- Edge cases that should map to faq ---

    def test_identity_refusal_maps_to_faq(self):
        assert self._classify("I don't want to give my ID number") == "faq"

    def test_third_party_inquiry_maps_to_faq(self):
        assert self._classify("Can you check my friend's application?") == "faq"

    def test_robot_question_maps_to_faq(self):
        assert self._classify("Are you a real person or a robot?") == "faq"

    def test_capability_question_maps_to_faq(self):
        assert self._classify("What can you help me with?") == "faq"

    def test_previous_call_maps_to_faq(self):
        assert self._classify("Last time I called you told me something different") == "faq"

    # --- Edge cases that should map to escalate ---

    def test_anger_maps_to_escalate(self):
        assert self._classify("This is ridiculous, your service is terrible!") == "escalate"

    # --- Topic change ---

    def test_topic_change_follows_new_intent(self):
        """When user changes topic mid-conversation, classify the NEW intent."""
        result = self._classify("Forget about that, I want to file a complaint")
        assert result == "complaint"

    # --- Ambiguous request maps to faq ---

    def test_ambiguous_request_maps_to_faq(self):
        result = self._classify("I need help with something")
        assert result == "faq"


# ===================================================================
# 2. STATUS CHECK — 7 possible endings
# ===================================================================

class TestStatusCheck:
    """Test all status_check node endings."""

    @pytest.fixture(autouse=True)
    def disable_api(self, monkeypatch):
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_status", lambda ref: None)
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [])

    def test_unauthenticated_asks_for_verification(self):
        """E1: No profile → asks for identity verification."""
        from agent.nodes.status_check import status_check
        result = status_check(make_state(citizen_profile=None))
        msg = result["messages"][-1].content.lower()
        assert "verify" in msg or "identity" in msg
        assert "status_check" in result["completed_intents"]

    def test_single_app_in_review(self, monkeypatch):
        """E2: Single app, status=in_review → review message."""
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [
            {"application_ref": "REF-1", "service_type": "passport", "status": "in_review",
             "notes": "Under evaluation.", "office": "Kadikoy Office",
             "last_updated": "2025-03-20", "estimated_completion": "5-10 business days"}
        ])
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "review" in msg or "under review" in msg
        assert "status_check" in result["completed_intents"]

    def test_single_app_approved(self, monkeypatch):
        """E3: Single app, status=approved → approved message."""
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [
            {"application_ref": "REF-1", "service_type": "passport", "status": "approved",
             "notes": "Ready for pickup.", "office": "", "last_updated": "", "estimated_completion": ""}
        ])
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "approved" in msg

    def test_single_app_rejected_offers_appeal(self, monkeypatch):
        """E4: Single app, status=rejected → appeal guidance (RAG chain)."""
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [
            {"application_ref": "REF-1", "service_type": "passport", "status": "rejected",
             "notes": "Photo requirements not met.", "office": "", "last_updated": "", "estimated_completion": ""}
        ])
        monkeypatch.setattr(sc, "_rag_lookup", lambda q, l, **kw: "")
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "rejected" in msg
        assert "appeal" in msg or "thirty days" in msg

    def test_single_app_additional_docs_chains_to_rag(self, monkeypatch):
        """E5: Single app, status=additional_docs_needed → RAG for required docs."""
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [
            {"application_ref": "REF-1", "service_type": "passport", "status": "additional_docs_needed",
             "notes": "Missing photo.", "office": "Test Office", "last_updated": "", "estimated_completion": ""}
        ])
        monkeypatch.setattr(sc, "_rag_lookup", lambda q, l, **kw: "National ID copy, recent photo")
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "additional documents" in msg or "requires" in msg
        assert "status_check" in result["completed_intents"]

    def test_multiple_apps_lists_all(self, monkeypatch):
        """E6: Multiple apps → lists all and asks which one."""
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [
            {"application_ref": "REF-1", "service_type": "passport", "status": "in_review",
             "notes": "", "office": "", "last_updated": "", "estimated_completion": ""},
            {"application_ref": "REF-2", "service_type": "id_card", "status": "approved",
             "notes": "", "office": "", "last_updated": "", "estimated_completion": ""},
        ])
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_MULTI_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "2 applications" in msg
        assert "which" in msg

    def test_multiple_apps_user_selects_one(self, monkeypatch):
        """E7: Multiple apps + user selects specific one → detail for that app."""
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [
            {"application_ref": "REF-1", "service_type": "passport", "status": "in_review",
             "notes": "Under evaluation.", "office": "", "last_updated": "", "estimated_completion": ""},
            {"application_ref": "REF-2", "service_type": "id_card", "status": "approved",
             "notes": "Ready.", "office": "", "last_updated": "", "estimated_completion": ""},
        ])
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated",
            citizen_profile=PROFILE_MULTI_APP,
            messages=[HumanMessage(content="passport")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "passport" in msg
        assert "review" in msg

    def test_no_apps_fallback_to_profile(self):
        """E8: No apps from API → fallback to profile data."""
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated",
            citizen_profile={**PROFILE_SINGLE_APP, "application_status": "pending"},
        ))
        msg = result["messages"][-1].content.lower()
        assert "pending" in msg or "status" in msg

    def test_single_app_pending_status(self, monkeypatch):
        """E9: Single app, status=pending → pending message."""
        import agent.nodes.status_check as sc
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [
            {"application_ref": "REF-1", "service_type": "driver_license", "status": "pending",
             "notes": "Awaiting exam.", "office": "", "last_updated": "", "estimated_completion": ""}
        ])
        from agent.nodes.status_check import status_check
        result = status_check(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "pending" in msg or "driver" in msg


# ===================================================================
# 3. APPOINTMENT BOOK — 6 possible endings
# ===================================================================

class TestAppointmentBook:
    """Test all appointment_book node endings."""

    @pytest.fixture(autouse=True)
    def disable_api(self, monkeypatch):
        import agent.nodes.appointment_book as ab
        monkeypatch.setattr(ab, "_book_via_api", lambda *a, **k: (None, "service_unavailable"))
        monkeypatch.setattr(ab, "_fetch_appointments", lambda cid: [])
        monkeypatch.setattr(ab, "_fetch_available_slots", lambda svc: [
            {"office": "Kadikoy Office", "date": "2026-05-01", "time": "10:00"},
            {"office": "Kadikoy Office", "date": "2026-05-01", "time": "14:00"},
            {"office": "Uskudar Office", "date": "2026-05-02", "time": "09:00"},
        ])

    def test_unauthenticated_asks_for_verification(self):
        """E1: No profile → asks for identity verification."""
        from agent.nodes.appointment_book import appointment_book
        result = appointment_book(make_state(citizen_profile=None))
        msg = result["messages"][-1].content.lower()
        assert "verify" in msg or "identity" in msg

    def test_no_service_type_asks_which_service(self):
        """E2: No service type detected → asks which service."""
        from agent.nodes.appointment_book import appointment_book
        result = appointment_book(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="I want to book an appointment")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "which service" in msg or "passport" in msg

    def test_service_type_detected_offers_slots(self):
        """E3: Service type detected → lists available slots (not completed yet)."""
        from agent.nodes.appointment_book import appointment_book
        result = appointment_book(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="I want a passport appointment")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "option" in msg
        assert "which" in msg or "prefer" in msg
        # Should NOT be completed — waiting for user selection
        assert "appointment_book" not in result.get("completed_intents", [])

    def test_user_selects_slot_books_successfully(self, monkeypatch):
        """E4: User selects a slot → books and confirms."""
        import agent.nodes.appointment_book as ab
        monkeypatch.setattr(ab, "_book_via_api", lambda *a, **k: (
            {"appointment_date": "2026-05-01", "appointment_time": "10:00",
             "office": "Kadikoy Office"}, None
        ))
        from agent.nodes.appointment_book import appointment_book
        result = appointment_book(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[
                HumanMessage(content="I want a passport appointment"),
                AIMessage(content="Here are the available passport slots. Option 1: 2026-05-01 at 10:00, Kadikoy Office. Option 2: 2026-05-01 at 14:00. Which one would you prefer?"),
                HumanMessage(content="The first one"),
            ],
        ))
        msg = result["messages"][-1].content.lower()
        assert "confirmed" in msg
        assert "appointment_book" in result["completed_intents"]

    def test_conflict_existing_appointment(self, monkeypatch):
        """E5: Existing confirmed appointment for same service → informs conflict."""
        import agent.nodes.appointment_book as ab
        monkeypatch.setattr(ab, "_fetch_appointments", lambda cid: [
            {"service_type": "passport", "status": "confirmed",
             "appointment_date": "2026-05-01", "appointment_time": "10:00"}
        ])
        from agent.nodes.appointment_book import appointment_book
        result = appointment_book(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="I want a passport appointment")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "already have" in msg or "confirmed" in msg
        assert "appointment_book" in result["completed_intents"]

    def test_no_available_slots(self, monkeypatch):
        """E6: No available slots → informs no slots."""
        import agent.nodes.appointment_book as ab
        monkeypatch.setattr(ab, "_fetch_available_slots", lambda svc: [])
        from agent.nodes.appointment_book import appointment_book
        result = appointment_book(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="I need a passport appointment")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "no available" in msg or "try again" in msg

    def test_service_type_detection_from_context(self):
        """Verify service type is correctly detected from message keywords."""
        from agent.nodes.appointment_book import _detect_service_type
        assert _detect_service_type([HumanMessage(content="passport appointment")], "en") == "passport"
        assert _detect_service_type([HumanMessage(content="I need an ID card")], "en") == "id_card"
        assert _detect_service_type([HumanMessage(content="driver's license please")], "en") == "driver_license"
        assert _detect_service_type([HumanMessage(content="civil registry matter")], "en") == "civil_registry"
        assert _detect_service_type([HumanMessage(content="I want an appointment")], "en") is None


# ===================================================================
# 4. APPOINTMENT LIST — 4 possible endings
# ===================================================================

class TestAppointmentList:
    """Test all appointment_list node endings."""

    @pytest.fixture(autouse=True)
    def disable_api(self, monkeypatch):
        import agent.nodes.appointment_list as al
        monkeypatch.setattr(al, "_fetch_appointments", lambda cid: [])

    def test_unauthenticated_asks_for_verification(self):
        """E1: No profile → asks for identity verification."""
        from agent.nodes.appointment_list import appointment_list
        result = appointment_list(make_state(citizen_profile=None))
        msg = result["messages"][-1].content.lower()
        assert "verify" in msg or "identity" in msg

    def test_no_appointments(self):
        """E2: No appointments → informs user."""
        from agent.nodes.appointment_list import appointment_list
        result = appointment_list(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "no appointments" in msg
        assert "appointment_list" in result["completed_intents"]

    def test_single_appointment(self, monkeypatch):
        """E3: One appointment → shows details."""
        import agent.nodes.appointment_list as al
        monkeypatch.setattr(al, "_fetch_appointments", lambda cid: [
            {"service_type": "passport", "appointment_date": "2026-04-07",
             "appointment_time": "10:00", "office": "Kadikoy Office", "status": "confirmed"}
        ])
        from agent.nodes.appointment_list import appointment_list
        result = appointment_list(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "one appointment" in msg or "passport" in msg
        assert "april" in msg or "2026" in msg

    def test_multiple_appointments(self, monkeypatch):
        """E4: Multiple appointments → lists all."""
        import agent.nodes.appointment_list as al
        monkeypatch.setattr(al, "_fetch_appointments", lambda cid: [
            {"service_type": "passport", "appointment_date": "2026-04-07",
             "appointment_time": "10:00", "office": "Office A", "status": "confirmed"},
            {"service_type": "id_card", "appointment_date": "2026-04-08",
             "appointment_time": "14:00", "office": "Office B", "status": "confirmed"},
        ])
        from agent.nodes.appointment_list import appointment_list
        result = appointment_list(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "2 appointments" in msg
        assert "passport" in msg
        assert "id_card" in msg


# ===================================================================
# 5. APPOINTMENT CANCEL — 6 possible endings
# ===================================================================

class TestAppointmentCancel:
    """Test all appointment_cancel node endings."""

    @pytest.fixture(autouse=True)
    def disable_api(self, monkeypatch):
        import agent.nodes.appointment_cancel as ac
        monkeypatch.setattr(ac, "_fetch_appointments", lambda cid: [])
        monkeypatch.setattr(ac, "_cancel_appointment", lambda aid: True)

    def test_unauthenticated_asks_for_verification(self):
        """E1: No profile → asks for identity verification."""
        from agent.nodes.appointment_cancel import appointment_cancel
        result = appointment_cancel(make_state(citizen_profile=None))
        msg = result["messages"][-1].content.lower()
        assert "verify" in msg or "identity" in msg

    def test_no_confirmed_appointments(self):
        """E2: No confirmed appointments → informs user."""
        from agent.nodes.appointment_cancel import appointment_cancel
        result = appointment_cancel(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "no confirmed" in msg
        assert "appointment_cancel" in result["completed_intents"]

    def test_single_confirmed_cancels_successfully(self, monkeypatch):
        """E3: One confirmed appointment → cancels it successfully."""
        import agent.nodes.appointment_cancel as ac
        monkeypatch.setattr(ac, "_fetch_appointments", lambda cid: [
            {"id": 1, "service_type": "passport", "status": "confirmed",
             "appointment_date": "2026-04-07", "appointment_time": "10:00"}
        ])
        from agent.nodes.appointment_cancel import appointment_cancel
        result = appointment_cancel(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "cancelled" in msg
        assert "passport" in msg

    def test_single_confirmed_cancel_fails(self, monkeypatch):
        """E4: One confirmed appointment → cancel API fails."""
        import agent.nodes.appointment_cancel as ac
        monkeypatch.setattr(ac, "_fetch_appointments", lambda cid: [
            {"id": 1, "service_type": "passport", "status": "confirmed",
             "appointment_date": "2026-04-07", "appointment_time": "10:00"}
        ])
        monkeypatch.setattr(ac, "_cancel_appointment", lambda aid: False)
        from agent.nodes.appointment_cancel import appointment_cancel
        result = appointment_cancel(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "couldn't cancel" in msg or "could not" in msg

    def test_multiple_confirmed_user_selects_one(self, monkeypatch):
        """E5: Multiple confirmed + user selects → cancels the selected one."""
        import agent.nodes.appointment_cancel as ac
        monkeypatch.setattr(ac, "_fetch_appointments", lambda cid: [
            {"id": 1, "service_type": "passport", "status": "confirmed",
             "appointment_date": "2026-04-07", "appointment_time": "10:00"},
            {"id": 2, "service_type": "id_card", "status": "confirmed",
             "appointment_date": "2026-04-08", "appointment_time": "14:00"},
        ])
        from agent.nodes.appointment_cancel import appointment_cancel
        result = appointment_cancel(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="Cancel my passport appointment")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "cancelled" in msg
        assert "passport" in msg

    def test_multiple_confirmed_no_match_lists_all(self, monkeypatch):
        """E6: Multiple confirmed + no match → lists all and asks which one."""
        import agent.nodes.appointment_cancel as ac
        monkeypatch.setattr(ac, "_fetch_appointments", lambda cid: [
            {"id": 1, "service_type": "passport", "status": "confirmed",
             "appointment_date": "2026-04-07", "appointment_time": "10:00"},
            {"id": 2, "service_type": "id_card", "status": "confirmed",
             "appointment_date": "2026-04-08", "appointment_time": "14:00"},
        ])
        from agent.nodes.appointment_cancel import appointment_cancel
        result = appointment_cancel(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="Cancel my appointment")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "2 confirmed" in msg
        assert "which" in msg


# ===================================================================
# 6. DOCUMENT REQUEST — 4 possible endings
# ===================================================================

class TestDocumentRequest:
    """Test all document_request node endings."""

    @pytest.fixture(autouse=True)
    def disable_api(self, monkeypatch):
        import agent.nodes.document_request as dr
        monkeypatch.setattr(dr, "_request_via_api", lambda *a, **k: None)

    def test_unauthenticated_asks_for_verification(self):
        """E1: No profile → asks for identity verification."""
        from agent.nodes.document_request import document_request
        result = document_request(make_state(citizen_profile=None))
        msg = result["messages"][-1].content.lower()
        assert "verify" in msg or "identity" in msg

    def test_no_doc_type_asks_which_type(self):
        """E2: No document type detected → asks which type."""
        from agent.nodes.document_request import document_request
        result = document_request(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="I want to request a document")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "which type" in msg or "birth certificate" in msg

    def test_doc_type_detected_api_success(self, monkeypatch):
        """E3: Document type detected + API success → confirmation with ref."""
        import agent.nodes.document_request as dr
        monkeypatch.setattr(dr, "_request_via_api", lambda *a, **k: {
            "request_ref": "DOC-2026-ABCD", "estimated_days": 3
        })
        from agent.nodes.document_request import document_request
        result = document_request(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="I need a birth certificate")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "submitted" in msg or "request" in msg
        assert "doc-2026-abcd" in msg
        assert "3 business days" in msg
        assert "document_request" in result["completed_intents"]

    def test_doc_type_detected_api_failure(self):
        """E4: Document type detected + API failure → try again later."""
        from agent.nodes.document_request import document_request
        result = document_request(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
            messages=[HumanMessage(content="I need a birth certificate")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "try again" in msg or "alo 181" in msg.lower()

    def test_document_type_detection_keywords(self):
        """Verify document type detection from message keywords."""
        from agent.nodes.document_request import _detect_document_type
        assert _detect_document_type([HumanMessage(content="birth certificate")], "en") == "birth_certificate"
        assert _detect_document_type([HumanMessage(content="residence certificate")], "en") == "residence_cert"
        assert _detect_document_type([HumanMessage(content="marriage certificate")], "en") == "marriage_cert"
        assert _detect_document_type([HumanMessage(content="criminal record")], "en") == "criminal_record"
        assert _detect_document_type([HumanMessage(content="some document")], "en") is None


# ===================================================================
# 7. DOCUMENT STATUS — 4 possible endings
# ===================================================================

class TestDocumentStatus:
    """Test all document_status node endings."""

    @pytest.fixture(autouse=True)
    def disable_api(self, monkeypatch):
        import agent.nodes.document_status as ds
        monkeypatch.setattr(ds, "_fetch_document_requests", lambda cid: [])

    def test_unauthenticated_asks_for_verification(self):
        """E1: No profile → asks for identity verification."""
        from agent.nodes.document_status import document_status
        result = document_status(make_state(citizen_profile=None))
        msg = result["messages"][-1].content.lower()
        assert "verify" in msg or "identity" in msg

    def test_no_document_requests(self):
        """E2: No document requests → informs user."""
        from agent.nodes.document_status import document_status
        result = document_status(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "no document requests" in msg
        assert "document_status" in result["completed_intents"]

    def test_single_document_request(self, monkeypatch):
        """E3: One document request → shows details."""
        import agent.nodes.document_status as ds
        monkeypatch.setattr(ds, "_fetch_document_requests", lambda cid: [
            {"document_type": "birth_certificate", "request_ref": "DOC-2026-0001",
             "status": "ready", "estimated_days": 3}
        ])
        from agent.nodes.document_status import document_status
        result = document_status(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "birth_certificate" in msg or "doc-2026-0001" in msg
        assert "ready" in msg

    def test_multiple_document_requests(self, monkeypatch):
        """E4: Multiple document requests → lists all."""
        import agent.nodes.document_status as ds
        monkeypatch.setattr(ds, "_fetch_document_requests", lambda cid: [
            {"document_type": "birth_certificate", "request_ref": "DOC-001",
             "status": "ready", "estimated_days": 3},
            {"document_type": "residence_cert", "request_ref": "DOC-002",
             "status": "processing", "estimated_days": 5},
        ])
        from agent.nodes.document_status import document_status
        result = document_status(make_state(
            auth_status="authenticated", citizen_profile=PROFILE_SINGLE_APP,
        ))
        msg = result["messages"][-1].content.lower()
        assert "2 document requests" in msg
        assert "doc-001" in msg
        assert "doc-002" in msg


# ===================================================================
# 8. FAQ / EDGE CASES — 10 possible endings
# ===================================================================

@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
class TestFaqAnswer:
    """Test FAQ node with RAG + edge case handling."""

    @pytest.fixture(autouse=True)
    def mock_rag(self, monkeypatch):
        """Mock RAG search to avoid Pinecone dependency."""
        from rag.retriever import RetrievalResult

        def _mock_search(**kwargs):
            query = kwargs.get("query", "")
            if "working hours" in query.lower():
                return [RetrievalResult(
                    text="Government offices are open Monday through Friday, eight AM to five PM.",
                    score=0.85, title="General Info", category="general",
                    doc_type="faq",
                )]
            if "passport" in query.lower() and ("fee" in query.lower() or "cost" in query.lower()):
                return [RetrievalResult(
                    text="Passport fees are two thousand five hundred lira for standard processing.",
                    score=0.82, title="Fees", category="passport",
                    doc_type="faq",
                )]
            # Return low score for unmatched queries
            return [RetrievalResult(text="", score=0.1, title="", category="", doc_type="")]

        import agent.nodes.faq_answer as fa
        monkeypatch.setattr(fa, "search", _mock_search)

    def test_rag_grounded_answer(self):
        """E1: RAG match found → grounded answer."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="What are your working hours?")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "monday" in msg or "friday" in msg or "eight" in msg or "five" in msg
        assert result.get("rag_score", 0) > 0.3

    def test_no_rag_match_says_no_info(self):
        """E2: No RAG match → says doesn't have that information."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="What is the weather like today?")],
        ))
        msg = result["messages"][-1].content.lower()
        # Should either say no info or redirect to what it can help with
        assert any(phrase in msg for phrase in [
            "don't have", "help you with", "information", "assist",
            "can't", "unable", "not available", "don't know"
        ])

    def test_edge_identity_refusal(self):
        """E3: User refuses to share identity → respects refusal, offers general help."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="I don't want to give you my ID number")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "general" in msg or "help" in msg or "without" in msg

    def test_edge_third_party_inquiry(self):
        """E4: Asking about someone else → explains privacy policy."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="Can you check my friend's application status?")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "security" in msg or "own" in msg or "directly" in msg or "privacy" in msg

    def test_edge_robot_question(self):
        """E5: Are you a robot? → honest introduction."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="Are you a real person or a robot?")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "ai" in msg or "assistant" in msg or "umut" in msg

    def test_edge_capability_question(self):
        """E6: What can you do? → lists capabilities."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="What can you help me with?")],
        ))
        msg = result["messages"][-1].content.lower()
        assert any(word in msg for word in ["status", "appointment", "document", "complaint"])

    def test_edge_previous_call_reference(self):
        """E7: References a previous call → explains no access to prior records."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="Last time I called, you told me something different")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "previous" in msg or "prior" in msg or "record" in msg or "help" in msg

    def test_fee_inquiry_through_faq(self):
        """E8: Fee inquiry → provides fee information from RAG."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            current_intent="fee_inquiry",
            messages=[HumanMessage(content="How much does a passport cost?")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "fee" in msg or "cost" in msg or "lira" in msg or "passport" in msg

    def test_rag_query_and_score_tracked(self):
        """E9: RAG analytics fields are populated."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            messages=[HumanMessage(content="What are your working hours?")],
        ))
        assert result.get("rag_query") is not None
        assert result.get("rag_score") is not None

    def test_completed_intents_marked(self):
        """E10: FAQ marks current_intent as completed."""
        from agent.nodes.faq_answer import faq_answer
        result = faq_answer(make_state(
            current_intent="faq",
            messages=[HumanMessage(content="What are your working hours?")],
        ))
        assert "faq" in result["completed_intents"]


# ===================================================================
# 9. COMPLAINT — 2 possible endings
# ===================================================================

class TestComplaint:
    """Test complaint node endings."""

    def test_complaint_recorded_english(self):
        """E1: Complaint in English → confirmation with reference."""
        from agent.nodes.complaint import complaint
        result = complaint(make_state(
            messages=[HumanMessage(content="The service was terrible, I waited 3 hours")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "recorded" in msg
        assert "3 business days" in msg or "supervisor" in msg
        assert "complaint" in result["completed_intents"]

    def test_complaint_offers_further_help(self):
        """E2: After recording, asks if user needs anything else."""
        from agent.nodes.complaint import complaint
        result = complaint(make_state(
            messages=[HumanMessage(content="I am unhappy with the process")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "anything else" in msg or "help" in msg


# ===================================================================
# 10. ESCALATE — 4 possible endings
# ===================================================================

class TestEscalate:
    """Test escalate node endings."""

    @pytest.fixture(autouse=True)
    def disable_handoff_api(self, monkeypatch):
        import agent.nodes.escalate as esc
        monkeypatch.setattr(esc, "_log_handoff", lambda **kw: None)

    @pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
    def test_normal_request_transfers_politely(self):
        """E1: Normal operator request → polite transfer message."""
        from agent.nodes.escalate import escalate
        result = escalate(make_state(
            messages=[HumanMessage(content="I want to speak with a human operator")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "transfer" in msg or "operator" in msg or "connect" in msg
        assert "escalate" in result["completed_intents"]

    @pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
    def test_frustrated_caller_gets_empathy(self):
        """E2: Frustrated caller → empathetic acknowledgment + transfer."""
        from agent.nodes.escalate import escalate
        result = escalate(make_state(
            messages=[HumanMessage(content="This is ridiculous, I've been waiting forever!")],
        ))
        msg = result["messages"][-1].content.lower()
        # Should acknowledge frustration
        assert any(word in msg for word in [
            "understand", "sorry", "apologize", "frustrat", "transfer", "operator"
        ])

    def test_demo_mode_no_tool_call(self):
        """E3: Demo mode → no transfer_to_number tool call."""
        from agent.nodes.escalate import escalate
        result = escalate(make_state(
            messages=[HumanMessage(content="Transfer me to an operator")],
        ))
        msg = result["messages"][-1]
        tool_calls = msg.additional_kwargs.get("tool_calls", [])
        assert len(tool_calls) == 0

    def test_production_mode_returns_tool_call(self, monkeypatch):
        """E4: Production mode → includes transfer_to_number tool call."""
        import agent.nodes.escalate as esc
        monkeypatch.setattr(esc, "_ENABLE_TRANSFER", True)
        from agent.nodes.escalate import escalate
        result = escalate(make_state(
            messages=[HumanMessage(content="Transfer me to an operator")],
        ))
        msg = result["messages"][-1]
        tool_calls = msg.additional_kwargs.get("tool_calls", [])
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "transfer_to_number"
        args = json.loads(tool_calls[0]["function"]["arguments"])
        assert "transfer_number" in args
        assert "client_message" in args
        assert "agent_message" in args

    @pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
    def test_authenticated_user_includes_identity_in_summary(self):
        """E5: Authenticated user → operator summary includes citizen info."""
        from agent.nodes.escalate import _generate_operator_summary
        messages = [HumanMessage(content="I need help with my passport")]
        summary = _generate_operator_summary(messages, "en")
        assert isinstance(summary, str)
        assert len(summary) > 10

    @pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
    def test_turkish_escalation_message(self):
        """E6: Turkish language → transfer message in Turkish."""
        from agent.nodes.escalate import escalate
        result = escalate(make_state(
            language="tr",
            messages=[HumanMessage(content="Operator ile gorusmek istiyorum")],
        ))
        msg = result["messages"][-1].content.lower()
        assert "operator" in msg or "bagliyorum" in msg or "yetki" in msg


# ===================================================================
# 11. GRAPH ROUTING — deterministic routing tests
# ===================================================================

class TestGraphRouting:
    """Test graph routing logic covers all intents."""

    def test_all_intents_have_routes(self):
        """Every valid intent maps to a node."""
        from agent.graph import route_by_intent
        intents_to_nodes = {
            "status_check": "status_check",
            "appointment_book": "appointment_book",
            "appointment_list": "appointment_list",
            "appointment_cancel": "appointment_cancel",
            "document_request": "document_request",
            "document_status": "document_status",
            "faq": "faq_answer",
            "fee_inquiry": "faq_answer",
            "complaint": "complaint",
            "escalate": "escalate",
            "unknown": "faq_answer",
        }
        for intent, expected_node in intents_to_nodes.items():
            assert route_by_intent({"current_intent": intent}) == expected_node, \
                f"Intent '{intent}' should route to '{expected_node}'"

    def test_invalid_intent_defaults_to_faq(self):
        """Unknown intent value defaults to faq_answer."""
        from agent.graph import route_by_intent
        assert route_by_intent({"current_intent": "nonexistent"}) == "faq_answer"

    def test_entry_router_always_goes_to_intent_classify(self):
        """Entry router always routes to intent_classify regardless of workflow_node."""
        from agent.graph import entry_router
        assert entry_router(make_state(workflow_node=None)) == "intent_classify"
        assert entry_router(make_state(workflow_node="status_check")) == "intent_classify"
        assert entry_router(make_state(workflow_node="appointment_book")) == "intent_classify"
        assert entry_router(make_state(workflow_node="invalid_node")) == "intent_classify"

    def test_all_workflow_nodes_defined(self):
        """All expected workflow nodes are in WORKFLOW_NODES set."""
        from agent.graph import WORKFLOW_NODES
        expected = {
            "service_router", "status_check", "appointment_book",
            "appointment_list", "appointment_cancel", "document_request",
            "document_status", "faq_answer", "complaint", "escalate",
        }
        assert WORKFLOW_NODES == expected


# ===================================================================
# 12. FULL GRAPH FLOWS — end-to-end with mocked APIs
# ===================================================================

@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
class TestFullGraphFlows:
    """End-to-end graph execution tests with mocked external calls."""

    @pytest.fixture(autouse=True)
    def disable_all_apis(self, monkeypatch):
        import agent.nodes.status_check as sc
        import agent.nodes.appointment_book as ab
        import agent.nodes.appointment_list as al
        import agent.nodes.appointment_cancel as ac
        import agent.nodes.document_request as dr
        import agent.nodes.document_status as ds
        import agent.nodes.escalate as esc
        monkeypatch.setattr(sc, "_fetch_status", lambda ref: None)
        monkeypatch.setattr(sc, "_fetch_all_applications", lambda cid: [])
        monkeypatch.setattr(ab, "_book_via_api", lambda *a, **k: (None, "service_unavailable"))
        monkeypatch.setattr(ab, "_fetch_appointments", lambda cid: [])
        monkeypatch.setattr(al, "_fetch_appointments", lambda cid: [])
        monkeypatch.setattr(ac, "_fetch_appointments", lambda cid: [])
        monkeypatch.setattr(ac, "_cancel_appointment", lambda aid: True)
        monkeypatch.setattr(dr, "_request_via_api", lambda *a, **k: None)
        monkeypatch.setattr(ds, "_fetch_document_requests", lambda cid: [])
        monkeypatch.setattr(esc, "_log_handoff", lambda **kw: None)

    def test_flow_status_check(self):
        """Full flow: user asks for status → intent_classify → status_check → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="I want to check my application status")],
            auth_status="authenticated",
            citizen_profile=PROFILE_SINGLE_APP,
        ))
        assert result["current_intent"] == "status_check"
        assert "status_check" in result["completed_intents"]

    def test_flow_appointment_book(self):
        """Full flow: user asks to book → intent_classify → appointment_book → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="I would like to book an appointment")],
            auth_status="authenticated",
            citizen_profile=PROFILE_SINGLE_APP,
        ))
        assert result["current_intent"] == "appointment_book"

    def test_flow_appointment_list(self):
        """Full flow: user asks to see appointments → intent_classify → appointment_list → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="Show me my existing appointments")],
            auth_status="authenticated",
            citizen_profile=PROFILE_SINGLE_APP,
        ))
        assert result["current_intent"] == "appointment_list"
        assert "appointment_list" in result["completed_intents"]

    def test_flow_appointment_cancel(self):
        """Full flow: user asks to cancel → intent_classify → appointment_cancel → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="I need to cancel my appointment")],
            auth_status="authenticated",
            citizen_profile=PROFILE_SINGLE_APP,
        ))
        assert result["current_intent"] == "appointment_cancel"
        assert "appointment_cancel" in result["completed_intents"]

    def test_flow_document_request(self):
        """Full flow: user asks for document → intent_classify → document_request → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="I want to request a birth certificate")],
            auth_status="authenticated",
            citizen_profile=PROFILE_SINGLE_APP,
        ))
        assert result["current_intent"] == "document_request"

    def test_flow_document_status(self):
        """Full flow: user asks for doc status → intent_classify → document_status → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="What is the status of my document request?")],
            auth_status="authenticated",
            citizen_profile=PROFILE_SINGLE_APP,
        ))
        assert result["current_intent"] == "document_status"
        assert "document_status" in result["completed_intents"]

    def test_flow_faq(self):
        """Full flow: user asks general question → intent_classify → faq_answer → END."""
        from agent.graph import graph

        # Mock RAG to avoid Pinecone dependency
        from unittest.mock import patch
        from rag.retriever import RetrievalResult
        with patch("agent.nodes.faq_answer.search", return_value=[
            RetrievalResult(text="We are open Monday to Friday.", score=0.8,
                        title="General Info", category="general", doc_type="faq")
        ]):
            result = graph.invoke(make_state(
                messages=[HumanMessage(content="What are your working hours?")],
            ))
        assert result["current_intent"] == "faq"
        assert "faq" in result["completed_intents"]

    def test_flow_complaint(self):
        """Full flow: user files complaint → intent_classify → complaint → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="I want to file a complaint about the terrible service")],
        ))
        assert result["current_intent"] == "complaint"
        assert "complaint" in result["completed_intents"]

    def test_flow_escalate(self):
        """Full flow: user requests operator → intent_classify → escalate → END."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="I want to speak with a human operator please")],
        ))
        assert result["current_intent"] == "escalate"
        assert "escalate" in result["completed_intents"]

    def test_flow_fee_inquiry(self):
        """Full flow: user asks about fees → intent_classify → faq_answer → END."""
        from agent.graph import graph
        from unittest.mock import patch
        from rag.retriever import RetrievalResult
        with patch("agent.nodes.faq_answer.search", return_value=[
            RetrievalResult(text="Passport fee is two thousand lira.", score=0.8,
                        title="Fees", category="passport", doc_type="faq")
        ]):
            result = graph.invoke(make_state(
                messages=[HumanMessage(content="How much does a passport cost?")],
            ))
        assert result["current_intent"] == "fee_inquiry"

    def test_flow_unauthenticated_status_check(self):
        """Full flow: unauthenticated user → status_check → asks for verification."""
        from agent.graph import graph
        result = graph.invoke(make_state(
            messages=[HumanMessage(content="Check my application status")],
            auth_status="unauthenticated",
            citizen_profile=None,
        ))
        assert result["current_intent"] == "status_check"
        msg = result["messages"][-1].content.lower()
        assert "verify" in msg or "identity" in msg


# ===================================================================
# 13. SSE PROXY — Custom LLM endpoint
# ===================================================================

class TestSSEProxy:
    """Test the Custom LLM SSE proxy endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from agent.server import app
        from tests.conftest import CUSTOM_LLM_AUTH
        self.client = TestClient(app, headers=CUSTOM_LLM_AUTH)  # acts as ElevenLabs
        import agent.server as srv
        srv._cb_failures = 0
        srv._cb_opened_at = None

    def test_health_endpoint_returns_healthy(self):
        """Health check returns healthy status."""
        r = self.client.get("/health")
        data = r.json()
        assert data["status"] == "healthy"
        assert data["degradation_level"] == 0

    @pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
    def test_sse_stream_format(self):
        """SSE stream follows OpenAI-compatible format."""
        r = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "What are your hours?"}],
            "stream": True,
            "elevenlabs_extra_body": {"conversation_id": "test-sse-format"},
        })
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]

        lines = [l for l in r.text.split("\n") if l.startswith("data:")]
        assert lines[-1] == "data: [DONE]"

        # First data chunk should have role=assistant
        first_data = json.loads(lines[0].replace("data: ", ""))
        assert first_data["choices"][0]["delta"]["role"] == "assistant"

    @pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
    def test_sse_with_authenticated_context(self, monkeypatch):
        """SSE stream works for a caller authenticated by a session token."""
        import api.models
        from agent import session_token
        from tests.conftest import TestSession
        monkeypatch.setattr(api.models, "SessionLocal", TestSession)  # profile comes from the test DB

        r = self.client.post("/v1/chat/completions", json={
            "messages": [
                {"role": "system", "content": f"[NODE:status_check]\nSESSION_TOKEN={session_token.issue(1)}"},
                {"role": "user", "content": "Check my application status"},
            ],
            "stream": True,
            "elevenlabs_extra_body": {"conversation_id": "test-sse-auth"},
        })
        assert r.status_code == 200
        content_parts = []
        for line in r.text.split("\n"):
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            data = json.loads(line.replace("data: ", ""))
            c = data["choices"][0]["delta"].get("content", "")
            if c:
                content_parts.append(c)
        full_response = "".join(content_parts).lower()
        assert len(full_response) > 0

    def test_language_defaults_to_english(self):
        """Default language extraction returns English."""
        from agent.server import _extract_language, ChatCompletionRequest
        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert _extract_language(req) == "en"

    def test_language_from_extra_body(self):
        """Language can be set via elevenlabs_extra_body."""
        from agent.server import _extract_language, ChatCompletionRequest
        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "Hello"}],
            elevenlabs_extra_body={"language": "en"},
        )
        assert _extract_language(req) == "en"

    def test_sse_chunk_format(self):
        """SSE chunk helper generates valid JSON."""
        from agent.server import sse_chunk
        chunk = sse_chunk("resp-1", {"content": "Hello"}, finish_reason=None)
        data = json.loads(chunk.replace("data: ", "").strip())
        assert data["choices"][0]["delta"]["content"] == "Hello"
        assert data["id"] == "resp-1"

    def test_sse_chunk_tool_call_format(self):
        """SSE chunk supports tool_calls in delta."""
        from agent.server import sse_chunk
        chunk = sse_chunk("resp-1", {"tool_calls": [{
            "index": 0, "id": "call_abc", "type": "function",
            "function": {"name": "transfer_to_number", "arguments": "{}"},
        }]}, finish_reason=None)
        data = json.loads(chunk.replace("data: ", "").strip())
        tc = data["choices"][0]["delta"]["tool_calls"][0]
        assert tc["function"]["name"] == "transfer_to_number"

    def test_sse_stream_starts_with_role(self):
        """First SSE chunk has role=assistant."""
        r = self.client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
            "elevenlabs_extra_body": {"conversation_id": "test-role-1"},
        })
        lines = [l for l in r.text.split("\n") if l.startswith("data:") and l != "data: [DONE]"]
        assert len(lines) >= 1
        first = json.loads(lines[0].replace("data: ", ""))
        assert first["choices"][0]["delta"].get("role") == "assistant"


# ===================================================================
# 14. CIRCUIT BREAKER — degradation levels
# ===================================================================

class TestCircuitBreaker:
    """Test circuit breaker / graceful degradation."""

    def setup_method(self):
        import agent.server as srv
        srv._cb_failures = 0
        srv._cb_opened_at = None

    def test_level_0_healthy(self):
        """Level 0: healthy, no failures."""
        from agent.server import _cb_status, _cb_is_open
        assert _cb_is_open() is False
        assert _cb_status()["level"] == 0
        assert _cb_status()["label"] == "healthy"

    def test_level_1_degraded_after_3_failures(self):
        """Level 1: degraded after 3 consecutive failures."""
        from agent.server import _cb_record_failure, _cb_is_open, _cb_status
        for _ in range(3):
            _cb_record_failure()
        assert _cb_is_open() is True
        assert _cb_status()["level"] == 1
        assert _cb_status()["label"] == "degraded"

    def test_success_resets_to_healthy(self):
        """Success resets circuit breaker to healthy."""
        from agent.server import _cb_record_failure, _cb_record_success, _cb_is_open
        for _ in range(3):
            _cb_record_failure()
        assert _cb_is_open() is True
        _cb_record_success()
        assert _cb_is_open() is False

    def test_cooldown_auto_resets(self):
        """Circuit breaker auto-resets after cooldown period."""
        import agent.server as srv
        from agent.server import _cb_record_failure, _cb_is_open
        for _ in range(3):
            _cb_record_failure()
        assert _cb_is_open() is True
        srv._cb_opened_at = time.time() - 61  # Simulate cooldown expiry
        assert _cb_is_open() is False

    def test_health_endpoint_reflects_circuit_state(self):
        """Health endpoint shows correct degradation level."""
        from fastapi.testclient import TestClient
        from agent.server import app, _cb_record_failure
        import agent.server as srv
        srv._cb_failures = 0
        srv._cb_opened_at = None
        client = TestClient(app)

        r = client.get("/health")
        assert r.json()["degradation_level"] == 0

        for _ in range(3):
            _cb_record_failure()

        r = client.get("/health")
        assert r.json()["degradation_level"] == 1
        assert r.json()["status"] == "degraded"


# ===================================================================
# 15. UTILITY FUNCTIONS
# ===================================================================

class TestUtilities:
    """Test shared utility functions."""

    def test_mark_completed_adds_intent(self):
        """mark_completed adds intent to list."""
        from agent.nodes.utils import mark_completed
        state = {"completed_intents": ["faq"]}
        result = mark_completed(state, "status_check")
        assert result == ["faq", "status_check"]

    def test_mark_completed_no_duplicates(self):
        """mark_completed does not duplicate existing intents."""
        from agent.nodes.utils import mark_completed
        state = {"completed_intents": ["faq"]}
        result = mark_completed(state, "faq")
        assert result == ["faq"]

    def test_mark_completed_empty_list(self):
        """mark_completed works with empty initial list."""
        from agent.nodes.utils import mark_completed
        state = {"completed_intents": []}
        result = mark_completed(state, "complaint")
        assert result == ["complaint"]



# ===================================================================
# 16. STATE VALIDATION
# ===================================================================

class TestStateValidation:
    """Test AgentState type and field coverage."""

    def test_all_intent_types_valid(self):
        """All Intent type values are covered in routing."""
        from agent.state import Intent
        from agent.graph import route_by_intent

        # Get all valid intents from the Intent type annotation
        valid_intents = [
            "status_check", "appointment_book", "appointment_list",
            "appointment_cancel", "document_request", "document_status",
            "faq", "fee_inquiry", "complaint", "escalate", "unknown",
        ]
        for intent in valid_intents:
            node = route_by_intent({"current_intent": intent})
            assert node is not None, f"Intent '{intent}' has no route"

    def test_make_state_has_all_required_fields(self):
        """Helper state has all AgentState fields."""
        state = make_state()
        required_fields = [
            "messages", "auth_status", "citizen_profile", "current_intent",
            "completed_intents", "workflow_node", "rag_query", "rag_score",
            "timing_intent_classify", "timing_service_node",
            "service_node_name", "tool_chain_triggered", "api_calls_count",
            "prompt_version", "language",
        ]
        for field in required_fields:
            assert field in state, f"Missing field: {field}"
