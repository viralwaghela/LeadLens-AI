import agents
from agents.router import route_task

task = {
    "title": "Hire an AI Automation Specialist",
    "department": "HR",
    "priority": "High",
    "reason": "LeadLens needs an AI Automation Specialist to accelerate product development."
}

result = route_task(task)

print(result)