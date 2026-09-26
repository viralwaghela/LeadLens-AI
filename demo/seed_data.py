"""Deterministic, fully synthetic dataset for the public demo tenant.

Pure Python — no database, no app imports, no network. `build_dataset()`
returns the same data every time for a given (anchor date, SEED), so the
demo can be reset to an identical state, and tests can assert exact
volumes.

Everything here is invented:
  * names are drawn from the two fixed lists below;
  * phone numbers are all in the North-American fictional block
    +1-555-0100 .. +1-555-0199 (reserved for fiction — they can never reach
    a real person);
  * emails are on example.com / *.example.com (RFC 2606 reserved);
  * the clinic, its website, and every company are made up.

Dates are relative to the `anchor` date (normally "today" when the demo is
seeded), so a freshly reset demo always looks current: patients seen in the
last few weeks, appointments today and over the next fortnight, payments this
month. A demo that has not been reset for a long time will look older — reset
it (scripts/reset_demo.py) to re-anchor.

Cross-references between records use `*_ref` indexes into the lists below
(e.g. an appointment's `patient_ref`); the seeder maps them to the real ids
the CRM assigns on insert.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

SEED = 20260926

DEMO_CLINIC_NAME = "LeadLens Demo Clinic"

FIRST_NAMES = [
    "Aiden", "Bella", "Carlos", "Diya", "Ethan", "Fatima", "Gabriel", "Hana", "Ishaan", "Julia",
    "Kabir", "Layla", "Mateo", "Nina", "Omar", "Priya", "Quinn", "Rhea", "Samir", "Tara",
    "Umar", "Vera", "Wyatt", "Xena", "Yusuf", "Zara", "Anaya", "Bruno", "Chloe", "Dev",
    "Elena", "Farhan", "Grace", "Hugo", "Isha", "Jonas", "Kiara", "Liam", "Maya", "Noah",
    "Olivia", "Pranav", "Rohan", "Sofia", "Tanvi", "Uma", "Victor", "Wren", "Yara", "Zane",
    "Aria", "Boris", "Cleo", "Dario", "Esha", "Felix", "Gia", "Hari", "Ines", "Jai",
]
LAST_NAMES = [
    "Abbott", "Bajwa", "Castillo", "Desai", "Ellison", "Fernandes", "Gill", "Hartley", "Iyer", "Jansen",
    "Kapoor", "Lindqvist", "Malhotra", "Novak", "Okafor", "Pillai", "Quintero", "Rao", "Sandhu", "Thornton",
    "Underhill", "Varma", "Whitfield", "Xu", "Yadav", "Zielinski", "Ahluwalia", "Bhatt", "Corbett", "Dhillon",
    "Emerson", "Faulkner", "Grewal", "Holloway", "Ingram", "Joshi", "Kessler", "Lobo", "Menon", "Nair",
    "Oliveira", "Prasad", "Qureshi", "Reddy", "Shetty", "Trivedi", "Ueda", "Vasquez", "Walia", "Yardley",
    "Zaveri", "Arora", "Banerjee", "Chopra", "Dutta", "Everett", "Fischer", "Gupta", "Hendricks", "Ivanov",
]

SERVICES = ["Physiotherapy Session", "Pilates 1:1", "Post-Op Rehab", "Sports Massage", "Follow-up Review"]
APPOINTMENT_TIMES = ["09:00", "10:00", "11:30", "14:00", "15:30", "17:00"]
PAYMENT_METHODS = ["UPI", "Card", "Cash", "Bank transfer"]

PACKAGE_TEMPLATES = [
    {"name": "Back Pain Recovery", "total_sessions": 8, "price": 12000,
     "description": "Assessment, manual therapy and a guided home programme for lower-back pain.", "status": "Active"},
    {"name": "Post-Surgery Rehab", "total_sessions": 12, "price": 17500,
     "description": "Structured post-operative rehabilitation with progress checkpoints.", "status": "Active"},
    {"name": "Pilates Foundation", "total_sessions": 10, "price": 15000,
     "description": "One-to-one clinical Pilates to build core stability and mobility.", "status": "Active"},
    {"name": "Sports Injury Package", "total_sessions": 6, "price": 9000,
     "description": "Targeted treatment and return-to-play planning for sports injuries.", "status": "Active"},
]

THERAPISTS = [
    {"name": "Dr. Priya Nair", "status": "Active", "weekly_capacity": 34},
    {"name": "Rohan Mehra", "status": "Active", "weekly_capacity": 30},
    {"name": "Sara Kim", "status": "Active", "weekly_capacity": 28},
    {"name": "Dr. Leo Martins", "status": "Active", "weekly_capacity": 32},
]

PROGRESS_SUMMARIES = [
    "Range of motion improving; pain reduced during daily activity.",
    "Good adherence to the home programme; strength gains noted.",
    "Pain plateaued this week; adjusted loading and added mobility work.",
    "Able to return to light work duties; continuing graded exercise.",
    "Stiffness in the morning is easing; posture cues are working well.",
]
PROGRESS_STATUSES = ["Improving", "Improving", "Stable", "Plateau"]
NEXT_STEPS = [
    "Continue current plan and review in one week.",
    "Progress to loaded exercises next session.",
    "Re-assess pain score and consider taping.",
    "Discuss renewal of the treatment package.",
]
LEAD_MESSAGES = [
    "Looking for help with lower-back pain that has lasted a few weeks.",
    "Interested in Pilates classes — do you have evening slots?",
    "Recovering from a knee operation and need a rehab plan.",
    "Enquiring about a corporate wellness session for our team.",
    "Sports injury from weekend football, would like an assessment.",
    "Neck and shoulder stiffness from desk work.",
]
CORPORATE_CLIENTS = [
    ("Harbor Point Software", "Anika Rao", "Contacted"),
    ("Blue Kite Logistics", "Dylan Cross", "Proposal Sent"),
    ("Maple & Finch Studios", "Meera Sethi", "New"),
    ("Ironbridge Analytics", "Jonas Weber", "Won"),
]


@dataclass
class DemoDataset:
    anchor: date
    company: dict[str, Any]
    settings: dict[str, Any]
    therapists: list[dict[str, Any]]
    package_templates: list[dict[str, Any]]
    patients: list[dict[str, Any]]
    packages: list[dict[str, Any]]
    appointments: list[dict[str, Any]]
    payments: list[dict[str, Any]]
    progress_notes: list[dict[str, Any]]
    leads: list[dict[str, Any]]
    corporate_clients: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    extra_approvals: list[dict[str, Any]]
    daily_logs: list[str]
    reports: list[dict[str, Any]]
    workflow_actions: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    council_sessions: list[dict[str, Any]]
    counts: dict[str, int] = field(default_factory=dict)

    def summarize(self) -> dict[str, int]:
        return {
            "therapists": len(self.therapists),
            "package_templates": len(self.package_templates),
            "patients": len(self.patients),
            "packages": len(self.packages),
            "appointments": len(self.appointments),
            "payments": len(self.payments),
            "progress_notes": len(self.progress_notes),
            "leads": len(self.leads),
            "corporate_clients": len(self.corporate_clients),
            "tasks": len(self.tasks),
            "decisions": len(self.decisions),
            "approvals_and_workflows": len(self.workflow_actions) + len(self.extra_approvals),
            "daily_logs": len(self.daily_logs),
            "reports": len(self.reports),
            "recommendations": len(self.recommendations),
            "council_sessions": len(self.council_sessions),
        }


def _iso(day: date) -> str:
    return day.isoformat()


class _Phones:
    """Hands out unique numbers from the reserved fictional block."""

    def __init__(self) -> None:
        self._next = 0

    def take(self) -> str:
        if self._next >= 100:
            raise RuntimeError("ran out of fictional phone numbers")
        number = f"+1-555-01{self._next:02d}"
        self._next += 1
        return number


def _email(first: str, last: str, domain: str = "example.com") -> str:
    return f"{first}.{last}@{domain}".lower()


def build_dataset(anchor: date | None = None, seed: int = SEED) -> DemoDataset:
    anchor = anchor or date.today()
    rng = random.Random(seed)
    phones = _Phones()

    firsts = FIRST_NAMES[:]
    lasts = LAST_NAMES[:]
    rng.shuffle(firsts)
    rng.shuffle(lasts)

    # ---- therapists & templates -------------------------------------------------
    therapists = [dict(t) for t in THERAPISTS]
    templates = [dict(t) for t in PACKAGE_TEMPLATES]

    # ---- patients (48) ----------------------------------------------------------
    statuses = ["Active"] * 30 + ["Renewal Due"] * 6 + ["Inactive"] * 12
    rng.shuffle(statuses)
    patients: list[dict[str, Any]] = []
    for i, status in enumerate(statuses):
        first, last = firsts[i], lasts[i]
        if status == "Active":
            last_visit, remaining = anchor - timedelta(days=rng.randint(1, 21)), rng.randint(3, 10)
        elif status == "Renewal Due":
            last_visit, remaining = anchor - timedelta(days=rng.randint(2, 14)), rng.randint(0, 2)
        else:
            last_visit, remaining = anchor - timedelta(days=rng.randint(50, 160)), rng.randint(0, 4)
        age = rng.randint(19, 74)
        dob = date(anchor.year - age, rng.randint(1, 12), rng.randint(1, 28))
        patients.append({
            "name": f"{first} {last}",
            "email": _email(first, last),
            "phone": phones.take(),
            "status": status,
            "last_visit": _iso(last_visit),
            "date_of_birth": _iso(dob),
            "sessions_remaining": remaining,
            "consent_to_contact": rng.random() < 0.88,
        })
    # A few birthdays landing in the next week, so the birthday automation has something to show.
    for offset, idx in zip((2, 4, 6), (0, 1, 2)):
        target = anchor + timedelta(days=offset)
        patients[idx]["date_of_birth"] = _iso(date(anchor.year - rng.randint(25, 60), target.month, target.day))
        patients[idx]["consent_to_contact"] = True

    active_refs = [i for i, p in enumerate(patients) if p["status"] == "Active"]
    renewal_refs = [i for i, p in enumerate(patients) if p["status"] == "Renewal Due"]
    inactive_refs = [i for i, p in enumerate(patients) if p["status"] == "Inactive"]

    # ---- packages ---------------------------------------------------------------
    packages: list[dict[str, Any]] = []
    recent_starts = 0
    for ref in active_refs + renewal_refs:
        template = rng.choice(templates)
        total = template["total_sessions"]
        remaining = min(patients[ref]["sessions_remaining"], total)
        patients[ref]["sessions_remaining"] = remaining
        if recent_starts < 14:
            start = anchor - timedelta(days=rng.randint(0, 20))
            recent_starts += 1
        else:
            start = anchor - timedelta(days=rng.randint(21, 75))
        packages.append({
            "patient_ref": ref, "name": template["name"], "total_sessions": total,
            "sessions_remaining": remaining, "start_date": _iso(start),
            "expiry_date": _iso(start + timedelta(days=120)), "status": "Active",
            "_price": template["price"],
        })
    for ref in inactive_refs[:6]:
        template = rng.choice(templates)
        start = anchor - timedelta(days=rng.randint(100, 170))
        packages.append({
            "patient_ref": ref, "name": template["name"], "total_sessions": template["total_sessions"],
            "sessions_remaining": 0, "start_date": _iso(start),
            "expiry_date": _iso(start + timedelta(days=90)), "status": "Completed",
            "_price": template["price"],
        })
        patients[ref]["sessions_remaining"] = 0

    # ---- payments (one per package) --------------------------------------------
    payments: list[dict[str, Any]] = []
    for n, package in enumerate(packages, start=1):
        status = "Paid"
        if n in (5, 17, 29):
            status = "Pending"
        elif n == 11:
            status = "Refunded"
        payments.append({
            "patient_ref": package["patient_ref"], "package_ref": n - 1,
            "amount": float(package["_price"]), "payment_date": package["start_date"],
            "method": rng.choice(PAYMENT_METHODS), "reference": f"DEMO-{n:05d}", "status": status,
        })

    # ---- appointments (72) -------------------------------------------------------
    appointments: list[dict[str, Any]] = []
    therapist_cycle = 0

    def add_appointment(ref: int, day: date, status: str, time: str | None = None) -> None:
        nonlocal therapist_cycle
        appointments.append({
            "patient_ref": ref, "therapist_ref": therapist_cycle % len(therapists),
            "appointment_date": _iso(day), "appointment_time": time or rng.choice(APPOINTMENT_TIMES),
            "status": status, "service": rng.choice(SERVICES),
        })
        therapist_cycle += 1

    completed_by_patient: dict[int, list[date]] = {}
    for position, ref in enumerate(active_refs):
        count = 2 if position < 18 else 1
        for _ in range(count):
            day = anchor - timedelta(days=rng.randint(1, 38))
            completed_by_patient.setdefault(ref, []).append(day)
            add_appointment(ref, day, "Completed")
    for ref in rng.sample(active_refs, 4):
        add_appointment(ref, anchor - timedelta(days=rng.randint(2, 20)), "No-show")
    for ref in rng.sample(renewal_refs, 3):
        add_appointment(ref, anchor - timedelta(days=rng.randint(3, 25)), "Cancelled")
    today_refs = rng.sample(active_refs, 3)
    for ref, time in zip(today_refs, ("09:00", "11:30", "16:00")):
        add_appointment(ref, anchor, "Scheduled", time)
    for ref in rng.sample(active_refs + renewal_refs, 14):
        add_appointment(ref, anchor + timedelta(days=rng.randint(1, 10)), "Scheduled")

    # ---- progress notes (30) ----------------------------------------------------
    progress_notes: list[dict[str, Any]] = []
    for ref in rng.sample(list(completed_by_patient), 30):
        visit = max(completed_by_patient[ref])
        progress_notes.append({
            "patient_ref": ref, "therapist_ref": rng.randrange(len(therapists)),
            "visit_date": _iso(visit), "progress_summary": rng.choice(PROGRESS_SUMMARIES),
            "pain_score": rng.randint(2, 7), "progress_status": rng.choice(PROGRESS_STATUSES),
            "next_step": rng.choice(NEXT_STEPS),
        })

    # ---- leads (20) -------------------------------------------------------------
    lead_statuses = ["New"] * 8 + ["Contacted"] * 5 + ["Booked"] * 3 + ["Converted"] * 2 + ["Lost"] * 2
    lead_sources = ["Website", "Phone", "Walk-in", "Referral", "Other"]
    leads: list[dict[str, Any]] = []
    for i, status in enumerate(lead_statuses):
        first, last = firsts[(48 + i) % 60], lasts[(i + 17) % 60]
        leads.append({
            "name": f"{first} {last}", "phone": phones.take(), "email": _email(first, last),
            "message": rng.choice(LEAD_MESSAGES), "source": lead_sources[i % len(lead_sources)], "status": status,
        })

    # ---- corporate clients (4) --------------------------------------------------
    corporate_clients: list[dict[str, Any]] = []
    for company, contact, status in CORPORATE_CLIENTS:
        slug = company.lower().replace("&", "and").replace(" ", "")
        corporate_clients.append({
            "company_name": company, "contact_name": contact, "phone": phones.take(),
            "email": f"people@{slug}.example.com", "notes": "Interested in an on-site wellness programme.",
            "status": status,
        })

    # ---- company profile & settings --------------------------------------------
    revenue = sum(p["amount"] for p in payments if p["status"] == "Paid" and p["payment_date"] >= _iso(anchor - timedelta(days=30)))
    company = {
        "business_name": DEMO_CLINIC_NAME, "industry": "Physiotherapy & Pilates", "location": "Demo City",
        "website": "https://demo.leadlens.invalid", "google_review_link": "https://demo.leadlens.invalid/review",
        "monthly_revenue": 420000, "monthly_expenses": 265000, "target_monthly_revenue": 500000,
        "is_demo": True,
    }
    settings = {k: v for k, v in company.items() if k != "is_demo"}
    _ = revenue  # (the dashboard derives its own revenue from the payments above)

    # ---- legacy sections --------------------------------------------------------
    tasks = [
        {"title": "Follow up with patients due for renewal", "department": "Operations", "priority": "High"},
        {"title": "Review this week's therapist schedule for gaps", "department": "Operations", "priority": "Medium"},
        {"title": "Confirm corporate wellness workshop details", "department": "Sales", "priority": "Medium"},
        {"title": "Reconcile pending payments", "department": "Finance", "priority": "High"},
        {"title": "Publish two patient-education posts", "department": "Marketing", "priority": "Low"},
        {"title": "Call back new website enquiries", "department": "Sales", "priority": "High"},
        {"title": "Restock treatment-room supplies", "department": "Operations", "priority": "Low"},
        {"title": "Prepare monthly clinic performance summary", "department": "Management", "priority": "Medium"},
    ]
    decisions = [
        {"title": "Add a Saturday morning Pilates block", "reason": "Weekday evening slots are consistently full.", "impact": "Medium"},
        {"title": "Offer renewal reminders two sessions before a package ends", "reason": "Renewals drop when reminders arrive late.", "impact": "High"},
        {"title": "Pause paid ads for two weeks", "reason": "Lead volume is healthy from referrals.", "impact": "Low"},
        {"title": "Introduce a corporate wellness package", "reason": "Four employers have asked for on-site sessions.", "impact": "High"},
    ]
    extra_approvals = [
        {"title": "Approve revised Saturday Pilates schedule", "department": "Operations", "risk_level": "Low"},
        {"title": "Approve corporate wellness pricing sheet", "department": "Sales", "risk_level": "Medium"},
    ]
    daily_logs = [
        "Reviewed the day's schedule; three appointments confirmed for this morning.",
        "Two patients flagged for renewal conversations this week.",
        "New website enquiry received and assigned for follow-up.",
        "Therapist capacity is at 78% for the coming fortnight.",
        "Reminder messages prepared for approval; nothing sent without sign-off.",
        "Pending payments reviewed; three follow-ups queued.",
        "Weekly revenue is tracking slightly ahead of plan.",
        "Corporate wellness proposal sent to Blue Kite Logistics.",
        "Progress notes reviewed; most patients trending towards lower pain scores.",
        "Inactive-patient list refreshed; twelve patients eligible for a check-in.",
        "Google review requests prepared for recent completed sessions.",
        "Capacity looks tight on Thursday evenings.",
        "Birthday wishes prepared for three patients this week.",
        "Missed-appointment follow-ups drafted for approval.",
        "Monthly performance summary drafted for the owner.",
    ]
    reports = [
        {"type": "Opportunity", "title": "12 inactive patients could be re-engaged", "message": "Patients unseen for 50+ days with consent to contact are ready for a check-in.", "department": "Operations", "check": "inactive_patient_recovery"},
        {"type": "Risk", "title": "Thursday evening capacity is tight", "message": "Booked sessions are close to therapist capacity on Thursday evenings.", "department": "Operations", "check": "capacity_alert"},
        {"type": "Info", "title": "Revenue is tracking above the 30-day average", "message": "Paid packages this month are slightly ahead of the trailing average.", "department": "Finance", "check": "revenue_monitoring"},
        {"type": "Opportunity", "title": "Three renewals due this week", "message": "Patients with two or fewer sessions remaining should be offered a renewal.", "department": "Sales", "check": "revenue_monitoring"},
        {"type": "Risk", "title": "Low bookings tomorrow", "message": "Only two appointments are scheduled for tomorrow — consider a fill-the-gap message.", "department": "Operations", "check": "low_booking_alert"},
        {"type": "Opportunity", "title": "New lead needs a call back", "message": "A website enquiry about post-surgery rehab has not been contacted yet.", "department": "Sales", "check": "lead_qualification_alert"},
        {"type": "Info", "title": "Monthly business review ready", "message": "Patient retention, revenue, and utilisation summarised for the month.", "department": "Management", "check": "monthly_business_review"},
        {"type": "Opportunity", "title": "Waiting-list patients can be offered a slot", "message": "A cancelled appointment created an opening that matches a waiting-list request.", "department": "Operations", "check": "waiting_list_automation"},
        {"type": "Info", "title": "Therapist schedule optimisation suggestion", "message": "Moving two follow-ups earlier would remove a mid-day gap for one therapist.", "department": "Operations", "check": "therapist_schedule_optimizer"},
        {"type": "Opportunity", "title": "Corporate lead moving to proposal", "message": "Ironbridge Analytics converted; Blue Kite Logistics has a proposal out.", "department": "Sales", "check": "corporate_lead_automation"},
    ]

    # ---- queued workflow actions (the approval queue) ---------------------------
    consenting = lambda refs: [r for r in refs if patients[r]["consent_to_contact"]]  # noqa: E731
    workflow_actions: list[dict[str, Any]] = []

    def add_action(check: str, ref: int, title_prefix: str, body: str, impact: str, decision: str | None = None) -> None:
        patient = patients[ref]
        workflow_actions.append({
            "check": check, "patient_ref": ref, "provider": "whatsapp", "action": "send_text",
            "payload": {"to": patient["phone"], "body": body.format(first=patient["name"].split()[0])},
            "title": f"{title_prefix} — {patient['name']}", "impact": impact, "decision": decision,
        })

    for ref in consenting(inactive_refs)[:4]:
        add_action("inactive_patient_recovery", ref, "Inactive patient check-in",
                   "Hi {first}, it's been a while since your last session at LeadLens Demo Clinic. Would you like to book a check-in?",
                   "Re-engage a lapsed patient")
    for ref in consenting(today_refs)[:3]:
        add_action("appointment_reminder", ref, "Appointment reminder (2hr)",
                   "Hi {first}, a reminder of your appointment today at LeadLens Demo Clinic. Reply to confirm.",
                   "Reduce no-shows")
    recent_completed = [r for r, days in completed_by_patient.items() if max(days) >= anchor - timedelta(days=7) and patients[r]["consent_to_contact"]]
    for ref in recent_completed[:2]:
        add_action("google_review_automation", ref, "Review request",
                   "Hi {first}, thank you for visiting LeadLens Demo Clinic! We'd love your feedback on Google.",
                   "Grow public reviews")
    for ref in (0, 1):
        add_action("birthday_automation", ref, "Birthday wishes",
                   "Happy birthday, {first}! Everyone at LeadLens Demo Clinic wishes you a healthy year ahead.",
                   "Strengthen patient relationships")
    no_show_refs = [a["patient_ref"] for a in appointments if a["status"] == "No-show" and patients[a["patient_ref"]]["consent_to_contact"]]
    for ref in no_show_refs[:1]:
        add_action("missed_appointment_recovery", ref, "Missed appointment follow-up",
                   "Hi {first}, we missed you at your appointment. Would you like to reschedule?",
                   "Recover a missed appointment")
    if len(workflow_actions) > 5:
        workflow_actions[5]["decision"] = "Approved"
    if len(workflow_actions) > 9:
        workflow_actions[9]["decision"] = "Rejected"

    # ---- Jarvis learning memory & council ---------------------------------------
    recommendations = [
        {"question": "How can we improve rebooking for lapsed patients?",
         "recommendation": "Send a friendly WhatsApp check-in to patients unseen for 50+ days, after owner approval.",
         "agents": ["Sales Agent", "Marketing Agent"], "tags": ["rebooking", "retention"],
         "outcome": {"result": "successful", "action_taken": "Sent 12 approved check-ins",
                     "metrics": {"contacted": 12, "rebooked": 5}, "notes": "About four in ten responded."}},
        {"question": "How do we fill weekday afternoon gaps?",
         "recommendation": "Offer a discounted 30-minute follow-up slot between 14:00 and 16:00.",
         "agents": ["Operations Agent"], "tags": ["capacity"],
         "outcome": {"result": "partial", "action_taken": "Trialled for one week",
                     "metrics": {"slots_filled": 3}, "notes": "Helpful, but demand was uneven."}},
        {"question": "Should we add a corporate wellness package?",
         "recommendation": "Yes — package a monthly on-site session for teams of 10 to 25 people.",
         "agents": ["Sales Agent", "Finance Agent"], "tags": ["corporate", "growth"], "outcome": None},
    ]
    agent_labels = ["Sales Agent", "Marketing Agent", "Finance Agent", "Operations Agent", "Clinical Agent"]

    def council(index: int, question: str, decision: str, day_offset: int) -> dict[str, Any]:
        created = anchor - timedelta(days=day_offset)
        return {
            "id": f"COUNCIL-DEMO{index:03d}", "created_at": f"{_iso(created)}T10:00:00", "question": question,
            "agents": [
                {"agent": label, "finding": f"{label}: the supplied clinic data supports this direction.",
                 "recommendation": "Proceed in small steps and measure the result.", "risk": "Low"}
                for label in agent_labels
            ],
            "synthesis": {
                "decision": decision,
                "learning_used": "Earlier check-in campaigns re-engaged roughly four in ten lapsed patients.",
                "management_rule": "Nothing external is sent without owner approval.",
                "supporting_actions": ["Prepare the message for approval", "Review results in one week"],
            },
        }

    council_sessions = [
        council(1, "Which patients should we contact this week?", "Prioritise renewals due, then lapsed patients with consent.", 6),
        council(2, "How should we use next month's marketing budget?", "Favour referrals and a corporate wellness offer over paid ads.", 13),
        council(3, "Is our therapist capacity sufficient for the next fortnight?", "Capacity is adequate except Thursday evenings; consider one extra slot.", 20),
    ]

    dataset = DemoDataset(
        anchor=anchor, company=company, settings=settings, therapists=therapists,
        package_templates=templates, patients=patients, packages=packages, appointments=appointments,
        payments=payments, progress_notes=progress_notes, leads=leads, corporate_clients=corporate_clients,
        tasks=tasks, decisions=decisions, extra_approvals=extra_approvals, daily_logs=daily_logs,
        reports=reports, workflow_actions=workflow_actions, recommendations=recommendations,
        council_sessions=council_sessions,
    )
    dataset.counts = dataset.summarize()
    return dataset
