"""Read-only specialist analyses for the LeadLens Chief of Staff workspace.

These functions analyse existing LeadLens business memory. They do not trigger
campaign generation, write files, create approvals, or change V1 workflows.
"""

from __future__ import annotations

from typing import Any

from core.memory import load_memory


def _money(value: Any) -> str:
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "Not available"


def _open_items(memory: dict[str, Any], section: str, department: str | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in memory.get(section, []):
        data = item.get("data", {})
        status = str(data.get("status", "Pending")).strip().casefold()
        item_department = str(data.get("department", "")).strip().casefold()
        if status not in {"pending", "open"}:
            continue
        if department and item_department != department.casefold():
            continue
        results.append(data)
    return results


def sales_analysis(query: str) -> str:
    memory = load_memory()
    company = memory.get("company", {})
    revenue = float(company.get("monthly_revenue", 0) or 0)
    pending = _open_items(memory, "tasks", "Sales")
    top_task = pending[0].get("title") if pending else "No open sales task is recorded."

    return f"""### Sales Agent

**Current signal**
- Monthly revenue: **{_money(revenue)}**
- Open sales tasks: **{len(pending)}**
- Main recorded priority: **{top_task}**

**Assessment**
Beyond Pain has a clear corporate-wellness offer and a defined monthly revenue base, but LeadLens does not yet contain pipeline, lead-source, conversion-rate, or deal-stage data. A precise growth trend cannot be calculated from the current records.

**Recommended next actions**
1. Track every corporate lead by company, source, stage, expected value, and next follow-up date.
2. Package posture assessments and ergonomic workshops into a fixed corporate offer.
3. Review new leads, follow-ups, conversions, and lost opportunities every week.
"""


def marketing_analysis(query: str) -> str:
    memory = load_memory()
    company = memory.get("company", {})
    budget = company.get("marketing_budget", 0)
    platforms = company.get("platforms", []) or []
    pending = _open_items(memory, "tasks", "Marketing")

    return f"""### Marketing Agent

**Current signal**
- Monthly marketing budget: **{_money(budget)}**
- Active channels: **{', '.join(platforms) if platforms else 'Not recorded'}**
- Open marketing tasks: **{len(pending)}**

**Assessment**
The current channel mix is suitable for a local healthcare business. The strongest near-term opportunity is to connect educational content with a measurable appointment or corporate-enquiry funnel rather than publishing awareness content without conversion tracking.

**Recommended next actions**
1. Use one call to action across Instagram, Google, WhatsApp, and email.
2. Create separate campaigns for patients, senior citizens, and corporate HR teams.
3. Track cost per enquiry, booked assessment, attendance, and treatment conversion.
"""


def finance_analysis(query: str) -> str:
    memory = load_memory()
    company = memory.get("company", {})
    revenue = float(company.get("monthly_revenue", 0) or 0)
    expenses = float(company.get("monthly_expenses", 0) or 0)
    profit = revenue - expenses
    margin = (profit / revenue * 100) if revenue else 0
    approvals = _open_items(memory, "approvals", "Marketing")

    return f"""### Finance Agent

**Current signal**
- Monthly revenue: **{_money(revenue)}**
- Monthly expenses: **{_money(expenses)}**
- Estimated operating profit: **{_money(profit)}**
- Estimated operating margin: **{margin:.1f}%**
- Pending marketing approvals: **{len(approvals)}**

**Assessment**
The recorded margin is healthy, but it is based on summary figures rather than transaction-level accounting. Any increase in marketing spend should have a defined lead, booking, and revenue target before approval.

**Recommended next actions**
1. Set a maximum customer-acquisition cost for each service line.
2. Separate fixed costs, variable costs, and owner withdrawals.
3. Approve budget increases only with a 30-day measurement plan.
"""


def hr_analysis(query: str) -> str:
    memory = load_memory()
    company = memory.get("company", {})
    employees = int(company.get("employees", 0) or 0)
    pending = _open_items(memory, "tasks", "HR")
    top_task = pending[0].get("title") if pending else "No open HR task is recorded."

    return f"""### HR Agent

**Current signal**
- Recorded team size: **{employees}**
- Open HR tasks: **{len(pending)}**
- Main recorded priority: **{top_task}**

**Assessment**
For a small clinic, role clarity and workload coverage matter more than adding headcount quickly. Any new hire should be tied to a measurable bottleneck such as lead follow-up, patient coordination, reporting, or therapist capacity.

**Recommended next actions**
1. Define the exact weekly outcomes expected from every role.
2. Measure current workload before approving a new position.
3. Use a 30-day scorecard for any new hire or contractor.
"""


def operations_analysis(query: str) -> str:
    memory = load_memory()
    company = memory.get("company", {})
    pending = _open_items(memory, "tasks", "Operations")
    completed = memory.get("completed_tasks", [])

    return f"""### Operations Agent

**Current signal**
- Open operations tasks: **{len(pending)}**
- Total completed tasks recorded: **{len(completed)}**
- Core services: **{str(company.get('services', 'Not recorded')).replace(chr(10), ', ')}**

**Assessment**
LeadLens currently records business tasks, but it does not yet capture appointment capacity, therapist utilisation, cancellations, patient wait time, or follow-up completion. Those are the operating metrics most likely to expose growth bottlenecks.

**Recommended next actions**
1. Track enquiries from first contact through appointment and payment.
2. Record cancellations, no-shows, reschedules, and follow-up completion.
3. Review therapist capacity and peak-hour demand every week.
"""


def executive_analysis(query: str) -> str:
    memory = load_memory()
    company = memory.get("company", {})
    revenue = float(company.get("monthly_revenue", 0) or 0)
    expenses = float(company.get("monthly_expenses", 0) or 0)
    profit = revenue - expenses
    open_tasks = _open_items(memory, "tasks")
    approvals = _open_items(memory, "approvals")

    return f"""### Executive Agent

**Business snapshot**
- Business: **{company.get('business_name', 'LeadLens business')}**
- Monthly revenue: **{_money(revenue)}**
- Estimated operating profit: **{_money(profit)}**
- Open tasks: **{len(open_tasks)}**
- Decisions awaiting approval: **{len(approvals)}**

**Chief-of-Staff view**
The immediate priority is to connect marketing activity to a measurable sales pipeline while protecting the current operating margin. The next management review should focus on lead conversion, appointment capacity, and the return generated by the marketing budget.

**Recommended decision sequence**
1. Establish baseline sales and marketing conversion metrics.
2. Resolve duplicate or stale approvals and assign one owner to each priority.
3. Review results after 30 days before increasing fixed costs.
"""


AGENT_HANDLERS = {
    "sales": sales_analysis,
    "marketing": marketing_analysis,
    "finance": finance_analysis,
    "hr": hr_analysis,
    "operations": operations_analysis,
    "executive": executive_analysis,
}
