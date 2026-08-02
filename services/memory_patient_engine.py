
import pandas as pd
def patient_churn(df):
    risks=[]
    if df is None or df.empty:return []
    for _,r in df.iterrows():
        days=r.get("days_since_visit",0)
        risk=min(95, max(5, days*4))
        risks.append({"patient":r.get("patient"),"risk":risk})
    return sorted(risks,key=lambda x:x["risk"], reverse=True)
