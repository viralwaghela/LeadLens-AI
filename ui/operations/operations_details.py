import streamlit as st

from ui.operations.download_center import show_download_center


def bullet(title, items):

    st.subheader(title)

    if not items:
        st.info("No data.")
        return

    for item in items:
        st.write(f"• {item}")


def show_operations_details(entry):

    data = entry.get("data", {})

    output = data.get("output", {})

    deliverables = data.get("deliverables", {})

    tabs = st.tabs([
        "Daily Plan",
        "Assignments",
        "Bottlenecks",
        "Risks",
        "Improvements",
        "Weekly Report",
        "Downloads"
    ])

    with tabs[0]:

        plan = output.get(
            "daily_operations_plan",
            {}
        )

        st.title(plan.get("objective",""))

        bullet(
            "Top Priorities",
            plan.get("top_priorities",[])
        )

        bullet(
            "Department Focus",
            plan.get("department_focus",[])
        )

        st.success(
            plan.get("expected_outcome","")
        )

    with tabs[1]:

        assignments = output.get(
            "task_assignment",
            []
        )

        for task in assignments:

            with st.expander(
                task.get("task","")
            ):

                st.write(
                    f"Department: {task.get('department','')}"
                )

                st.write(
                    f"Owner: {task.get('owner_role','')}"
                )

                st.write(
                    f"Priority: {task.get('priority','')}"
                )

                st.write(
                    f"Deadline: {task.get('deadline','')}"
                )

                st.success(
                    task.get(
                        "success_metric",
                        ""
                    )
                )

    with tabs[2]:

        for item in output.get(
            "bottleneck_detection",
            []
        ):

            with st.expander(
                item.get("bottleneck","")
            ):

                st.write(item.get("impact",""))

                st.success(
                    item.get("solution","")
                )

    with tabs[3]:

        for risk in output.get(
            "operational_risks",
            []
        ):

            with st.expander(
                risk.get("risk","")
            ):

                st.warning(
                    risk.get("severity","")
                )

                st.write(
                    risk.get(
                        "mitigation",
                        ""
                    )
                )

    with tabs[4]:

        improvements = output.get(
            "process_improvements",
            []
        )

        for item in improvements:

            with st.expander(
                item.get("process","")
            ):

                st.write(
                    item.get(
                        "recommendation",
                        ""
                    )
                )

                st.success(
                    item.get(
                        "expected_benefit",
                        ""
                    )
                )

    with tabs[5]:

        report = output.get(
            "weekly_operations_report",
            {}
        )

        st.info(
            report.get("summary","")
        )

        bullet(
            "Completed Work",
            report.get(
                "completed_work",
                []
            )
        )

        bullet(
            "Pending Work",
            report.get(
                "pending_work",
                []
            )
        )

        bullet(
            "Next Week Focus",
            report.get(
                "next_week_focus",
                []
            )
        )

    with tabs[6]:

        show_download_center(
            deliverables
        )