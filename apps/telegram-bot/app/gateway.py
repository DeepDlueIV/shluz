import httpx


class GatewayClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')

    async def chat(self, token: str, messages: list[dict], model: str):
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f'{self.base_url}/v1/chat/completions',
                headers={'Authorization': f'Bearer {token}'},
                json={'model': model, 'messages': messages},
            )
            response.raise_for_status()
            return response.json()
