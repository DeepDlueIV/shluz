from fastapi.testclient import TestClient

from app.factory import create_app
from app.providers.base import ChatRequest, ChatResult, ModelInfo, ProviderError
from app.providers.registry import ProviderRegistry


def test_chat_returns_openai_compatible_completion(client, authorized_headers):
    response = client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"].startswith("chatcmpl-")
    assert body["object"] == "chat.completion"
    assert body["model"] == "mock-chat"
    assert isinstance(body["created"], int)
    assert body["choices"] == [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Shluz mock reply: Привет",
            },
            "finish_reason": "stop",
        }
    ]
    assert body["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_chat_rejects_unknown_model(client, authorized_headers):
    response = client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={
            "model": "unknown-model",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "message": "Unknown model: unknown-model",
            "type": "model_not_found",
            "param": "model",
            "code": "model_not_found",
        }
    }


def test_chat_rejects_empty_messages(client, authorized_headers):
    response = client.post(
        "/v1/chat/completions",
        headers=authorized_headers,
        json={"model": "mock-chat", "messages": []},
    )

    assert response.status_code == 422


class FailingProvider:
    @property
    def name(self) -> str:
        return "failing"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="failing-chat", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        raise ProviderError("upstream unavailable")


def test_chat_maps_provider_failure_to_safe_502(test_settings):
    app = create_app(
        test_settings,
        registry=ProviderRegistry([FailingProvider()]),
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-api-token"},
        json={
            "model": "failing-chat",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "message": "The model provider is temporarily unavailable",
            "type": "provider_error",
            "param": None,
            "code": "provider_error",
        }
    }
    assert "upstream unavailable" not in response.text
