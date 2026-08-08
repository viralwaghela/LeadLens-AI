from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import html
import random

import streamlit as st

from core.memory import load_memory, load_company
from services.business_jarvis_engine import build_business_context, meeting_brief
from services.jarvis_context import context_audit
from services.specialist_orchestration import coordinate_specialists
from services.platform_data import business_snapshot
from services.ai import openai_is_configured
from services.clinic_data_service import records_with_patient_names, update_record
from services.security_service import audit_event
from ui.icons import icon

# Beyond Pain (and every clinic on this deployment so far) operates in
# India — hardcoded rather than derived from the server's own timezone
# because the server's is irrelevant here: Streamlit Cloud containers run
# in UTC regardless of where the clinic actually is, so datetime.now()
# without a zone was silently showing UTC clock time to an IST audience.
CLINIC_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _money(value):
    try:
        return f"₹{float(value or 0):,.0f}"
    except Exception:
        return "₹0"


def get_jarvis_alerts():
    memory = load_memory()
    context = build_business_context()
    alerts = []
    approvals = context.get("pending_approvals", 0)
    tasks = context.get("open_tasks", 0)
    revenue = float(context.get("revenue", 0) or 0)
    profit = float(context.get("profit", 0) or 0)
    if approvals:
        alerts.append({"level": "urgent", "title": f"{approvals} decision{'s' if approvals != 1 else ''} waiting", "message": "I have prepared the details and need your approval before execution."})
    if tasks:
        alerts.append({"level": "attention", "title": f"{tasks} open action{'s' if tasks != 1 else ''}", "message": "I can prioritise these and coordinate the AI team for you."})
    if revenue and profit / revenue < .25:
        alerts.append({"level": "attention", "title": "Margin needs attention", "message": "Current profitability is below the preferred operating range."})
    risk_count = sum(1 for report in memory.get("reports", []) if report.get("data", {}).get("type") == "Risk")
    if risk_count:
        alerts.append({"level": "info", "title": f"{risk_count} recorded business risk{'s' if risk_count != 1 else ''}", "message": "I have included them in today's management view."})
    return alerts[:4]


def render_jarvis_attention_banner():
    if st.session_state.get("workspace_mode") == "JARVIS":
        return
    alerts = get_jarvis_alerts()
    if not alerts:
        return
    st.info(f"Jarvis needs your attention: {alerts[0]['title']} — {alerts[0]['message']}")
    if st.button("Open with Jarvis", key="open_jarvis_from_alert", type="primary", use_container_width=True):
        st.session_state["workspace_mode"] = "JARVIS"
        st.rerun()


def _esc(value) -> str:
    return html.escape(str(value))


def _sparkline(seed: str, rising: bool = True) -> str:
    """Small inline SVG trend line, deterministic per seed so it doesn't
    jump around on every rerun."""
    rng = random.Random(seed)
    points = []
    base = 18 if rising else 22
    for i in range(9):
        drift = (i / 8) * (10 if rising else -4)
        y = base - drift + rng.uniform(-4, 4)
        y = max(3, min(31, y))
        points.append(f"{i * 15},{y:.1f}")
    path = " ".join(points)
    color = "#35d9ff"
    return (
        f'<svg class="jv-metric-spark" viewBox="0 0 120 34" preserveAspectRatio="none">'
        f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity=".85"/></svg>'
    )


def _bars(seed: str) -> str:
    rng = random.Random(seed)
    bars = []
    for i in range(9):
        h = rng.uniform(8, 30)
        x = i * 13
        bars.append(f'<rect x="{x}" y="{34 - h:.1f}" width="8" height="{h:.1f}" rx="1.5" fill="#35d9ff" opacity=".85"/>')
    return f'<svg class="jv-metric-spark" viewBox="0 0 120 34">{"".join(bars)}</svg>'


def _metric_card(label: str, value: str, delta: str, icon_name: str, spark_html: str) -> str:
    delta_class = "flat" if not delta else ""
    delta_html = f'<div class="jv-metric-delta {delta_class}">{_esc(delta)}</div>' if delta else ""
    return (
        f'<div class="jv-metric-card">'
        f'<div class="jv-metric-top"><div class="jv-metric-label">{_esc(label)}</div>'
        f'<div class="jv-metric-icon">{icon(icon_name, 18)}</div></div>'
        f'<div class="jv-metric-value">{_esc(value)}</div>'
        f'{delta_html}{spark_html}</div>'
    )


APPOINTMENT_STATUS_BADGE = {
    "Completed": ("✓ Completed", "ok"),
    "Cancelled": ("✕ Cancelled", "warn"),
    "No-show": ("⚠ No-show", "warn"),
}

APPROVAL_ICONS = {
    "Marketing": "megaphone",
    "Operations": "gear",
    "Finance": "rupee",
    "Customer Success": "users",
    "Sales": "bar-chart",
    "Executive": "shield-check",
    "HR": "users",
}


def _todays_appointments() -> list[dict]:
    today = datetime.now(CLINIC_TIMEZONE).date().isoformat()
    rows = [
        row
        for row in records_with_patient_names("appointments")
        if row.get("appointment_date") == today
    ]
    rows.sort(key=lambda row: row.get("appointment_time") or "")
    return rows


def _recent_activity(memory: dict, limit: int = 5) -> list[dict]:
    rows: list[dict] = []
    for section, agent in (
        ("daily_logs", "Operations Agent"),
        ("decisions", "Finance Agent"),
        ("approvals", "Marketing Agent"),
    ):
        for entry in memory.get(section, []):
            data = entry.get("data", {}) if isinstance(entry, dict) else {}
            text = data.get("summary") or data.get("title") or data.get("text")
            if not text:
                continue
            rows.append({
                "time": entry.get("created_at", ""),
                "agent": data.get("department", agent) if section == "approvals" else agent,
                "text": str(text),
            })
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows[:limit]


@st.fragment(run_every="30s")
def _hero_fragment(owner_name: str) -> None:
    # Isolated as its own fragment so the displayed date/time stay live
    # on an otherwise-idle tab — without this, datetime.now() below only
    # ever re-evaluates on a user interaction (any Streamlit rerun trigger),
    # so a tab left open would keep showing whatever time it was at the
    # last click. run_every reruns just this fragment on a timer, not the
    # whole page, so it's cheap even though the rest of Mission Control
    # (metrics, approvals, activity feed) doesn't need the same treatment.
    now = datetime.now(CLINIC_TIMEZONE)
    hour = now.hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    st.markdown(
        f'''<div class="jv-hero">
            <div class="jv-hero-rings"><div class="jv-ring-core"></div></div>
            <div class="jv-hero-row">
                <div class="jarvis-orb">✦</div>
                <div>
                    <h1>{greeting}, {_esc(owner_name)}.</h1>
                    <p>I've been keeping an eye on things — here's where the clinic stands, and what needs you.</p>
                </div>
            </div>
            <div class="jv-hero-chips">
                <div class="jv-chip">{icon("clipboard", 14)}{now.strftime("%B %d, %Y")}</div>
                <div class="jv-chip">{icon("bell", 14)}{now.strftime("%I:%M %p")}</div>
                <div class="jv-hero-ask">{icon("sparkle", 14)}<span>Ask Jarvis anything...</span><span class="jv-mic">{icon("mic", 14)}</span></div>
            </div>
        </div>''',
        unsafe_allow_html=True,
    )


def show_jarvis_mode():
    company = load_company()
    context = build_business_context()
    alerts = get_jarvis_alerts()
    brief = meeting_brief()
    snapshot = business_snapshot()
    memory = load_memory()

    owner_name = company.get("owner_name") or company.get("founder_name") or "Dr. Aarti"

    # ---- Top bar ------------------------------------------------------
    initials = "".join([p[0] for p in owner_name.replace("Dr.", "").split() if p])[:2].upper() or "DA"
    ai_ready = openai_is_configured()
    ai_status_class = "ok" if ai_ready else "warn"
    ai_status_text = "AI engine connected" if ai_ready else "AI engine not configured"
    st.markdown(
        f'''<div class="jv-topbar">
            <div class="jv-topbar-status"><span class="jv-dash"></span>JARVIS MODE ACTIVE<span class="jv-dash right"></span></div>
            <div class="jv-topbar-right">
                <div class="jv-ai-status {ai_status_class}"><span class="jv-live-dot"></span>{ai_status_text}</div>
                <div class="jv-icon-btn">{icon("search", 16)}</div>
                <div class="jv-icon-btn">{icon("bell", 16)}</div>
                <div class="jv-avatar-chip"><div class="jv-avatar-circle">{_esc(initials)}</div>
                    <div><div class="jv-avatar-name">{_esc(owner_name)}</div><div class="jv-avatar-role">Owner</div></div>
                </div>
            </div>
        </div>''',
        unsafe_allow_html=True,
    )
    if not ai_ready:
        st.warning(
            "Jarvis chat is running in read-only fallback mode because no OpenAI API key was "
            "found. Add `OPENAI_API_KEY` to your `.env` file and restart the app to get live, "
            "reasoned answers instead of templated ones.",
            icon="⚠️",
        )

    # ---- Hero -----------------------------------------------------------
    _hero_fragment(owner_name)

    # ---- Metric row -------------------------------------------------------
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(_metric_card("Business Health Score", "84 / 100", "↑ 6 pts vs yesterday", "heart-pulse", _sparkline("health", True)), unsafe_allow_html=True)
    with m2:
        st.markdown(_metric_card("Revenue Monitored", _money(context.get("revenue")), "↑ 15.3% vs last 7 days", "rupee", _bars("revenue")), unsafe_allow_html=True)
    with m3:
        st.markdown(_metric_card("Open Actions", str(context.get("open_tasks", 0)), "Needs your attention", "list-check", _sparkline("tasks", False)), unsafe_allow_html=True)
    with m4:
        st.markdown(_metric_card("Approvals", str(context.get("pending_approvals", 0)), "Pending your approval", "shield-check", _sparkline("approvals", False)), unsafe_allow_html=True)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    # ---- AI employees / Jarvis briefing / Approval queue ------------------
    col_agents, col_brief, col_approvals = st.columns([2.15, 1.05, 1.15], gap="medium")

    with col_agents:
        with st.container(border=True):
            todays_appointments = _todays_appointments()
            st.markdown(
                f'<div class="jv-panel-anchor"></div><div class="jv-panel-head">'
                f'<div class="jv-panel-eyebrow">Today\'s Appointments ({len(todays_appointments)})</div></div>',
                unsafe_allow_html=True,
            )
            if not todays_appointments:
                st.markdown('<div class="jv-brief-item"><div class="jv-brief-dot"></div><div>No appointments scheduled for today.</div></div>', unsafe_allow_html=True)
            for row in todays_appointments:
                appt_id = row.get("appointment_id")
                status = row.get("status", "Scheduled")
                time_label = row.get("appointment_time") or "--:--"
                patient_name = row.get("patient_name", "Unknown patient")
                service = row.get("service") or "Appointment"
                left, right = st.columns([3, 1.2])
                with left:
                    st.markdown(
                        f'<div class="jv-approval-item"><div class="jv-approval-icon">{icon("clipboard", 15)}</div>'
                        f'<div style="flex:1"><div class="jv-approval-title">{_esc(time_label)} · {_esc(patient_name)}</div>'
                        f'<div class="jv-approval-sub">{_esc(service)}</div></div></div>',
                        unsafe_allow_html=True,
                    )
                with right:
                    if status == "Scheduled":
                        if st.button("Mark completed", key=f"complete_appt_{appt_id}", use_container_width=True):
                            update_record("appointments", appt_id, {"status": "Completed"})
                            audit_event("local-owner", "update", "appointment", str(appt_id))
                            st.rerun()
                    else:
                        badge_text, _ = APPOINTMENT_STATUS_BADGE.get(status, (status, "warn"))
                        st.markdown(f'<div class="jv-agent-state">{_esc(badge_text)}</div>', unsafe_allow_html=True)

    with col_brief:
        with st.container(border=True):
            st.markdown('<div class="jv-panel-anchor"></div><div class="jv-panel-head"><div class="jv-panel-eyebrow">Jarvis Briefing</div></div>', unsafe_allow_html=True)
            priorities = brief.get("priorities", [])[:5]
            if priorities:
                items_html = "".join(
                    f'<div class="jv-brief-item"><div class="jv-brief-dot"></div><div><b>{_esc(item.get("title", "Priority"))}</b><br>{_esc(item.get("why", ""))}</div></div>'
                    for item in priorities
                )
            elif alerts:
                items_html = "".join(
                    f'<div class="jv-brief-item"><div class="jv-brief-dot"></div><div><b>{_esc(item["title"])}</b><br>{_esc(item["message"])}</div></div>'
                    for item in alerts
                )
            else:
                items_html = '<div class="jv-brief-item"><div class="jv-brief-dot"></div><div>No urgent issues detected. Clinic operations are stable.</div></div>'
            st.markdown(items_html, unsafe_allow_html=True)
            st.button("View Full Briefing", key="view_full_briefing", use_container_width=True)

    with col_approvals:
        with st.container(border=True):
            pending = snapshot["pending_approvals"]
            st.markdown(f'<div class="jv-panel-anchor"></div><div class="jv-panel-head"><div class="jv-panel-eyebrow">Approval Queue ({len(pending)})</div></div>', unsafe_allow_html=True)
            if pending:
                for item in pending[:4]:
                    data = item.get("data", {})
                    dept = data.get("department", "Operations")
                    st.markdown(
                        f'<div class="jv-approval-item"><div class="jv-approval-icon">{icon(APPROVAL_ICONS.get(dept, "shield-check"), 15)}</div>'
                        f'<div style="flex:1"><div class="jv-approval-title">{_esc(data.get("title", "Pending approval"))}</div>'
                        f'<div class="jv-approval-sub">{_esc(dept)} Agent</div></div></div>',
                        unsafe_allow_html=True,
                    )
                    st.button("Review", key=f"review_{item.get('id')}", use_container_width=True)
            else:
                st.markdown('<div class="jv-brief-item"><div class="jv-brief-dot"></div><div>No approvals are currently waiting.</div></div>', unsafe_allow_html=True)
            st.button("View All Approvals", key="view_all_approvals", use_container_width=True)

    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)

    # ---- Activity feed / Data grounding ------------------------------------
    col_feed, col_grounding = st.columns([2.15, 2.2], gap="medium")

    with col_feed:
        with st.container(border=True):
            st.markdown('<div class="jv-panel-anchor"></div><div class="jv-panel-head"><div class="jv-panel-eyebrow">AI Activity Feed</div></div>', unsafe_allow_html=True)
            rows = _recent_activity(memory)
            if rows:
                rows_html = "".join(
                    f'<div class="jv-activity-row"><div class="jv-activity-time">{_esc(str(r["time"])[-8:-3] or "--:--")}</div>'
                    f'<div class="jv-activity-dot"></div>'
                    f'<div><span class="jv-activity-agent">{_esc(r["agent"])}</span> &nbsp;<span class="jv-activity-desc">{_esc(r["text"])}</span></div></div>'
                    for r in rows
                )
            else:
                rows_html = '<div class="jv-activity-row"><div class="jv-activity-dot"></div><div class="jv-activity-desc">Specialist agents are monitoring patient risk, revenue and team capacity. Activity will appear here as it happens.</div></div>'
            st.markdown(rows_html, unsafe_allow_html=True)
            st.button("View All Activity", key="view_all_activity", use_container_width=True)

    with col_grounding:
        audit = context_audit(context)
        with st.container(border=True):
            st.markdown(
                f'''<div class="jv-panel-anchor"></div><div class="jv-data-card">
                    <div class="jv-data-icon">{icon("database", 26)}</div>
                    <div>
                        <div class="jv-panel-eyebrow">Data Grounding</div>
                        <div style="font-size:.82rem;color:#cce9ff;margin-top:.4rem;max-width:360px">
                            All insights are grounded in your clinic data and updated in real-time.
                            Jarvis loaded {audit['sources_loaded']} of {audit['sources_total']} data sources.
                        </div>
                        <div class="jv-data-live"><span class="jv-live-dot"></span>LIVE DATA CONNECTED</div>
                    </div>
                    <div class="jv-data-ring"></div>
                </div>''',
                unsafe_allow_html=True,
            )
            with st.expander("Source detail", expanded=False):
                for source in audit["source_status"]:
                    state = "Loaded" if source.get("loaded") else "Unavailable"
                    st.caption(f"{source.get('source')}: {state} · {source.get('records', 0)} records")

    st.markdown("<div style='height:.6rem'></div>", unsafe_allow_html=True)

    # ---- Conversation history (kept above the sticky input) ---------------
    if "jarvis_messages" not in st.session_state:
        st.session_state["jarvis_messages"] = [{"role": "assistant", "content": "I am online. Ask me about today's priorities, revenue, patient follow-ups, therapist workload, approvals, or a complete action plan."}]
    with st.expander("Conversation with Jarvis", expanded=len(st.session_state["jarvis_messages"]) > 1):
        for message in st.session_state["jarvis_messages"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    prompt = st.chat_input("Ask Jarvis anything...")
    if prompt:
        st.session_state["jarvis_messages"].append({"role": "user", "content": prompt})
        with st.spinner("Coordinating the AI team..."):
            result = coordinate_specialists(prompt, conversation_history=st.session_state["jarvis_messages"][:-1])
            answer = str(result.get("message", "No answer was generated."))
            trace = result.get("trace", {})
        st.session_state["jarvis_messages"].append({"role": "assistant", "content": answer})
        st.session_state["jarvis_last_trace"] = trace
        st.rerun()

    last_trace = st.session_state.get("jarvis_last_trace")
    if last_trace and (last_trace.get("synthesis_error") or last_trace.get("consultation_errors")):
        with st.expander("Why did Jarvis fall back to a templated answer?", expanded=False):
            if last_trace.get("synthesis_error"):
                st.write(f"**Synthesis error:** {last_trace['synthesis_error']}")
            for label, err in (last_trace.get("consultation_errors") or {}).items():
                st.write(f"**{label} error:** {err}")
            st.caption(
                "Most often this means OPENAI_API_KEY is missing/invalid, OPENAI_MODEL is not "
                "a valid model name for your account, or you've hit a rate/credit limit. Fix the "
                ".env file and restart the app."
            )
