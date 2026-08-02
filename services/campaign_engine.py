from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from core.memory import load_memory, save_memory, generate_id
from services.memory_engine import compact_context

CHANNEL_MIX = ["Instagram Reel", "Instagram Carousel", "Instagram Story", "Email", "WhatsApp"]


def create_campaign(brief: dict[str, Any]) -> dict[str, Any]:
    memory = load_memory()
    context = compact_context()
    company = context.get("company", {})
    days = max(3, min(int(brief.get("days", 14)), 30))
    audience = brief.get("audience") or company.get("target_audience") or "High-intent local customers"
    objective = brief.get("objective") or "Generate qualified leads"
    offer = brief.get("offer") or "Book a consultation"
    budget = float(brief.get("budget", 0) or 0)
    theme = brief.get("theme") or objective
    start = datetime.now().date()
    calendar = []
    for i in range(days):
        channel = CHANNEL_MIX[i % len(CHANNEL_MIX)]
        hook = f"{theme}: {['problem', 'proof', 'offer', 'education', 'trust'][i % 5].title()} angle"
        calendar.append({
            "day": i + 1,
            "date": (start + timedelta(days=i)).isoformat(),
            "channel": channel,
            "hook": hook,
            "cta": offer,
            "status": "Draft",
        })
    campaign = {
        "id": generate_id("campaigns"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": {
            "name": brief.get("name") or f"{theme} Campaign",
            "objective": objective,
            "audience": audience,
            "offer": offer,
            "budget": budget,
            "days": days,
            "status": "Awaiting Approval",
            "strategy": [
                f"Lead with the cost of inaction for {audience}.",
                "Use proof-led creative before direct promotional content.",
                "Retarget engaged users with a clear time-bound offer.",
                "Review results after the first 3 publishing days before reallocating budget.",
            ],
            "calendar": calendar,
            "captions": [
                f"Still dealing with the same problem? {offer}. Reply 'INFO' and our team will guide you.",
                f"A better outcome starts with the right first step. {offer}.",
                f"What most people get wrong about {theme.lower()}: waiting too long. Take action today.",
            ],
            "creative_prompts": [
                f"Premium social media campaign visual for {company.get('business_name', 'the brand')}, focused on {theme}, modern commercial photography, clear space for headline and CTA",
                f"High-conversion before-and-after concept for {theme}, authentic Indian business context, polished advertising composition",
            ],
            "success_metrics": ["Qualified leads", "Cost per lead", "Booking conversion rate", "Revenue attributed"],
        },
    }
    memory.setdefault("campaigns", []).append(campaign)
    memory.setdefault("approvals", []).append({
        "id": generate_id("approvals"),
        "created_at": campaign["created_at"],
        "data": {"title": f"Approve campaign: {campaign['data']['name']}", "department": "Marketing", "risk_level": "Medium", "status": "Pending", "reference_id": campaign["id"]},
    })
    save_memory(memory)
    return campaign


def update_campaign_status(campaign_id: str, status: str) -> bool:
    memory = load_memory()
    changed = False
    for item in memory.get("campaigns", []):
        if item.get("id") == campaign_id:
            item.setdefault("data", {})["status"] = status
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            changed = True
    if changed:
        save_memory(memory)
    return changed
