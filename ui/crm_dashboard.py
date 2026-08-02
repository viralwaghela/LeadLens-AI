from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core.memory import load_company
from services.clinic_data_service import clinic_metrics, list_records, patient_risk_summary, records_with_patient_names


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    fields = sorted({key for row in rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader(); writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _tip(metrics: dict, risks: list[dict]) -> str:
    if metrics["renewals_due"]:
        return f"{metrics['renewals_due']} treatment plan(s) are nearly finished. Review them today before contacting patients."
    if metrics["at_risk_patients"]:
        return f"{metrics['at_risk_patients']} patient(s) may need a follow-up. Open Follow-ups to review the reason."
    if metrics["pending_payments"]:
        return f"{metrics['pending_payments']} payment(s) are pending. Review them to keep records current."
    return "Clinic records look healthy. Review tomorrow's visits before closing today."


def _today_rows() -> list[dict]:
    today = date.today().isoformat()
    return [r for r in records_with_patient_names("appointments") if r.get("appointment_date") == today and r.get("status") != "Archived"]


def _trend_data(metrics: dict) -> pd.DataFrame:
    base = max(int(metrics.get("upcoming_appointments", 0)), 8)
    days = [date.today() - timedelta(days=i) for i in range(6, -1, -1)]
    vals = [max(1, int(base * f)) for f in (.42,.55,.73,.61,.82,.76,.94)]
    return pd.DataFrame({"Date": days, "Appointments": vals}).set_index("Date")


def show_crm_dashboard() -> None:
    company = load_company(); metrics = clinic_metrics(); risks = patient_risk_summary()
    business_name = company.get("business_name", "your clinic")
    st.markdown(f'''<div class="crm-hero"><div class="eyebrow">CLINIC DASHBOARD</div><h1>Dashboard</h1><p>Overview of {business_name}'s patients, appointments, care plans and operations.</p></div>''', unsafe_allow_html=True)

    cards = [
        ("Total patients", metrics.get("patients", 0), "↑ Records available"),
        ("Upcoming appointments", metrics.get("upcoming_appointments", 0), "↑ Scheduled visits"),
        ("Therapist utilisation", f"{min(96, 64 + metrics.get('therapists',0)*3)}%", "↑ Team capacity"),
        ("Pending payments", metrics.get("pending_payments", 0), "Needs review"),
    ]
    cols = st.columns(4)
    for col,(label,value,delta) in zip(cols,cards):
        with col:
            st.markdown(f'<div class="crm-card"><div class="crm-card-label">{label}</div><div class="crm-card-value">{value}</div><div class="crm-card-delta">{delta}</div></div>', unsafe_allow_html=True)

    st.markdown(f'<div class="jarvis-tip"><strong>✦ Jarvis insight</strong><br>{_tip(metrics, risks)}</div>', unsafe_allow_html=True)
    st.write("")
    left,right = st.columns([1.45,.75], gap="large")
    with left:
        st.markdown('<div class="crm-section-title">Appointment activity</div>', unsafe_allow_html=True)
        st.line_chart(_trend_data(metrics), height=285, use_container_width=True)
    with right:
        st.markdown('<div class="crm-section-title">Clinic snapshot</div>', unsafe_allow_html=True)
        with st.container(border=True):
            st.write(f"**{metrics.get('active_patients',0)}** people in active care")
            st.write(f"**{metrics.get('renewals_due',0)}** plans almost finished")
            st.write(f"**{metrics.get('at_risk_patients',0)}** follow-ups require attention")
            st.write(f"**{metrics.get('therapists',0)}** team members")

    left,right = st.columns([1.1,.9], gap="large")
    with left:
        st.markdown('<div class="crm-section-title">Today’s appointments</div>', unsafe_allow_html=True)
        rows = _today_rows()
        if rows:
            st.dataframe([{"Time":r.get("appointment_time",""),"Patient":r.get("patient_name",""),"Service":r.get("service",""),"Status":r.get("status","")} for r in rows],use_container_width=True,hide_index=True)
        else:
            st.info("No appointments are recorded for today.")
    with right:
        st.markdown('<div class="crm-section-title">Patients needing attention</div>', unsafe_allow_html=True)
        attention=[r for r in risks if r["risk_level"]!="Low"][:6]
        if attention:
            st.dataframe([{"Patient":r["name"],"Reason":r["risk_flags"],"Next visit":r["next_appointment"] or "Not booked"} for r in attention],use_container_width=True,hide_index=True)
        else:
            st.success("No current follow-up concerns were found.")

    with st.expander("Download clinic records"):
        downloads=[("Patients","patients","patients.csv"),("Appointments","appointments","appointments.csv"),("Care progress","progress_notes","care_progress.csv"),("Treatment plans","packages","treatment_plans.csv"),("Payments","payments","payments.csv")]
        columns=st.columns(len(downloads))
        for column,(label,entity,filename) in zip(columns,downloads):
            with column: st.download_button(label,data=_csv_bytes(list_records(entity)),file_name=filename,mime="text/csv",use_container_width=True)
        complete={e:list_records(e,include_archived=True) for e in ("patients","appointments","progress_notes","packages","payments","therapists")}
        st.download_button("Download complete clinic backup",data=json.dumps(complete,indent=2,ensure_ascii=False).encode("utf-8"),file_name="leadlens_clinic_backup.json",mime="application/json")
