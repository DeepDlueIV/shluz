from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


class ProviderError(RuntimeError):
    """A provider failed in a controlled, user-safe way."""


class ProviderAuthenticationError(ProviderError):
    """Provider credentials are missing, expired, or invalid."""


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
    content: str
    # Нативные OpenAI-части (text/image_url); задаются вместо content при наличии.
    parts: tuple[dict, ...] | None = None
    # История tool-цикла: вызовы на assistant, ссылка на вызов на tool.
    tool_calls: tuple[dict, ...] | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    max_completion_tokens: int | None = None
    tools: tuple[dict, ...] | None = None
    tool_choice: str | dict | None = None


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_id: str | None = None
    cost_usd: Decimal = Decimal("0")
    cost_diem: Decimal = Decimal("0")
    cost_known: bool = True
    usage_known: bool = True
    tool_calls: tuple[dict, ...] | None = None


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
    tool_calls: tuple[dict, ...] | None = None


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


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def list_models(self) -> list[ModelInfo]: ...

    def chat_completion(self, request: ChatRequest) -> ChatResult: ...

    def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]: ...
