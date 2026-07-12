import json

from services.ai import generate_ai_response
from services.json_utils import parse_json_response
from core.memory import load_memory


def build_hr_prompt(task, memory):
    return f"""
You are the Human Resources Department inside LeadLens AI.

Your job is to create a complete HR hiring package.

Return ONLY valid JSON.

Business Memory:
{json.dumps(memory, indent=2)}

HR Task:
{json.dumps(task, indent=2)}

Return exactly:

{{
    "package_name":"HR Hiring Package",

    "job_description":{{
        "title":"",
        "department":"",
        "summary":"",
        "responsibilities":[],
        "requirements":[],
        "salary_range":""
    }},

    "candidate_profile":{{
        "experience":"",
        "education":"",
        "technical_skills":[],
        "soft_skills":[]
    }},

    "interview_questions":[
        {{
            "type":"Technical",
            "question":""
        }}
    ],

    "evaluation_scorecard":[
        {{
            "criteria":"",
            "weight":""
        }}
    ],

    "onboarding_plan":{{
        "week1":[],
        "week2":[],
        "week3":[],
        "week4":[]
    }},

    "performance_review":{{
        "kpis":[],
        "strengths":[],
        "improvement_areas":[],
        "goals":[]
    }},

    "hiring_recommendation":{{
        "decision":"",
        "reason":""
    }},

    "kpis":{{
        "time_to_hire":"",
        "quality_of_hire":"",
        "success_metric":""
    }}
}}
"""


def generate_hr_package(task):

    memory = load_memory()

    prompt = build_hr_prompt(task, memory)

    response = generate_ai_response(
        prompt,
        "You are an expert HR Director."
    )

    parsed = parse_json_response(response)
    if parsed is not None:
        return parsed
    return {
            "error": "Invalid JSON",
            "raw_response": response
        }