"""Read-only specialist consultation and Chief-of-Staff synthesis."""
from __future__ import annotations

from datetime import datetime
import json
import os
from typing import Any

from services.ai import generate_ai_response
from services.agent_router import determine_agents
from services.jarvis_context import build_jarvis_context
from services.jarvis_tools import run_read_only_tool


AGENT_SPECS: dict[str, dict[str, Any]] = {
    "sales": {
        "label": "Sales Agent",
        "focus": "pipeline, corporate demand, conversion and revenue growth",
        "tools": ["revenue_summary", "lead_pipeline", "management_work"],
    },
    "marketing": {
        "label": "Marketing Agent",
        "focus": "demand, channels, campaign economics and lead generation",
        "tools": ["business_snapshot", "lead_pipeline", "management_work"],
    },
    "finance": {
        "label": "Finance Agent",
        "focus": "profitability, affordability, budget risk and return thresholds",
        "tools": ["revenue_summary", "management_work"],
    },
    "operations": {
        "label": "Operations Agent",
        "focus": "capacity, appointment flow, delivery and operational constraints",
        "tools": ["therapist_capacity", "management_work"],
    },
    "hr": {
        "label": "HR Agent",
        "focus": "workload, hiring justification, role design and people risk",
        "tools": ["business_snapshot", "therapist_capacity", "management_work"],
    },
    "customer_success": {
        "label": "Customer Success Agent",
        "focus": "patient inactivity, renewals, retention and follow-up",
        "tools": ["patient_risks", "management_work", "learning_signals"],
    },
    "analytics": {
        "label": "Analytics Agent",
        "focus": "cross-functional trends, missing measurements and evidence quality",
        "tools": [
            "business_snapshot",
            "patient_risks",
            "therapist_capacity",
            "lead_pipeline",
            "management_work",
        ],
    },
}


SPECIALIST_SYSTEM_PROMPT = """
You are a specialist member of the LeadLens AI leadership team.
Analyse only the scoped read-only evidence supplied to you.

Return concise Markdown with:
1. Finding
2. Evidence
3. Risk or limitation
4. Recommended next step

Label inferences. Say when information is unavailable. Do not claim that you
contacted anyone, changed a record, scheduled anything or executed an action.
Do not request or reveal patient identities, contact details or clinical notes.
""".strip()


SYNTHESIS_SYSTEM_PROMPT = """
You are Jarvis, the calm and decisive AI Chief of Staff for a healthcare
business. Synthesize the specialist consultations into one management answer.

Rules:
- Answer the owner's question directly.
- Name the specialists consulted.
- Distinguish confirmed facts, inferences and unavailable information.
- Resolve conflicting advice and give no more than three priorities unless the
  owner asks for a different number.
- Give an owner, measurable next step and review point for each priority.
- Ask for approval before any message, calendar change, spending, workflow
  execution or business-record change.
- Never claim an external action occurred. Every consultation in this request
  is read-only.
""".strip()


def _fallback_consultation(
    agent: str,
    tool_results: dict[str, Any],
) -> str:
    spec = AGENT_SPECS[agent]
    sources = sorted({
        source
        for result in tool_results.values()
        for source in (
            result.get("sources", [])
            or ([result["source"]] if result.get("source") else [])
        )
    })
    return (
        f"### {spec['label']}\n\n"
        f"**Finding:** Reviewed {spec['focus']} using the available records.\n\n"
        f"**Evidence:** {', '.join(sources) or 'No source was available'}.\n\n"
        "**Risk or limitation:** Live specialist reasoning was unavailable; "
        "no facts were invented.\n\n"
        "**Recommended next step:** Review the available evidence with a "
        "named owner and measurable checkpoint."
    )


def consult_specialist(
    agent: str,
    question: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    spec = AGENT_SPECS[agent]
    tool_results = {
        tool_name: run_read_only_tool(tool_name, context)
        for tool_name in spec["tools"]
    }
    prompt = f"""
OWNER QUESTION
{question}

YOUR ROLE
{spec['label']}: {spec['focus']}

SCOPED READ-ONLY EVIDENCE
{json.dumps(tool_results, ensure_ascii=False, indent=2, default=str)}
""".strip()
    fast_model = os.getenv("OPENAI_FAST_MODEL", "").strip() or None
    error_detail = None
    try:
        answer = generate_ai_response(
            prompt,
            SPECIALIST_SYSTEM_PROMPT,
            model_name=fast_model,
            max_output_tokens=550,
        )
        status = "completed"
    except RuntimeError as exc:
        error_detail = str(exc)
        answer = _fallback_consultation(agent, tool_results)
        status = "fallback"
    return {
        "agent": agent,
        "label": spec["label"],
        "status": status,
        "tools": list(tool_results),
        "analysis": answer,
        "error": error_detail,
    }


def coordinate_specialists(
    question: str,
    conversation_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_question = str(question or "").strip()
    if not clean_question:
        return {
            "success": False,
            "message": "Please enter a business question.",
            "agents": [],
            "consultations": [],
            "trace": {},
        }

    context = build_jarvis_context(clean_question)
    selected = determine_agents(clean_question)
    consultations = [
        consult_specialist(agent, clean_question, context)
        for agent in selected
    ]
    safe_history = [
        {
            "role": str(item.get("role", ""))[:20],
            "content": str(item.get("content", ""))[:1500],
        }
        for item in (conversation_history or [])[-6:]
        if item.get("role") in {"user", "assistant"}
        and str(item.get("content", "")).strip()
    ]
    synthesis_prompt = f"""
OWNER QUESTION
{clean_question}

RECENT CONVERSATION
{json.dumps(safe_history, ensure_ascii=False, indent=2)}

SPECIALIST CONSULTATIONS
{json.dumps(consultations, ensure_ascii=False, indent=2)}

GROUNDING STATUS
{json.dumps(context.get('model_context', {}).get('source_register', []),
            ensure_ascii=False, indent=2)}
""".strip()
    try:
        message = generate_ai_response(
            synthesis_prompt,
            SYNTHESIS_SYSTEM_PROMPT,
            max_output_tokens=1200,
        )
        synthesis_status = "completed"
        synthesis_error = None
    except RuntimeError as exc:
        synthesis_status = "fallback"
        synthesis_error = str(exc)
        names = ", ".join(item["label"] for item in consultations)
        findings = "\n\n".join(item["analysis"] for item in consultations)
        message = (
            f"> Live Chief-of-Staff synthesis is temporarily unavailable "
            f"({synthesis_error}). "
            f"Read-only consultations completed: **{names}**.\n\n{findings}"
        )

    trace = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "read_only",
        "external_actions_executed": False,
        "selected_agents": [item["label"] for item in consultations],
        "tools_used": {
            item["label"]: item["tools"]
            for item in consultations
        },
        "synthesis_status": synthesis_status,
        "synthesis_error": synthesis_error,
        "consultation_errors": {
            item["label"]: item["error"]
            for item in consultations
            if item.get("error")
        },
    }
    return {
        "success": True,
        "message": message,
        "agents": selected,
        "consultations": consultations,
        "trace": trace,
    }
