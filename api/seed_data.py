"""
Seed the citizen database with 20+ realistic sample records.

Usage:
    python -m api.seed_data          # Seed the database
    python -m api.seed_data --reset  # Drop and recreate tables, then seed

TC Kimlik numbers use valid checksums. All PII is fictional.
Identifiers are stored as keyed hashes (api/crypto.py) — ID_HASH_PEPPER must be set.

Schema changes are not migrated: if the citizens table predates a column,
delete data/citizens.db and run this again.
"""

import argparse

from sqlalchemy import inspect

from api.crypto import keyed_hash
from api.models import Base, Citizen, Application, Appointment, DocumentRequest, Office, SessionLocal, engine, init_db


def generate_valid_tc(first_9: str) -> str:
    """Generate a valid 11-digit TC Kimlik from the first 9 digits."""
    digits = [int(d) for d in first_9]
    odd_sum = sum(digits[i] for i in range(0, 9, 2))
    even_sum = sum(digits[i] for i in range(1, 8, 2))
    d10 = (odd_sum * 7 - even_sum) % 10
    d11 = (sum(digits) + d10) % 10
    return first_9 + str(d10) + str(d11)


# Fictional citizens with valid TC Kimlik checksums
# (first_9_digits, first_name, last_name, father_name, dob, lang, phone, gender)
# phone=None means no registered phone number (full KBA required)
SEED_CITIZENS = [
    ("100000001", "Ahmet", "Yilmaz", "Mehmet", "15/03/1990", "tr", "+905551000001", "M"),
    ("200000002", "Fatma", "Kaya", "Ali", "22/07/1985", "tr", "+905551000002", "F"),
    ("300000003", "Mehmet", "Demir", "Hasan", "01/01/1978", "tr", "+905551000003", "M"),
    ("400000004", "Ayse", "Celik", "Mustafa", "30/11/1995", "tr", None, "F"),
    ("500000005", "Mustafa", "Sahin", "Ibrahim", "14/06/1982", "tr", None, "M"),
    ("600000006", "Emine", "Yildiz", "Osman", "08/09/1973", "tr", "+905551000006", "F"),
    ("700000007", "Huseyin", "Ozturk", "Yusuf", "25/12/1988", "tr", None, "M"),
    ("800000008", "Zeynep", "Aydin", "Kemal", "03/04/1992", "tr", None, "F"),
    ("900000009", "Ali", "Arslan", "Huseyin", "19/10/1980", "tr", None, "M"),
    ("110000001", "Hatice", "Dogan", "Ahmet", "27/02/1969", "tr", None, "F"),
    ("120000001", "Ibrahim", "Kilic", "Omer", "11/08/1975", "tr", None, "M"),
    ("130000001", "Meryem", "Koc", "Hasan", "05/05/1998", "tr", None, "F"),
    ("140000001", "Hasan", "Ozdemir", "Ali", "16/01/1987", "tr", None, "M"),
    ("150000001", "Elif", "Polat", "Mehmet", "29/09/1993", "tr", None, "F"),
    ("160000001", "Omer", "Erdogan", "Mustafa", "07/12/1970", "tr", None, "M"),
    ("170000001", "Sule", "Tas", "Ibrahim", "20/06/1984", "tr", None, "F"),
    ("180000001", "Osman", "Cinar", "Yusuf", "12/03/1991", "tr", None, "M"),
    ("190000001", "Merve", "Acar", "Kemal", "23/11/1996", "tr", None, "F"),
    ("210000002", "Yusuf", "Kurt", "Osman", "04/07/1977", "tr", None, "M"),
    ("220000002", "Busra", "Oz", "Ahmet", "18/04/1989", "tr", None, "F"),
    # English-preference citizens
    ("230000002", "John", "Smith", "Robert", "10/02/1985", "en", "+15551000021", "M"),
    ("240000002", "Sarah", "Johnson", "Michael", "28/08/1992", "en", None, "F"),
    ("250000002", "David", "Williams", "James", "15/05/1978", "en", None, "M"),
]

# Applications: (citizen_index, ref, service_type, status, notes, submitted_date, last_updated, office)
# citizen_index is 0-based index into SEED_CITIZENS
SEED_APPLICATIONS = [
    # Ahmet — 2 applications
    (0, "2024-TR-0001", "passport", "additional_docs_needed",
     "Missing national ID copy and a recent photo taken within the last six months.",
     "2024-11-15", "2025-03-20", "Kadikoy Nufus Mudurlugu"),
    (0, "2024-TR-0024", "id_card", "approved",
     "ID card printed. Please visit the office for pickup.",
     "2024-09-10", "2025-02-28", "Kadikoy Nufus Mudurlugu"),
    # Fatma — 1 application
    (1, "2024-TR-0002", "id_card", "approved",
     "ID card ready. Available for pickup at the registry office.",
     "2024-10-05", "2025-01-15", "Uskudar Nufus Mudurlugu"),
    # Mehmet — 2 applications
    (2, "2024-TR-0003", "driver_license", "pending",
     "Application received. Exam date not yet scheduled.",
     "2025-03-01", "2025-03-01", "Besiktas Nufus Mudurlugu"),
    (2, "2024-TR-0025", "passport", "rejected",
     "Photo requirements not met. You may reapply.",
     "2024-08-20", "2025-01-10", "Besiktas Nufus Mudurlugu"),
    # Ayse — 1 application
    (3, "2024-TR-0004", "civil_registry", "rejected",
     "Rejected due to missing documents. You have thirty days to appeal.",
     "2024-07-15", "2025-02-01", "Bakirkoy Nufus Mudurlugu"),
    # Mustafa — 1 application
    (4, "2024-TR-0005", "passport", "in_review",
     "Security verification in progress.",
     "2025-01-20", "2025-03-15", "Bakirkoy Nufus Mudurlugu"),
    # Emine — 2 applications
    (5, "2024-TR-0006", "id_card", "approved",
     "ID card ready. Available for pickup at your nearest registry office.",
     "2024-06-10", "2024-12-20", "Uskudar Nufus Mudurlugu"),
    (5, "2024-TR-0026", "civil_registry", "pending",
     "Birth certificate application is being processed.",
     "2025-03-10", "2025-03-10", "Uskudar Nufus Mudurlugu"),
    # Huseyin — 1 application
    (6, "2024-TR-0007", "passport", "additional_docs_needed",
     "Missing national ID copy and a recent photo taken within the last six months.",
     "2024-12-01", "2025-03-25", "Kadikoy Nufus Mudurlugu"),
    # Zeynep — 1 application
    (7, "2024-TR-0008", "driver_license", "pending",
     "Driver's license exam application received. Waiting for exam schedule.",
     "2025-02-15", "2025-02-15", "Besiktas Nufus Mudurlugu"),
    # Ali — 1 application
    (8, "2024-TR-0009", "id_card", "in_review",
     "ID card renewal application under review.",
     "2025-02-01", "2025-03-18", "Kadikoy Nufus Mudurlugu"),
    # Hatice — 1 application
    (9, "2024-TR-0010", "passport", "approved",
     "Passport printed. Available for pickup at Kadikoy Office.",
     "2024-05-20", "2024-11-30", "Kadikoy Nufus Mudurlugu"),
    # Ibrahim — 1 application
    (10, "2024-TR-0011", "civil_registry", "pending",
     "Residence certificate application is being processed.",
     "2025-03-20", "2025-03-20", "Bakirkoy Nufus Mudurlugu"),
    # Meryem — 1 application
    (11, "2024-TR-0012", "driver_license", "in_review",
     "Driver's license exam results are being evaluated.",
     "2025-01-10", "2025-03-22", "Uskudar Nufus Mudurlugu"),
    # Hasan — 1 application
    (12, "2024-TR-0013", "id_card", "approved",
     "ID card ready. Available for pickup at Besiktas Office.",
     "2024-08-15", "2025-01-05", "Besiktas Nufus Mudurlugu"),
    # Elif — 1 application
    (13, "2024-TR-0014", "passport", "rejected",
     "Processing fee not completed. You may reapply after payment.",
     "2024-09-25", "2025-02-10", "Bakirkoy Nufus Mudurlugu"),
    # Omer — 1 application
    (14, "2024-TR-0015", "driver_license", "additional_docs_needed",
     "Missing health report. A certified report from an authorized health institution is required.",
     "2024-11-05", "2025-03-28", "Kadikoy Nufus Mudurlugu"),
    # Sule — 1 application
    (15, "2024-TR-0016", "civil_registry", "pending",
     "Marriage certificate application received.",
     "2025-03-05", "2025-03-05", "Uskudar Nufus Mudurlugu"),
    # Osman — 1 application
    (16, "2024-TR-0017", "passport", "in_review",
     "Application is being evaluated. Additional information may be requested.",
     "2025-02-20", "2025-03-30", "Besiktas Nufus Mudurlugu"),
    # Merve — 1 application
    (17, "2024-TR-0018", "id_card", "approved",
     "ID card ready. Available for pickup at Bakirkoy Office.",
     "2024-10-15", "2025-02-25", "Bakirkoy Nufus Mudurlugu"),
    # Yusuf — 1 application
    (18, "2024-TR-0019", "driver_license", "pending",
     "Driver's license application received. Document review pending.",
     "2025-03-15", "2025-03-15", "Kadikoy Nufus Mudurlugu"),
    # Busra — 1 application
    (19, "2024-TR-0020", "passport", "in_review",
     "Passport application under review.",
     "2025-01-25", "2025-03-12", "Uskudar Nufus Mudurlugu"),
    # English citizens
    (20, "2024-EN-0001", "passport", "approved",
     "Passport printed. Available for pickup at Kadikoy Office.",
     "2024-07-10", "2025-01-20", "Kadikoy Nufus Mudurlugu"),
    (21, "2024-EN-0002", "id_card", "in_review",
     "ID card renewal application under review.",
     "2025-02-05", "2025-03-19", "Besiktas Nufus Mudurlugu"),
    (22, "2024-EN-0003", "driver_license", "pending",
     "Driver's license application received. Awaiting exam schedule.",
     "2025-03-18", "2025-03-18", "Bakirkoy Nufus Mudurlugu"),
]


def seed(reset: bool = False):
    """Populate the database with sample citizen records.

    --reset only clears seed data tables (Citizen, Application, Appointment,
    DocumentRequest). Analytics tables (ConversationLog, AuthAuditLog) are
    preserved to retain conversation history and dashboard data.
    """
    # create_all never adds columns to an existing table — catch an old schema
    # here with a clear message instead of an SQL "no such column" error.
    inspector = inspect(engine)
    if inspector.has_table("citizens"):
        columns = {c["name"] for c in inspector.get_columns("citizens")}
        if "tc_last4_hash" not in columns:
            print("citizens table is from an older schema (no tc_last4_hash column).")
            print("Delete data/citizens.db and run `python -m api.seed_data` again.")
            return

    if reset:
        db = SessionLocal()
        db.query(DocumentRequest).delete()
        db.query(Appointment).delete()
        db.query(Application).delete()
        db.query(Citizen).delete()
        db.query(Office).delete()
        db.commit()
        db.close()
        print("Cleared seed data tables (citizens, applications, appointments, document_requests, offices).")
        print("Analytics tables (conversation_logs, auth_audit_log) preserved.")

    init_db()
    print("Tables created.")

    db = SessionLocal()
    try:
        existing = db.query(Citizen).count()
        if existing > 0 and not reset:
            print(f"Database already has {existing} records. Use --reset to recreate.")
            return

        # Seed citizens
        citizens = []
        for row in SEED_CITIZENS:
            first_9, first_name, last_name, father_name, dob, lang, phone, gender = row
            tc_kimlik = generate_valid_tc(first_9)

            citizen = Citizen(
                tc_kimlik_hash=keyed_hash(tc_kimlik),
                tc_last4_hash=keyed_hash(tc_kimlik[-4:]),
                first_name=first_name,
                last_name=last_name,
                father_name=father_name,
                date_of_birth=dob,
                gender=gender,
                phone_number=phone,
                language_preference=lang,
            )
            db.add(citizen)
            citizens.append(citizen)

        db.flush()  # Assign IDs before creating applications
        print(f"Seeded {len(SEED_CITIZENS)} citizen records.")

        # Seed applications (1:N with citizens)
        for citizen_idx, ref, service_type, status, notes, submitted, updated, office in SEED_APPLICATIONS:
            app = Application(
                citizen_id=citizens[citizen_idx].id,
                application_ref=ref,
                service_type=service_type,
                status=status,
                notes=notes,
                submitted_date=submitted,
                last_updated=updated,
                office=office,
            )
            db.add(app)

        print(f"Seeded {len(SEED_APPLICATIONS)} applications ({len([a for a in SEED_APPLICATIONS if a[0] in (0, 2, 5)])} citizens with multiple).")

        # Seed offices
        offices = [
            Office(name="Kadikoy Nufus Mudurlugu", city="Istanbul",
                   services="passport,id_card", open_time="09:00", close_time="17:00", slot_duration_min=60),
            Office(name="Uskudar Nufus Mudurlugu", city="Istanbul",
                   services="id_card,civil_registry", open_time="09:00", close_time="17:00", slot_duration_min=60),
            Office(name="Besiktas Nufus Mudurlugu", city="Istanbul",
                   services="driver_license,passport", open_time="08:00", close_time="16:00", slot_duration_min=60),
            Office(name="Bakirkoy Nufus Mudurlugu", city="Istanbul",
                   services="civil_registry,driver_license", open_time="09:00", close_time="17:00", slot_duration_min=60),
        ]
        db.add_all(offices)
        db.flush()
        print(f"Seeded {len(offices)} offices.")

        # Seed sample appointments — dynamic dates (always in the future)
        from datetime import date, timedelta
        base = date.today() + timedelta(days=1)
        appointments = [
            Appointment(citizen_id=citizens[0].id, service_type="passport", appointment_date=str(base),
                       appointment_time="10:00", office="Kadikoy Nufus Mudurlugu"),
            Appointment(citizen_id=citizens[1].id, service_type="id_card", appointment_date=str(base + timedelta(days=1)),
                       appointment_time="14:00", office="Uskudar Nufus Mudurlugu"),
            Appointment(citizen_id=citizens[2].id, service_type="driver_license", appointment_date=str(base + timedelta(days=2)),
                       appointment_time="09:00", office="Besiktas Nufus Mudurlugu"),
            Appointment(citizen_id=citizens[0].id, service_type="id_card", appointment_date=str(base - timedelta(days=30)),
                       appointment_time="11:00", office="Kadikoy Nufus Mudurlugu", status="completed"),
            Appointment(citizen_id=citizens[4].id, service_type="passport", appointment_date=str(base + timedelta(days=3)),
                       appointment_time="15:00", office="Bakirkoy Nufus Mudurlugu"),
        ]
        db.add_all(appointments)

        # Seed sample document requests
        doc_requests = [
            DocumentRequest(citizen_id=citizens[0].id, document_type="birth_certificate",
                          request_ref="DOC-2026-0001", status="ready", estimated_days=3),
            DocumentRequest(citizen_id=citizens[1].id, document_type="residence_cert",
                          request_ref="DOC-2026-0002", status="processing", estimated_days=5),
            DocumentRequest(citizen_id=citizens[5].id, document_type="marriage_cert",
                          request_ref="DOC-2026-0003", status="delivered", estimated_days=3),
        ]
        db.add_all(doc_requests)

        db.commit()
        print(f"Seeded {len(appointments)} appointments, {len(doc_requests)} document requests.")

        # Print sample for verification (no raw TC Kimlik)
        print("\nSample records:")
        for c in db.query(Citizen).limit(3):
            apps = db.query(Application).filter(Application.citizen_id == c.id).all()
            app_strs = [f"{a.application_ref}({a.service_type}:{a.status})" for a in apps]
            print(f"  {c.first_name} {c.last_name} | apps: {', '.join(app_strs)}")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed citizen database")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate tables")
    args = parser.parse_args()
    seed(reset=args.reset)
