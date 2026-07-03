import streamlit as st
from ai_client import generate_ai_response


FOCUS_OPTIONS = {
    "growth": [
        "Lead Generation",
        "SEO Strategy",
        "Content Marketing",
        "Customer Acquisition"
    ],
    "competitor": [
        "Pricing Strategy",
        "Feature Comparison",
        "Competitive Advantages",
        "Customer Reviews"
    ],
    "product": [
        "Product Positioning",
        "Target Audience",
        "Go-to-Market Strategy",
        "Pricing Strategy"
    ],
    "sales": [
        "Sales Funnel",
        "Cold Outreach",
        "Lead Qualification",
        "Closing Strategy"
    ],
    "default": [
        "Marketing Ideas",
        "Customer Acquisition",
        "Competitive Positioning",
        "Growth Opportunities"
    ]
}


def get_focus_options(research_goal):
    goal = research_goal.lower()

    if "growth" in goal or "marketing" in goal:
        return FOCUS_OPTIONS["growth"]

    if "competitor" in goal or "competition" in goal:
        return FOCUS_OPTIONS["competitor"]

    if "product" in goal:
        return FOCUS_OPTIONS["product"]

    if "sales" in goal:
        return FOCUS_OPTIONS["sales"]

    return FOCUS_OPTIONS["default"]


def build_prompt(company, industry, target_audience, research_goal, focus_areas):
    return f"""
Create a clear business research and growth strategy report.

Company: {company}
Industry: {industry}
Target Audience: {target_audience}
Research Goal: {research_goal}
Special Focus Areas: {", ".join(focus_areas)}

Include:
1. Executive Summary
2. Overall Priority Score out of 10 with reasoning
3. Company Overview
4. Possible Competitors
5. Strengths
6. Weaknesses
7. Opportunities
8. Threats
9. Marketing Ideas
10. Suggested Next Steps
11. Quick Wins
12. 30-Day Action Plan
13. Recommended AI Tools Stack

Rules:
- Be practical.
- Do not invent exact statistics.
- Clearly mention when something is an assumption.
- Keep the report structured and easy to read.
- Prioritize the selected special focus areas.
"""


st.set_page_config(
    page_title="LeadLens AI",
    page_icon="📊",
    layout="wide"
)

st.title("📊 LeadLens AI")
st.caption("AI-powered business intelligence.")

st.divider()

company = st.text_input("Company Name")
industry = st.text_input("Industry")
target_audience = st.text_input("Target Audience")
research_goal = st.text_area("Research Goal", height=100)

focus_options = get_focus_options(research_goal)

st.subheader("Suggested Focus Areas")

selected_focus = []

cols = st.columns(4)

for index, option in enumerate(focus_options):
    with cols[index]:
        if st.checkbox(option):
            selected_focus.append(option)

custom_focus = st.text_input("Custom Focus Area (optional)")

if custom_focus.strip():
    selected_focus.append(custom_focus)

generate = st.button("Generate Report", use_container_width=True)

if generate:
    if company.strip() == "" or industry.strip() == "" or target_audience.strip() == "" or research_goal.strip() == "":
        st.error("Please fill all fields before generating the report.")
    elif len(selected_focus) == 0:
        st.error("Please select at least one focus area or enter a custom focus.")
    else:
        prompt = build_prompt(
            company,
            industry,
            target_audience,
            research_goal,
            selected_focus
        )

        with st.spinner("Generating business research report..."):
            report = generate_ai_response(
                prompt,
                "You are a practical business research and growth strategy analyst."
            )

        st.subheader("📊 Business Research Report")

        with st.expander("Full Report", expanded=True):
            st.write(report)

        st.download_button(
            label="Download Report",
            data=report,
            file_name="leadlens_report.txt",
            mime="text/plain",
            use_container_width=True
        )