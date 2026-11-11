"""
Tests for keyed identifier hashing (api/crypto.py) and webhook auth.

- keyed_hash is deterministic per pepper, changes with the pepper, refuses weak peppers
- No plain SHA-256 of identifiers is left in api/
- api/auth.py does not depend on seed data
- Seed data is stored with keyed hashes and every seeded citizen can authenticate
- A citizen created outside the seed data can authenticate through the webhook
- Ambiguous matches (same last 4 + DOB + father initial) fail
"""

import ast
import hashlib
import pathlib
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api.seed_data as seed_data
from api.auth import AuthWebhookRequest, verify_webhook
from api.crypto import MIN_PEPPER_LENGTH, keyed_hash
from api.models import Base, Citizen
from api.seed_data import SEED_CITIZENS, generate_valid_tc
from api.server import app
from tests.conftest import PLATFORM_AUTH, TestSession


API_DIR = pathlib.Path(__file__).resolve().parent.parent / "api"
SEED_PREFIXES = {row[0] for row in SEED_CITIZENS}

client = TestClient(app, headers=PLATFORM_AUTH)  # acts as the ElevenLabs dispatch tool


def _plain_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _webhook(last4: str, dob: str, father_initial: str):
    # Fresh conversation per call so the attempt limiter never interferes here
    return client.post("/auth/verify/webhook", json={
        "tc_kimlik_last4": last4,
        "date_of_birth": dob,
        "father_initial": father_initial,
        "conversation_id": f"conv-{uuid.uuid4().hex}",
    })


def _tc_with_same_last4(tc: str) -> str:
    """Find a different valid TC Kimlik ending in the same 4 digits."""
    for prefix in range(1_000_000, 10_000_000):
        candidate = generate_valid_tc(f"{prefix}{tc[7:9]}")
        if candidate != tc and candidate[-4:] == tc[-4:]:
            return candidate
    raise AssertionError("no TC Kimlik with matching last 4 digits found")


# --- keyed_hash ---

class TestKeyedHash:

    def test_same_input_same_pepper_same_hash(self):
        assert keyed_hash("12345678901") == keyed_hash("12345678901")

    def test_different_pepper_different_hash(self, monkeypatch):
        before = keyed_hash("12345678901")
        monkeypatch.setenv("ID_HASH_PEPPER", "another-pepper-" + "x" * MIN_PEPPER_LENGTH)
        assert keyed_hash("12345678901") != before

    def test_different_input_different_hash(self):
        assert keyed_hash("12345678901") != keyed_hash("12345678902")

    def test_is_not_plain_sha256(self):
        assert keyed_hash("12345678901") != _plain_sha256("12345678901")

    def test_refuses_short_pepper(self, monkeypatch):
        monkeypatch.setenv("ID_HASH_PEPPER", "x" * (MIN_PEPPER_LENGTH - 1))
        with pytest.raises(RuntimeError, match="ID_HASH_PEPPER"):
            keyed_hash("12345678901")

    def test_refuses_missing_pepper(self, monkeypatch):
        monkeypatch.delenv("ID_HASH_PEPPER", raising=False)
        with pytest.raises(RuntimeError, match="ID_HASH_PEPPER"):
            keyed_hash("12345678901")

    def test_accepts_minimum_length_pepper(self, monkeypatch):
        monkeypatch.setenv("ID_HASH_PEPPER", "x" * MIN_PEPPER_LENGTH)
        assert len(keyed_hash("12345678901")) == 64


# --- Source checks ---

class TestNoUnkeyedHashesInApi:

    def test_no_plain_sha256_call_in_api(self):
        offenders = [
            p.name for p in API_DIR.glob("*.py")
            if re.search(r"hashlib\.sha256\(|\bsha256\(", p.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_only_crypto_module_imports_hashlib(self):
        importers = []
        for p in API_DIR.glob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if "hashlib" in names:
                    importers.append(p.name)
        assert importers == ["crypto.py"]

    def test_auth_does_not_import_seed_data(self):
        source = (API_DIR / "auth.py").read_text(encoding="utf-8")
        modules = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        assert "api.seed_data" not in modules
        assert "SEED_CITIZENS" not in source
        assert "generate_valid_tc" not in source


# --- Real seed data (api/seed_data.seed) into a fresh database ---

@pytest.fixture
def fresh_seed_db(monkeypatch):
    """Run api.seed_data.seed() against a fresh in-memory DB and return a session."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(seed_data, "engine", engine)
    monkeypatch.setattr(seed_data, "SessionLocal", Session)
    monkeypatch.setattr(seed_data, "init_db", lambda: Base.metadata.create_all(engine))
    seed_data.seed()
    db = Session()
    yield db
    db.close()
    engine.dispose()


class TestSeedData:

    def test_seed_stores_keyed_hashes(self, fresh_seed_db):
        assert fresh_seed_db.query(Citizen).count() == len(SEED_CITIZENS)
        for first_9, first_name, last_name, *_ in SEED_CITIZENS:
            tc = generate_valid_tc(first_9)
            citizen = fresh_seed_db.query(Citizen).filter_by(first_name=first_name, last_name=last_name).one()
            assert citizen.tc_kimlik_hash == keyed_hash(tc)
            assert citizen.tc_last4_hash == keyed_hash(tc[-4:])
            assert citizen.tc_kimlik_hash != _plain_sha256(tc)

    @pytest.mark.parametrize("row", SEED_CITIZENS, ids=[f"{r[1]} {r[2]}" for r in SEED_CITIZENS])
    def test_every_seeded_citizen_authenticates(self, fresh_seed_db, row):
        first_9, first_name, _, father_name, dob, *_ = row
        tc = generate_valid_tc(first_9)
        result = verify_webhook(
            AuthWebhookRequest(
                tc_kimlik_last4=tc[-4:], date_of_birth=dob, father_initial=father_name[0],
                conversation_id=f"seed-{first_9}",
            ),
            db=fresh_seed_db,
        )
        assert result.is_error is False
        assert result.citizen_profile["first_name"] == first_name

    def test_seed_refuses_old_schema(self, monkeypatch, capsys):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE citizens (id INTEGER PRIMARY KEY, tc_kimlik_hash TEXT)"))
        monkeypatch.setattr(seed_data, "engine", engine)
        monkeypatch.setattr(seed_data, "SessionLocal", sessionmaker(bind=engine))
        monkeypatch.setattr(seed_data, "init_db", lambda: Base.metadata.create_all(engine))

        seed_data.seed()

        assert "Delete data/citizens.db" in capsys.readouterr().out
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM citizens")).scalar() == 0
        engine.dispose()


# --- Webhook auth against the shared test DB ---

@pytest.fixture
def add_citizen():
    """Insert citizens into the shared test DB; remove them after the test."""
    created_ids = []

    def _add(tc: str, first_name: str, father_name: str, dob: str) -> None:
        db = TestSession()
        citizen = Citizen(
            tc_kimlik_hash=keyed_hash(tc),
            tc_last4_hash=keyed_hash(tc[-4:]),
            first_name=first_name, last_name="Testoglu",
            father_name=father_name, date_of_birth=dob,
        )
        db.add(citizen)
        db.commit()
        created_ids.append(citizen.id)
        db.close()

    yield _add

    db = TestSession()
    db.query(Citizen).filter(Citizen.id.in_(created_ids)).delete(synchronize_session=False)
    db.commit()
    db.close()


class TestWebhookAuth:

    TC_AHMET = generate_valid_tc("100000001")

    def test_seeded_citizen_authenticates_over_http(self):
        r = _webhook(self.TC_AHMET[-4:], "15/03/1990", "M")
        assert r.status_code == 200
        assert r.json()["citizen_profile"]["first_name"] == "Ahmet"

    def test_spoken_date_format_accepted(self):
        r = _webhook(self.TC_AHMET[-4:], "15 Mart 1990", "m")
        assert r.status_code == 200

    def test_citizen_outside_seed_data_authenticates(self, add_citizen):
        assert "987654321" not in SEED_PREFIXES
        tc = generate_valid_tc("987654321")
        add_citizen(tc, "Deniz", "Kemal", "09/09/1999")

        r = _webhook(tc[-4:], "09/09/1999", "K")
        assert r.status_code == 200
        assert r.json()["is_error"] is False
        assert r.json()["citizen_profile"]["first_name"] == "Deniz"

    @pytest.mark.parametrize("last4,dob,father", [
        ("0000", "15/03/1990", "M"),  # wrong last 4
        (None, "16/03/1990", "M"),    # wrong date of birth
        (None, "15/03/1990", "K"),    # wrong father initial
    ], ids=["wrong-last4", "wrong-dob", "wrong-father-initial"])
    def test_wrong_credentials_fail(self, last4, dob, father):
        r = _webhook(last4 or self.TC_AHMET[-4:], dob, father)
        assert r.status_code == 401

    def test_ambiguous_match_fails(self, add_citizen):
        tc_a = generate_valid_tc("987654321")
        tc_b = _tc_with_same_last4(tc_a)
        add_citizen(tc_a, "Deniz", "Kemal", "09/09/1999")
        add_citizen(tc_b, "Derya", "Kerem", "09/09/1999")  # same last 4, DOB, father initial

        r = _webhook(tc_a[-4:], "09/09/1999", "K")
        assert r.status_code == 401

    def test_father_initial_separates_same_last4_and_dob(self, add_citizen):
        tc_a = generate_valid_tc("987654321")
        tc_b = _tc_with_same_last4(tc_a)
        add_citizen(tc_a, "Deniz", "Kemal", "09/09/1999")
        add_citizen(tc_b, "Derya", "Selim", "09/09/1999")

        assert _webhook(tc_a[-4:], "09/09/1999", "K").json()["citizen_profile"]["first_name"] == "Deniz"
        assert _webhook(tc_b[-4:], "09/09/1999", "S").json()["citizen_profile"]["first_name"] == "Derya"

    def test_missing_pepper_never_authenticates(self, monkeypatch):
        monkeypatch.delenv("ID_HASH_PEPPER", raising=False)
        r = TestClient(app, headers=PLATFORM_AUTH, raise_server_exceptions=False).post(
            "/auth/verify/webhook", json={
                "tc_kimlik_last4": self.TC_AHMET[-4:],
                "date_of_birth": "15/03/1990",
                "father_initial": "M",
                "conversation_id": "conv-no-pepper",
            },
        )
        assert r.status_code == 500  # fails closed (not a 403 from a missing header)
