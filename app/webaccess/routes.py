"""Web cookie API: гостю только ограниченный демо-вызов, не служебный Bearer API."""

import time
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import AuthenticatedPrincipal
from app.api.routes.chat import chat_completions
from app.api.routes.me import my_account
from app.api.schemas import ChatCompletionRequestSchema
from app.db.models import Account, ModelPolicy
from app.product.routes import own_usage, plans
from app.product.service import (
    DEFAULT_MODELS,
    active_subscription,
    allowed_models,
    demo_allowance,
    grant_demo,
    locked_session,
    models_in,
    policy,
)
from app.webaccess import tokens as tokens_service
from app.webaccess.models import WebRequest, WebSession
from app.webaccess.otp import request_code, verify_code
from app.webaccess.security import (
    client_network,
    consume,
    control,
    current_session,
    digest,
    new_session,
    prune,
    reject,
    require_enabled,
    require_origin,
    set_session_cookie,
    verify_captcha,
)

router = APIRouter(prefix="/v1/web", tags=["web-access"])


class OtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    turnstile_token: str = Field(min_length=1, max_length=2048)


class OtpVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: str = Field(min_length=32, max_length=128)
    code: str = Field(pattern=r"^\d{6}$")


class WebChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=200000)
    request_id: UUID
    turnstile_token: str = Field(default="", max_length=2048)
    model: str | None = Field(default=None, max_length=200)


def principal_for(row, db):
    if not row.account_id:
        reject("account_required", 401, "Сначала попробуйте демо или войдите.")
    with db.session() as s:
        account = s.get(Account, row.account_id)
        if not account or account.status != "active":
            reject("access_unavailable", 401, "Доступ к аккаунту закрыт.")
    return AuthenticatedPrincipal(row.account_id, "web", "web:" + row.id)


@router.post("/session")
def session_start(request: Request, response: Response):
    settings = require_enabled(request)
    require_origin(request, settings)
    _, network = client_network(request, settings)
    key, now = settings.secret.get_secret_value(), int(time.time())
    with locked_session(request.app.state.database) as s:
        raw = request.cookies.get(settings.cookie_name, "")
        row = s.get(WebSession, digest(key, "session", raw)) if 32 <= len(raw) <= 128 else None
        if not row or row.revoked or row.expires <= now:
            cfg = control(s)
            consume(s, "session:" + network, cfg.sessions_hourly, 3600, now)
            consume(s, "session:global", cfg.sessions_global_daily, 86400, now)
            prune(s, now)
            raw, row = new_session(s, settings)
        data = {
            "csrf_token": digest(key, "csrf", raw),
            "authenticated": row.authenticated,
            "expires_at": row.expires,
            "captcha_sitekey": settings.captcha_sitekey,
            "demo": {
                "requests": policy(s).demo_requests,
                "max_input_chars": policy(s).demo_max_input_chars,
                "max_output_tokens": policy(s).demo_max_output_tokens,
            },
        }
    set_session_cookie(response, raw, settings, row.authenticated)
    return data


@router.get("/me")
def web_me(request: Request, response: Response):
    db = request.app.state.database
    with db.session() as s:
        row = current_session(request, s)
        authenticated = row.authenticated
        if not row.account_id:
            return {
                "authenticated": False,
                "account": None,
                "plan": {"kind": "demo"},
                "allowance": {
                    "requests": {
                        "limit": policy(s).demo_requests,
                        "used": 0,
                        "remaining": policy(s).demo_requests,
                    },
                    "resets_at": None,
                },
            }
    data = my_account(principal_for(row, db), db, response)
    data["authenticated"] = authenticated
    return data


@router.get("/models")
def web_models(request: Request):
    db = request.app.state.database
    with db.session() as s:
        row = current_session(request, s)
        if row.account_id:
            account = s.get(Account, row.account_id)
            if not account or account.status != "active":
                reject("access_unavailable", 401)
            allowed = allowed_models(s, row.account_id, "web")
        else:
            channel = s.get(ModelPolicy, "channel:web")
            allowed = models_in(policy(s).demo_models_json) & (
                models_in(channel.models_json) if channel else DEFAULT_MODELS
            )
    return {
        "object": "list",
        "data": [
            {"id": m.id, "object": "model", "owned_by": m.provider}
            for m in request.app.state.provider_registry.list_models()
            if m.id in allowed
        ],
    }


@router.get("/plans")
def web_plans(request: Request, response: Response):
    with request.app.state.database.session() as s:
        row = current_session(request, s)
    # Каталог не даёт доступа к аккаунту или платным функциям.
    p = AuthenticatedPrincipal(row.account_id or "guest-catalog", "web", "web:" + row.id)
    return plans(p, request.app.state.database, response)


@router.get("/me/usage")
def web_usage(
    request: Request,
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    db = request.app.state.database
    with db.session() as s:
        row = current_session(request, s)
    return own_usage(principal_for(row, db), db, response, limit, offset)


@router.post("/auth/otp/request")
async def otp_request(payload: OtpRequest, request: Request):
    return await request_code(request, payload)


@router.post("/auth/otp/verify")
def otp_verify(payload: OtpVerify, request: Request, response: Response):
    raw, row = verify_code(request, payload)
    settings = request.app.state.web_settings
    set_session_cookie(response, raw, settings, True)
    return {
        "authenticated": True,
        "account_id": row.account_id,
        "csrf_token": digest(settings.secret.get_secret_value(), "csrf", raw),
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    settings = require_enabled(request)
    with locked_session(request.app.state.database) as s:
        current_session(request, s, csrf=True).revoked = True
    response.delete_cookie(
        settings.cookie_name,
        path="/",
        secure=settings.secure_cookie,
        httponly=True,
        samesite="strict",
    )
    return {"authenticated": False}


class GuardedUsage:
    """Тот же биллинг, с дополнительным допуском в его атомарной транзакции."""

    def __init__(self, service, request, session_id, request_id, network, captcha_verified):
        self.service, self.request = service, request
        self.session_id, self.request_id, self.network = session_id, request_id, network
        self.captcha_verified = captcha_verified

    def authorize_request(self, **kwargs):
        def guard(s, event):
            row = current_session(self.request, s, csrf=True)
            if row.id != self.session_id or row.account_id != event.account_id:
                reject("session_changed", 401)
            key = row.id + ":" + str(self.request_id)
            if s.get(WebRequest, key):
                reject(
                    "request_already_used", 409, "Этот запрос уже отправлялся. Не повторяйте его."
                )
            cfg = control(s)
            if event.quota_kind == "demo":
                if not self.captcha_verified:
                    reject("verification_required", 403)
                if not row.authenticated and policy(s).mode != "demo":
                    reject("demo_closed", 403)
                consume(s, "free:" + self.network, cfg.network_daily, 86400)
            s.add(
                WebRequest(
                    id=key, event_id=event.id, expires=max(row.expires, int(time.time()) + 86400)
                )
            )

        return self.service.authorize_request(web_context=guard, **kwargs)

    def record_success(self, **kwargs):
        return self.service.record_success(**kwargs)

    def record_failure(self, *args, **kwargs):
        return self.service.record_failure(*args, **kwargs)


@router.post("/chat/completions", responses={200: {"content": {"text/event-stream": {}}}})
async def web_chat(payload: WebChat, request: Request):
    settings, db = require_enabled(request), request.app.state.database
    if not payload.prompt.strip():
        reject("empty_prompt", 422, "Введите вопрос.")
    with db.session() as s:
        browser = current_session(request, s, csrf=True)
        if browser.account_id:
            account = s.get(Account, browser.account_id)
            if not account or account.status != "active":
                reject("access_unavailable", 401)
        demo = not browser.account_id or not active_subscription(
            s, browser.account_id, source="web"
        )
        cfg = policy(s)
        if s.get(WebRequest, browser.id + ":" + str(payload.request_id)):
            reject("request_already_used", 409)
        if demo:
            if cfg.mode != "demo" and not browser.authenticated:
                reject("demo_closed", 403, "Демо временно закрыто.")
            if payload.model is not None:
                reject("model_selection_unavailable", 403, "Модель демо выбирает сервис.")
            if len(payload.prompt) > cfg.demo_max_input_chars:
                reject("input_too_large", 400, "Сократите вопрос до указанного числа символов.")
            remaining = demo_allowance(s, browser.account_id) if browser.account_id else None
            if remaining is not None and remaining["remaining"] <= 0:
                reject("demo_exhausted", 429, "Пробный запрос уже использован.")
    _, network = client_network(request, settings)
    if demo:
        network = await verify_captcha(request, payload.turnstile_token, "demo_chat")
    with locked_session(db) as s:
        browser = current_session(request, s, csrf=True)
        if not browser.account_id:
            if policy(s).mode != "demo":
                reject("demo_closed")
            account = Account(display_name="Гость сайта", role="guest")
            s.add(account)
            s.flush()
            browser.account_id = account.id
            grant_demo(s, account.id)
        allowed = allowed_models(s, browser.account_id, "web")
        if not allowed:
            reject("model_not_allowed", 403, "Модель демо пока не подключена.")
        model = payload.model or sorted(allowed)[0]
        session_id = browser.id
    p = principal_for(browser, db)
    body = ChatCompletionRequestSchema(
        model=model,
        stream=True,
        stream_options={"include_usage": True},
        messages=[{"role": "user", "content": payload.prompt}],
    )
    service = GuardedUsage(
        request.app.state.usage_service, request, session_id, payload.request_id, network, demo
    )
    result = await chat_completions(body, request, p, request.app.state.provider_registry, service)
    return result


async def web_http_error(request, exc):
    if request.url.path.startswith("/v1/") and not request.url.path.startswith("/v1/web/"):
        # API-клиенты получают OpenAI-стиль; detail не эхом — там может быть секрет.
        # Заголовки исключения (например, WWW-Authenticate) сохраняются.
        if isinstance(exc.detail, dict):
            message = str(exc.detail.get("message") or "Запрос отклонён.")
            code = str(exc.detail.get("code") or "request_rejected")
        else:
            message = exc.detail if isinstance(exc.detail, str) else "Запрос отклонён."
            code = "request_rejected"
        return JSONResponse(
            {
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": None,
                    "code": code,
                }
            },
            exc.status_code,
            headers={"Cache-Control": "no-store", **(exc.headers or {})},
        )
    if not request.url.path.startswith("/v1/web/"):
        from app.admin.errors import admin_http_error

        return await admin_http_error(request, exc)
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_rejected"}
    return JSONResponse({"error": detail}, exc.status_code, headers={"Cache-Control": "no-store"})


async def web_validation_error(request, exc):
    if request.url.path.startswith("/v1/") and not request.url.path.startswith("/v1/web/"):
        # OpenAI-стиль для API-клиентов; input из Pydantic НЕ отражаем:
        # он может содержать prompt, OTP или cookie.
        return JSONResponse(
            {
                "error": {
                    "message": "Проверьте формат запроса.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_request",
                }
            },
            422,
            headers={"Cache-Control": "no-store"},
        )
    if not request.url.path.startswith("/v1/web/"):
        from app.admin.errors import admin_validation_error

        return await admin_validation_error(request, exc)
    # Не отражаем input из Pydantic: он может содержать OTP, cookie или prompt.
    return JSONResponse(
        {"error": {"code": "invalid_request", "message": "Проверьте введённые данные."}},
        422,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/me/wallet")
def web_wallet(
    request: Request,
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    from app.api.routes.me import own_wallet

    db = request.app.state.database
    with db.session() as session:
        row = current_session(request, session)
    return own_wallet(principal_for(row, db), db, response, limit, offset)


class TokenCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TokenRevokePayload(BaseModel):
    id: str = Field(min_length=1, max_length=64)


class CherryAuthorizePayload(BaseModel):
    port: int = Field(ge=1024, le=65535)


class TokenExchangePayload(BaseModel):
    code: str = Field(min_length=16, max_length=128)


def _account_of(request, csrf: bool):
    """Сессия → активный аккаунт. Гость/неактивный аккаунт — отказ."""
    db = request.app.state.database
    with db.session() as s:
        row = current_session(request, s, csrf=csrf)
        if not row.authenticated or not row.account_id:
            reject("login_required", 401, "Войдите на сайт, чтобы управлять токенами.")
        account = tokens_service._require_active_account(s, row.account_id)
    return row, account


@router.get("/tokens")
def web_tokens_list(request: Request):
    row, account = _account_of(request, csrf=False)
    with locked_session(request.app.state.database) as s:
        return {"data": tokens_service.list_own_tokens(s, account.id)}


@router.post("/tokens")
def web_tokens_create(payload: TokenCreatePayload, request: Request):
    row, account = _account_of(request, csrf=True)
    with locked_session(request.app.state.database) as s:
        token, raw = tokens_service.create_own_token(s, account.id, payload.name)
        data = {
            "id": token.id,
            "name": token.name,
            "token_prefix": token.token_prefix,
            "raw_token": raw,
            "created_at": token.created_at.isoformat(),
        }
    # Сырьё показывается один раз; после закрытия сессии SQLAlchemy оно недоступно.
    return data


@router.post("/tokens/revoke")
def web_tokens_revoke(payload: TokenRevokePayload, request: Request):
    row, account = _account_of(request, csrf=True)
    with locked_session(request.app.state.database) as s:
        tokens_service.revoke_own_token(s, account.id, payload.id)
    return {"revoked": True}


@router.post("/authorize/cherry")
def web_authorize_cherry(payload: CherryAuthorizePayload, request: Request):
    row, account = _account_of(request, csrf=True)
    settings = require_enabled(request)
    with locked_session(request.app.state.database) as s:
        grant = tokens_service.mint_cherry_code(
            s, account.id, row.id, payload.port
        )
    return grant


@router.post("/token")
def web_token_exchange(payload: TokenExchangePayload, request: Request):
    """Обмен одноразового кода на персональный токен (Cherry, без сессии)."""
    with locked_session(request.app.state.database) as s:
        token, raw = tokens_service.exchange_cherry_code(s, payload.code)
        data = {"raw_token": raw, "name": token.name, "token_prefix": token.token_prefix}
    return data
