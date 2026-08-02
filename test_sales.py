import agents
from agents.router import route_task

task = {
    "title": "Create corporate wellness sales campaign",
    "department": "Sales",
    "priority": "High",
    "reason": "Beyond Pain wants to generate corporate wellness leads."
}

result = route_task(task)

print(result)