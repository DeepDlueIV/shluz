import asyncio
import json

import httpx
import pytest

from telegram_bot.gateway import GatewayClient, GatewayError


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, frames):
        self.frames = frames
        self.closed = False

    async def __aiter__(self):
        for data in self.frames:
            yield data
            await asyncio.sleep(0)

    async def aclose(self):
        self.closed = True


def chunk(text):
    return ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode()


def test_client_stream_updates_before_done_and_only_one_post():
    async def scenario():
        bodies, updates = [], []
        body = ByteStream([chunk("one"), chunk(" two"), b"data: [DONE]\n\n"])

        async def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

        async def update(text):
            updates.append(text)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = GatewayClient("http://gateway", http=http)
            answer = await client.chat("secret", "mock-chat", [], "request", on_delta=update)
        assert answer == "one two" and updates == ["one", "one two"]
        assert len(bodies) == 1 and bodies[0]["stream"] is True
        assert body.closed

    asyncio.run(scenario())


def test_client_refuses_truncated_stream_without_replay():
    async def scenario():
        calls, updates = [], []

        async def handler(request):
            calls.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=ByteStream([chunk("partial")]),
            )

        async def update(text):
            updates.append(text)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = GatewayClient("http://gateway", http=http)
            with pytest.raises(GatewayError) as exc:
                await client.chat("secret", "mock-chat", [], "id", on_delta=update)
            assert exc.value.code == "outcome_unknown"
        assert len(calls) == 1 and updates == ["partial"]

    asyncio.run(scenario())
