"""Текст и function tools: общий wire-контракт без выполнения кода инструментов."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TextPart(StrictModel):
    type: Literal["text"]
    text: str


class FunctionCall(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    arguments: str  # Не разбираем как JSON: ошибки аргументов обрабатывает клиент.


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=255)
    type: Literal["function"]
    function: FunctionCall


class FunctionDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    strict: bool | None = None


class ToolDefinition(StrictModel):
    type: Literal["function"]
    function: FunctionDefinition


class FunctionName(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class NamedToolChoice(StrictModel):
    type: Literal["function"]
    function: FunctionName


class FunctionDelta(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    arguments: str | None = None


class ToolDelta(StrictModel):
    index: int = Field(ge=0, le=127, strict=True)
    id: str | None = Field(default=None, min_length=1, max_length=255)
    type: Literal["function"] | None = None
    function: FunctionDelta | None = None


class ChatMessageSchema(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[TextPart] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=255)
    tool_calls: list[ToolCall] | None = Field(default=None, max_length=128)
    reasoning_content: str | None = None

    @model_validator(mode="after")
    def consistent_role(self):
        if self.role != "assistant" and (self.tool_calls is not None or self.reasoning_content):
            raise ValueError("tool_calls/reasoning_content require assistant role")
        if self.role == "tool":
            if not self.tool_call_id or self.content is None:
                raise ValueError("Tool result requires content and tool_call_id")
        elif self.tool_call_id is not None:
            raise ValueError("tool_call_id requires tool role")
        if self.content is None and not (self.role == "assistant" and self.tool_calls):
            raise ValueError("Null or absent content requires assistant tool_calls")
        return self

    def wire(self):
        value = self.model_dump(exclude_none=True)
        if self.content is None:
            value["content"] = None
        return value
