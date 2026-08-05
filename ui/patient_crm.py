from __future__ import annotations

from datetime import date, time

import streamlit as st

from services.clinic_data_service import (
    APPOINTMENT_STATUSES,
    PACKAGE_STATUSES,
    PATIENT_STATUSES,
    PAYMENT_STATUSES,
    THERAPIST_STATUSES,
    add_record,
    archive_record,
    clinic_metrics,
    list_records,
    patient_profile,
    patient_risk_summary,
    records_with_patient_names,
    search_records,
    update_record,
)
from services.appointment_messaging import send_appointment_confirmation
from services.security_service import audit_event, mask_sensitive


def _money(value):
    return f"₹{float(value or 0):,.0f}"


def _label(row, id_field):
    return f"{row.get('name', 'Unknown')} · {row.get(id_field, '')}"


def _options(entity, id_field):
    rows = list_records(entity)
    mapping = {_label(row, id_field): str(row.get(id_field)) for row in rows}
    return mapping, list(mapping)


def _show_patient_profile(patient_id):
    profile = patient_profile(patient_id)
    patient = profile["patient"]
    st.subheader(patient.get("name", "Patient"))
    st.caption(
        f"{patient_id} · {patient.get('status', 'Unknown')} · "
        f"Risk: {profile['risk_level']}"
    )
    a, b, c, d = st.columns(4)
    a.metric("Sessions remaining", profile["sessions_remaining"])
    b.metric("Last visit", profile["last_visit"] or "Not recorded")
    c.metric("Next appointment", profile["next_appointment"] or "None")
    d.metric("Payments received", _money(profile["payments_total"]))

    if profile["risk_flags"]:
        st.warning(" · ".join(profile["risk_flags"]))
    else:
        st.success("No current relationship or follow-up risks detected.")

    a, b = st.columns(2)
    a.write(f"**Phone:** {patient.get('phone') or 'Not recorded'}")
    b.write(f"**Email:** {patient.get('email') or 'Not recorded'}")
    st.write(
        "**Permission to contact:** "
        + ("Recorded" if patient.get("consent_to_contact") else "Not recorded")
    )

    progress, appointments, packages, payments, edit = st.tabs(
        ["Progress", "Appointments", "Packages", "Payments", "Edit"]
    )
    with progress:
        latest = profile.get("latest_progress")
        if latest:
            st.markdown(
                f"**Latest update · {latest.get('visit_date', '')}**"
            )
            st.write(latest.get("progress_summary", ""))
            detail_bits = [
                f"Progress: {latest.get('progress_status')}"
                if latest.get("progress_status")
                else "",
                f"Pain score: {latest.get('pain_score')}/10"
                if latest.get("pain_score") is not None
                else "",
                f"Next step: {latest.get('next_step')}"
                if latest.get("next_step")
                else "",
            ]
            st.caption(" · ".join(bit for bit in detail_bits if bit))
        else:
            st.info("No care progress has been recorded yet.")

        therapist_map, therapist_labels = _options(
            "therapists", "therapist_id"
        )
        with st.form(f"add_progress_{patient_id}", clear_on_submit=True):
            st.markdown("#### Record progress")
            visit_date = st.date_input(
                "Progress date", value=date.today(), key=f"progress_date_{patient_id}"
            )
            therapist_label = st.selectbox(
                "Recorded by",
                ["Not assigned"] + therapist_labels,
                key=f"progress_therapist_{patient_id}",
            )
            progress_status = st.selectbox(
                "How is the patient progressing?",
                [
                    "Improving",
                    "Stable",
                    "Needs attention",
                    "Goal achieved",
                ],
                key=f"progress_status_{patient_id}",
            )
            pain_score = st.slider(
                "Pain score", 0, 10, 5, key=f"pain_score_{patient_id}"
            )
            progress_summary = st.text_area(
                "Progress summary *",
                placeholder=(
                    "Record what changed since the previous visit and how "
                    "the patient responded."
                ),
                key=f"progress_summary_{patient_id}",
            )
            next_step = st.text_area(
                "Next step",
                placeholder="Plan for the next visit or home-care follow-up.",
                key=f"progress_next_{patient_id}",
            )
            st.caption(
                "Saved to this patient's private CRM record. Progress notes "
                "are not sent to the AI model."
            )
            if st.form_submit_button("Save progress", type="primary"):
                try:
                    created = add_record(
                        "progress_notes",
                        {
                            "patient_id": patient_id,
                            "therapist_id": therapist_map.get(
                                therapist_label, ""
                            ),
                            "visit_date": visit_date.isoformat(),
                            "progress_status": progress_status,
                            "pain_score": pain_score,
                            "progress_summary": progress_summary,
                            "next_step": next_step,
                        },
                    )
                    audit_event(
                        "local-owner",
                        "create",
                        "patient_progress",
                        str(created["progress_id"]),
                    )
                    st.success("Patient progress saved.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))

        if profile.get("progress_notes"):
            st.markdown("#### Progress history")
            display_rows = [
                {
                    "Date": row.get("visit_date", ""),
                    "Progress": row.get("progress_status", ""),
                    "Pain score": row.get("pain_score", ""),
                    "Summary": row.get("progress_summary", ""),
                    "Next step": row.get("next_step", ""),
                }
                for row in profile["progress_notes"]
            ]
            st.dataframe(display_rows, use_container_width=True)
    with appointments:
        if profile["appointments"]:
            st.dataframe(profile["appointments"], use_container_width=True)
        else:
            st.info("No appointments recorded for this patient.")
    with packages:
        if profile["packages"]:
            st.dataframe(profile["packages"], use_container_width=True)
        else:
            st.info("No packages recorded for this patient.")
    with payments:
        if profile["payments"]:
            st.dataframe(profile["payments"], use_container_width=True)
        else:
            st.info("No payments recorded for this patient.")
    with edit:
        statuses = sorted(PATIENT_STATUSES - {"Archived"})
        current_status = patient.get("status", "Active")
        with st.form(f"edit_patient_{patient_id}"):
            name = st.text_input("Patient name", value=patient.get("name", ""))
            phone = st.text_input("Phone", value=patient.get("phone", ""))
            email = st.text_input("Email", value=patient.get("email", ""))
            status = st.selectbox(
                "Status",
                statuses,
                index=statuses.index(current_status)
                if current_status in statuses
                else 0,
            )
            consent = st.checkbox(
                "Permission to contact recorded",
                value=bool(patient.get("consent_to_contact", False)),
            )
            if st.form_submit_button("Save patient changes", type="primary"):
                try:
                    update_record(
                        "patients",
                        patient_id,
                        {
                            "name": name,
                            "phone": phone,
                            "email": email,
                            "status": status,
                            "consent_to_contact": consent,
                        },
                    )
                    audit_event(
                        "local-owner",
                        "update",
                        "patient",
                        f"{patient_id}: contact/status updated",
                    )
                    st.success("Patient record updated.")
                    st.rerun()
                except (ValueError, KeyError) as error:
                    st.error(str(error))

        st.divider()
        confirm = st.checkbox(
            "I understand this hides the patient from active CRM views.",
            key=f"archive_confirm_{patient_id}",
        )
        if st.button(
            "Archive patient",
            disabled=not confirm,
            key=f"archive_patient_{patient_id}",
        ):
            archive_record("patients", patient_id)
            audit_event("local-owner", "archive", "patient", patient_id)
            st.success("Patient archived. Linked history was retained.")
            st.rerun()


def _show_patients():
    filter_col, table_col = st.columns([1, 2])
    with filter_col:
        query = st.text_input(
            "Search patients",
            placeholder="Name, ID, phone or email",
        )
        statuses = ["All"] + sorted(PATIENT_STATUSES - {"Archived"})
        status_filter = st.selectbox("Patient status", statuses)
    rows = search_records(
        "patients",
        query,
        fields=["patient_id", "name", "phone", "email"],
    )
    if status_filter != "All":
        rows = [row for row in rows if row.get("status") == status_filter]

    with table_col:
        st.caption(
            "Contact details are masked in the directory. Open a profile "
            "to view the complete record."
        )
        directory = [
            {
                "Patient ID": row.get("patient_id", ""),
                "Name": row.get("name", ""),
                "Status": row.get("status", ""),
                "Phone": mask_sensitive(row.get("phone", ""), visible=2)
                if row.get("phone")
                else "",
                "Email": row.get("email", ""),
                "Contact consent": "Yes"
                if row.get("consent_to_contact")
                else "No",
            }
            for row in rows
        ]
        if directory:
            st.dataframe(directory, use_container_width=True)
        else:
            st.info("No patients match the current filters.")

    detail, create = st.tabs(["Open patient profile", "Add patient"])
    with detail:
        if rows:
            choices = {
                _label(row, "patient_id"): str(row.get("patient_id"))
                for row in rows
            }
            selected = st.selectbox("Select patient", list(choices))
            _show_patient_profile(choices[selected])
        else:
            st.info("Add a patient or change the filters to open a profile.")
    with create:
        with st.form("create_patient", clear_on_submit=True):
            name = st.text_input("Patient name *")
            phone = st.text_input("Phone")
            email = st.text_input("Email")
            status = st.selectbox(
                "Status", sorted(PATIENT_STATUSES - {"Archived"})
            )
            consent = st.checkbox("Permission to contact recorded")
            if st.form_submit_button("Create patient", type="primary"):
                try:
                    created = add_record(
                        "patients",
                        {
                            "name": name,
                            "phone": phone,
                            "email": email,
                            "status": status,
                            "consent_to_contact": consent,
                            "sessions_remaining": 0,
                        },
                    )
                    audit_event(
                        "local-owner",
                        "create",
                        "patient",
                        str(created["patient_id"]),
                    )
                    st.success(f"Patient {created['patient_id']} created.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))


def _show_appointments():
    patient_map, patient_labels = _options("patients", "patient_id")
    therapist_map, therapist_labels = _options("therapists", "therapist_id")
    rows = records_with_patient_names("appointments")
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("No appointments have been recorded.")

    create, update = st.tabs(["Schedule appointment", "Update appointment"])
    with create:
        if not patient_labels:
            st.warning("Create a patient before scheduling an appointment.")
        else:
            with st.form("create_appointment", clear_on_submit=True):
                patient_label = st.selectbox("Patient *", patient_labels)
                therapist_label = st.selectbox(
                    "Therapist", ["Unassigned"] + therapist_labels
                )
                appointment_date = st.date_input(
                    "Appointment date", value=date.today()
                )
                appointment_time = st.time_input(
                    "Appointment time", value=time(10, 0)
                )
                service = st.text_input(
                    "Service", placeholder="e.g. Physiotherapy"
                )
                statuses = sorted(APPOINTMENT_STATUSES - {"Archived"})
                status = st.selectbox(
                    "Status", statuses, index=statuses.index("Scheduled")
                )
                if st.form_submit_button("Save appointment", type="primary"):
                    try:
                        created = add_record(
                            "appointments",
                            {
                                "patient_id": patient_map[patient_label],
                                "therapist_id": therapist_map.get(
                                    therapist_label, ""
                                ),
                                "appointment_date": appointment_date.isoformat(),
                                "appointment_time": appointment_time.strftime(
                                    "%H:%M"
                                ),
                                "service": service,
                                "status": status,
                            },
                        )
                        audit_event(
                            "local-owner",
                            "create",
                            "appointment",
                            str(created["appointment_id"]),
                        )
                        confirmation = send_appointment_confirmation(created)
                        if confirmation is None:
                            st.success(
                                "Appointment saved. No confirmation sent — "
                                "patient has no phone on file or hasn't "
                                "recorded contact consent."
                            )
                        elif confirmation["dry_run"]:
                            st.success(
                                "Appointment saved. WhatsApp confirmation "
                                "simulated (no live WhatsApp credentials "
                                "configured yet)."
                            )
                        elif confirmation["ok"]:
                            st.success(
                                "Appointment saved and WhatsApp confirmation sent."
                            )
                        else:
                            st.warning(
                                "Appointment saved, but the WhatsApp "
                                f"confirmation failed: {confirmation['detail']}"
                            )
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
    with update:
        appointments = list_records("appointments")
        if not appointments:
            st.info("There is no appointment to update.")
        else:
            choices = {
                (
                    f"{row.get('appointment_date', '')} "
                    f"{row.get('appointment_time', '')} · "
                    f"{row.get('appointment_id', '')}"
                ): row
                for row in appointments
            }
            selected = st.selectbox("Appointment", list(choices))
            appointment = choices[selected]
            statuses = sorted(APPOINTMENT_STATUSES - {"Archived"})
            with st.form("update_appointment"):
                status = st.selectbox(
                    "Updated status",
                    statuses,
                    index=statuses.index(appointment.get("status"))
                    if appointment.get("status") in statuses
                    else 0,
                )
                if st.form_submit_button("Update appointment"):
                    update_record(
                        "appointments",
                        str(appointment["appointment_id"]),
                        {"status": status},
                    )
                    audit_event(
                        "local-owner",
                        "update",
                        "appointment",
                        f"{appointment['appointment_id']}: {status}",
                    )
                    st.success("Appointment updated.")
                    st.rerun()


def _show_packages():
    patient_map, patient_labels = _options("patients", "patient_id")
    rows = records_with_patient_names("packages")
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("No packages have been recorded.")

    create, update = st.tabs(["Assign package", "Update package"])
    with create:
        if not patient_labels:
            st.warning("Create a patient before assigning a package.")
        else:
            with st.form("create_package", clear_on_submit=True):
                patient_label = st.selectbox("Patient *", patient_labels)
                name = st.text_input("Package name *")
                total = st.number_input(
                    "Total sessions", min_value=1, value=10, step=1
                )
                remaining = st.number_input(
                    "Sessions remaining", min_value=0, value=10, step=1
                )
                start_date = st.date_input("Start date", value=date.today())
                has_expiry = st.checkbox("Record an expiry date")
                expiry_date = st.date_input(
                    "Expiry date", value=date.today(), disabled=not has_expiry
                )
                status = st.selectbox(
                    "Status", sorted(PACKAGE_STATUSES - {"Archived"})
                )
                if st.form_submit_button("Assign package", type="primary"):
                    try:
                        created = add_record(
                            "packages",
                            {
                                "patient_id": patient_map[patient_label],
                                "name": name,
                                "total_sessions": total,
                                "sessions_remaining": remaining,
                                "start_date": start_date.isoformat(),
                                "expiry_date": expiry_date.isoformat()
                                if has_expiry
                                else "",
                                "status": status,
                            },
                        )
                        audit_event(
                            "local-owner",
                            "create",
                            "package",
                            str(created["package_id"]),
                        )
                        st.success("Package assigned.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
    with update:
        packages = list_records("packages")
        if not packages:
            st.info("There is no package to update.")
        else:
            choices = {
                f"{row.get('name', '')} · {row.get('package_id', '')}": row
                for row in packages
            }
            selected = st.selectbox("Package", list(choices))
            package = choices[selected]
            statuses = sorted(PACKAGE_STATUSES - {"Archived"})
            with st.form("update_package"):
                remaining = st.number_input(
                    "Sessions remaining",
                    min_value=0,
                    max_value=int(package.get("total_sessions", 0) or 0),
                    value=int(package.get("sessions_remaining", 0) or 0),
                )
                status = st.selectbox(
                    "Package status",
                    statuses,
                    index=statuses.index(package.get("status"))
                    if package.get("status") in statuses
                    else 0,
                )
                if st.form_submit_button("Update package"):
                    update_record(
                        "packages",
                        str(package["package_id"]),
                        {
                            "sessions_remaining": remaining,
                            "status": status,
                        },
                    )
                    audit_event(
                        "local-owner",
                        "update",
                        "package",
                        f"{package['package_id']}: {remaining} remaining",
                    )
                    st.success("Package updated.")
                    st.rerun()


def _show_payments():
    patient_map, patient_labels = _options("patients", "patient_id")
    rows = records_with_patient_names("payments")
    paid = sum(
        float(row.get("amount", 0) or 0)
        for row in rows
        if row.get("status") == "Paid"
    )
    pending = sum(
        float(row.get("amount", 0) or 0)
        for row in rows
        if row.get("status") == "Pending"
    )
    a, b = st.columns(2)
    a.metric("Payments received", _money(paid))
    b.metric("Pending amount", _money(pending))
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("No payments have been recorded.")
    if not patient_labels:
        st.warning("Create a patient before recording a payment.")
        return

    with st.form("create_payment", clear_on_submit=True):
        patient_label = st.selectbox("Patient *", patient_labels)
        patient_id = patient_map[patient_label]
        patient_packages = [
            row
            for row in list_records("packages")
            if str(row.get("patient_id")) == patient_id
        ]
        packages = {
            f"{row.get('name', '')} · {row.get('package_id', '')}": str(
                row.get("package_id")
            )
            for row in patient_packages
        }
        package_label = st.selectbox(
            "Related package", ["No package"] + list(packages)
        )
        amount = st.number_input(
            "Amount *", min_value=0.0, value=0.0, step=500.0
        )
        payment_date = st.date_input("Payment date", value=date.today())
        method = st.selectbox(
            "Payment method",
            ["UPI", "Card", "Cash", "Bank transfer", "Other"],
        )
        reference = st.text_input("Reference (optional)")
        status = st.selectbox(
            "Payment status", sorted(PAYMENT_STATUSES - {"Archived"})
        )
        if st.form_submit_button("Record payment", type="primary"):
            try:
                created = add_record(
                    "payments",
                    {
                        "patient_id": patient_id,
                        "package_id": packages.get(package_label, ""),
                        "amount": amount,
                        "payment_date": payment_date.isoformat(),
                        "method": method,
                        "reference": reference,
                        "status": status,
                    },
                )
                audit_event(
                    "local-owner",
                    "create",
                    "payment",
                    str(created["payment_id"]),
                )
                st.success("Payment recorded.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))


def _show_therapists():
    rows = list_records("therapists")
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("No therapists have been recorded.")
    create, update = st.tabs(["Add therapist", "Update therapist"])
    with create:
        with st.form("create_therapist", clear_on_submit=True):
            name = st.text_input("Therapist name *")
            capacity = st.number_input(
                "Comfortable appointments per week",
                min_value=0,
                value=35,
            )
            status = st.selectbox(
                "Status", sorted(THERAPIST_STATUSES - {"Archived"})
            )
            if st.form_submit_button("Add therapist", type="primary"):
                try:
                    created = add_record(
                        "therapists",
                        {
                            "name": name,
                            "weekly_capacity": capacity,
                            "status": status,
                        },
                    )
                    audit_event(
                        "local-owner",
                        "create",
                        "therapist",
                        str(created["therapist_id"]),
                    )
                    st.success("Therapist added.")
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
    with update:
        if not rows:
            st.info("There is no therapist to update.")
        else:
            choices = {_label(row, "therapist_id"): row for row in rows}
            selected = st.selectbox("Therapist", list(choices))
            therapist = choices[selected]
            statuses = sorted(THERAPIST_STATUSES - {"Archived"})
            with st.form("update_therapist"):
                capacity = st.number_input(
                    "Comfortable appointments per week",
                    min_value=0,
                    value=int(therapist.get("weekly_capacity", 0) or 0),
                )
                status = st.selectbox(
                    "Therapist status",
                    statuses,
                    index=statuses.index(therapist.get("status"))
                    if therapist.get("status") in statuses
                    else 0,
                )
                if st.form_submit_button("Update therapist"):
                    update_record(
                        "therapists",
                        str(therapist["therapist_id"]),
                        {"weekly_capacity": capacity, "status": status},
                    )
                    audit_event(
                        "local-owner",
                        "update",
                        "therapist",
                        str(therapist["therapist_id"]),
                    )
                    st.success("Therapist updated.")
                    st.rerun()


def show_patient_crm():
    st.markdown(
        '<div class="eyebrow">PATIENT RELATIONSHIP OPERATIONS</div>',
        unsafe_allow_html=True,
    )
    st.title("Clinic CRM")
    st.caption(
        "Manage patients, appointments, care progress, treatment plans and "
        "payments from one workspace."
    )
    metrics = clinic_metrics()
    a, b, c, d, e = st.columns(5)
    a.metric("Active patients", metrics["active_patients"])
    b.metric("Upcoming visits", metrics["upcoming_appointments"])
    c.metric("Follow-ups", metrics["at_risk_patients"])
    d.metric("Renewals due", metrics["renewals_due"])
    e.metric("Payments received", _money(metrics["payments_total"]))

    tabs = st.tabs(
        ["Patients", "Appointments", "Packages", "Payments", "Therapists"]
    )
    with tabs[0]:
        _show_patients()
    with tabs[1]:
        _show_appointments()
    with tabs[2]:
        _show_packages()
    with tabs[3]:
        _show_payments()
    with tabs[4]:
        _show_therapists()


def show_patient_directory():
    st.markdown(
        '<div class="eyebrow">CRM · PATIENT RECORDS</div>',
        unsafe_allow_html=True,
    )
    st.title("Patients")
    st.caption(
        "Open a patient profile, record progress, or add a new patient."
    )
    metrics = clinic_metrics()
    a, b, c = st.columns(3)
    a.metric("Active patients", metrics["active_patients"])
    b.metric("Need follow-up", metrics["at_risk_patients"])
    c.metric("Upcoming visits", metrics["upcoming_appointments"])
    _show_patients()


def show_appointments_page():
    st.markdown(
        '<div class="eyebrow">CRM · APPOINTMENTS</div>',
        unsafe_allow_html=True,
    )
    st.title("Appointments")
    st.caption("Schedule visits and keep appointment status up to date.")
    _show_appointments()


def show_treatment_plans():
    st.markdown(
        '<div class="eyebrow">CRM · TREATMENT PLANS</div>',
        unsafe_allow_html=True,
    )
    st.title("Treatments & plans")
    st.caption(
        "Assign care packages, update remaining sessions, and spot renewals."
    )
    _show_packages()


def show_payments_page():
    st.markdown(
        '<div class="eyebrow">CRM · PAYMENTS</div>',
        unsafe_allow_html=True,
    )
    st.title("Payments")
    st.caption("Record payments and check what is still pending.")
    _show_payments()


def show_team_page():
    st.markdown(
        '<div class="eyebrow">CRM · CLINIC TEAM</div>',
        unsafe_allow_html=True,
    )
    st.title("Clinic team")
    st.caption("Maintain therapist availability and weekly capacity.")
    _show_therapists()


def show_crm_insights():
    st.markdown(
        '<div class="eyebrow">CRM RISK & FOLLOW-UP INTELLIGENCE</div>',
        unsafe_allow_html=True,
    )
    st.title("Patient relationship insights")
    st.caption(
        "Operational signals derived from visits, packages, status and "
        "contact consent—not clinical diagnoses."
    )
    rows = patient_risk_summary()
    if not rows:
        st.info("Add patients to generate relationship insights.")
        return
    a, b, c = st.columns(3)
    a.metric("High-risk follow-ups", sum(x["risk_level"] == "High" for x in rows))
    b.metric(
        "Medium-risk follow-ups",
        sum(x["risk_level"] == "Medium" for x in rows),
    )
    c.metric(
        "Contactable patients",
        sum(bool(x["consent_to_contact"]) for x in rows),
    )
    st.dataframe(rows, use_container_width=True)
    st.info(
        "External outreach remains approval-first. A risk flag does not send "
        "a message or change a record automatically."
    )
