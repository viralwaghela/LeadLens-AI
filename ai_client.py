import os
import requests


API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openai/gpt-4o-mini"


def generate_ai_response(prompt, system_message):
    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        return "API key not found. Please set OPENROUTER_API_KEY."

    try:
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.6
            }
        )

        data = response.json()

        if response.status_code != 200:
            return f"API Error: {data}"

        return data["choices"][0]["message"]["content"]

    except Exception as error:
        return f"Something went wrong: {error}"