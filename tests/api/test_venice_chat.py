import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.factory import build_provider_registry, create_app
from app.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    ProviderAuthenticationError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderRateLimitError,
)
from app.providers.registry import ProviderRegistry


def test_default_settings_keep_free_mock_provider(test_settings):
    registry = build_provider_registry(test_settings)

    assert registry.provider_names() == ["mock"]
    assert [model.id for model in registry.list_models()] == ["mock-chat"]


def test_venice_settings_register_venice_without_network_call():
    settings = Settings(
        active_provider="venice",
        venice_api_key=SecretStr("venice-test-key"),
    )

    registry = build_provider_registry(settings)

    assert registry.provider_names() == ["venice"]


class ErrorProvider:
    def __init__(self, error: ProviderError) -> None:
        self._error = error

    @property
    def name(self) -> str:
        return "error"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="error-model", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        raise self._error


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (
            ProviderAuthenticationError("secret upstream detail"),
            502,
            "provider_authentication_error",
            "The model provider credentials are invalid",
        ),
        (
            ProviderInsufficientBalanceError("secret upstream detail"),
            503,
            "provider_balance_exhausted",
            "The model provider balance is unavailable",
        ),
        (
            ProviderRateLimitError("secret upstream detail"),
            429,
            "provider_rate_limit",
            "The model provider rate limit was reached",
        ),
    ],
)
def test_chat_maps_specific_provider_failures_safely(
    test_settings,
    error,
    status_code,
    code,
    message,
):
    app = create_app(
        test_settings,
        registry=ProviderRegistry([ErrorProvider(error)]),
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={
            "model": "error-model",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"] == message
    assert "secret upstream detail" not in response.text
