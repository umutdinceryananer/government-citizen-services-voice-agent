"""
Government services API endpoints.

Provides application status lookup, appointment booking,
document requests, and service catalog.

Access (see api/security.py):
- public_router — read-only catalog + slot lookup, no key
- router        — citizen data, X-Internal-Key (LangGraph nodes only)
- admin_router  — GDPR erasure, X-Admin-Key (operators only, never the voice flow)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agent.logging_config import get_logger
from api.models import Application, Appointment, Citizen, DocumentRequest, Office, get_db
from api.security import require_admin_key, require_internal_key

logger = get_logger(__name__)

public_router = APIRouter(tags=["services"])
router = APIRouter(tags=["services"], dependencies=[Depends(require_internal_key)])
admin_router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin_key)])


# --- Response models ---

class ApplicationStatusResponse(BaseModel):
    application_ref: str
    service_type: str
    status: str
    citizen_id: int | None = None
    first_name: str
    last_name_initial: str
    estimated_completion: str | None = None
    notes: str | None = None
    submitted_date: str | None = None
    last_updated: str | None = None
    office: str | None = None


class AppointmentRequest(BaseModel):
    citizen_id: int
    service_type: str
    preferred_date: str  # YYYY-MM-DD


class AppointmentResponse(BaseModel):
    id: int
    service_type: str
    appointment_date: str
    appointment_time: str
    office: str
    status: str


class DocumentRequestCreate(BaseModel):
    citizen_id: int
    document_type: str


class DocumentRequestResponse(BaseModel):
    request_ref: str
    document_type: str
    status: str
    estimated_days: int


class ServiceInfo(BaseModel):
    id: str
    name_tr: str
    name_en: str
    description_tr: str
    description_en: str
    requires_auth: bool


# --- Available services catalog ---

SERVICES_CATALOG = [
    ServiceInfo(
        id="passport", name_tr="Pasaport Islemleri", name_en="Passport Services",
        description_tr="Yeni pasaport, yenileme, kayip pasaport islemleri",
        description_en="New passport, renewal, lost passport services",
        requires_auth=True,
    ),
    ServiceInfo(
        id="id_card", name_tr="Kimlik Karti Islemleri", name_en="ID Card Services",
        description_tr="Yeni kimlik karti, yenileme, kayip kimlik islemleri",
        description_en="New ID card, renewal, lost ID card services",
        requires_auth=True,
    ),
    ServiceInfo(
        id="driver_license", name_tr="Ehliyet Islemleri", name_en="Driver's License Services",
        description_tr="Yeni ehliyet, yenileme, sinif yukseltme islemleri",
        description_en="New license, renewal, class upgrade services",
        requires_auth=True,
    ),
    ServiceInfo(
        id="civil_registry", name_tr="Nufus Hizmetleri", name_en="Civil Registry Services",
        description_tr="Dogum belgesi, ikametgah, evlilik islemleri",
        description_en="Birth certificate, residence, marriage services",
        requires_auth=True,
    ),
    ServiceInfo(
        id="general_inquiry", name_tr="Genel Bilgi", name_en="General Information",
        description_tr="Calisma saatleri, ucretler, gerekli belgeler hakkinda bilgi",
        description_en="Working hours, fees, required documents information",
        requires_auth=False,
    ),
]

class SlotResponse(BaseModel):
    office: str
    date: str
    time: str


def _generate_slots_for_office(
    office: Office, service_type: str, booked: set[tuple[str, str, str]], days_ahead: int = 7,
) -> list[SlotResponse]:
    """Generate available slots for an office, excluding booked ones.

    Generates 1-hour slots within office working hours for the next N business days.
    Skips weekends and already-booked (office, date, time) combinations.
    """
    from datetime import date, timedelta

    if service_type not in office.services.split(","):
        return []

    open_h, open_m = map(int, office.open_time.split(":"))
    close_h, close_m = map(int, office.close_time.split(":"))
    duration = office.slot_duration_min

    slots = []
    current = date.today() + timedelta(days=1)
    days_counted = 0

    while days_counted < days_ahead:
        if current.weekday() < 5:  # Skip weekends
            days_counted += 1
            hour, minute = open_h, open_m
            while hour < close_h or (hour == close_h and minute < close_m):
                time_str = f"{hour:02d}:{minute:02d}"
                date_str = str(current)

                if (office.name, date_str, time_str) not in booked:
                    slots.append(SlotResponse(office=office.name, date=date_str, time=time_str))

                minute += duration
                if minute >= 60:
                    hour += minute // 60
                    minute = minute % 60

        current += timedelta(days=1)

    return slots


@public_router.get("/appointments/slots/{service_type}", response_model=list[SlotResponse])
def get_available_slots(service_type: str, db: Session = Depends(get_db)):
    """Return available appointment slots for a service type.

    Queries offices from DB, generates slots from working hours,
    filters out booked ones. Returns first 3 for voice-friendly output.
    """
    offices = db.query(Office).all()

    confirmed = db.query(Appointment).filter(Appointment.status == "confirmed").all()
    booked = {(a.office, a.appointment_date, a.appointment_time) for a in confirmed}

    all_slots = []
    for office in offices:
        all_slots.extend(_generate_slots_for_office(office, service_type, booked))

    all_slots.sort(key=lambda s: (s.date, s.time))
    return all_slots[:3]


ESTIMATED_COMPLETION = {
    "pending": "15-20 business days",
    "in_review": "5-10 business days",
    "approved": "Completed",
    "rejected": "Rejected",
    "additional_docs_needed": "Awaiting additional documents",
}


# --- Endpoints ---

@router.get("/applications/{ref_number}", response_model=ApplicationStatusResponse)
def get_application_status(ref_number: str, db: Session = Depends(get_db)):
    """Look up application status by reference number."""
    application = db.query(Application).filter(
        Application.application_ref == ref_number.strip().upper()
    ).first()

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    citizen = db.query(Citizen).filter(Citizen.id == application.citizen_id).first()

    logger.info(f"Application lookup | ref={ref_number}")

    return ApplicationStatusResponse(
        application_ref=application.application_ref,
        service_type=application.service_type,
        status=application.status,
        citizen_id=application.citizen_id,
        first_name=citizen.first_name if citizen else "Unknown",
        last_name_initial=(citizen.last_name[0] + "***") if citizen else "?***",
        estimated_completion=ESTIMATED_COMPLETION.get(application.status),
        notes=application.notes,
        submitted_date=application.submitted_date,
        last_updated=application.last_updated,
        office=application.office,
    )


@router.get("/applications", response_model=list[ApplicationStatusResponse])
def list_citizen_applications(citizen_id: int, db: Session = Depends(get_db)):
    """List all applications for a citizen."""
    citizen = db.query(Citizen).filter(Citizen.id == citizen_id).first()
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    applications = db.query(Application).filter(
        Application.citizen_id == citizen_id
    ).order_by(Application.id.desc()).all()

    logger.info(f"Applications list | citizen_id={citizen_id} | count={len(applications)}")

    return [
        ApplicationStatusResponse(
            application_ref=app.application_ref,
            service_type=app.service_type,
            status=app.status,
            first_name=citizen.first_name,
            last_name_initial=citizen.last_name[0] + "***",
            estimated_completion=ESTIMATED_COMPLETION.get(app.status),
            notes=app.notes,
            submitted_date=app.submitted_date,
            last_updated=app.last_updated,
            office=app.office,
        )
        for app in applications
    ]


@router.post("/appointments", response_model=AppointmentResponse)
def book_appointment(request: AppointmentRequest, db: Session = Depends(get_db)):
    """Book a new appointment with edge case handling."""
    citizen = db.query(Citizen).filter(Citizen.id == request.citizen_id).first()
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Edge case: past date
    if request.preferred_date:
        from datetime import date as date_type
        try:
            preferred = date_type.fromisoformat(request.preferred_date)
            if preferred < date_type.today():
                raise HTTPException(
                    status_code=400,
                    detail="Cannot book an appointment in the past. Please choose a future date."
                )
        except ValueError:
            pass  # Invalid date format — let it fall through to slot matching

    # Edge case: duplicate appointment (same citizen, same service, same date)
    if request.preferred_date:
        existing = db.query(Appointment).filter(
            Appointment.citizen_id == request.citizen_id,
            Appointment.service_type == request.service_type,
            Appointment.appointment_date == request.preferred_date,
            Appointment.status == "confirmed",
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"You already have a {request.service_type} appointment on {request.preferred_date} at {existing.appointment_time}."
            )

    # Find first available slot from DB offices
    confirmed = db.query(Appointment).filter(Appointment.status == "confirmed").all()
    booked = {(a.office, a.appointment_date, a.appointment_time) for a in confirmed}

    offices = db.query(Office).all()
    all_slots = []
    for ofc in offices:
        all_slots.extend(_generate_slots_for_office(ofc, request.service_type, booked))
    all_slots.sort(key=lambda s: (s.date, s.time))

    # Filter by preferred date if specified
    if request.preferred_date:
        all_slots = [s for s in all_slots if s.date == request.preferred_date]

    if not all_slots:
        raise HTTPException(status_code=409, detail="No available appointment slots for the requested date. Please try a different date.")

    chosen = all_slots[0]
    office = chosen.office
    date = chosen.date
    time = chosen.time

    appointment = Appointment(
        citizen_id=request.citizen_id,
        service_type=request.service_type,
        appointment_date=date,
        appointment_time=time,
        office=office,
        status="confirmed",
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    logger.info(f"Appointment booked | citizen={request.citizen_id} | {date} {time} @ {office}")

    return AppointmentResponse(
        id=appointment.id,
        service_type=appointment.service_type,
        appointment_date=appointment.appointment_date,
        appointment_time=appointment.appointment_time,
        office=appointment.office,
        status=appointment.status,
    )


@router.get("/appointments/{citizen_id}", response_model=list[AppointmentResponse])
def get_appointments(citizen_id: int, db: Session = Depends(get_db)):
    """Get all appointments for a citizen."""
    appointments = db.query(Appointment).filter(Appointment.citizen_id == citizen_id).all()

    return [
        AppointmentResponse(
            id=a.id, service_type=a.service_type,
            appointment_date=a.appointment_date, appointment_time=a.appointment_time,
            office=a.office, status=a.status,
        )
        for a in appointments
    ]


@router.delete("/appointments/{appointment_id}")
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db)):
    """Cancel a confirmed appointment."""
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()

    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    if appointment.status != "confirmed":
        raise HTTPException(status_code=409, detail=f"Cannot cancel appointment with status '{appointment.status}'")

    appointment.status = "cancelled"
    db.commit()

    logger.info(f"Appointment cancelled | id={appointment_id}")

    return {"message": "Appointment cancelled successfully", "appointment_id": appointment_id}


@router.get("/documents/{citizen_id}", response_model=list[DocumentRequestResponse])
def get_document_requests(citizen_id: int, db: Session = Depends(get_db)):
    """Get all document requests for a citizen."""
    docs = db.query(DocumentRequest).filter(DocumentRequest.citizen_id == citizen_id).all()

    return [
        DocumentRequestResponse(
            request_ref=d.request_ref,
            document_type=d.document_type,
            status=d.status,
            estimated_days=d.estimated_days,
        )
        for d in docs
    ]


@router.post("/documents/request", response_model=DocumentRequestResponse)
def request_document(request: DocumentRequestCreate, db: Session = Depends(get_db)):
    """Request preparation of an official document."""
    citizen = db.query(Citizen).filter(Citizen.id == request.citizen_id).first()
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    estimated = {"birth_certificate": 3, "residence_cert": 5, "marriage_cert": 5,
                 "criminal_record": 7, "general": 5}

    doc_ref = f"DOC-2026-{uuid.uuid4().hex[:4].upper()}"

    doc_request = DocumentRequest(
        citizen_id=request.citizen_id,
        document_type=request.document_type,
        request_ref=doc_ref,
        status="processing",
        estimated_days=estimated.get(request.document_type, 5),
    )
    db.add(doc_request)
    db.commit()
    db.refresh(doc_request)

    logger.info(f"Document requested | citizen={request.citizen_id} | type={request.document_type} | ref={doc_ref}")

    return DocumentRequestResponse(
        request_ref=doc_request.request_ref,
        document_type=doc_request.document_type,
        status=doc_request.status,
        estimated_days=doc_request.estimated_days,
    )


@admin_router.delete("/citizens/{citizen_id}")
def delete_citizen_data(citizen_id: int, db: Session = Depends(get_db)):
    """GDPR Article 17 — Right to erasure.

    Deletes all data associated with a citizen: personal record,
    applications, appointments, document requests, and conversation logs.
    Auth audit logs are retained (they contain only hashed identifiers).
    """
    from api.models import ConversationLog

    citizen = db.query(Citizen).filter(Citizen.id == citizen_id).first()
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Delete all related records
    deleted = {
        "document_requests": db.query(DocumentRequest).filter(DocumentRequest.citizen_id == citizen_id).delete(),
        "appointments": db.query(Appointment).filter(Appointment.citizen_id == citizen_id).delete(),
        "applications": db.query(Application).filter(Application.citizen_id == citizen_id).delete(),
        "conversation_logs": db.query(ConversationLog).filter(ConversationLog.citizen_id == citizen_id).delete(),
    }
    db.delete(citizen)
    db.commit()

    logger.info(f"GDPR erasure | citizen_id={citizen_id} | deleted={deleted}")

    return {
        "message": "All citizen data has been permanently deleted",
        "citizen_id": citizen_id,
        "deleted_records": deleted,
    }


@public_router.get("/services", response_model=list[ServiceInfo])
def list_services():
    """Return catalog of available government services."""
    return SERVICES_CATALOG
