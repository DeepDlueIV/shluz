import asyncio

from app.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    StreamChunk,
    UnsupportedRequestError,
)


class MockProvider:
    """Deterministic provider used for local development and tests."""

    @property
    def name(self) -> str:
        return "mock"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-chat", provider=self.name, tool_calling=True)]

    def validate_request(self, request: ChatRequest):
        if any(
            t["function"]["name"] != "noclip_test_echo"
            for t in request.options.get("tools", [])
        ):
            raise UnsupportedRequestError("mock_supports_only_noclip_test_echo")
        if any(c["function"]["name"] != "noclip_test_echo"
               for m in request.messages for c in m.tool_calls or []):
            raise UnsupportedRequestError("mock_supports_only_noclip_test_echo")

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        self.validate_request(request)
        last = request.messages[-1] if request.messages else None
        if last and last.role == "tool":
            return ChatResult(content=f"Shluz mock tool result: {last.content}")
        if request.options.get("tools") and request.options.get("tool_choice") != "none":
            ordinal = 1 + sum(len(m.tool_calls or []) for m in request.messages)
            return ChatResult(content=None, finish_reason="tool_calls", tool_calls=[{
                "id": f"call_noclip_echo_{ordinal}", "type": "function", "function": {
                    "name": "noclip_test_echo", "arguments": '{"text":"hello"}'}}])
        content = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        if isinstance(content, list):
            content = "".join(p["text"] for p in content)
        return ChatResult(content=f"Shluz mock reply: {content}")

    async def chat_completion_async(self, request: ChatRequest) -> ChatResult:
        return self.chat_completion(request)

    async def chat_stream(self, request: ChatRequest):
        result = self.chat_completion(request)
        for index, call in enumerate(result.tool_calls or []):
            arguments = call["function"]["arguments"]
            yield StreamChunk(tool_calls=[{"index": index, "id": call["id"], "type": "function",
                "function": {"name": call["function"]["name"], "arguments": arguments[:8]}}])
            await asyncio.sleep(0.005)
            yield StreamChunk(tool_calls=[{
                "index": index, "function": {"arguments": arguments[8:]}
            }])
        for offset in range(0, len(result.content or ""), 16):
            await asyncio.sleep(0.005)
            yield StreamChunk(content=result.content[offset : offset + 16])
        yield StreamChunk(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            cost_diem=result.cost_diem,
            finish_reason=result.finish_reason,
        )
        yield StreamChunk(done=True)
