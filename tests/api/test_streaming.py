"""Поток проходит те же права и учёт, неизвестный результат не успешный ответ."""

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import UsageEvent


def frames(response):
    return [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_delivers_deltas_and_terminal_usage(client, authorized_headers):
    response = client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "Привет"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    data = frames(response)
    assert data[-1] == "[DONE]"
    chunks = [json.loads(item) for item in data[:-1]]
    text = "".join(c["delta"].get("content", "") for f in chunks for c in f["choices"])
    assert text == "Shluz mock reply: Привет"
    assert sum(bool(f["choices"]) for f in chunks) > 2
    assert chunks[-1]["choices"] == [] and "usage" in chunks[-1]
    with client.app.state.database.session() as session:
        rows = list(session.scalars(select(UsageEvent)))
        assert len(rows) == 1 and rows[0].status == "success"
        assert rows[0].streamed is True
        assert rows[0].duration_ms >= rows[0].first_token_ms >= 0


def test_stream_without_usage_option_omits_terminal_usage(client, authorized_headers):
    response = client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    data = frames(response)
    assert data and data[-1] == "[DONE]"
    assert all("usage" not in json.loads(f) for f in data[:-1])


def test_stream_does_not_bypass_auth(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    assert response.status_code == 401


def test_diagnostics_require_owner_and_include_timings(client, authorized_headers, test_settings):
    assert client.get("/admin/diagnostics").status_code == 401
    assert client.get("/admin/diagnostics/data", headers=authorized_headers).status_code == 401
    client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "private-content-marker"}],
            "stream": True,
        },
    )
    auth = (test_settings.admin_username, test_settings.admin_password.get_secret_value())
    report = client.get("/admin/diagnostics/data", auth=auth)
    assert report.status_code == 200
    assert "private-content-marker" not in report.text
    assert "test-api-token" not in report.text
    assert report.json()["summary"]["requests"] == 1
    assert report.json()["models"][0]["first_token_ms"] is not None
    html = client.get("/admin/diagnostics", auth=auth)
    assert html.status_code == 200 and "Диагностика" in html.text


def test_stream_error_after_content_is_sanitized_and_not_success(client, authorized_headers):
    from app.providers.base import ProviderError, StreamChunk

    provider = client.app.state.provider_registry.provider_for_model("mock-chat")

    async def broken(_):
        yield StreamChunk(content="visible prefix")
        raise ProviderError("secret-key-and-prompt-must-not-leak")

    provider.chat_stream = broken
    response = client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "private"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "visible prefix" in response.text and "stream_interrupted" in response.text
    assert "[DONE]" not in response.text and "secret-key" not in response.text
    with client.app.state.database.session() as session:
        row = session.scalar(select(UsageEvent))
        assert row.status == "interrupted"
        assert row.cost_known is False and row.usage_known is False
        assert row.duration_ms >= row.first_token_ms


def test_provider_rejection_before_headers_has_original_http_status(client, authorized_headers):
    from app.providers.base import ProviderInsufficientBalanceError

    provider = client.app.state.provider_registry.provider_for_model("mock-chat")

    async def rejected(_):
        raise ProviderInsufficientBalanceError("private balance")
        yield

    provider.chat_stream = rejected
    response = client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_balance_exhausted"
    assert "private balance" not in response.text


def test_opentelemetry_spans_never_capture_content_or_credentials(client, authorized_headers):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    client.app.state.telemetry.traces.add_span_processor(SimpleSpanProcessor(exporter))
    client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "SECRET-PROMPT"}],
            "stream": True,
        },
    )
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["shluz.outcome"] == "success"
    assert "shluz.first_token_seconds" in spans[0].attributes
    raw = str(spans[0].attributes)
    assert "SECRET-PROMPT" not in raw and "test-api-token" not in raw
    assert "account_id" not in raw


def test_total_timeout_also_bounds_blocked_client_send(client):
    import asyncio
    import time

    from app.providers.base import ChatMessage, ChatRequest
    from app.streaming.response import ManagedStreamResponse, StreamSession

    async def scenario():
        app = client.app
        usage = app.state.usage_service
        reservation = usage.authorize_request(
            account_id="bootstrap", source="harness", provider="mock", model="mock-chat"
        )
        observation = app.state.telemetry.begin("mock", "mock-chat", "harness", True)
        provider = app.state.provider_registry.provider_for_model("mock-chat")
        session = StreamSession(
            provider,
            ChatRequest("mock-chat", [ChatMessage("user", "hi")]),
            reservation,
            usage,
            observation,
            app.state.settings,
            False,
        )
        first = await session.next()
        session.deadline = time.monotonic() + 0.05
        response = ManagedStreamResponse(session, first)

        async def receive():
            await asyncio.Event().wait()

        async def send(event):
            if event["type"] == "http.response.body":
                await asyncio.Event().wait()

        await asyncio.wait_for(
            response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send), 0.5
        )
        assert session.closed and session.settled

    asyncio.run(scenario())


def test_opentelemetry_metrics_record_timings_without_private_labels(monkeypatch, test_settings):
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from app.factory import create_app
    from app.streaming import telemetry

    reader = InMemoryMetricReader()

    def meter_provider(**kwargs):
        return MeterProvider(resource=kwargs["resource"], metric_readers=[reader])

    monkeypatch.setattr(telemetry, "MeterProvider", meter_provider)
    with TestClient(create_app(test_settings)) as c:
        c.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-api-token"},
            json={
                "model": "mock-chat",
                "messages": [{"role": "user", "content": "PRIVATE-TEXT"}],
                "stream": True,
            },
        )
        records = reader.get_metrics_data()
        metrics = {
            metric.name: metric
            for resource in records.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        assert metrics["shluz.requests"].data.data_points[0].value == 1
        assert metrics["shluz.requests.active"].data.data_points[0].value == 0
        assert metrics["shluz.request.duration"].data.data_points[0].count == 1
        assert metrics["shluz.request.first_token"].data.data_points[0].sum >= 0
        labels = str(
            [point.attributes for metric in metrics.values() for point in metric.data.data_points]
        )
        assert "PRIVATE-TEXT" not in labels and "test-api-token" not in labels
        assert "account_id" not in labels and "request_id" not in labels


def test_stream_session_settles_once_on_client_disconnect(tmp_path):
    """Обрыв клиента: одна финализация «interrupted», резерв не зависает, повтор — no-op."""
    import asyncio

    from pydantic import SecretStr
    from sqlalchemy import select

    from app.config import Settings
    from app.db.models import UsageEvent
    from app.factory import create_app
    from app.providers.base import ChatMessage, ChatRequest
    from app.streaming.response import StreamSession

    settings = Settings(
        bootstrap_api_token=SecretStr("bootstrap"),
        admin_username="test-admin",
        admin_password=SecretStr("admin-password"),
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'stream-disconnect.db'}"),
    )
    app = create_app(settings)
    client = TestClient(app)

    reservation = client.app.state.usage_service.authorize_request(
        account_id="bootstrap",
        source="harness",
        provider="mock",
        model="mock-chat",
        input_chars=6,
        requested_output_tokens=100,
    )
    usage = client.app.state.usage_service
    observation = client.app.state.telemetry.begin("mock", "mock-chat", "harness", True, None)
    chat_request = ChatRequest(
        "mock-chat", [ChatMessage("user", "привет")], max_completion_tokens=100
    )
    session = StreamSession(
        client.app.state.provider_registry.provider_for_model("mock-chat"),
        chat_request,
        reservation,
        usage,
        observation,
        client.app.state.settings,
        False,
    )

    async def scenario():
        first = await session.next()
        session.accept(first)
        await session.close()
        await session.settle(code="client_disconnected")
        await session.close()
        await session.settle(code="client_disconnected")

    asyncio.run(scenario())

    with client.app.state.database.session() as s:
        event = s.scalar(
            select(UsageEvent).where(UsageEvent.id == reservation.event_id)
        )
        assert event is not None
        assert event.status != "success"  # обрыв — не успешный ответ
        assert event.completed_at is not None
    # Повторное settle не создало вторых записей и не упало — одна финализация.
    with client.app.state.database.session() as s:
        count = s.query(UsageEvent).filter(UsageEvent.id == reservation.event_id).count()
        assert count == 1
