import json

from services.ai import generate_ai_response
from services.json_utils import parse_json_response
from core.memory import load_memory


def build_sales_campaign_prompt(task, memory):
    return f"""
You are the Sales Department inside LeadLens AI.

Your job is to create a practical, execution-ready sales campaign for the business.

Return ONLY valid JSON.
Do NOT wrap JSON in markdown.
Do NOT explain anything outside the JSON.

Business Memory:
{json.dumps(memory, indent=2)}

Sales Task:
{json.dumps(task, indent=2)}

Return exactly this JSON structure:

{{
    "campaign_name": "Sales campaign name",
    "lead_strategy": {{
        "objective": "Sales objective",
        "ideal_customer_profile": "Ideal customer profile",
        "target_segments": [
            "Segment 1",
            "Segment 2"
        ],
        "decision_makers": [
            "Decision maker 1",
            "Decision maker 2"
        ],
        "prospecting_channels": [
            "LinkedIn",
            "Email",
            "WhatsApp"
        ],
        "sales_angle": "Main sales angle",
        "primary_offer": "Offer idea",
        "primary_cta": "Main CTA"
    }},
    "prospect_list": [
        {{
            "segment": "Corporate HR Teams",
            "why_target": "Reason this segment is relevant",
            "outreach_angle": "How to approach them"
        }}
    ],
    "cold_emails": [
        {{
            "subject": "Email subject",
            "body": "Email body",
            "cta": "Email CTA"
        }}
    ],
    "whatsapp_messages": [
        {{
            "stage": "Initial Message",
            "message": "WhatsApp message text"
        }}
    ],
    "sales_call_script": {{
        "opening": "Opening script",
        "discovery_questions": [
            "Question 1",
            "Question 2"
        ],
        "objection_handling": [
            {{
                "objection": "Objection",
                "response": "Response"
            }}
        ],
        "closing_script": "Closing script"
    }},
    "proposal": {{
        "title": "Proposal title",
        "problem_statement": "Problem statement",
        "solution": "Recommended solution",
        "deliverables": [
            "Deliverable 1",
            "Deliverable 2"
        ],
        "pricing_suggestion": "Pricing suggestion",
        "next_steps": "Next steps"
    }},
    "follow_up_sequence": [
        {{
            "day": "Day 2",
            "channel": "Email",
            "message": "Follow-up message"
        }}
    ],
    "kpis": {{
        "target_leads": "Estimated leads",
        "expected_conversions": "Estimated conversions",
        "success_metric": "Main sales KPI"
    }}
}}
"""


def generate_sales_campaign(task):
    memory = load_memory()

    prompt = build_sales_campaign_prompt(task, memory)

    response = generate_ai_response(
        prompt,
        "You are an expert B2B and small-business sales strategist."
    )

    parsed = parse_json_response(response)
    if parsed is not None:
        return parsed
    return {
            "error": "Invalid JSON returned by Sales Engine",
            "raw_response": response
        }