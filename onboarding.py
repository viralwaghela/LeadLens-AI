import streamlit as st

from core.memory import save_company


def show_onboarding():

    st.title("👋 Welcome to LeadLens AI")

    st.caption(
        "Let's set up your business. This only takes a couple of minutes."
    )

    with st.form("company_onboarding"):

        st.subheader("Business Information")

        business_name = st.text_input("Business Name")

        industry = st.selectbox(
            "Industry",
            [
                "Healthcare",
                "Education",
                "SaaS",
                "Retail",
                "Finance",
                "Manufacturing",
                "Real Estate",
                "Restaurant",
                "E-Commerce",
                "Other"
            ]
        )

        website = st.text_input("Website")

        business_email = st.text_input("Business Email")

        phone = st.text_input("Phone Number")

        location = st.text_input("Business Location")

        st.divider()

        st.subheader("Business Model")

        products = st.text_area("Products")

        services = st.text_area("Services")

        pricing = st.text_input("Average Pricing")

        employees = st.number_input(
            "Number of Employees",
            min_value=1,
            value=1
        )

        years = st.number_input(
            "Years in Business",
            min_value=0,
            value=0
        )

        st.divider()

        st.subheader("Customers")

        audience = st.text_area("Target Audience")

        problem = st.text_area("Problem You Solve")

        competitors = st.text_area(
            "Competitors (comma separated)"
        )

        st.divider()

        st.subheader("Business Goals")

        goals = st.multiselect(
            "Select your goals",
            [
                "Increase Revenue",
                "Reduce Costs",
                "Generate Leads",
                "Improve Marketing",
                "Hire Employees",
                "Scale Operations",
                "Improve Customer Retention"
            ]
        )

        st.divider()

        st.subheader("Financial Snapshot")

        revenue = st.number_input(
            "Monthly Revenue",
            min_value=0.0,
            step=1000.0
        )

        expenses = st.number_input(
            "Monthly Expenses",
            min_value=0.0,
            step=1000.0
        )

        marketing_budget = st.number_input(
            "Monthly Marketing Budget",
            min_value=0.0,
            step=1000.0
        )

        employee_cost = st.number_input(
            "Monthly Employee Cost",
            min_value=0.0,
            step=1000.0
        )

        st.divider()

        st.subheader("Preferred Platforms")

        platforms = st.multiselect(
            "Platforms",
            [
                "Instagram",
                "Facebook",
                "LinkedIn",
                "Google",
                "Email",
                "WhatsApp"
            ]
        )

        st.divider()

        st.subheader("AI Permissions")

        permissions = st.multiselect(
            "Allow LeadLens to",
            [
                "Generate Content",
                "Generate Emails",
                "Schedule Tasks",
                "Manage Calendar",
                "Access Files",
                "Analyze Reports"
            ]
        )

        submit = st.form_submit_button(
            "Create My Company",
            use_container_width=True
        )

        if submit:

            company = {

                "business_name": business_name,

                "industry": industry,

                "website": website,

                "business_email": business_email,

                "phone": phone,

                "location": location,

                "products": products,

                "services": services,

                "pricing": pricing,

                "employees": employees,

                "years_in_business": years,

                "target_audience": audience,

                "problem": problem,

                "competitors": [
                    x.strip()
                    for x in competitors.split(",")
                    if x.strip()
                ],

                "goals": goals,

                "monthly_revenue": revenue,

                "monthly_expenses": expenses,

                "marketing_budget": marketing_budget,

                "employee_cost": employee_cost,

                "platforms": platforms,

                "permissions": permissions

            }

            save_company(company)

            st.success("Company created successfully.")

            st.rerun()