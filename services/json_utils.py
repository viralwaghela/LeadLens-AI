import json
import re
from typing import Any


def parse_json_response(response: str) -> Any | None:
    """Parse plain or fenced JSON returned by an LLM."""
    if not isinstance(response, str):
        return None

    text = response.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    start = min((pos for pos in (text.find("{"), text.find("[")) if pos != -1), default=-1)
    if start == -1:
        return None

    for end in range(len(text), start, -1):
        candidate = text[start:end].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None
