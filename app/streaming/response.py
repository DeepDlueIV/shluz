"""SSE с единственной финализацией расхода и закрытием upstream при отключении клиента."""

import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal
from functools import partial

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.providers.base import ChatResult, ProviderError


def frame(data):
    return (
        "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"
    ).encode()


class StreamSession:
    def __init__(
        self, provider, chat_request, reservation, usage, observation, settings, include_usage
    ):
        self.iterator = provider.chat_stream(chat_request).__aiter__()
        self.reservation, self.usage, self.observation = reservation, usage, observation
        self.deadline = time.monotonic() + min(
            settings.stream_timeout_seconds,
            reservation.timeout_seconds or settings.stream_timeout_seconds,
            max(1, settings.reservation_ttl_seconds - 5),
        )
        self.idle_timeout = min(
            settings.stream_idle_timeout_seconds,
            reservation.idle_seconds or settings.stream_idle_timeout_seconds,
        )
        self.include_usage = include_usage
        self.parts = []
        self.chars = 0
        self.prompt = self.completion = self.cost = self.diem = None
        self.provider_id = None
        self.finish_reason = "stop"
        self.closed = self.settled = False
        self.lock = asyncio.Lock()
        self.id = "chatcmpl-" + reservation.event_id
        self.created = int(time.time())

    async def next(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return await asyncio.wait_for(anext(self.iterator), min(remaining, self.idle_timeout))

    def accept(self, chunk):
        if chunk.content:
            self.observation.first_content()
            self.chars += len(chunk.content)
            if self.chars > 200000:
                raise ProviderError("Stream text limit")
            self.parts.append(chunk.content)
        if chunk.request_id:
            self.provider_id = chunk.request_id[:255]
        for source, target in [
            ("prompt_tokens", "prompt"),
            ("completion_tokens", "completion"),
            ("cost_usd", "cost"),
            ("cost_diem", "diem"),
        ]:
            if getattr(chunk, source) is not None:
                setattr(self, target, getattr(chunk, source))
        if chunk.finish_reason:
            self.finish_reason = chunk.finish_reason

    def result(self):
        return ChatResult(
            content="".join(self.parts),
            prompt_tokens=self.prompt or 0,
            completion_tokens=self.completion or 0,
            cost_usd=self.cost or Decimal("0"),
            cost_diem=self.diem or Decimal("0"),
            cost_known=self.cost is not None,
            usage_known=self.prompt is not None and self.completion is not None,
            request_id=self.provider_id,
        )

    async def settle(self, success=False, code="stream_interrupted", definite=False):
        with anyio.CancelScope(shield=True):
            async with self.lock:
                if self.settled:
                    return
                result = self.result()
                if not success:
                    result = replace(result, cost_known=False, usage_known=False)
                if success:
                    await run_in_threadpool(
                        partial(
                            self.usage.record_success,
                            reservation=self.reservation,
                            result=result,
                            timing=self.observation.timing(),
                        )
                    )
                else:
                    await run_in_threadpool(
                        partial(
                            self.usage.record_failure,
                            self.reservation,
                            error_code=code,
                            timing=self.observation.timing(),
                            partial=None
                            if definite
                            else replace(result, cost_known=False, usage_known=False),
                        )
                    )
                self.settled = True
                self.observation.finish("success" if success else code, result)

    async def close(self):
        if not self.closed:
            self.closed = True
            with anyio.move_on_after(5, shield=True):
                await self.iterator.aclose()

    def envelope(self, choices, **extra):
        return {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.reservation.model,
            "choices": choices,
            **extra,
        }

    async def body(self, first):
        try:
            chunk = first
            while True:
                self.accept(chunk)
                if chunk.content:
                    yield frame(
                        self.envelope(
                            [
                                {
                                    "index": 0,
                                    "delta": {"content": chunk.content},
                                    "finish_reason": None,
                                }
                            ]
                        )
                    )
                if chunk.done:
                    await self.settle(success=True)
                    yield frame(
                        self.envelope(
                            [{"index": 0, "delta": {}, "finish_reason": self.finish_reason}]
                        )
                    )
                    if self.include_usage:
                        r = self.result()
                        usage = (
                            None
                            if not r.usage_known
                            else {
                                "prompt_tokens": r.prompt_tokens,
                                "completion_tokens": r.completion_tokens,
                                "total_tokens": r.prompt_tokens + r.completion_tokens,
                            }
                        )
                        yield frame(self.envelope([], usage=usage))
                    yield b"data: [DONE]\n\n"
                    return
                chunk = await self.next()
        except (ProviderError, TimeoutError, StopAsyncIteration):
            await self.settle(code="stream_interrupted")
            yield frame(
                {
                    "error": {
                        "code": "stream_interrupted",
                        "type": "provider_error",
                        "message": "Поток прерван. Не повторяйте запрос автоматически.",
                    }
                }
            )
        except asyncio.CancelledError:
            await self.settle(
                code=(
                    "stream_timeout" if time.monotonic() >= self.deadline else "client_disconnected"
                )
            )
            raise
        finally:
            try:
                await self.close()
            finally:
                await self.settle()


class ManagedStreamResponse(StreamingResponse):
    def __init__(self, session, first):
        self.session = session
        super().__init__(
            session.body(first),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    async def __call__(self, scope, receive, send):
        try:
            with anyio.move_on_after(max(0.001, self.session.deadline - time.monotonic())):
                return await super().__call__(scope, receive, send)
        finally:
            # Покрывает и обрыв до первого вызова генератора body.
            try:
                await self.session.close()
            finally:
                await self.session.settle(code="client_disconnected")
