from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageSchema(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatCompletionRequestSchema(BaseModel):
    model: str = Field(min_length=1)
    messages: list[ChatMessageSchema] = Field(min_length=1)
    stream: bool = False

    model_config = ConfigDict(extra="allow")


class AssistantMessageSchema(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


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
