import agents
from agents.router import route_task

task = {
    "title": "Create monthly finance analysis and budget report",
    "department": "Finance",
    "priority": "High",
    "reason": "Beyond Pain needs financial clarity before increasing marketing and hiring."
}

result = route_task(task)

print(result)