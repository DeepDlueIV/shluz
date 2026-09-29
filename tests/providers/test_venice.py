from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.providers.base import (
    ChatMessage,
    ChatRequest,
    ProviderAuthenticationError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderRateLimitError,
)
from app.providers.venice import VeniceProvider


def _provider(handler) -> VeniceProvider:
    settings = Settings(
        venice_api_key=SecretStr("venice-test-secret"),
        venice_base_url="https://api.venice.test/api/v1",
        venice_timeout_seconds=2,
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=settings.venice_base_url,
    )
    return VeniceProvider(settings=settings, client=client)


def test_list_models_parses_text_model_pricing():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        assert request.url.params["type"] == "text"
        assert request.headers["authorization"] == "Bearer venice-test-secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "venice-uncensored",
                        "type": "text",
                        "model_spec": {
                            "name": "Venice Uncensored",
                            "privacy": "private",
                            "pricing": {
                                "input": {"usd": 0.5, "diem": 0.5},
                                "output": {"usd": 1.5, "diem": 1.5},
                            },
                        },
                    }
                ]
            },
        )

    model = _provider(handler).list_models()[0]

    assert model.id == "venice-uncensored"
    assert model.provider == "venice"
    assert model.type == "text"
    assert model.name == "Venice Uncensored"
    assert model.privacy == "private"
    assert model.input_price_usd == Decimal("0.5")
    assert model.output_price_usd == Decimal("1.5")


def test_chat_completion_parses_usage_cost_and_request_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/chat/completions"
        payload = __import__("json").loads(request.content)
        assert payload == {
            "model": "venice-uncensored",
            "messages": [{"role": "user", "content": "Привет"}],
            "stream": False,
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-venice-1",
                "choices": [{"message": {"content": "Здравствуйте"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
                "cost": {"usd": 0.0012, "diem": 0},
            },
        )

    result = _provider(handler).chat_completion(
        ChatRequest(
            model="venice-uncensored",
            messages=[ChatMessage(role="user", content="Привет")],
        )
    )

    assert result.content == "Здравствуйте"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 7
    assert result.request_id == "chatcmpl-venice-1"
    assert result.cost_usd == Decimal("0.0012")
    assert result.cost_diem == Decimal("0")


def test_chat_completion_accepts_missing_optional_usage_and_cost():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Минимальный ответ"}}],
            },
        )

    result = _provider(handler).chat_completion(
        ChatRequest(model="model", messages=[ChatMessage(role="user", content="Тест")])
    )

    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.cost_usd == Decimal("0")
    assert result.request_id is None


def test_account_snapshot_keeps_balance_when_beta_analytics_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/billing/balance"):
            return httpx.Response(
                200,
                json={
                    "canConsume": True,
                    "consumptionCurrency": "USD",
                    "balances": {"usd": 25.5, "diem": 3},
                    "diemEpochAllocation": 100,
                },
            )
        if request.url.path.endswith("/api_keys/rate_limits"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "accessPermitted": True,
                        "apiTier": {"id": "paid", "isCharged": True},
                        "keyExpiration": "2027-01-01T00:00:00.000Z",
                        "rateLimits": [
                            {
                                "apiModelId": "venice-uncensored",
                                "rateLimits": [{"type": "RPM", "amount": 100}],
                            }
                        ],
                    }
                },
            )
        if request.url.path.endswith("/billing/usage-analytics"):
            assert request.url.params["lookback"] == "7d"
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        raise AssertionError(f"Unexpected request: {request.url}")

    snapshot = _provider(handler).get_account_snapshot(lookback="7d")

    assert snapshot.balance is not None
    assert snapshot.balance.usd == Decimal("25.5")
    assert snapshot.balance.can_consume is True
    assert snapshot.api_tier == "paid"
    assert snapshot.key_expiration == "2027-01-01T00:00:00.000Z"
    assert snapshot.rate_limits[0].model_id == "venice-uncensored"
    assert snapshot.rate_limits[0].limits == {"RPM": 100}
    assert snapshot.analytics is None
    assert snapshot.warnings == ("Аналитика Venice временно недоступна",)


def test_account_snapshot_parses_official_usage_analytics_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/billing/balance"):
            return httpx.Response(
                200,
                json={
                    "canConsume": True,
                    "consumptionCurrency": "USD",
                    "balances": {"usd": 25.5, "diem": 3},
                    "diemEpochAllocation": 100,
                },
            )
        if request.url.path.endswith("/api_keys/rate_limits"):
            return httpx.Response(200, json={"data": {"rateLimits": []}})
        if request.url.path.endswith("/billing/usage-analytics"):
            return httpx.Response(
                200,
                json={
                    "lookback": "7d",
                    "byDate": [
                        {"date": "2026-09-28", "USD": 0.5, "DIEM": 10.25},
                        {"date": "2026-09-29", "USD": 0.3, "DIEM": 8.75},
                    ],
                    "byModel": [
                        {
                            "modelName": "GLM 5.1",
                            "unitType": "tokens",
                            "modelType": "LLM",
                            "totalUsd": 0.4,
                            "totalDiem": 12.5,
                            "totalUnits": 50000,
                            "breakdown": [
                                {"type": "Output", "usd": 0.3, "diem": 10, "units": 35000},
                                {"type": "Input", "usd": 0.1, "diem": 2.5, "units": 15000},
                            ],
                        }
                    ],
                    "byKey": [
                        {
                            "apiKeyId": "key_abc123",
                            "description": "Production Key",
                            "totalUsd": 0.8,
                            "totalDiem": 15,
                            "totalUnits": 75000,
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    analytics = _provider(handler).get_account_snapshot(lookback="7d").analytics

    assert analytics is not None
    assert analytics.lookback == "7d"
    assert analytics.total_usd == Decimal("0.8")
    assert analytics.total_diem == Decimal("19.00")
    assert analytics.total_units == 50000
    assert analytics.prompt_tokens == 15000
    assert analytics.completion_tokens == 35000
    assert analytics.by_model[0]["modelName"] == "GLM 5.1"
    assert analytics.by_key[0]["description"] == "Production Key"


@pytest.mark.parametrize(
    ("status_code", "exception_type"),
    [
        (401, ProviderAuthenticationError),
        (402, ProviderInsufficientBalanceError),
        (429, ProviderRateLimitError),
        (500, ProviderError),
    ],
)
def test_provider_maps_remote_errors_to_safe_types(status_code, exception_type):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "remote secret details"})

    with pytest.raises(exception_type, match="Venice"):
        _provider(handler).chat_completion(
            ChatRequest(model="model", messages=[ChatMessage(role="user", content="Тест")])
        )
