from dataclasses import dataclass
from typing import Protocol


class ProviderError(RuntimeError):
    """A provider failed in a controlled, user-safe way."""


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


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    provider: str


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def list_models(self) -> list[ModelInfo]: ...

    def chat_completion(self, request: ChatRequest) -> ChatResult: ...
