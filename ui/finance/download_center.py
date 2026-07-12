import os
import streamlit as st


def get_label(key):
    labels = {
        "financial_summary_docx": "Financial Summary",
        "expense_analysis_docx": "Expense Analysis",
        "budget_plan_docx": "Budget Plan",
        "profitability_report_docx": "Profitability Report",
        "forecast_docx": "Financial Forecast",
        "investment_recommendations_docx": "Investment Recommendations",
    }

    return labels.get(key, key)


def get_mime(path):
    if path.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    if path.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    if path.endswith(".txt"):
        return "text/plain"

    return "application/octet-stream"


def show_download_center(deliverables):
    st.subheader("⬇ Download Finance Deliverables")

    if not deliverables:
        st.info("No deliverables generated.")
        return

    files = deliverables.get("files", {})

    if not files:
        st.info("No files found.")
        return

    for key, path in files.items():
        if not os.path.exists(path):
            continue

        with open(path, "rb") as file:
            st.download_button(
                label=f"Download {get_label(key)}",
                data=file,
                file_name=os.path.basename(path),
                mime=get_mime(path),
                use_container_width=True,
                key=path
            )