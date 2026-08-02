
def generate_brief(data):
    return {
        "appointments_today": data.get("appointments",0),
        "expected_revenue": data.get("revenue",0),
        "patient_risks": data.get("patient_risks",[]),
        "recommendations": []
    }
