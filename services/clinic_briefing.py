
from datetime import datetime
def generate_brief(metrics):
    return {
        "date": str(datetime.now().date()),
        "appointments": metrics.get("appointments",0),
        "revenue": metrics.get("revenue",0),
        "message":"Focus on inactive patients and therapist utilization."
    }
