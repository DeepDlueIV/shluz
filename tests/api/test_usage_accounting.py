from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import UsageEvent
from app.db.session import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.factory import create_app
from app.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    ProviderRateLimitError,
)
from app.providers.registry import ProviderRegistry
from app.services.usage import UsageStorageError


class SuccessfulProvider:
    called = False

    @property
    def name(self) -> str:
        return "paid-test"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="paid-test-model", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        self.called = True
        return ChatResult(
            content="Готово",
            prompt_tokens=12,
            completion_tokens=7,
            request_id="provider-request-1",
            cost_usd=Decimal("0.00120000"),
        )


class RateLimitedProvider(SuccessfulProvider):
    def chat_completion(self, request: ChatRequest) -> ChatResult:
        self.called = True
        raise ProviderRateLimitError("secret upstream details")


class BrokenUsageService:
    def begin(self, **kwargs):
        raise UsageStorageError("database unavailable")

    def succeed(self, *args, **kwargs):
        raise AssertionError("succeed must not be called")

    def fail(self, *args, **kwargs):
        raise AssertionError("fail must not be called")


def _session_factory():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    initialize_database(engine)
    return create_session_factory(engine)


def test_successful_chat_is_recorded_with_tokens_and_provider_cost(test_settings):
    session_factory = _session_factory()
    provider = SuccessfulProvider()
    app = create_app(
        test_settings,
        registry=ProviderRegistry([provider]),
        session_factory=session_factory,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={
            "model": "paid-test-model",
            "messages": [{"role": "user", "content": "Сделай"}],
        },
    )

    assert response.status_code == 200
    assert provider.called is True
    with session_factory() as session:
        event = session.scalar(select(UsageEvent))
        assert event is not None
        assert event.status == "succeeded"
        assert event.channel == "api"
        assert event.provider == "paid-test"
        assert event.model == "paid-test-model"
        assert event.provider_request_id == "provider-request-1"
        assert event.prompt_tokens == 12
        assert event.completion_tokens == 7
        assert event.cost_usd == Decimal("0.00120000")


def test_provider_failure_is_recorded_with_safe_error_code(test_settings):
    session_factory = _session_factory()
    provider = RateLimitedProvider()
    app = create_app(
        test_settings,
        registry=ProviderRegistry([provider]),
        session_factory=session_factory,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={
            "model": "paid-test-model",
            "messages": [{"role": "user", "content": "Сделай"}],
        },
    )

    assert response.status_code == 429
    assert "secret upstream details" not in response.text
    with session_factory() as session:
        event = session.scalar(select(UsageEvent))
        assert event is not None
        assert event.status == "failed"
        assert event.error_code == "provider_rate_limit"
        assert event.cost_usd == Decimal("0")


def test_unavailable_usage_storage_prevents_paid_provider_call(test_settings):
    provider = SuccessfulProvider()
    app = create_app(
        test_settings,
        registry=ProviderRegistry([provider]),
        session_factory=_session_factory(),
        usage_service=BrokenUsageService(),
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={
            "model": "paid-test-model",
            "messages": [{"role": "user", "content": "Сделай"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "usage_storage_unavailable"
    assert provider.called is False
