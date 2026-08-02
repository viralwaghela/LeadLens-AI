import streamlit as st

from ui.sales.download_center import show_download_center


def show_strategy(strategy):
    st.subheader("🎯 Lead Generation Strategy")

    col1, col2 = st.columns(2)

    with col1:
        st.write("**Objective**")
        st.info(strategy.get("objective", ""))

        st.write("**Ideal Customer Profile**")
        st.info(strategy.get("ideal_customer_profile", ""))

        st.write("**Primary CTA**")
        st.success(strategy.get("primary_cta", ""))

    with col2:
        st.write("**Sales Angle**")
        st.info(strategy.get("sales_angle", ""))

        st.write("**Primary Offer**")
        st.warning(strategy.get("primary_offer", ""))

    st.write("### Target Segments")

    for segment in strategy.get("target_segments", []):
        st.write(f"• {segment}")

    st.write("### Decision Makers")

    for person in strategy.get("decision_makers", []):
        st.write(f"• {person}")

    st.write("### Prospecting Channels")

    channels = strategy.get("prospecting_channels", [])

    if channels:
        st.code(" | ".join(channels))


def show_prospects(prospects):
    st.subheader("👥 Prospect List")

    if not prospects:
        st.info("No prospects generated.")
        return

    for prospect in prospects:
        with st.expander(prospect.get("segment", "Segment")):
            st.write("**Why Target**")
            st.write(prospect.get("why_target", ""))

            st.write("**Outreach Angle**")
            st.info(prospect.get("outreach_angle", ""))


def show_cold_emails(emails):
    st.subheader("📧 Cold Emails")

    if not emails:
        st.info("No emails generated.")
        return

    for index, email in enumerate(emails, start=1):
        with st.expander(f"Email {index}"):

            st.write("**Subject**")
            st.info(email.get("subject", ""))

            st.write("**Body**")
            st.write(email.get("body", ""))

            st.write("**CTA**")
            st.success(email.get("cta", ""))


def show_whatsapp(messages):
    st.subheader("💬 WhatsApp Outreach")

    if not messages:
        st.info("No WhatsApp messages generated.")
        return

    for msg in messages:
        with st.expander(msg.get("stage", "Message")):
            st.write(msg.get("message", ""))


def show_call_script(script):
    st.subheader("📞 Sales Call Script")

    st.write("### Opening")
    st.info(script.get("opening", ""))

    st.write("### Discovery Questions")

    for q in script.get("discovery_questions", []):
        st.write(f"• {q}")

    st.write("### Objection Handling")

    for obj in script.get("objection_handling", []):
        st.write(f"**Objection:** {obj.get('objection','')}")
        st.success(obj.get("response", ""))

    st.write("### Closing Script")
    st.info(script.get("closing_script", ""))


def show_proposal(proposal):
    st.subheader("📄 Proposal")

    st.write("### Problem")
    st.info(proposal.get("problem_statement", ""))

    st.write("### Solution")
    st.write(proposal.get("solution", ""))

    st.write("### Deliverables")

    for item in proposal.get("deliverables", []):
        st.write(f"• {item}")

    st.write("### Pricing Suggestion")
    st.success(proposal.get("pricing_suggestion", ""))

    st.write("### Next Steps")
    st.info(proposal.get("next_steps", ""))


def show_followups(sequence):
    st.subheader("🔁 Follow-up Sequence")

    if not sequence:
        st.info("No follow-up sequence generated.")
        return

    for item in sequence:
        with st.expander(
            f"{item.get('day','')} • {item.get('channel','')}"
        ):
            st.write(item.get("message", ""))


def show_kpis(kpis):
    st.subheader("📊 Sales KPIs")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Target Leads",
        kpis.get("target_leads", "N/A")
    )

    col2.metric(
        "Expected Conversions",
        kpis.get("expected_conversions", "N/A")
    )

    col3.metric(
        "Success Metric",
        kpis.get("success_metric", "N/A")
    )


def show_lead_details(entry):

    data = entry.get("data", {})

    task = data.get("task", {})

    output = data.get("output", {})

    deliverables = data.get("deliverables", {})

    campaign_name = output.get(
        "campaign_name",
        task.get("title", "Sales Campaign")
    )

    st.title(campaign_name)

    st.caption(entry.get("created_at", ""))

    tabs = st.tabs([
        "Strategy",
        "Prospects",
        "Cold Emails",
        "WhatsApp",
        "Call Script",
        "Proposal",
        "Follow-ups",
        "Downloads"
    ])

    with tabs[0]:
        show_strategy(output.get("lead_strategy", {}))
        show_kpis(output.get("kpis", {}))

    with tabs[1]:
        show_prospects(output.get("prospect_list", []))

    with tabs[2]:
        show_cold_emails(output.get("cold_emails", []))

    with tabs[3]:
        show_whatsapp(output.get("whatsapp_messages", []))

    with tabs[4]:
        show_call_script(output.get("sales_call_script", {}))

    with tabs[5]:
        show_proposal(output.get("proposal", {}))

    with tabs[6]:
        show_followups(output.get("follow_up_sequence", []))

    with tabs[7]:
        show_download_center(deliverables)