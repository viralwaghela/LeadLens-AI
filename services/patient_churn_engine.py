def calculate_churn_risk(patient):
    score = 0
    score += min(patient.get("days_since_visit",0),30)
    score += patient.get("cancelled_sessions",0)*10
    return min(score,100)
