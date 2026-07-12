import streamlit as st

from ceo_command_center import show_ceo_command_center
from coo_dashboard import show_coo_dashboard
from core.memory import load_company
from executive_dashboard import show_executive_dashboard
from finance_dashboard import show_finance_dashboard
from hr_dashboard import show_hr_dashboard
from marketing_dashboard import show_marketing_dashboard
from memory_dashboard import show_memory_dashboard
from operations_dashboard import show_operations_dashboard
from sales_dashboard import show_sales_dashboard
from ui.activity_timeline import show_activity_timeline
from ui.notification_center import show_notification_center


def _show_company_settings(company):
    st.markdown("## Company settings")
    a, b = st.columns(2)
    with a:
        st.text_input("Business name", value=company.get("business_name", ""), disabled=True)
        st.text_input("Industry", value=company.get("industry", ""), disabled=True)
        st.text_input("Location", value=company.get("location", ""), disabled=True)
        st.text_input("Website", value=company.get("website", ""), disabled=True)
    with b:
        st.text_area("Products", value=company.get("products", ""), disabled=True)
        st.text_area("Services", value=company.get("services", ""), disabled=True)
        st.text_area("Target audience", value=company.get("target_audience", ""), disabled=True)
    st.info("Editing company settings is reserved for the next release. Current data remains available to every AI department.")


def show_dashboard():
    company = load_company()

    with st.sidebar:
        st.markdown("# ✦ LeadLens")
        st.caption("AI business operating system")
        st.divider()
        page = st.radio(
            "Navigation",
            ["Home", "Departments", "AI Executive", "Reports", "Settings"],
            label_visibility="collapsed",
        )
        st.divider()
        st.caption(company.get("business_name", "LeadLens AI"))
        st.caption(company.get("industry", ""))

    if page == "Home":
        show_executive_dashboard()
        return

    if page == "Departments":
        st.markdown("## Department workspaces")
        department = st.segmented_control(
            "Department",
            ["Marketing", "Sales", "Finance", "HR", "Operations"],
            default="Marketing",
            label_visibility="collapsed",
        )
        if department == "Marketing":
            show_marketing_dashboard()
        elif department == "Sales":
            show_sales_dashboard()
        elif department == "Finance":
            show_finance_dashboard()
        elif department == "HR":
            show_hr_dashboard()
        else:
            show_operations_dashboard()
        return

    if page == "AI Executive":
        tab1, tab2 = st.tabs(["CEO Command Center", "AI COO"])
        with tab1:
            show_ceo_command_center()
        with tab2:
            show_coo_dashboard()
        return

    if page == "Reports":
        tab1, tab2, tab3 = st.tabs(["Notifications", "Activity", "Business Memory"])
        with tab1:
            show_notification_center()
        with tab2:
            show_activity_timeline()
        with tab3:
            show_memory_dashboard()
        return

    _show_company_settings(company)
