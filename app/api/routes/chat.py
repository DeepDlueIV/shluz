import asyncio
import time
import uuid
from dataclasses import replace
from typing import Annotated

import anyio
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
    ChatResult,
    ModelNotFoundError,
    ProviderAccessDeniedError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderRateLimitError,
    UnsupportedRequestError,
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
    response_model_exclude_unset=True,
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

    chat_request = ChatRequest(
        model=payload.model,
        messages=[ChatMessage(**m.wire()) for m in payload.messages],
        options=payload.provider_options(),
    )
    try:
        if payload.uses_tools:
            info = next(m for m in registry.list_models() if m.id == payload.model)
            if info.tool_calling is False:
                raise UnsupportedRequestError("model_does_not_support_tools")
        validate = getattr(provider, "validate_request", None)
        if validate:
            validate(chat_request)
    except UnsupportedRequestError as exc:
        return _error_response(status_code=400, message=str(exc),
            error_type="invalid_request_error", param="tools", code="tools_unsupported")

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
            input_chars=payload.input_chars,
            uses_tools=payload.uses_tools,
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

    chat_request = replace(chat_request, max_completion_tokens=reservation.max_output_tokens)
    session = None
    try:
        if payload.stream or reservation.demo:
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
            if payload.stream:
                return ManagedStreamResponse(session, first)
            # JSON-демо использует тот же отменяемый upstream и общий deadline,
            # а не поток, который продолжит работать в фоне после wait_for(thread).
            chunk = first
            while True:
                session.accept(chunk)
                if chunk.done:
                    result = session.result()
                    break
                chunk = await session.next()
            await session.close()
        else:
            result = await _json_completion(provider, chat_request, request)
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
            message="The model provider rejected authentication for this request",
            error_type="provider_authentication_error",
            param=None,
            code="provider_authentication_error",
        )
    except ProviderAccessDeniedError:
        await run_in_threadpool(
            usage_service.record_failure,
            reservation,
            error_code="provider_access_denied",
            timing=observation.timing(),
        )
        observation.finish("provider_access_denied")
        if session:
            session.settled = True
            await session.close()
        return _error_response(
            status_code=502,
            message="The selected model is not permitted by the provider",
            error_type="provider_access_denied",
            param="model",
            code="provider_access_denied",
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
    except (ProviderError, TimeoutError, StopAsyncIteration):
        await run_in_threadpool(
            usage_service.record_failure,
            reservation,
            error_code="provider_error",
            timing=observation.timing(),
            partial=replace(session.result(), cost_known=False, usage_known=False) if session
            else ChatResult("", cost_known=False, usage_known=False),
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
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(
                    usage_service.record_failure, reservation, error_code="client_disconnected",
                    timing=observation.timing(),
                    partial=ChatResult("", cost_known=False, usage_known=False),
                )
                observation.finish("client_disconnected")
        raise

    await run_in_threadpool(
        usage_service.record_success,
        reservation=reservation,
        result=result,
        timing=observation.timing(),
    )
    if session:
        session.settled = True
    observation.finish("success", result)

    return ChatCompletionResponseSchema(
        id=result.request_id or f"chatcmpl-{uuid.uuid4().hex}",
        object="chat.completion",
        created=int(time.time()),
        model=payload.model,
        choices=[
            ChatChoiceSchema(
                index=0,
                message=AssistantMessageSchema(
                    role="assistant", content=result.content,
                    **({"tool_calls": result.tool_calls} if result.tool_calls else {}),
                    **({"reasoning_content": result.reasoning_content}
                       if result.reasoning_content is not None else {}),
                ),
                finish_reason=result.finish_reason,
            )
        ],
        usage=UsageSchema(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
        ) if result.usage_known else None,
    )


async def _json_completion(provider, chat_request, request):
    """JSON Venice тоже отменяемый; долгий tool-вызов не переживает браузер/клиент."""
    method = getattr(provider, "chat_completion_async", None)
    if not callable(method):
        # Совместимость синхронных сторонних адаптеров. Venice использует async ниже.
        return await run_in_threadpool(provider.chat_completion, chat_request)

    async def disconnected():
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                return

    task = asyncio.create_task(method(chat_request))
    watcher = asyncio.create_task(disconnected())
    try:
        async with asyncio.timeout(min(request.app.state.settings.stream_timeout_seconds,
                                       request.app.state.settings.reservation_ttl_seconds - 5)):
            done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                return task.result()
            raise asyncio.CancelledError()
    finally:
        task.cancel()
        watcher.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(task, watcher, return_exceptions=True)
