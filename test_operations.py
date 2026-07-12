import agents
from agents.router import route_task

task = {
    "title": "Generate weekly operations execution package",
    "department": "Operations",
    "priority": "High",
    "reason": "Beyond Pain wants better operational execution."
}

result = route_task(task)

print(result)