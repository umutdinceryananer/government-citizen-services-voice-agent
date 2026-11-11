"""
Shared test fixtures — single test DB for all API tests.

Each test runs in its own transaction that gets rolled back after,
so tests never see each other's writes. Seed data (Ahmet, Fatma)
is always available because it's committed before tests start.
"""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.models import Application, Base, Citizen, AuthAuditLog, Office, get_db
from api.server import app
from api.crypto import keyed_hash
from api.seed_data import generate_valid_tc

# Keys for the guarded government routes (api/security.py). Assigned (not
# setdefault) so tests never depend on a developer's .env values.
TEST_INTERNAL_KEY = "test-internal-key"
TEST_ADMIN_KEY = "test-admin-key"
os.environ["INTERNAL_API_KEY"] = TEST_INTERNAL_KEY
os.environ["ADMIN_API_KEY"] = TEST_ADMIN_KEY
# Pepper for keyed identifier hashes (api/crypto.py), >= 32 characters
TEST_ID_HASH_PEPPER = "test-pepper-0123456789abcdef0123456789"
os.environ["ID_HASH_PEPPER"] = TEST_ID_HASH_PEPPER
# Auth webhook secrets (api/security.py, agent/session_token.py)
TEST_PLATFORM_SECRET = "test-platform-secret"
TEST_SESSION_TOKEN_SECRET = "test-session-secret-0123456789abcdef0123"
os.environ["PLATFORM_WEBHOOK_SECRET"] = TEST_PLATFORM_SECRET
os.environ["SESSION_TOKEN_SECRET"] = TEST_SESSION_TOKEN_SECRET
PLATFORM_AUTH = {"Authorization": f"Bearer {TEST_PLATFORM_SECRET}"}
# Custom LLM endpoint secret (agent/server.py)
TEST_CUSTOM_LLM_SECRET = "test-custom-llm-secret"
os.environ["CUSTOM_LLM_SECRET"] = TEST_CUSTOM_LLM_SECRET
CUSTOM_LLM_AUTH = {"Authorization": f"Bearer {TEST_CUSTOM_LLM_SECRET}"}

# Single in-memory DB shared across all test modules
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=test_engine)


@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    """Create tables and seed citizen data once for the entire test session."""
    Base.metadata.create_all(test_engine)

    db = TestSession()
    tc_ahmet = generate_valid_tc("100000001")
    tc_fatma = generate_valid_tc("200000002")

    ahmet = Citizen(
        tc_kimlik_hash=keyed_hash(tc_ahmet),
        tc_last4_hash=keyed_hash(tc_ahmet[-4:]),
        first_name="Ahmet", last_name="Yilmaz", father_name="Mehmet",
        date_of_birth="15/03/1990",
        gender="M",
        language_preference="tr",
    )
    fatma = Citizen(
        tc_kimlik_hash=keyed_hash(tc_fatma),
        tc_last4_hash=keyed_hash(tc_fatma[-4:]),
        first_name="Fatma", last_name="Kaya", father_name="Ali",
        date_of_birth="22/07/1985",
        gender="F",
        language_preference="tr",
    )
    db.add_all([ahmet, fatma])
    db.flush()  # Assign IDs

    db.add_all([
        Application(
            citizen_id=ahmet.id, application_ref="2024-TR-0024",
            service_type="id_card", status="approved",
        ),
        Application(
            citizen_id=ahmet.id, application_ref="2024-TR-0001",
            service_type="passport", status="in_review",
        ),
        Application(
            citizen_id=fatma.id, application_ref="2024-TR-0002",
            service_type="id_card", status="approved",
        ),
    ])

    # Seed offices for appointment slot tests
    db.add_all([
        Office(name="Kadikoy Nufus Mudurlugu", city="Istanbul",
               services="passport,id_card", open_time="09:00", close_time="17:00", slot_duration_min=60),
        Office(name="Uskudar Nufus Mudurlugu", city="Istanbul",
               services="id_card,civil_registry", open_time="09:00", close_time="17:00", slot_duration_min=60),
        Office(name="Besiktas Nufus Mudurlugu", city="Istanbul",
               services="driver_license,passport", open_time="08:00", close_time="16:00", slot_duration_min=60),
        Office(name="Bakirkoy Nufus Mudurlugu", city="Istanbul",
               services="civil_registry,driver_license", open_time="09:00", close_time="17:00", slot_duration_min=60),
    ])
    db.commit()
    db.close()

    yield

    Base.metadata.drop_all(test_engine)


@pytest.fixture(autouse=True)
def clean_audit_log():
    """Wipe audit log before each test so tests never see each other's writes."""
    db = TestSession()
    db.query(AuthAuditLog).delete()
    db.commit()
    db.close()

    yield


@pytest.fixture(autouse=True)
def reset_auth_attempts():
    """Clear the in-memory auth attempt limiter so tests never lock each other out."""
    from api.auth import attempt_limiter
    attempt_limiter.reset()
    yield


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
