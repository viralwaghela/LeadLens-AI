from __future__ import annotations
import json
import streamlit as st
from services.platform_data import business_snapshot, preview_uploaded_file, save_company_profile, save_uploaded_file


def show_data_hub() -> None:
    st.markdown('<div class="eyebrow">BUSINESS MEMORY</div>', unsafe_allow_html=True)
    st.markdown("## Data hub")
    st.caption("Maintain the business context shared by every LeadLens agent and add supporting business files.")
    snapshot = business_snapshot()
    company = snapshot["company"]
    tab1, tab2, tab3 = st.tabs(["Business profile", "Upload data", "Memory export"])
    with tab1:
        with st.form("company_profile"):
            c1, c2 = st.columns(2)
            profile = dict(company)
            profile["business_name"] = c1.text_input("Business name", value=str(company.get("business_name", "")))
            profile["industry"] = c2.text_input("Industry", value=str(company.get("industry", "")))
            profile["location"] = c1.text_input("Location", value=str(company.get("location", "")))
            profile["website"] = c2.text_input("Website", value=str(company.get("website", "")))
            profile["monthly_revenue"] = c1.number_input("Monthly revenue", min_value=0.0, value=float(company.get("monthly_revenue", 0) or 0), step=1000.0)
            profile["monthly_expenses"] = c2.number_input("Monthly expenses", min_value=0.0, value=float(company.get("monthly_expenses", 0) or 0), step=1000.0)
            profile["marketing_budget"] = c1.number_input("Marketing budget", min_value=0.0, value=float(company.get("marketing_budget", 0) or 0), step=1000.0)
            profile["employees"] = c2.number_input("Team size", min_value=0, value=int(company.get("employees", 0) or 0), step=1)
            profile["services"] = st.text_area("Services", value=str(company.get("services", "")), height=120)
            profile["target_audience"] = st.text_area("Target audience", value=str(company.get("target_audience", "")), height=100)
            profile["goals"] = st.text_area("Current business goals", value=str(company.get("goals", "")), height=100)
            if st.form_submit_button("Save business memory", type="primary"):
                save_company_profile(profile)
                st.success("Business memory updated for every agent.")
                st.rerun()
    with tab2:
        upload = st.file_uploader("Add business data", type=["csv", "json", "txt", "md", "xlsx"])
        if upload is not None:
            raw = upload.getvalue()
            path = save_uploaded_file(upload.name, raw)
            st.success(f"Stored: {path.name}")
            try:
                rows, summary = preview_uploaded_file(upload.name, raw)
                st.caption(summary)
                if rows:
                    st.dataframe(rows, use_container_width=True)
            except Exception as error:
                st.warning(f"The file was stored but could not be previewed: {error}")
            st.info("Uploaded files are stored locally in database/uploads. Automated ingestion into agent answers is prepared for the next connector layer.")
    with tab3:
        payload = json.dumps(snapshot["memory"], indent=2, ensure_ascii=False)
        st.download_button("Download complete business memory", payload, "leadlens_business_memory.json", "application/json", use_container_width=True)
        st.json({key: len(value) if isinstance(value, list) else "profile" for key, value in snapshot["memory"].items()})
