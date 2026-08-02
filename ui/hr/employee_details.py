import streamlit as st

from ui.hr.download_center import show_download_center


def bullet(title, items):

    st.subheader(title)

    if not items:
        st.info("No data.")
        return

    for item in items:
        st.write(f"• {item}")


def show_employee_details(entry):

    data = entry.get("data", {})

    output = data.get("output", {})

    deliverables = data.get("deliverables", {})

    tabs = st.tabs([
        "Job Description",
        "Candidate",
        "Interview",
        "Scorecard",
        "Onboarding",
        "Performance",
        "Recommendation",
        "Downloads"
    ])

    with tabs[0]:

        jd = output.get("job_description", {})

        st.title(jd.get("title", ""))

        st.info(jd.get("summary", ""))

        bullet("Responsibilities", jd.get("responsibilities", []))

        bullet("Requirements", jd.get("requirements", []))

        st.success(jd.get("salary_range", ""))

    with tabs[1]:

        candidate = output.get("candidate_profile", {})

        st.subheader("Experience")

        st.info(candidate.get("experience", ""))

        st.subheader("Education")

        st.info(candidate.get("education", ""))

        bullet(
            "Technical Skills",
            candidate.get("technical_skills", [])
        )

        bullet(
            "Soft Skills",
            candidate.get("soft_skills", [])
        )

    with tabs[2]:

        for q in output.get(
            "interview_questions",
            []
        ):

            with st.expander(
                q.get("type", "")
            ):

                st.write(q.get("question", ""))

    with tabs[3]:

        scorecard = output.get(
            "evaluation_scorecard",
            []
        )

        for row in scorecard:

            st.write(
                f"**{row.get('criteria','')}** : {row.get('weight','')}"
            )

    with tabs[4]:

        onboarding = output.get(
            "onboarding_plan",
            {}
        )

        for week in [
            "week1",
            "week2",
            "week3",
            "week4"
        ]:

            bullet(
                week.title(),
                onboarding.get(week, [])
            )

    with tabs[5]:

        review = output.get(
            "performance_review",
            {}
        )

        bullet("KPIs", review.get("kpis", []))

        bullet("Strengths", review.get("strengths", []))

        bullet(
            "Improvement Areas",
            review.get(
                "improvement_areas",
                []
            )
        )

        bullet("Goals", review.get("goals", []))

    with tabs[6]:

        hiring = output.get(
            "hiring_recommendation",
            {}
        )

        st.metric(
            "Decision",
            hiring.get("decision", "")
        )

        st.info(
            hiring.get("reason", "")
        )

    with tabs[7]:

        show_download_center(
            deliverables
        )