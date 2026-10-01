"""Shluz Telegram bot entrypoint."""

import os

import httpx


async def send_to_shluz(messages: list[dict[str, str]]) -> str:
    """Forward a chat request to the gateway."""
    url = os.environ.get("SHLUZ_URL", "http://shluz:8000/v1/chat/completions")
    token = os.environ.get("SHLUZ_TOKEN", "")
    model = os.environ.get("SHLUZ_MODEL", "mock")

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"model": model, "messages": messages},
        )
        response.raise_for_status()
        data = response.json()

    return data["choices"][0]["message"]["content"]


if __name__ == "__main__":
    print("Telegram bot scaffold. Configure Telegram framework before production.")
