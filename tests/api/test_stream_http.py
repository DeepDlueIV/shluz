"""Настоящий HTTP: префикс приходит до окончания; disconnect закрывает генератор."""

import asyncio
import socket
import threading
import time

import httpx
import uvicorn
from sqlalchemy import select

from app.db.models import UsageEvent
from app.factory import create_app
from app.providers.base import StreamChunk
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry


def test_live_http_stream_disconnect_finalizes_without_replay(test_settings):
    closed = threading.Event()
    calls = []

    class Slow(MockProvider):
        async def chat_stream(self, request):
            calls.append(request)
            try:
                yield StreamChunk(content="FIRST")
                await asyncio.sleep(5)
                yield StreamChunk(content="LAST", done=True)
            finally:
                closed.set()

    app = create_app(test_settings, registry=ProviderRegistry([Slow()]))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        began = time.monotonic()
        with httpx.Client(timeout=3) as http, http.stream(
            "POST",
            f"http://127.0.0.1:{port}/v1/chat/completions",
            headers={"Authorization": "Bearer test-api-token"},
            json={
                "model": "mock-chat",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200
            assert any("FIRST" in line for line in response.iter_lines())
            assert time.monotonic() - began < 3, "Proxy buffered until completion"
        assert closed.wait(3), "Provider connection was left running after disconnect"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with app.state.database.session() as session:
                event = session.scalar(select(UsageEvent))
                if event.status == "interrupted":
                    break
            time.sleep(0.02)
        assert event.status == "interrupted" and event.usage_known is False
        assert len(calls) == 1
    finally:
        server.should_exit = True
        thread.join(timeout=5)
