import httpx


class GatewayClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def chat(self, model: str, messages: list[dict[str, str]]) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"model": model, "messages": messages},
            )
            response.raise_for_status()
            return response.json()
