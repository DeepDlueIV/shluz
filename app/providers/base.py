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


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: list[ChatMessage]


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_id: str | None = None
    cost_usd: Decimal = Decimal("0")
    cost_diem: Decimal = Decimal("0")


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
