from core.memory import load_memory


def calculate_business_health(memory):

    company = memory.get("company", {})

    score = 100

    revenue = company.get("monthly_revenue", 0)
    expenses = company.get("monthly_expenses", 0)

    if revenue <= expenses:
        score -= 30

    if expenses > revenue * 0.8:
        score -= 15

    if len(memory.get("tasks", [])) > 10:
        score -= 10

    if len(memory.get("approvals", [])) > 5:
        score -= 5

    if len(memory.get("daily_logs", [])) == 0:
        score -= 5

    return max(score, 0)


def determine_business_stage(memory):

    company = memory.get("company", {})

    revenue = company.get("monthly_revenue", 0)

    if revenue < 100000:
        return "Startup"

    if revenue < 500000:
        return "Growing"

    if revenue < 2000000:
        return "Scaling"

    return "Enterprise"


def get_open_task_count(memory):

    return len(memory.get("tasks", []))


def get_pending_approvals(memory):
    return sum(
        1
        for approval in memory.get("approvals", [])
        if approval.get("data", {}).get("status") == "Pending"
    )


def generate_business_snapshot():

    memory = load_memory()

    return {

        "health_score": calculate_business_health(memory),

        "business_stage": determine_business_stage(memory),

        "open_tasks": get_open_task_count(memory),

        "pending_approvals": get_pending_approvals(memory)

    }