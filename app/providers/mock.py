import asyncio

from app.providers.base import ChatRequest, ChatResult, ModelInfo, StreamChunk


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

    async def chat_stream(self, request: ChatRequest):
        result = self.chat_completion(request)
        for offset in range(0, len(result.content), 16):
            await asyncio.sleep(0.005)
            yield StreamChunk(content=result.content[offset : offset + 16])
        yield StreamChunk(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            cost_diem=result.cost_diem,
            finish_reason="stop",
        )
        yield StreamChunk(done=True)
