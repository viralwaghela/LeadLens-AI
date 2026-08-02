
import re
import streamlit as st
from core.memory import load_memory, load_company, add_task, add_approval
from services.ai import openai_is_configured
from ui.jarvis_mode import render_jarvis_attention_banner


def _money(value):
    try: return f"₹{float(value):,.0f}"
    except Exception: return str(value or "₹0")


def _data():
    memory=load_memory(); company=load_company()
    revenue=float(company.get("monthly_revenue",0) or 0)
    expenses=float(company.get("monthly_expenses",0) or 0)
    tasks=memory.get("tasks",[]); reports=memory.get("reports",[])
    return memory, company, revenue, expenses, tasks, reports


def show_morning_brief():
    from datetime import datetime

    memory, company, revenue, expenses, tasks, reports = _data()
    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    owner_name = company.get("owner_name") or company.get("founder_name") or "there"
    profit = revenue - expenses
    risks = sum(1 for r in reports if r.get("data", {}).get("type") == "Risk")

    st.markdown(
        f"""
        <div class="welcome-hero">
            <div>
                <div class="eyebrow">YOUR DAILY BRIEFING</div>
                <h1>{greeting}, {owner_name}.</h1>
                <p>I have reviewed the clinic and organised the day for you. Start with the items below, and I will keep monitoring everything in the background.</p>
            </div>
            <div class="welcome-badge">
                <span>✦</span>
                <small>Jarvis monitoring</small>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_jarvis_attention_banner()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Monthly revenue", _money(revenue))
    c2.metric("Estimated profit", _money(profit))
    c3.metric("Open actions", len(tasks))
    c4.metric("Business risks", risks)

    left, right = st.columns([1.2, 0.8], gap="large")
    with left:
        st.markdown("### Here is what I would handle first")
        priorities = []
        if tasks:
            priorities.append(
                f"Review {len(tasks)} open action{'s' if len(tasks) != 1 else ''}, beginning with the highest-priority item."
            )
        if revenue and expenses / revenue > 0.6:
            priorities.append(
                "Operating costs are above 60% of revenue. I would review margins before adding fixed expenses."
            )
        priorities.extend([
            "Contact patients whose packages are close to completion.",
            "Move suitable appointments into underused morning slots.",
            "Check therapist capacity before accepting additional evening bookings.",
        ])

        for index, priority in enumerate(priorities[:5], 1):
            with st.container(border=True):
                st.markdown(f"**{index}. {priority}**")

    with right:
        st.markdown("### Your AI team")
        st.success("All specialist employees are online.")
        st.write("**Jarvis is coordinating:**")
        st.write("• Patient follow-ups")
        st.write("• Revenue opportunities")
        st.write("• Therapist capacity")
        st.write("• Approvals and execution")
        if st.button("Start a briefing with Jarvis", type="primary", use_container_width=True):
            st.session_state["jarvis_mode"] = True
            st.rerun()


def show_patient_intelligence():
    st.title("Patient Intelligence")
    st.caption("Test churn, follow-up and renewal recommendations with patient data.")
    with st.form("patient-risk"):
        name=st.text_input("Patient name", "Sample Patient")
        days=st.number_input("Days since last visit",0,365,18)
        missed=st.number_input("Missed appointments",0,20,2)
        sessions=st.number_input("Sessions remaining",0,100,1)
        submitted=st.form_submit_button("Analyse patient", type="primary")
    if submitted:
        score=min(95, 15+days*2+missed*12+(20 if sessions<=1 else 0))
        level="High" if score>=70 else "Medium" if score>=40 else "Low"
        st.metric("Churn risk", f"{score}%", level)
        action="Call today and prepare a personalised renewal offer." if score>=70 else "Send a follow-up within 48 hours." if score>=40 else "Continue normal engagement."
        st.info(f"**Recommendation for {name}:** {action}")
        if st.button("Create follow-up task"):
            add_task(f"Follow up with {name}","Customer Success","High" if score>=70 else "Medium")
            st.success("Task added to Action Center.")


def show_revenue_intelligence():
    _, company, revenue, expenses, _, _=_data()
    st.title("Revenue Intelligence")
    growth=st.slider("Expected monthly growth",-20,50,10)
    forecast=revenue*(1+growth/100)
    margin=((revenue-expenses)/revenue*100) if revenue else 0
    c1,c2,c3=st.columns(3)
    c1.metric("Current revenue",_money(revenue)); c2.metric("Forecast",_money(forecast),f"{growth}%"); c3.metric("Operating margin",f"{margin:.1f}%")
    st.subheader("Jarvis recommendation")
    if margin<25: st.warning("Protect margin before increasing fixed costs. Prioritise renewals and higher-margin services.")
    else: st.success("Margin can support measured growth. Test corporate wellness and package renewals before hiring.")


def show_therapist_intelligence():
    st.title("Therapist Intelligence")
    therapists=st.number_input("Active therapists",1,50,2)
    weekly=st.number_input("Appointments per week",0,1000,85)
    capacity=st.number_input("Comfortable appointments per therapist/week",1,100,35)
    utilisation=min(200, weekly/(therapists*capacity)*100)
    st.metric("Team utilisation",f"{utilisation:.0f}%")
    if utilisation>90: st.error("Capacity risk: add part-time coverage or redistribute slots before demand increases.")
    elif utilisation>75: st.warning("Near capacity: begin hiring pipeline and protect recovery/admin time.")
    else: st.success("Capacity is healthy. Focus on filling underused slots.")


def show_memory_center():
    memory, company, *_=_data()
    st.title("Memory Center")
    st.caption("The shared clinic context used by the Chief of Staff and specialist agents.")
    c1,c2,c3=st.columns(3)
    c1.metric("Daily logs",len(memory.get('daily_logs',[]))); c2.metric("Decisions",len(memory.get('decisions',[]))); c3.metric("Reports",len(memory.get('reports',[])))
    st.json({"company":company,"recent_logs":memory.get('daily_logs',[])[-3:],"recent_reports":memory.get('reports',[])[-3:]}, expanded=False)


def show_autonomous_workflows():
    st.title("Autonomous Workflows")
    st.caption("Safe, approval-first automation. Nothing external is sent without confirmation.")
    workflows=[("Inactive patient recovery","Detect inactivity → draft follow-up → create task → request approval"),("Package renewal","Find expiring packages → draft offer → create renewal task"),("Corporate growth","Prepare target list → proposal → outreach plan")]
    for name,flow in workflows:
        with st.container(border=True):
            st.subheader(name); st.write(flow)
            if st.button(f"Prepare {name}",key=name):
                add_task(name,"Operations","Medium")
                add_approval(f"Approve workflow: {name}","Operations","Medium")
                st.success("Prepared and sent to Action Center for approval.")


def show_integrations():
    st.title("Integrations")
    st.caption("Connection readiness. Credentials are required before live actions can run.")
    openai_status = (
        "Configured through .env"
        if openai_is_configured()
        else "API key required"
    )
    rows=[("OpenAI",openai_status,"Jarvis reasoning"),("Google Calendar","Not connected","Appointment sync"),("Gmail","Not connected","Email follow-ups"),("WhatsApp Business","Not connected","Patient reminders"),("Payments","Not connected","Payment links")]
    for name,status,purpose in rows:
        with st.container(border=True):
            a,b=st.columns([3,1]); a.write(f"**{name}**") ; a.caption(purpose); b.write(status)
