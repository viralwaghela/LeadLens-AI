import json

from services.ai import generate_ai_response
from services.json_utils import parse_json_response
from core.memory import load_memory


def build_operations_prompt(task, memory):
    return f"""
You are the Operations Department inside LeadLens AI.

Your job is to create a practical operations execution package for the business.

Return ONLY valid JSON.
Do NOT wrap JSON in markdown.
Do NOT explain anything outside the JSON.

Business Memory:
{json.dumps(memory, indent=2)}

Operations Task:
{json.dumps(task, indent=2)}

Return exactly this JSON structure:

{{
    "package_name": "Operations Execution Package",
    "daily_operations_plan": {{
        "objective": "",
        "top_priorities": [],
        "department_focus": [],
        "expected_outcome": ""
    }},
    "task_assignment": [
        {{
            "task": "",
            "department": "",
            "owner_role": "",
            "priority": "High",
            "deadline": "",
            "success_metric": ""
        }}
    ],
    "bottleneck_detection": [
        {{
            "bottleneck": "",
            "impact": "",
            "solution": ""
        }}
    ],
    "operational_risks": [
        {{
            "risk": "",
            "severity": "Medium",
            "mitigation": ""
        }}
    ],
    "process_improvements": [
        {{
            "process": "",
            "recommendation": "",
            "expected_benefit": ""
        }}
    ],
    "weekly_operations_report": {{
        "summary": "",
        "completed_work": [],
        "pending_work": [],
        "next_week_focus": []
    }},
    "kpis": {{
        "execution_score": "",
        "task_completion_target": "",
        "main_operations_metric": ""
    }}
}}
"""


def generate_operations_package(task):
    memory = load_memory()

    prompt = build_operations_prompt(task, memory)

    response = generate_ai_response(
        prompt,
        "You are an expert operations manager for small businesses."
    )

    parsed = parse_json_response(response)
    if parsed is not None:
        return parsed
    return {
            "error": "Invalid JSON returned by Operations Engine",
            "raw_response": response
        }