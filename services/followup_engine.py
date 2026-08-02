
def prepare_followups(patients):
    return [
        {
            "patient": p["name"],
            "message": f"Hello {p['name']}, we noticed you haven't visited recently. Would you like to schedule your next session?"
        }
        for p in patients
    ]
