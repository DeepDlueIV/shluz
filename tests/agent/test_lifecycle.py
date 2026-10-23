"""Реальное локальное HTTP-соединение: Stop/timeout не оставляет upstream живым."""

import asyncio
import socket
import threading
import time
from contextlib import contextmanager
from decimal import Decimal

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Account, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token
from app.factory import create_app
from app.providers.base import ChatResult, StreamChunk
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from tests.agent.test_contract import TOOL

TOKEN = "shluz_" + "z" * 43
HEADERS = {"Authorization": "Bearer " + TOKEN}
PAYLOAD = {
    "model": "mock-chat",
    "messages": [{"role": "user", "content": "hello"}],
    "tools": [TOOL],
    "max_tokens": 64,
}


def app_with(settings, provider):
    app = create_app(settings, registry=ProviderRegistry([provider]))
    with app.state.database.session() as db:
        account = Account(display_name="Isolated agent")
        plan = Plan(code="isolated", name="Isolated", monthly_request_limit=8)
        db.add_all([account, plan])
        db.flush()
        db.add(Subscription(account_id=account.id, plan_id=plan.id, source="harness"))
        create_api_token(
            db, account_id=account.id, name="Temporary test", source="harness", raw_token=TOKEN
        )
    return app


@contextmanager
def running(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        end = time.monotonic() + 5
        while not server.started and time.monotonic() < end:
            time.sleep(0.02)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()


class Slow(MockProvider):
    def __init__(self):
        self.closed = threading.Event()
        self.started = threading.Event()
        self.calls = 0

    async def chat_completion_async(self, request):
        self.calls += 1
        self.started.set()
        try:
            await asyncio.sleep(30)
            return ChatResult("late")
        finally:
            self.closed.set()

    async def chat_stream(self, request):
        self.calls += 1
        self.started.set()
        try:
            yield StreamChunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_cancel",
                        "type": "function",
                        "function": {"name": "noclip_test_echo", "arguments": '{"text":'},
                    }
                ]
            )
            await asyncio.sleep(30)
            yield StreamChunk(done=True)
        finally:
            self.closed.set()


def assert_interrupted_once(app):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        with app.state.database.session() as db:
            rows = list(db.scalars(select(UsageEvent)))
        if len(rows) == 1 and rows[0].status == "interrupted":
            assert rows[0].usage_known is False and rows[0].cost_known is False
            return
        time.sleep(0.02)
    pytest.fail("Request did not settle exactly once as interrupted")


@pytest.mark.parametrize("stream", [False, True])
def test_real_disconnect_cancels_agent_request_without_replay(test_settings, stream):
    provider = Slow()
    app = app_with(test_settings, provider)
    with running(app) as url:
        if stream:
            with (
                httpx.Client(timeout=3) as http,
                http.stream(
                    "POST",
                    url + "/v1/chat/completions",
                    headers=HEADERS,
                    json={**PAYLOAD, "stream": True},
                ) as r,
            ):
                assert r.status_code == 200
                assert any("call_cancel" in line for line in r.iter_lines())
        else:
            with httpx.Client(timeout=0.3) as http, pytest.raises(httpx.ReadTimeout):
                http.post(url + "/v1/chat/completions", headers=HEADERS, json=PAYLOAD)
        assert provider.started.is_set()
        assert provider.closed.wait(3), "Upstream remained alive after client disconnect"
        assert_interrupted_once(app)
        assert provider.calls == 1


@pytest.mark.parametrize("stream", [False, True])
def test_total_and_idle_timeout_finalizes_once(test_settings, stream):
    provider = Slow()
    settings = test_settings.model_copy(
        update={"stream_timeout_seconds": 0.12, "stream_idle_timeout_seconds": 0.05}
    )
    app = app_with(settings, provider)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", headers=HEADERS, json={**PAYLOAD, "stream": stream})
        if stream:
            assert r.status_code == 200 and "stream_interrupted" in r.text
            assert "[DONE]" not in r.text
        else:
            assert r.status_code == 502
        assert provider.closed.wait(1) and provider.calls == 1
        assert_interrupted_once(app)


def test_payg_one_tool_round_charged_only_once(test_settings):
    from app.billing.service import credit, snapshot
    from app.product.service import locked_session
    from app.providers.base import ChatRequest

    app = app_with(test_settings, MockProvider())
    with app.state.database.session() as db:
        aid = db.scalar(select(Account).where(Account.display_name == "Isolated agent")).id
        plan = db.scalar(select(Plan))
        plan.billing_mode = "payg"
        plan.markup_percent = Decimal("20")
    with locked_session(app.state.database) as db:
        credit(db, aid, Decimal("1"), "agent-fixture-credit", "test", "Isolated test")
    usage = app.state.usage_service
    reservation = usage.authorize_request(
        account_id=aid,
        source="harness",
        provider="mock",
        model="mock-chat",
        uses_tools=True,
        input_chars=100,
        requested_output_tokens=64,
    )
    result = MockProvider().chat_completion(ChatRequest("mock-chat", [], options={"tools": [TOOL]}))
    usage.record_success(reservation=reservation, result=result)
    usage.record_success(reservation=reservation, result=result)
    with app.state.database.session() as db:
        assert len(list(db.scalars(select(UsageEvent)))) == 1
        assert Decimal(snapshot(db, aid)["balance_usd"]) == 1
