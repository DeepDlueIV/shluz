from app.providers.base import ChatRequest, ChatResult, ModelInfo


class MockProvider:
    """Deterministic provider used for local development and tests."""

    @property
    def name(self) -> str:
        return "mock"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-chat", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        last_user_message = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        return ChatResult(content=f"Shluz mock reply: {last_user_message}")
