"""
Database models for the citizen services platform.

Uses SQLAlchemy ORM for database abstraction. Default: SQLite for demo.
Production: switch to PostgreSQL by changing DATABASE_URL in .env.

    SQLite:      sqlite:///data/citizens.db
    PostgreSQL:  postgresql://user:pass@host/dbname
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/citizens.db")

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Citizen(Base):
    """Mock citizen record for identity verification."""

    __tablename__ = "citizens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Keyed HMAC-SHA256 hashes (api/crypto.py) — never plain SHA-256
    tc_kimlik_hash = Column(String, nullable=False, unique=True, index=True)  # full 11 digits
    tc_last4_hash = Column(String, nullable=False, index=True)  # last 4 digits, webhook auth (not unique)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    father_name = Column(String, nullable=False, default="")
    date_of_birth = Column(String, nullable=False)  # DD/MM/YYYY
    gender = Column(String, nullable=False, default="M")  # M or F
    phone_number = Column(String, nullable=True, index=True)  # E.164 format (+905551234567)
    language_preference = Column(String, nullable=False, default="tr")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Application(Base):
    """Citizen service applications (1:N with Citizen)."""

    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    citizen_id = Column(Integer, ForeignKey("citizens.id"), nullable=False, index=True)
    application_ref = Column(String, unique=True, nullable=False, index=True)
    service_type = Column(String, nullable=False)  # passport, id_card, driver_license, civil_registry
    status = Column(String, nullable=False, default="pending")  # pending, in_review, approved, rejected, additional_docs_needed
    notes = Column(String, nullable=True)  # Detail/explanation (e.g. "Eksik belge: nufus cuzdani fotokopisi")
    submitted_date = Column(String, nullable=True)  # YYYY-MM-DD
    last_updated = Column(String, nullable=True)  # YYYY-MM-DD
    office = Column(String, nullable=True)  # Processing office
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AuthAuditLog(Base):
    """Audit trail for authentication attempts and handoffs.

    No raw PII and never the auth factors (last 4 digits, DOB, father initial).
    """

    __tablename__ = "auth_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    session_id = Column(String, nullable=False, index=True)  # ElevenLabs conversation id
    method = Column(String, nullable=False)  # "webhook_last4_dob_father" or "handoff"
    attempt_number = Column(Integer, nullable=False)
    result = Column(String, nullable=False)  # "success" / "failure" (auth), "transferred" (handoff)
    citizen_id_hash = Column(String, nullable=True)  # keyed HMAC-SHA256 (api/crypto.py), null on failure
    failure_reason = Column(String, nullable=True)  # auth: "format" / "no_match" / "locked"; handoff: reason


class Appointment(Base):
    """Booked appointments for citizens."""

    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    citizen_id = Column(Integer, ForeignKey("citizens.id"), nullable=False, index=True)
    service_type = Column(String, nullable=False)  # passport, id_card, driver_license, etc.
    appointment_date = Column(String, nullable=False)  # YYYY-MM-DD
    appointment_time = Column(String, nullable=False)  # HH:MM
    office = Column(String, nullable=False)
    status = Column(String, nullable=False, default="confirmed")  # confirmed, cancelled, completed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DocumentRequest(Base):
    """Document preparation requests from citizens."""

    __tablename__ = "document_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    citizen_id = Column(Integer, ForeignKey("citizens.id"), nullable=False, index=True)
    document_type = Column(String, nullable=False)  # birth_certificate, residence_cert, etc.
    request_ref = Column(String, unique=True, nullable=False)  # DOC-YYYY-NNNN
    status = Column(String, nullable=False, default="processing")  # processing, ready, delivered
    estimated_days = Column(Integer, nullable=False, default=5)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ConversationLog(Base):
    """Analytics log for every Custom LLM request."""

    __tablename__ = "conversation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    conversation_id = Column(String, nullable=False, index=True)
    intent = Column(String, nullable=True)  # status_check, appointment_book, faq, etc.
    workflow_node = Column(String, nullable=True)  # from [NODE:xxx] marker
    auth_status = Column(String, nullable=False, default="unauthenticated")
    citizen_id = Column(Integer, nullable=True)
    language = Column(String, nullable=False, default="tr")
    message_count = Column(Integer, nullable=False, default=0)
    response_time_ms = Column(Integer, nullable=True)  # LangGraph processing time
    rag_query = Column(String, nullable=True)  # RAG search query (if faq intent)
    rag_score = Column(Float, nullable=True)  # Top RAG result relevance score
    intent_classify_ms = Column(Integer, nullable=True)  # Intent classification time
    service_node_name = Column(String, nullable=True)  # which LangGraph service node ran
    tool_chain_triggered = Column(String, nullable=True)  # tool chain (e.g. "status->rag_docs")
    api_calls_count = Column(Integer, nullable=True)  # number of API calls made by node


class Office(Base):
    """Government service offices with working hours and supported services."""

    __tablename__ = "offices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    city = Column(String, nullable=False, default="Istanbul")
    services = Column(String, nullable=False)  # Comma-separated: "passport,id_card"
    open_time = Column(String, nullable=False, default="09:00")  # HH:MM
    close_time = Column(String, nullable=False, default="17:00")  # HH:MM
    slot_duration_min = Column(Integer, nullable=False, default=60)  # Minutes per slot


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


def get_db():
    """Get a database session. Use as context manager or FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
