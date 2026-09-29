from app.providers.base import ChatMessage, ChatRequest
from app.providers.mock import MockProvider


def test_mock_provider_lists_its_model():
    provider = MockProvider()

    models = provider.list_models()

    assert len(models) == 1
    assert models[0].id == "mock-chat"
    assert models[0].provider == "mock"


def test_mock_provider_returns_last_user_message():
    provider = MockProvider()
    request = ChatRequest(
        model="mock-chat",
        messages=[
            ChatMessage(role="system", content="Be useful"),
            ChatMessage(role="user", content="Привет"),
        ],
    )

    result = provider.chat_completion(request)

    assert result.content == "Shluz mock reply: Привет"
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
