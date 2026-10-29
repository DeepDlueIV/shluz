import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.providers.base import ChatMessage, ChatRequest, ProviderInsufficientBalanceError
from app.providers.venice import VeniceProvider
from app.streaming.sse import SSEError, events


class Fragments(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    async def __aiter__(self):
        # Однобайтные фрагменты разделяют и UTF-8, и границы SSE.
        for byte in self.data:
            yield bytes([byte])

    async def aclose(self):
        self.closed = True


def test_venice_stream_parses_fragmented_unicode_and_actual_accounting():
    async def scenario():
        frames = [
            {"id": "upstream-id", "choices": [{"delta": {"content": "Привет 🚀"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3},
                "cost": {"usd": "0.002", "diem": 0},
            },
        ]
        wire = "".join("data: " + json.dumps(f, ensure_ascii=False) + "\r\n\r\n" for f in frames)
        body = Fragments((wire + "data: [DONE]\r\n\r\n").encode())
        calls = []

        async def handler(req):
            calls.append(req)
            assert req.headers["authorization"] == "Bearer synthetic-key"
            assert json.loads(req.content)["stream_options"]["include_usage"] is True
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VeniceProvider(
                Settings(venice_api_key=SecretStr("synthetic-key")), async_client=client
            )
            chunks = [
                chunk
                async for chunk in provider.chat_stream(
                    ChatRequest("test-model", [ChatMessage("user", "hi")], 32)
                )
            ]
        assert "".join(c.content for c in chunks) == "Привет 🚀"
        assert chunks[-1].done and chunks[-2].cost_usd == Decimal("0.002")
        assert chunks[-2].prompt_tokens == 4 and body.closed and len(calls) == 1

    asyncio.run(scenario())


def test_venice_stream_balance_rejection_is_not_retried():
    async def scenario():
        calls = []

        async def handler(req):
            calls.append(req)
            return httpx.Response(402, json={"private": "upstream-secret"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VeniceProvider(
                Settings(venice_api_key=SecretStr("synthetic-key")), async_client=client
            )
            with pytest.raises(ProviderInsufficientBalanceError) as exc:
                _ = [c async for c in provider.chat_stream(ChatRequest("test-model", [], 32))]
            assert "upstream-secret" not in str(exc.value)
        assert len(calls) == 1

    asyncio.run(scenario())


def test_sse_decoder_rejects_oversized_frame_without_waiting_for_end():
    async def scenario():
        async def source():
            yield b"data: " + b"x" * 200
            raise AssertionError("Parser should already have stopped")

        with pytest.raises(SSEError):
            _ = [f async for f in events(source(), max_frame=64)]

    asyncio.run(scenario())


def test_venice_stream_passes_tool_call_fragments_and_tool_finish():
    async def scenario():
        frames = [
            {
                "id": "venice-2",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {"name": "calc", "arguments": "{\"a\":1}"},
                                }
                            ]
                        }
                    }
                ],
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                "cost": {"usd": "0.002", "diem": 0},
            },
        ]
        wire = "".join("data: " + json.dumps(f, ensure_ascii=False) + "\r\n\r\n" for f in frames)
        body = Fragments((wire + "data: [DONE]\r\n\r\n").encode())

        async def handler(req):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VeniceProvider(
                Settings(venice_api_key=SecretStr("synthetic-key")), async_client=client
            )
            chunks = [
                chunk
                async for chunk in provider.chat_stream(
                    ChatRequest(
                        "venice-model",
                        [ChatMessage("user", "Считай")],
                        tools=({"type": "function", "function": {"name": "calc"}},),
                    )
                )
            ]
        assert chunks[0].tool_calls[0]["function"]["arguments"] == '{"a":1}'
        assert chunks[1].finish_reason == "tool_calls"
        assert chunks[-1].done

    asyncio.run(scenario())
