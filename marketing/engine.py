import json

from services.ai import generate_ai_response
from services.json_utils import parse_json_response
from core.memory import load_memory


def build_marketing_campaign_prompt(task, memory):
    return f"""
You are the Marketing Department inside LeadLens AI.

Your job is to create a practical, execution-ready marketing campaign for the business.

Return ONLY valid JSON.
Do NOT wrap JSON in markdown.
Do NOT explain anything outside the JSON.

Business Memory:
{json.dumps(memory, indent=2)}

Marketing Task:
{json.dumps(task, indent=2)}

Return exactly this JSON structure:

{{
    "campaign_name": "Campaign name",
    "strategy": {{
        "objective": "Campaign objective",
        "target_audience": "Target audience",
        "pain_points": [
            "Pain point 1",
            "Pain point 2"
        ],
        "positioning": "Positioning statement",
        "offer": "Offer idea",
        "primary_cta": "Primary CTA"
    }},
    "content_calendar": [
        {{
            "day": "Day 1",
            "platform": "Instagram",
            "content_type": "Reel",
            "topic": "Content topic",
            "goal": "Awareness"
        }}
    ],
    "reel_ideas": [
        {{
            "title": "Reel title",
            "hook": "Opening hook",
            "script": "Short reel script",
            "cta": "Call to action"
        }}
    ],
    "captions": [
        {{
            "platform": "Instagram",
            "caption": "Caption text"
        }}
    ],
    "hashtags": [
        "#hashtag1",
        "#hashtag2"
    ],
    "image_prompts": [
        {{
            "title": "Image prompt title",
            "prompt": "Detailed image generation prompt"
        }}
    ],
    "meta_ads": [
        {{
            "primary_text": "Ad primary text",
            "headline": "Ad headline",
            "description": "Ad description",
            "cta": "Ad CTA"
        }}
    ],
    "kpis": {{
        "expected_reach": "Estimated reach",
        "expected_leads": "Estimated leads",
        "success_metric": "Main KPI"
    }}
}}
"""


def generate_marketing_campaign(task):
    memory = load_memory()

    prompt = build_marketing_campaign_prompt(task, memory)

    response = generate_ai_response(
        prompt,
        "You are an expert marketing strategist for small businesses."
    )

    parsed = parse_json_response(response)
    if parsed is not None:
        return parsed
    return {
            "error": "Invalid JSON returned by Marketing Engine",
            "raw_response": response
        }