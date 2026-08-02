import streamlit as st

from ui.finance.download_center import show_download_center


def show_financial_summary(summary):
    st.subheader("📌 Financial Summary")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Monthly Revenue", summary.get("monthly_revenue", "N/A"))
    col2.metric("Monthly Expenses", summary.get("monthly_expenses", "N/A"))
    col3.metric("Estimated Profit", summary.get("estimated_profit", "N/A"))
    col4.metric("Profit Margin", summary.get("profit_margin", "N/A"))

    st.write("### Financial Health")
    st.info(summary.get("financial_health", ""))


def show_expense_analysis(items):
    st.subheader("💸 Expense Analysis")

    if not items:
        st.info("No expense analysis generated.")
        return

    for item in items:
        with st.expander(item.get("category", "Expense Category")):
            st.write("**Observation**")
            st.write(item.get("observation", ""))

            st.write("**Recommendation**")
            st.success(item.get("recommendation", ""))


def show_cash_flow(cash_flow):
    st.subheader("💧 Cash Flow Analysis")

    col1, col2 = st.columns(2)

    with col1:
        st.write("**Current Position**")
        st.info(cash_flow.get("current_position", ""))

        st.write("**Runway Estimate**")
        st.success(cash_flow.get("runway_estimate", ""))

    with col2:
        st.write("**Risk Level**")
        st.warning(cash_flow.get("risk_level", ""))

        st.write("**Notes**")
        st.write(cash_flow.get("notes", ""))


def show_budget_plan(plan):
    st.subheader("📊 Budget Plan")

    if not plan:
        st.info("No budget plan generated.")
        return

    for item in plan:
        with st.expander(item.get("department", "Department")):
            st.write("**Suggested Budget**")
            st.success(item.get("suggested_budget", ""))

            st.write("**Reason**")
            st.write(item.get("reason", ""))


def show_profitability(report):
    st.subheader("📈 Profitability Report")

    st.write("### Revenue Drivers")
    for item in report.get("revenue_drivers", []):
        st.write(f"• {item}")

    st.write("### Cost Drivers")
    for item in report.get("cost_drivers", []):
        st.write(f"• {item}")

    st.write("### Profit Improvement Actions")
    for item in report.get("profit_improvement_actions", []):
        st.success(item)


def show_forecast(forecast):
    st.subheader("🔮 Financial Forecast")

    st.write("### Next 30 Days")
    st.info(forecast.get("next_30_days", ""))

    st.write("### Next 90 Days")
    st.info(forecast.get("next_90_days", ""))

    st.write("### Next 12 Months")
    st.info(forecast.get("next_12_months", ""))


def show_investments(items):
    st.subheader("💡 Investment Recommendations")

    if not items:
        st.info("No investment recommendations generated.")
        return

    for item in items:
        with st.expander(item.get("recommendation", "Recommendation")):
            st.write("**Priority**")
            st.warning(item.get("priority", ""))

            st.write("**Reason**")
            st.write(item.get("reason", ""))


def show_kpis(kpis):
    st.subheader("📊 Finance KPIs")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Profit Margin", kpis.get("profit_margin", "N/A"))
    col2.metric("Expense Ratio", kpis.get("expense_ratio", "N/A"))
    col3.metric("Recommended Savings", kpis.get("recommended_savings", "N/A"))
    col4.metric("Success Metric", kpis.get("success_metric", "N/A"))


def show_report_details(entry):
    data = entry.get("data", {})
    task = data.get("task", {})
    output = data.get("output", {})
    deliverables = data.get("deliverables", {})

    report_name = output.get(
        "report_name",
        task.get("title", "Finance Report")
    )

    st.title(report_name)
    st.caption(entry.get("created_at", ""))

    if "error" in output:
        st.error(output.get("error"))
        st.text_area("Raw Response", output.get("raw_response", ""), height=300)
        return

    tabs = st.tabs([
        "Summary",
        "Expenses",
        "Cash Flow",
        "Budget",
        "Profitability",
        "Forecast",
        "Investments",
        "Downloads"
    ])

    with tabs[0]:
        show_financial_summary(output.get("financial_summary", {}))
        show_kpis(output.get("kpis", {}))

    with tabs[1]:
        show_expense_analysis(output.get("expense_analysis", []))

    with tabs[2]:
        show_cash_flow(output.get("cash_flow_analysis", {}))

    with tabs[3]:
        show_budget_plan(output.get("budget_plan", []))

    with tabs[4]:
        show_profitability(output.get("profitability_report", {}))

    with tabs[5]:
        show_forecast(output.get("forecast", {}))

    with tabs[6]:
        show_investments(output.get("investment_recommendations", []))

    with tabs[7]:
        show_download_center(deliverables)