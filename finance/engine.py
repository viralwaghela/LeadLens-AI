import json

from services.ai import generate_ai_response
from services.json_utils import parse_json_response
from core.memory import load_memory


def build_finance_report_prompt(task, memory):
    return f"""
You are the Finance Department inside LeadLens AI.

Your job is to create a practical financial analysis and planning report for the business.

Return ONLY valid JSON.
Do NOT wrap JSON in markdown.
Do NOT explain anything outside the JSON.

Business Memory:
{json.dumps(memory, indent=2)}

Finance Task:
{json.dumps(task, indent=2)}

Return exactly this JSON structure:

{{
    "report_name": "Finance report name",
    "financial_summary": {{
        "monthly_revenue": "Revenue amount",
        "monthly_expenses": "Expense amount",
        "estimated_profit": "Estimated profit",
        "profit_margin": "Profit margin",
        "financial_health": "Short financial health summary"
    }},
    "expense_analysis": [
        {{
            "category": "Expense category",
            "observation": "Observation",
            "recommendation": "Recommendation"
        }}
    ],
    "cash_flow_analysis": {{
        "current_position": "Cash flow position",
        "risk_level": "Low/Medium/High",
        "runway_estimate": "Estimated runway",
        "notes": "Cash flow notes"
    }},
    "budget_plan": [
        {{
            "department": "Marketing",
            "suggested_budget": "Suggested budget",
            "reason": "Reason"
        }}
    ],
    "profitability_report": {{
        "revenue_drivers": [
            "Driver 1",
            "Driver 2"
        ],
        "cost_drivers": [
            "Cost driver 1",
            "Cost driver 2"
        ],
        "profit_improvement_actions": [
            "Action 1",
            "Action 2"
        ]
    }},
    "forecast": {{
        "next_30_days": "30-day forecast",
        "next_90_days": "90-day forecast",
        "next_12_months": "12-month forecast"
    }},
    "investment_recommendations": [
        {{
            "recommendation": "Investment recommendation",
            "priority": "High",
            "reason": "Reason"
        }}
    ],
    "kpis": {{
        "profit_margin": "Estimated profit margin",
        "expense_ratio": "Expense ratio",
        "recommended_savings": "Recommended savings",
        "success_metric": "Main finance KPI"
    }}
}}
"""


def generate_finance_report(task):
    memory = load_memory()

    prompt = build_finance_report_prompt(task, memory)

    response = generate_ai_response(
        prompt,
        "You are an expert finance manager for small businesses."
    )

    parsed = parse_json_response(response)
    if parsed is not None:
        return parsed
    return {
            "error": "Invalid JSON returned by Finance Engine",
            "raw_response": response
        }