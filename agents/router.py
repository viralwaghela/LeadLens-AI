AGENT_REGISTRY = {}


def register_agent(name, handler):
    AGENT_REGISTRY[name.lower()] = handler


def get_agent(name):
    return AGENT_REGISTRY.get(name.lower())


def route_task(task):
    department = task.get("department", "").lower().strip()

    agent = get_agent(department)

    if agent is None:
        return {
            "success": False,
            "department": department,
            "message": f"No agent registered for '{department}'."
        }

    return agent(task)


def available_agents():
    return list(AGENT_REGISTRY.keys())