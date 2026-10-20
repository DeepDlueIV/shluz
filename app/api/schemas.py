import json
from typing import Literal

from pydantic import Field, model_validator

from app.api.chat_types import (
    ChatMessageSchema,
    NamedToolChoice,
    StrictModel,
    ToolCall,
    ToolDefinition,
)


class StreamOptionsSchema(StrictModel):
    include_usage: bool = False


class ChatCompletionRequestSchema(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessageSchema] = Field(min_length=1, max_length=1024)
    stream: bool = False
    stream_options: StreamOptionsSchema | None = None
    max_completion_tokens: int | None = Field(default=None, gt=0, le=100000)
    max_tokens: int | None = Field(default=None, gt=0, le=100000)
    tools: list[ToolDefinition] | None = Field(default=None, max_length=128)
    tool_choice: Literal["auto", "none", "required"] | NamedToolChoice | None = None
    parallel_tool_calls: bool | None = None
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None
    ) = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    seed: int | None = None
    stop: str | list[str] | None = None
    user: str | None = Field(default=None, max_length=255)
    n: Literal[1] = 1
    # Совместимость старых клиентов: никогда не передаётся upstream и не меняет канал.
    source: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def consistent_tools(self):
        if self.max_tokens and self.max_completion_tokens and (
            self.max_tokens != self.max_completion_tokens
        ):
            raise ValueError("Conflicting token limits")
        names = [t.function.name for t in self.tools or []]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate tool names")
        if isinstance(self.tool_choice, NamedToolChoice):
            if self.tool_choice.function.name not in names:
                raise ValueError("Selected tool is not defined")
        elif self.tool_choice in {"auto", "required"} and not names:
            raise ValueError("tool_choice requires tools")
        if isinstance(self.stop, list) and (not self.stop or len(self.stop) > 4):
            raise ValueError("stop must have 1 to 4 items")
        pending, seen = set(), set()
        for m in self.messages:
            if m.role == "tool":
                if m.tool_call_id not in pending:
                    raise ValueError("Unmatched or repeated tool result")
                pending.remove(m.tool_call_id)
                continue
            if pending:
                raise ValueError("Missing tool results before next message")
            for call in m.tool_calls or []:
                if call.id in seen:
                    raise ValueError("Duplicate tool_call id")
                pending.add(call.id)
                seen.add(call.id)
        if pending:
            raise ValueError("Missing tool result")
        # Schema — произвольный JSON, но не NaN/Infinity и не код для выполнения.
        if self.tools:
            json.dumps([t.model_dump(exclude_none=True) for t in self.tools], allow_nan=False)
        return self

    @property
    def uses_tools(self):
        return bool(self.tools or any(m.tool_calls or m.role == "tool" for m in self.messages))

    @property
    def input_chars(self):
        # В бюджет входят не только слова пользователя, но и описания/результаты инструментов.
        total = 0
        for m in self.messages:
            content = m.content
            total += len(content) if isinstance(content, str) else sum(
                len(p.text) for p in content or []
            )
            total += len(m.reasoning_content or "") + len(m.name or "")
            total += len(m.tool_call_id or "")
            if m.tool_calls:
                total += len(json.dumps([t.model_dump() for t in m.tool_calls], ensure_ascii=False))
        if self.tools:
            total += len(json.dumps(
                [t.model_dump(exclude_none=True) for t in self.tools], ensure_ascii=False
            ))
        return total

    def provider_options(self):
        names = {"temperature", "top_p", "frequency_penalty", "presence_penalty", "seed",
                 "stop", "user", "tool_choice", "parallel_tool_calls", "tools", "reasoning_effort"}
        return self.model_dump(include=names, exclude_none=True)


class AssistantMessageSchema(StrictModel):
    role: Literal["assistant"] = "assistant"
    content: str | None
    tool_calls: list[ToolCall] | None = None
    reasoning_content: str | None = None


class ChatChoiceSchema(StrictModel):
    index: int
    message: AssistantMessageSchema
    finish_reason: str


class UsageSchema(StrictModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponseSchema(StrictModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoiceSchema]
    usage: UsageSchema | None


class ModelObjectSchema(StrictModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str


class ModelListResponseSchema(StrictModel):
    object: Literal["list"] = "list"
    data: list[ModelObjectSchema]


class ErrorDetailSchema(StrictModel):
    message: str
    type: str
    param: str | None
    code: str


class ErrorResponseSchema(StrictModel):
    error: ErrorDetailSchema
