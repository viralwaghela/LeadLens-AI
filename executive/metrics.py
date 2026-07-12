from core.memory import load_memory


def calculate_overall_health(memory):
    company = memory.get("company", {})

    score = 100

    revenue = float(company.get("monthly_revenue", 0) or 0)
    expenses = float(company.get("monthly_expenses", 0) or 0)

    if revenue <= 0:
        score -= 25

    if expenses >= revenue:
        score -= 30

    if expenses > revenue * 0.8:
        score -= 15

    if len(memory.get("approvals", [])) > 5:
        score -= 10

    if len(memory.get("reports", [])) > 20:
        score -= 5

    return max(score, 0)


def get_department_counts(memory):
    return {
        "marketing": len(memory.get("marketing", [])),
        "sales": len(memory.get("sales", [])),
        "finance": len(memory.get("finance", [])),
        "hr": len(memory.get("hr", [])),
        "operations": len(memory.get("operations", [])),
    }


def get_financial_snapshot(memory):
    company = memory.get("company", {})

    revenue = float(company.get("monthly_revenue", 0) or 0)
    expenses = float(company.get("monthly_expenses", 0) or 0)
    profit = revenue - expenses

    if revenue > 0:
        margin = (profit / revenue) * 100
    else:
        margin = 0

    return {
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "margin": margin,
    }


def get_pending_approvals(memory):
    approvals = memory.get("approvals", [])

    return [
        item
        for item in approvals
        if str(item.get("data", {}).get("status", "Pending"))
        .strip()
        .casefold()
        in {"pending", "open"}
    ]


def get_latest_item(memory, section):
    items = memory.get(section, [])

    if not items:
        return None

    return items[-1]


def build_executive_metrics():
    memory = load_memory()

    return {
        "health_score": calculate_overall_health(memory),
        "department_counts": get_department_counts(memory),
        "financial_snapshot": get_financial_snapshot(memory),
        "pending_approvals": get_pending_approvals(memory),
        "latest_marketing": get_latest_item(memory, "marketing"),
        "latest_sales": get_latest_item(memory, "sales"),
        "latest_finance": get_latest_item(memory, "finance"),
        "latest_hr": get_latest_item(memory, "hr"),
        "latest_operations": get_latest_item(memory, "operations"),
    }