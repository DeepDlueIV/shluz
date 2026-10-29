from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContentPartSchema(BaseModel):
    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: str | dict[str, str] | None = None


class ChatMessageSchema(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    # OpenAI-формат: строка или массив частей (Cherry Studio шлёт массив всегда).
    content: str | list[ContentPartSchema]


class StreamOptionsSchema(BaseModel):
    include_usage: bool = False


class ToolFunctionSchema(BaseModel):
    name: str
    description: str | None = None
    parameters: dict | None = None


class ToolSchema(BaseModel):
    type: Literal["function"]
    function: ToolFunctionSchema


class ChatCompletionRequestSchema(BaseModel):
    model: str = Field(min_length=1)
    messages: list[ChatMessageSchema] = Field(min_length=1)
    stream: bool = False
    stream_options: StreamOptionsSchema | None = None
    max_completion_tokens: int | None = Field(default=None, gt=0, le=100000)
    max_tokens: int | None = Field(default=None, gt=0, le=100000)
    tools: list[ToolSchema] | None = None
    tool_choice: str | dict | None = None

    model_config = ConfigDict(extra="allow")


class AssistantMessageSchema(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[dict] | None = None


class ChatChoiceSchema(BaseModel):
    index: int
    message: AssistantMessageSchema
    finish_reason: str


class UsageSchema(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponseSchema(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoiceSchema]
    usage: UsageSchema


class ModelObjectSchema(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str


class ModelListResponseSchema(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelObjectSchema]


class ErrorDetailSchema(BaseModel):
    message: str
    type: str
    param: str | None
    code: str


class ErrorResponseSchema(BaseModel):
    error: ErrorDetailSchema
