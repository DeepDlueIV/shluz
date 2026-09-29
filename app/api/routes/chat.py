import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_provider_registry
from app.api.schemas import (
    AssistantMessageSchema,
    ChatChoiceSchema,
    ChatCompletionRequestSchema,
    ChatCompletionResponseSchema,
    ErrorDetailSchema,
    ErrorResponseSchema,
    UsageSchema,
)
from app.providers.base import (
    ChatMessage,
    ChatRequest,
    ModelNotFoundError,
    ProviderError,
)
from app.providers.registry import ProviderRegistry

router = APIRouter()


def _error_response(
    *,
    status_code: int,
    message: str,
    error_type: str,
    param: str | None,
    code: str,
) -> JSONResponse:
    payload = ErrorResponseSchema(
        error=ErrorDetailSchema(
            message=message,
            type=error_type,
            param=param,
            code=code,
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponseSchema,
    responses={404: {"model": ErrorResponseSchema}, 502: {"model": ErrorResponseSchema}},
)
def chat_completions(
    payload: ChatCompletionRequestSchema,
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
) -> ChatCompletionResponseSchema | JSONResponse:
    try:
        provider = registry.provider_for_model(payload.model)
        result = provider.chat_completion(
            ChatRequest(
                model=payload.model,
                messages=[
                    ChatMessage(role=message.role, content=message.content)
                    for message in payload.messages
                ],
            )
        )
    except ModelNotFoundError as exc:
        return _error_response(
            status_code=404,
            message=str(exc),
            error_type="model_not_found",
            param="model",
            code="model_not_found",
        )
    except ProviderError:
        return _error_response(
            status_code=502,
            message="The model provider is temporarily unavailable",
            error_type="provider_error",
            param=None,
            code="provider_error",
        )

    return ChatCompletionResponseSchema(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        model=payload.model,
        choices=[
            ChatChoiceSchema(
                index=0,
                message=AssistantMessageSchema(content=result.content),
                finish_reason="stop",
            )
        ],
        usage=UsageSchema(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
        ),
    )
