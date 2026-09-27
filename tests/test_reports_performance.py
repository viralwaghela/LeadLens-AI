"""Regression test for the public-demo Reports-page hang.

Root cause: services/clinic_data_service.py's patient_risk_summary() called
patient_profile() once PER PATIENT, and patient_profile() itself issued 4 separate
organization-wide list_records() reads (appointments, packages, payments,
progress_notes) and filtered to one patient in Python. Against a real Postgres
database (the public demo's), each of those reads costs a real network round trip —
with 48 seeded patients that was 190+ round trips, and the Reports page (which calls
both patient_risk_summary() and clinic_metrics(), which itself calls
patient_risk_summary() again) never finished loading.

This test proves the fix by asserting query COUNT does not scale with patient count
— the only deployment-independent way to catch this: a wall-clock timing assertion
against the fast local SQLite fixture used here would not have caught the original
bug (SQLite has no meaningful network round-trip cost), and a wall-clock assertion
against a real remote Postgres has no place in a fast, hermetic test suite. Query
count is exactly the metric the bug was about.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import pytest
import streamlit as st
from sqlalchemy import event
from sqlalchemy.engine import Engine

import core.auth as auth
import core.memory as business_memory
import services.clinic_data_service as crm
import services.crm_read_router as crm_router
from core.db.base import Base
import core.db.models  # noqa: F401 (populates Base.metadata)
from core.db.models.identity import MembershipRole
from core.db.session import make_engine
from core.identity import membership_service, organization_service, user_service
from core.identity.permissions import permissions_for_role
from core.identity.session import AuthenticatedSession, store_session

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def tenant_authoritative(tmp_path, monkeypatch):
    """Same shape as tests/test_phase8_saas_onboarding.py's `isolated` fixture:
    a real relational SQLite database with LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED
    genuinely on — the mode the public demo runs in, and the only mode where
    patient_risk_summary()/clinic_metrics() route through crm_read_router's
    per-call organization resolution this fix also addresses."""
    monkeypatch.setattr(business_memory, "DATABASE_FOLDER", tmp_path / "database")
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(auth, "_V2_ENGINE", engine)
    monkeypatch.setattr(crm_router, "_ENGINE", engine)
    monkeypatch.setenv("LEADLENS_V2_AUTH_ENABLED", "true")
    monkeypatch.setenv("LEADLENS_V2_CRM_TENANT_AUTHORITATIVE_ENABLED", "true")
    monkeypatch.setattr(crm_router, "TENANT_AUTHORITATIVE_ENABLED", True)
    st.session_state.clear()
    st.query_params.clear()
    yield engine
    st.session_state.clear()
    st.query_params.clear()
    engine.dispose()


def _seed_clinic(engine, *, patients: int, slug: str = "perf-clinic") -> int:
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        org = organization_service.create_organization(session, name="Perf Clinic", slug=slug)
        user = user_service.create_user(session, email=f"owner@{slug}.example", password=PASSWORD)
        membership = membership_service.create_membership(
            session, user_id=user.id, organization_id=org.id, role=MembershipRole.OWNER,
        )
        session.commit()
        org_id, user_id, membership_id = org.id, user.id, membership.id

    store_session(
        AuthenticatedSession(
            user_id=user_id, email="owner@perf-clinic.example", organization_id=org_id,
            organization_name="Perf Clinic", membership_id=membership_id,
            role=MembershipRole.OWNER, permissions=permissions_for_role(MembershipRole.OWNER),
        )
    )

    for i in range(patients):
        patient = crm.add_record("patients", {
            "name": f"Patient {i}", "status": "Active", "consent_to_contact": True,
        })
        pid = patient["patient_id"]
        crm.add_record("appointments", {
            "patient_id": pid, "appointment_date": "2026-01-01", "appointment_time": "09:00",
            "status": "Completed", "service": "Session",
        })
        pkg = crm.add_record("packages", {
            "patient_id": pid, "status": "Active", "sessions_remaining": 5, "name": "Standard",
        })
        crm.add_record("payments", {
            "patient_id": pid, "package_id": pkg["package_id"], "amount": 100, "status": "Paid",
            "payment_date": "2026-01-01",
        })
        crm.add_record("progress_notes", {
            "patient_id": pid, "visit_date": "2026-01-01", "progress_summary": "Fine.",
        })
    return org_id


def _count_statements(fn):
    counter = {"n": 0}

    @event.listens_for(Engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    try:
        result = fn()
    finally:
        event.remove(Engine, "before_cursor_execute", _count)
    return result, counter["n"]


def test_patient_risk_summary_query_count_does_not_scale_with_patient_count(tenant_authoritative):
    """The actual regression test: the N+1 this whole fix was about. Before the fix,
    query count grew roughly 4x per patient (190+ for 48 patients); after it, it is
    a small constant regardless of patient count."""
    engine = tenant_authoritative
    _seed_clinic(engine, patients=3, slug="perf-clinic-small")
    _, few_count = _count_statements(lambda: crm.patient_risk_summary())

    _seed_clinic(engine, patients=25, slug="perf-clinic-large")  # a second, unrelated organization
    # patient_risk_summary() always reads the CURRENT live organization (the second
    # clinic just logged in as, above), so this measures the larger patient count.
    _, many_count = _count_statements(lambda: crm.patient_risk_summary())

    assert many_count <= few_count + 2, (
        f"query count scaled with patient count ({few_count} -> {many_count}) — "
        "the N+1 is back: patient_risk_summary()/patient_profile() must read each "
        "entity once for the whole organization, never once per patient."
    )
    assert many_count < 20, f"expected a small, patient-count-independent query count, got {many_count}"


def test_clinic_metrics_does_not_recompute_risk_summary_when_given_one(tenant_authoritative):
    engine = tenant_authoritative
    _seed_clinic(engine, patients=10)

    risks, risk_query_count = _count_statements(lambda: crm.patient_risk_summary())
    assert risk_query_count > 0

    _, metrics_with_risk_count = _count_statements(lambda: crm.clinic_metrics(risk_rows=risks))
    _, metrics_without_risk_count = _count_statements(lambda: crm.clinic_metrics())

    assert metrics_with_risk_count < metrics_without_risk_count, (
        "clinic_metrics(risk_rows=...) should skip its own patient_risk_summary() "
        "recomputation — see ui/crm_dashboard.py's show_crm_dashboard(), which "
        "would otherwise run the whole O(patients) computation twice per page load."
    )


def test_organization_resolved_once_not_once_per_entity(tenant_authoritative):
    """resolve_current_organization_id() should be used once and threaded through,
    not re-resolved by every individual list_records() call inside
    patient_risk_summary()/clinic_metrics()."""
    from services.crm_read_router import resolve_current_organization_id

    engine = tenant_authoritative
    _seed_clinic(engine, patients=5)

    calls = {"n": 0}
    real = resolve_current_organization_id

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    import services.clinic_data_service as cds_module

    cds_module.patient_risk_summary.__globals__  # sanity: module exists
    import services.crm_read_router as router_module

    orig = router_module.resolve_current_organization_id
    router_module.resolve_current_organization_id = _counting
    try:
        crm.patient_risk_summary()
    finally:
        router_module.resolve_current_organization_id = orig

    assert calls["n"] == 1, f"expected exactly one organization resolution, got {calls['n']}"


def test_reports_dashboard_renders_without_error_for_many_patients(tenant_authoritative):
    """End-to-end-ish: the exact call sequence ui/crm_dashboard.py's
    show_crm_dashboard() makes, against a clinic with enough patients that the
    original N+1 would have made this test itself painfully slow."""
    engine = tenant_authoritative
    _seed_clinic(engine, patients=30)

    risks = crm.patient_risk_summary()
    metrics = crm.clinic_metrics(risk_rows=risks)

    assert len(risks) == 30
    assert metrics["patients"] == 30
    assert metrics["active_patients"] == 30
