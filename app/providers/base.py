from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """A provider failed in a controlled, user-safe way."""


class UnsupportedRequestError(ValueError):
    """Безопасная локальная ошибка capability до запроса к поставщику и резерва."""


class ProviderAuthenticationError(ProviderError):
    """Provider credentials are missing, expired, or invalid."""


class ProviderAccessDeniedError(ProviderError):
    """Provider forbids the requested model, key scope or request context."""


class ProviderInsufficientBalanceError(ProviderError):
    """Provider account does not have enough balance for the request."""


class ProviderRateLimitError(ProviderError):
    """Provider rejected the request because a rate limit was reached."""


class ModelNotFoundError(LookupError):
    """The requested model is not registered in Shluz."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"Unknown model: {model_id}")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str | list[dict[str, Any]] | None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None

    def wire(self) -> dict[str, Any]:
        data = {"role": self.role, "content": self.content}
        for name in ("name", "tool_call_id", "tool_calls", "reasoning_content"):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    max_completion_tokens: int | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def uses_tools(self) -> bool:
        return bool(self.options.get("tools") or any(
            m.tool_calls or m.role == "tool" for m in self.messages
        ))

    def wire(self, *, stream: bool) -> dict[str, Any]:
        data = {**self.options, "model": self.model,
                "messages": [m.wire() for m in self.messages], "stream": stream}
        if self.max_completion_tokens is not None:
            data["max_completion_tokens"] = self.max_completion_tokens
        if stream:
            data["stream_options"] = {"include_usage": True}
        return data


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_id: str | None = None
    cost_usd: Decimal = Decimal("0")
    cost_diem: Decimal = Decimal("0")
    cost_known: bool = True
    usage_known: bool = True
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None


@dataclass(frozen=True, slots=True)
class StreamChunk:
    content: str = ""
    request_id: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: Decimal | None = None
    cost_diem: Decimal | None = None
    done: bool = False
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None
    role: str | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    provider: str
    type: str | None = None
    name: str | None = None
    privacy: str | None = None
    input_price_usd: Decimal | None = None
    output_price_usd: Decimal | None = None
    unit_price_usd: Decimal | None = None
    tool_calling: bool | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def list_models(self) -> list[ModelInfo]: ...

    def chat_completion(self, request: ChatRequest) -> ChatResult: ...

    def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]: ...
