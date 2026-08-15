"""Relational clinic entity tables — dormant Phase 0 infrastructure.

Field names, status enums and validation semantics are copied directly
from services/clinic_data_service.py's current live ENTITY_META and
_validate_record() (verified against the real file, not guessed), so a
future migration maps onto this schema without reconciling incompatible
fields. `external_id` on every entity holds the current string ID format
(e.g. "P-001", "A-014") so a future backfill can preserve it exactly —
see docs/V2_COEXISTENCE.md's backfill strategy section.

Cross-entity foreign keys (Appointment -> Patient, Payment -> Package,
etc.) are composite: (organization_id, <parent>_id) referencing
(organization_id, id) on the parent table, not a bare parent_id FK. This
is deliberate, not incidental complexity — a bare `patient_id INTEGER
REFERENCES patients(id)` would let an Appointment row in Organization B
reference a Patient row in Organization A, which is exactly the kind of
cross-tenant FK bug the V2 audit's "foreign keys and organization-scoped
uniqueness are correct" requirement is checking for. The composite FK
makes that reference impossible at the database level, not just by
application-code convention.

No live code reads or writes any table in this file.
"""
from __future__ import annotations

import enum

from sqlalchemy import (
    Date,
    Enum,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.db.models.mixins import OrgScopedMixin, TimestampMixin


# ---------------------------------------------------------------------------
# Status enums — copied verbatim from services/clinic_data_service.py
# ---------------------------------------------------------------------------

class PatientStatus(str, enum.Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    RENEWAL_DUE = "Renewal Due"
    ARCHIVED = "Archived"


class TherapistStatus(str, enum.Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    ARCHIVED = "Archived"


class AppointmentStatus(str, enum.Enum):
    SCHEDULED = "Scheduled"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    NO_SHOW = "No-show"
    ARCHIVED = "Archived"


class PackageStatus(str, enum.Enum):
    ACTIVE = "Active"
    COMPLETED = "Completed"
    EXPIRED = "Expired"
    PAUSED = "Paused"
    ARCHIVED = "Archived"


class PackageTemplateStatus(str, enum.Enum):
    ACTIVE = "Active"
    ARCHIVED = "Archived"


class PaymentStatus(str, enum.Enum):
    PAID = "Paid"
    PENDING = "Pending"
    REFUNDED = "Refunded"
    ARCHIVED = "Archived"


class LeadStatus(str, enum.Enum):
    NEW = "New"
    CONTACTED = "Contacted"
    BOOKED = "Booked"
    CONVERTED = "Converted"
    LOST = "Lost"
    ARCHIVED = "Archived"


class LeadSource(str, enum.Enum):
    WEBSITE = "Website"
    PHONE = "Phone"
    WALK_IN = "Walk-in"
    REFERRAL = "Referral"
    OTHER = "Other"


class CorporateClientStatus(str, enum.Enum):
    NEW = "New"
    CONTACTED = "Contacted"
    PROPOSAL_SENT = "Proposal Sent"
    WON = "Won"
    LOST = "Lost"
    ARCHIVED = "Archived"


class ServiceStatus(str, enum.Enum):
    """No live counterpart today — services/clinic_data_service.py has no
    "services" entity; service names are free-text on Appointment/Package
    records (e.g. "Physiotherapy"). This model is new, forward-looking
    infrastructure requested explicitly for Phase 0, not a migration
    target for anything currently live."""

    ACTIVE = "Active"
    ARCHIVED = "Archived"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

class Therapist(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "therapists"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_therapists_org_id"),
        UniqueConstraint("organization_id", "external_id", name="uq_therapists_org_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TherapistStatus] = mapped_column(
        Enum(TherapistStatus, name="therapist_status"), nullable=False, default=TherapistStatus.ACTIVE
    )
    weekly_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Patient(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_patients_org_id"),
        UniqueConstraint("organization_id", "external_id", name="uq_patients_org_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[PatientStatus] = mapped_column(
        Enum(PatientStatus, name="patient_status"), nullable=False, default=PatientStatus.ACTIVE
    )
    last_visit: Mapped[Date | None] = mapped_column(Date)
    date_of_birth: Mapped[Date | None] = mapped_column(Date)
    sessions_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consent_to_contact: Mapped[bool] = mapped_column(nullable=False, default=False)


class Appointment(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_appointments_org_external_id"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_appointments_patient_same_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "therapist_id"],
            ["therapists.organization_id", "therapists.id"],
            name="fk_appointments_therapist_same_org",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    therapist_id: Mapped[int | None] = mapped_column(Integer, index=True)
    appointment_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    appointment_time: Mapped[str | None] = mapped_column(String(20))
    service: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status"),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
    )


class PackageTemplate(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "package_templates"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_package_templates_org_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    total_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PackageTemplateStatus] = mapped_column(
        Enum(PackageTemplateStatus, name="package_template_status"),
        nullable=False,
        default=PackageTemplateStatus.ACTIVE,
    )


class Package(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "packages"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_packages_org_id"),
        UniqueConstraint("organization_id", "external_id", name="uq_packages_org_external_id"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_packages_patient_same_org",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    total_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sessions_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_date: Mapped[Date | None] = mapped_column(Date)
    expiry_date: Mapped[Date | None] = mapped_column(Date)
    status: Mapped[PackageStatus] = mapped_column(
        Enum(PackageStatus, name="package_status"), nullable=False, default=PackageStatus.ACTIVE
    )


class Payment(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_payments_org_external_id"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_payments_patient_same_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "package_id"],
            ["packages.organization_id", "packages.id"],
            name="fk_payments_package_same_org",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    package_id: Mapped[int | None] = mapped_column(Integer, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    payment_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), nullable=False, default=PaymentStatus.PAID
    )
    method: Mapped[str | None] = mapped_column(String(80))
    reference: Mapped[str | None] = mapped_column(String(200))


class ProgressNote(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "progress_notes"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_progress_notes_org_external_id"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["patients.organization_id", "patients.id"],
            name="fk_progress_notes_patient_same_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "therapist_id"],
            ["therapists.organization_id", "therapists.id"],
            name="fk_progress_notes_therapist_same_org",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    therapist_id: Mapped[int | None] = mapped_column(Integer, index=True)
    visit_date: Mapped[Date] = mapped_column(Date, nullable=False)
    progress_summary: Mapped[str] = mapped_column(Text, nullable=False)
    progress_status: Mapped[str | None] = mapped_column(String(80))
    pain_score: Mapped[int | None] = mapped_column(Integer)
    next_step: Mapped[str | None] = mapped_column(Text)


class Lead(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_leads_org_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(320))
    message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[LeadSource] = mapped_column(
        Enum(LeadSource, name="lead_source"), nullable=False, default=LeadSource.OTHER
    )
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status"), nullable=False, default=LeadStatus.NEW
    )


class CorporateClient(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "corporate_clients"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_corporate_clients_org_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(40))
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(320))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CorporateClientStatus] = mapped_column(
        Enum(CorporateClientStatus, name="corporate_client_status"),
        nullable=False,
        default=CorporateClientStatus.NEW,
    )


class Service(OrgScopedMixin, TimestampMixin, Base):
    """New, forward-looking catalog entity — see ServiceStatus docstring
    above for why there's no existing live data this maps onto."""

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_services_org_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus, name="service_status"), nullable=False, default=ServiceStatus.ACTIVE
    )
