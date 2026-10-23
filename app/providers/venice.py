import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.config import Settings
from app.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    ProviderAuthenticationError,
    ProviderAccessDeniedError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderRateLimitError,
    StreamChunk,
)
from app.streaming.sse import SSEError, events


@dataclass(frozen=True, slots=True)
class VeniceBalance:
    can_consume: bool
    consumption_currency: str | None
    usd: Decimal
    diem: Decimal
    diem_epoch_allocation: Decimal


@dataclass(frozen=True, slots=True)
class VeniceRateLimit:
    model_id: str
    limits: dict[str, int]


@dataclass(frozen=True, slots=True)
class VeniceUsageAnalytics:
    lookback: str
    total_usd: Decimal
    total_diem: Decimal
    total_units: int
    prompt_tokens: int
    completion_tokens: int
    by_model: tuple[dict[str, Any], ...]
    by_key: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class VeniceAccountSnapshot:
    balance: VeniceBalance | None
    access_permitted: bool | None
    api_tier: str | None
    is_charged: bool | None
    key_expiration: str | None
    rate_limits: tuple[VeniceRateLimit, ...]
    analytics: VeniceUsageAnalytics | None
    warnings: tuple[str, ...]


def _decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _known_cost(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        result = Decimal(str(value))
        return result.is_finite() and result >= 0
    except (InvalidOperation, ValueError, TypeError):
        return False


def _price_usd(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("usd")
    if value is None:
        return None
    return _decimal(value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class VeniceProvider:
    """Adapter for Venice's OpenAI-compatible API and account endpoints."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        api_key = settings.venice_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise ValueError("SHLUZ_VENICE_API_KEY is required for Venice")

        self._api_key = api_key.get_secret_value()
        self._stream_client = async_client
        self._stream_url = settings.venice_base_url.rstrip("/") + "/chat/completions"
        self._stream_timeout = settings.venice_timeout_seconds
        self._client = client or httpx.Client(
            base_url=settings.venice_base_url.rstrip("/"),
            timeout=settings.venice_timeout_seconds,
        )

    @property
    def name(self) -> str:
        return "venice"

    def list_models(self) -> list[ModelInfo]:
        return self._fetch_models(model_type="text")

    def list_model_catalog(self) -> list[ModelInfo]:
        return self._fetch_models(model_type="all")

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        response = self._request(
            "POST",
            "/chat/completions",
            json={
                "model": request.model,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "stream": False,
                **(
                    {"max_completion_tokens": request.max_completion_tokens}
                    if request.max_completion_tokens
                    else {}
                ),
            },
        )
        payload = self._json(response)
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError("Venice returned an invalid chat response")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderError("Venice returned an unsupported chat response")

        usage = payload.get("usage") or {}
        cost = payload.get("cost") or {}
        return ChatResult(
            content=content,
            prompt_tokens=_int(usage.get("prompt_tokens")),
            completion_tokens=_int(usage.get("completion_tokens")),
            request_id=payload.get("id"),
            cost_usd=_decimal(cost.get("usd")) if _known_cost(cost.get("usd")) else Decimal("0"),
            cost_known=_known_cost(cost.get("usd")),
            cost_diem=_decimal(cost.get("diem")),
        )

    async def chat_stream(self, request: ChatRequest):
        # Отдельная сессия запроса: отмена закрывает соединение, POST не повторяется.
        client = self._stream_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._stream_timeout, connect=10),
            follow_redirects=False,
        )
        response = None
        try:
            req = client.build_request(
                "POST",
                self._stream_url,
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "text/event-stream"},
                json={
                    "model": request.model,
                    "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "max_completion_tokens": request.max_completion_tokens,
                },
            )
            response = await client.send(req, stream=True, follow_redirects=False)
            self._raise_for_status(response)
            if not response.headers.get("content-type", "").startswith("text/event-stream"):
                raise ProviderError("Invalid upstream stream type")
            async for frame in events(response.aiter_bytes()):
                if frame == "[DONE]":
                    yield StreamChunk(done=True)
                    return
                data = json.loads(frame)
                if not isinstance(data, dict) or "error" in data:
                    raise ProviderError("Upstream stream failed")
                usage, cost = data.get("usage") or {}, data.get("cost") or {}
                if not isinstance(usage, dict) or not isinstance(cost, dict):
                    raise ProviderError("Invalid upstream accounting")

                def count(name, usage=usage):
                    value = usage.get(name)
                    return value if type(value) is int and 0 <= value <= 10000000 else None

                content, finish = "", None
                choices = data.get("choices") or []
                if not isinstance(choices, list):
                    raise ProviderError("Invalid upstream choices")
                if choices:
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise ProviderError("Invalid upstream choice")
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        raise ProviderError("Invalid upstream delta")
                    content = delta.get("content") or ""
                    if not isinstance(content, str):
                        raise ProviderError("Unsupported upstream content")
                    finish = choice.get("finish_reason")
                    if finish not in {None, "stop", "length", "content_filter"}:
                        raise ProviderError("Unsupported upstream finish")
                yield StreamChunk(
                    content=content,
                    finish_reason=finish,
                    request_id=data.get("id") if isinstance(data.get("id"), str) else None,
                    prompt_tokens=count("prompt_tokens"),
                    completion_tokens=count("completion_tokens"),
                    cost_usd=_decimal(cost["usd"]) if _known_cost(cost.get("usd")) else None,
                    cost_diem=_decimal(cost["diem"]) if _known_cost(cost.get("diem")) else None,
                )
            raise ProviderError("Upstream stream ended without terminal marker")
        except (httpx.RequestError, SSEError, ValueError, TypeError) as exc:
            raise ProviderError("Upstream stream interrupted") from exc
        finally:
            if response is not None:
                await response.aclose()
            if self._stream_client is None:
                await client.aclose()

    def get_account_snapshot(self, lookback: str = "7d") -> VeniceAccountSnapshot:
        warnings: list[str] = []
        balance: VeniceBalance | None = None
        access_permitted: bool | None = None
        api_tier: str | None = None
        is_charged: bool | None = None
        key_expiration: str | None = None
        rate_limits: tuple[VeniceRateLimit, ...] = ()
        analytics: VeniceUsageAnalytics | None = None

        try:
            payload = self._json(self._request("GET", "/billing/balance"))
            data = payload.get("data", payload)
            balances = data.get("balances") or {}
            balance = VeniceBalance(
                can_consume=bool(data.get("canConsume", False)),
                consumption_currency=data.get("consumptionCurrency"),
                usd=_decimal(balances.get("usd")),
                diem=_decimal(balances.get("diem")),
                diem_epoch_allocation=_decimal(data.get("diemEpochAllocation")),
            )
        except ProviderAuthenticationError:
            # Inference Only keys cannot access /billing, yet may generate normally.
            warnings.append("Баланс через Inference Only ключ не показывается; права генерации проверяются отдельно")
        except ProviderError:
            warnings.append("Баланс Venice временно недоступен")

        try:
            payload = self._json(self._request("GET", "/api_keys/rate_limits"))
            data = payload.get("data", payload)
            access_permitted = data.get("accessPermitted")
            tier = data.get("apiTier") or {}
            api_tier = tier.get("id") or tier.get("name")
            is_charged = tier.get("isCharged")
            key_expiration = data.get("keyExpiration")
            parsed_limits = []
            for item in data.get("rateLimits") or []:
                limits = {
                    str(limit.get("type")): _int(limit.get("amount"))
                    for limit in item.get("rateLimits") or []
                    if limit.get("type")
                }
                parsed_limits.append(
                    VeniceRateLimit(
                        model_id=str(item.get("apiModelId") or item.get("modelId") or "unknown"),
                        limits=limits,
                    )
                )
            rate_limits = tuple(parsed_limits)
        except ProviderAuthenticationError:
            warnings.append("Venice не подтвердил ключ через бесплатную проверку доступа (401)")
        except ProviderAccessDeniedError:
            warnings.append("Venice запретил проверку доступа для этого ключа (403)")
        except ProviderError:
            warnings.append("Не удалось проверить права ключа Venice")

        try:
            payload = self._json(
                self._request(
                    "GET",
                    "/billing/usage-analytics",
                    params={"lookback": lookback},
                )
            )
            analytics = self._parse_analytics(payload, lookback)
        except ProviderAuthenticationError:
            warnings.append("Аналитика доступна только с ключом Admin; Inference Only ключ может работать")
        except ProviderError:
            warnings.append("Аналитика Venice временно недоступна")

        return VeniceAccountSnapshot(
            balance=balance,
            access_permitted=access_permitted,
            api_tier=api_tier,
            is_charged=is_charged,
            key_expiration=key_expiration,
            rate_limits=rate_limits,
            analytics=analytics,
            warnings=tuple(warnings),
        )

    def _fetch_models(self, model_type: str) -> list[ModelInfo]:
        payload = self._json(self._request("GET", "/models", params={"type": model_type}))
        models = []
        for item in payload.get("data") or []:
            model_id = item.get("id")
            if not model_id:
                continue
            spec = item.get("model_spec") or item.get("modelSpec") or {}
            pricing = spec.get("pricing") or item.get("pricing") or {}
            models.append(
                ModelInfo(
                    id=str(model_id),
                    provider=self.name,
                    type=item.get("type") or spec.get("type"),
                    name=spec.get("name") or item.get("name"),
                    privacy=spec.get("privacy") or item.get("privacy"),
                    input_price_usd=_price_usd(pricing.get("input")),
                    output_price_usd=_price_usd(pricing.get("output")),
                    unit_price_usd=_price_usd(
                        pricing.get("unit") or pricing.get("per_image") or pricing.get("image")
                    ),
                )
            )
        return models

    def _parse_analytics(
        self,
        payload: dict[str, Any],
        lookback: str,
    ) -> VeniceUsageAnalytics:
        data = payload.get("data", payload)
        by_date = tuple(data.get("byDate") or data.get("by_date") or ())
        by_model = tuple(data.get("byModel") or data.get("by_model") or ())
        by_key = tuple(data.get("byKey") or data.get("by_key") or ())

        if by_date:
            total_usd = sum(
                (_decimal(item.get("USD", item.get("usd"))) for item in by_date),
                Decimal("0"),
            )
            total_diem = sum(
                (_decimal(item.get("DIEM", item.get("diem"))) for item in by_date),
                Decimal("0"),
            )
        else:
            total_usd = sum(
                (_decimal(item.get("totalUsd", item.get("total_usd"))) for item in by_model),
                Decimal("0"),
            )
            total_diem = sum(
                (_decimal(item.get("totalDiem", item.get("total_diem"))) for item in by_model),
                Decimal("0"),
            )

        total_units = sum(
            (_int(item.get("totalUnits", item.get("total_units"))) for item in by_model),
            0,
        )
        prompt_tokens = 0
        completion_tokens = 0
        for model in by_model:
            if str(model.get("unitType", model.get("unit_type", ""))).lower() != "tokens":
                continue
            for item in model.get("breakdown") or ():
                item_type = str(item.get("type", "")).lower()
                units = _int(item.get("units"))
                if item_type in {"input", "prompt"}:
                    prompt_tokens += units
                elif item_type in {"output", "completion"}:
                    completion_tokens += units

        return VeniceUsageAnalytics(
            lookback=str(data.get("lookback") or lookback),
            total_usd=total_usd,
            total_diem=total_diem,
            total_units=total_units,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            by_model=by_model,
            by_key=by_key,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        attempts = 2 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    path,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "application/json",
                    },
                    **kwargs,
                )
            except httpx.RequestError as exc:
                if attempt + 1 < attempts:
                    continue
                raise ProviderError("Venice is temporarily unreachable") from exc

            if response.status_code >= 500 and attempt + 1 < attempts:
                continue
            self._raise_for_status(response)
            return response

        raise ProviderError("Venice is temporarily unavailable")

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status == 401:
            # 401 can mean a rejected key or a restricted model, not necessarily bad credentials.
            raise ProviderAuthenticationError("Venice did not authorize this request")
        if status == 403:
            raise ProviderAccessDeniedError("Venice denied access to the selected model")
        if status == 402:
            raise ProviderInsufficientBalanceError("Venice balance is insufficient")
        if status == 429:
            raise ProviderRateLimitError("Venice rate limit was reached")
        if status >= 400:
            raise ProviderError(f"Venice request failed with status {status}")

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Venice returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError("Venice returned an invalid response")
        return payload
