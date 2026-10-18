import asyncio
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

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
from app.product.service import PolicyError
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
from app.streaming.response import ManagedStreamResponse, StreamSession
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
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        402: {"model": ErrorResponseSchema},
        404: {"model": ErrorResponseSchema},
        429: {"model": ErrorResponseSchema},
        502: {"model": ErrorResponseSchema},
        503: {"model": ErrorResponseSchema},
    },
)
async def chat_completions(
    payload: ChatCompletionRequestSchema,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_api_token)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_service: Annotated[UsageService, Depends(get_usage_service)],
) -> ChatCompletionResponseSchema | Response:
    try:
        provider = await run_in_threadpool(registry.provider_for_model, payload.model)
    except ModelNotFoundError as exc:
        return _error_response(
            status_code=404,
            message=str(exc),
            error_type="model_not_found",
            param="model",
            code="model_not_found",
        )

    observation = request.app.state.telemetry.begin(
        provider.name,
        payload.model,
        principal.source,
        payload.stream,
        getattr(request.state, "request_started", None),
    )
    if payload.stream and not callable(getattr(provider, "chat_stream", None)):
        observation.finish("stream_unsupported")
        return _error_response(
            status_code=400,
            message="Streaming is unavailable for this provider",
            error_type="invalid_request_error",
            param="stream",
            code="stream_unsupported",
        )
    try:
        reservation = await run_in_threadpool(
            usage_service.authorize_request,
            account_id=principal.account_id,
            source=principal.source,
            provider=provider.name,
            model=payload.model,
            input_chars=sum(len(m.content) for m in payload.messages),
            requested_output_tokens=payload.max_completion_tokens or payload.max_tokens,
        )
    except PolicyError as exc:
        observation.finish(exc.code)
        response = _error_response(
            status_code=exc.status,
            message=exc.message,
            error_type="access_policy_error",
            param="model" if exc.status == 403 else None,
            code=exc.code,
        )
        if exc.code == "request_rate_limited":
            response.headers["Retry-After"] = "60"
        return response
    except SubscriptionRequiredError:
        observation.finish("subscription_required")
        return _error_response(
            status_code=402,
            message="An active subscription is required",
            error_type="billing_error",
            param=None,
            code="subscription_required",
        )
    except CreditLimitExceededError:
        observation.finish("credit_limit_exceeded")
        return _error_response(
            status_code=402,
            message="The monthly credit allowance is exhausted",
            error_type="billing_error",
            param=None,
            code="credit_limit_exceeded",
        )
    except SpendLimitExceededError:
        observation.finish("spend_limit_exceeded")
        return _error_response(
            status_code=402,
            message="The monthly provider budget is exhausted",
            error_type="billing_error",
            param=None,
            code="spend_limit_exceeded",
        )
    except RequestLimitExceededError:
        observation.finish("request_limit_exceeded")
        return _error_response(
            status_code=429,
            message="The monthly request allowance is exhausted",
            error_type="rate_limit_error",
            param=None,
            code="request_limit_exceeded",
        )

    chat_request = ChatRequest(
        model=payload.model,
        max_completion_tokens=reservation.max_output_tokens,
        messages=[ChatMessage(role=m.role, content=m.content) for m in payload.messages],
    )
    session = None
    try:
        if payload.stream:
            session = StreamSession(
                provider,
                chat_request,
                reservation,
                usage_service,
                observation,
                request.app.state.settings,
                bool(payload.stream_options and payload.stream_options.include_usage),
            )
            try:
                first = await session.next()
            except (TimeoutError, StopAsyncIteration) as exc:
                raise ProviderError("Stream did not start") from exc
            return ManagedStreamResponse(session, first)
        result = await run_in_threadpool(provider.chat_completion, chat_request)
    except ProviderAuthenticationError:
        await run_in_threadpool(
            usage_service.record_failure,
            reservation,
            error_code="provider_authentication_error",
            timing=observation.timing(),
        )
        observation.finish("provider_authentication_error")
        if session:
            session.settled = True
            await session.close()
        return _error_response(
            status_code=502,
            message="The model provider credentials are invalid",
            error_type="provider_authentication_error",
            param=None,
            code="provider_authentication_error",
        )
    except ProviderInsufficientBalanceError:
        await run_in_threadpool(
            usage_service.record_failure,
            reservation,
            error_code="provider_balance_exhausted",
            timing=observation.timing(),
        )
        observation.finish("provider_balance_exhausted")
        if session:
            session.settled = True
            await session.close()
        return _error_response(
            status_code=503,
            message="The model provider balance is unavailable",
            error_type="provider_balance_exhausted",
            param=None,
            code="provider_balance_exhausted",
        )
    except ProviderRateLimitError:
        await run_in_threadpool(
            usage_service.record_failure,
            reservation,
            error_code="provider_rate_limit",
            timing=observation.timing(),
        )
        observation.finish("provider_rate_limit")
        if session:
            session.settled = True
            await session.close()
        return _error_response(
            status_code=429,
            message="The model provider rate limit was reached",
            error_type="provider_rate_limit",
            param=None,
            code="provider_rate_limit",
        )
    except ProviderError:
        await run_in_threadpool(
            usage_service.record_failure,
            reservation,
            error_code="provider_error",
            timing=observation.timing(),
            partial=session.result() if session else None,
        )
        observation.finish("provider_error")
        if session:
            session.settled = True
            await session.close()
        return _error_response(
            status_code=502,
            message="The model provider is temporarily unavailable",
            error_type="provider_error",
            param=None,
            code="provider_error",
        )

    except asyncio.CancelledError:
        if session:
            await session.close()
            await session.settle(code="client_disconnected")
        else:
            observation.finish("client_disconnected")
        raise

    await run_in_threadpool(
        usage_service.record_success,
        reservation=reservation,
        result=result,
        timing=observation.timing(),
    )
    observation.finish("success", result)

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
