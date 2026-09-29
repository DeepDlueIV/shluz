import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.auth import AuthenticatedPrincipal, require_api_token
from app.api.dependencies import get_provider_registry, get_usage_service
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
    ProviderAuthenticationError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderRateLimitError,
)
from app.providers.registry import ProviderRegistry
from app.usage.service import (
    CreditLimitExceededError,
    RequestLimitExceededError,
    SpendLimitExceededError,
    SubscriptionRequiredError,
    UsageService,
)

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
    responses={
        402: {"model": ErrorResponseSchema},
        404: {"model": ErrorResponseSchema},
        429: {"model": ErrorResponseSchema},
        502: {"model": ErrorResponseSchema},
        503: {"model": ErrorResponseSchema},
    },
)
def chat_completions(
    payload: ChatCompletionRequestSchema,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_api_token)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_service: Annotated[UsageService, Depends(get_usage_service)],
) -> ChatCompletionResponseSchema | JSONResponse:
    try:
        provider = registry.provider_for_model(payload.model)
    except ModelNotFoundError as exc:
        return _error_response(
            status_code=404,
            message=str(exc),
            error_type="model_not_found",
            param="model",
            code="model_not_found",
        )

    try:
        reservation = usage_service.authorize_request(
            account_id=principal.account_id,
            source=principal.source,
            provider=provider.name,
            model=payload.model,
        )
    except SubscriptionRequiredError:
        return _error_response(
            status_code=402,
            message="An active subscription is required",
            error_type="billing_error",
            param=None,
            code="subscription_required",
        )
    except CreditLimitExceededError:
        return _error_response(
            status_code=402,
            message="The monthly credit allowance is exhausted",
            error_type="billing_error",
            param=None,
            code="credit_limit_exceeded",
        )
    except SpendLimitExceededError:
        return _error_response(
            status_code=402,
            message="The monthly provider budget is exhausted",
            error_type="billing_error",
            param=None,
            code="spend_limit_exceeded",
        )
    except RequestLimitExceededError:
        return _error_response(
            status_code=429,
            message="The monthly request allowance is exhausted",
            error_type="rate_limit_error",
            param=None,
            code="request_limit_exceeded",
        )

    try:
        result = provider.chat_completion(
            ChatRequest(
                model=payload.model,
                messages=[
                    ChatMessage(role=message.role, content=message.content)
                    for message in payload.messages
                ],
            )
        )
    except ProviderAuthenticationError:
        usage_service.record_failure(
            reservation,
            error_code="provider_authentication_error",
        )
        return _error_response(
            status_code=502,
            message="The model provider credentials are invalid",
            error_type="provider_authentication_error",
            param=None,
            code="provider_authentication_error",
        )
    except ProviderInsufficientBalanceError:
        usage_service.record_failure(
            reservation,
            error_code="provider_balance_exhausted",
        )
        return _error_response(
            status_code=503,
            message="The model provider balance is unavailable",
            error_type="provider_balance_exhausted",
            param=None,
            code="provider_balance_exhausted",
        )
    except ProviderRateLimitError:
        usage_service.record_failure(
            reservation,
            error_code="provider_rate_limit",
        )
        return _error_response(
            status_code=429,
            message="The model provider rate limit was reached",
            error_type="provider_rate_limit",
            param=None,
            code="provider_rate_limit",
        )
    except ProviderError:
        usage_service.record_failure(
            reservation,
            error_code="provider_error",
        )
        return _error_response(
            status_code=502,
            message="The model provider is temporarily unavailable",
            error_type="provider_error",
            param=None,
            code="provider_error",
        )

    usage_service.record_success(
        reservation=reservation,
        result=result,
    )

    return ChatCompletionResponseSchema(
        id=result.request_id or f"chatcmpl-{uuid.uuid4().hex}",
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
