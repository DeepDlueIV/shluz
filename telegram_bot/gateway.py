"""Единственный путь к моделям — HTTP-контракт Shluz; платные POST не повторяются."""

import httpx


class GatewayError(Exception):
    def __init__(self, code: str, status: int = 0) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


ERROR_TEXT = {
    "unauthorized": "🔒 Доступ отозван или ключ недействителен. Обратитесь к администратору.",
    "wrong_channel": "🔒 Для этого бота нужен персональный ключ канала Telegram.",
    "identity_conflict": "🔒 Привязка аккаунта не совпадает. Обратитесь к администратору.",
    "subscription_required": "📋 Активный тариф не назначен. Обратитесь к администратору.",
    "credit_limit_exceeded": "💳 Кредиты закончились. Откройте «Мой тариф» для проверки лимита.",
    "request_limit_exceeded": "📊 Лимит запросов исчерпан. Откройте «Мой тариф».",
    "spend_limit_exceeded": "📊 Достигнут лимит доступа. Обратитесь к администратору.",
    "model_not_found": "🤖 Эта модель недоступна. Выберите другую в разделе «Модели».",
    "provider_rate_limit": "⏳ Модель перегружена. Попробуйте позже; автоповтора не будет.",
    "rate_limit": "⏳ Слишком много запросов. Попробуйте позже.",
    "provider_unavailable": "🛠 Модель временно недоступна. Попробуйте позже.",
    "unavailable": "🛠 Шлюз сейчас недоступен. Попробуйте позже.",
    "outcome_unknown": (
        "⚠️ Не удалось подтвердить результат запроса. Он мог быть выполнен и учтён шлюзом. "
        "Автоматически повторять его не буду. Проверьте лимиты перед новым запросом."
    ),
    "invalid_response": (
        "⚠️ Шлюз вернул некорректный ответ. Расход мог быть учтён. Сообщите администратору."
    ),
    "invalid_request": "⚠️ Запрос не принят. Начните новый чат или выберите другую модель.",
    "response_too_large": "📄 Ответ превысил защитный лимит клиента. Сообщите администратору.",
}


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120,
        http: httpx.AsyncClient | None = None,
        max_response_chars: int = 100000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10))
        self._owned = http is None
        self.max_response_chars = max_response_chars

    async def close(self) -> None:
        if self._owned:
            await self.http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json=None,
        chargeable: bool = False,
        request_id: str | None = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            response = await self.http.request(
                method,
                self.base_url + path,
                headers=headers,
                json=json,
            )
        except httpx.RequestError as exc:
            raise GatewayError("outcome_unknown" if chargeable else "unavailable") from exc
        try:
            data = response.json()
        except (ValueError, UnicodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not response.is_success:
            error = data.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if code not in ERROR_TEXT:
                code = {
                    401: "unauthorized",
                    403: "wrong_channel",
                    409: "identity_conflict",
                    402: "subscription_required",
                    404: "model_not_found",
                    400: "invalid_request",
                    422: "invalid_request",
                    429: "rate_limit",
                }.get(response.status_code, "provider_unavailable")
            raise GatewayError(code, response.status_code)
        return data

    async def profile(self, token: str) -> dict:
        data = await self._request("GET", "/v1/me", token)
        if data.get("channel") != "telegram":
            raise GatewayError("wrong_channel")
        if not isinstance(data.get("account"), dict) or not isinstance(data.get("allowance"), dict):
            raise GatewayError("invalid_response")
        return data

    async def bind(self, token: str, user_id: int, username: str | None) -> None:
        await self._request(
            "POST", "/v1/me/telegram", token, json={"telegram_id": user_id, "username": username}
        )

    async def models(self, token: str) -> list[str]:
        data = await self._request("GET", "/v1/models", token)
        rows = data.get("data", [])
        if not isinstance(rows, list):
            raise GatewayError("invalid_response")
        models = list(
            dict.fromkeys(
                item["id"]
                for item in rows
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
            )
        )
        if not models:
            raise GatewayError("model_not_found")
        return models

    async def chat(
        self, token: str, model: str, messages: list[dict[str, str]], request_id: str
    ) -> str:
        data = await self._request(
            "POST",
            "/v1/chat/completions",
            token,
            json={"model": model, "messages": messages, "stream": False},
            chargeable=True,
            request_id=request_id,
        )
        try:
            answer = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("invalid_response") from exc
        if not isinstance(answer, str) or not answer.strip():
            raise GatewayError("invalid_response")
        if len(answer) > self.max_response_chars:
            raise GatewayError("response_too_large")
        return answer
