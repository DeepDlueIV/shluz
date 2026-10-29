"""HTTP-управление общим Store/Controller: никогда не вызывает модель и не пишет в чат."""

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from telegram_bot.gateway import GatewayError
from telegram_bot.miniapp.auth import Sessions, validate_init_data
from telegram_bot.storage import DialogLimit
from telegram_bot.ui import STYLE_LABELS

STATIC = Path(__file__).parent / "static"
DESTRUCTIVE = {"clear", "delete", "forget"}
DIALOG_ACTIONS = {"activate", "rename", "export", "clear", "delete"}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Login(Input):
    init_data: str = Field(min_length=1, max_length=16384)


class Intent(Input):
    action: Literal["clear", "delete", "forget"]
    target: str = Field(default="", max_length=64)


class Action(Input):
    action: Literal[
        "new", "activate", "rename", "export", "clear", "delete", "forget", "model", "style", "stop"
    ]
    target: str = Field(default="", max_length=200)
    value: str = Field(default="", max_length=200)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")
    confirmation: str = Field(default="", max_length=64)


def create_miniapp(controller):
    settings, store, gateway = controller.settings, controller.store, controller.gateway
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions = Sessions()
    app.state.sessions = sessions
    locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
    parsed = urlsplit(settings.miniapp_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    @app.middleware("http")
    async def boundary(request, call_next):
        if settings.interface != "miniapp":
            return JSONResponse({"detail": "Панель выключена"}, status_code=404)
        if request.url.path.startswith("/miniapp/api/"):
            sent_origin = request.headers.get("origin")
            if (
                (sent_origin is not None and sent_origin != origin)
                or (request.method != "GET" and sent_origin != origin)
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 16384:
                    return JSONResponse({"detail": "Запрос слишком большой"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": "default-src 'self'; "
                "script-src 'self' https://telegram.org; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; form-action 'none'; "
                "frame-ancestors https://web.telegram.org https://*.telegram.org",
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Не отражаем initData, токены и строки пользователя в ответе об ошибке.
        return JSONResponse({"detail": "Некорректное действие"}, status_code=422)

    @app.exception_handler(GatewayError)
    async def gateway_error(request, exc):
        code = 403 if exc.status in {401, 403} else 503
        return JSONResponse(
            {
                "detail": "Доступ закрыт"
                if code == 403
                else "Шлюз временно недоступен. Попробуйте позже.",
                "code": exc.code,
            },
            status_code=code,
        )

    async def get_token(user):
        if settings.trial_owner_id and user["id"] != settings.trial_owner_id:
            raise HTTPException(403, "Сейчас идёт закрытая проверка.")
        legacy = controller.access.token_for(user["id"])
        if settings.connector_file:
            admission = await gateway.onboard(
                user["id"],
                user["first_name"],
                user["username"],
                legacy_token=legacy,
            )
            if admission["state"] != "active":
                raise HTTPException(
                    403,
                    "Доступ ожидает одобрения"
                    if admission["state"] == "pending"
                    else "Доступ закрыт",
                )
            return SecretStr(admission["token"])
        if legacy is None:
            raise HTTPException(403, "Доступ закрыт")
        return legacy

    async def authorized(request: Request):
        scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
        session = sessions.get(bearer) if scheme == "Bearer" else None
        if session is None:
            raise HTTPException(401, "Откройте меню заново в Telegram")
        # Не кешируем разрешение: архивирование/отзыв действует и в уже открытой панели.
        token = await get_token(session.user)
        remote = await gateway.profile(token.get_secret_value())
        if remote["account"]["id"] != session.account_id:
            raise HTTPException(403, "Привязка аккаунта изменилась")
        session.gateway_token = token
        return session, remote

    Auth = Annotated[tuple, Depends(authorized)]

    def snapshot(uid):
        with store.connection() as db:
            rows = db.execute(
                "SELECT d.id,d.title,d.updated_at,COUNT(t.id),MAX(t.updated_at) "
                "FROM dialogs d LEFT JOIN turns t ON t.dialog_id=d.id "
                "WHERE d.user_id=? GROUP BY d.id ORDER BY d.id",
                (uid,),
            ).fetchall()
        raw = json.dumps([list(row) for row in rows], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def state(uid, remote):
        local = store.profile(uid)
        dialogs = store.dialogs(uid, limit=settings.max_dialogs)
        return {
            "account": {"id": remote["account"]["id"]},
            "plan": remote.get("plan"),
            "allowance": remote.get("allowance", {}),
            "wallet": remote.get("wallet"),
            "model": local["model"],
            "style": local["style"],
            "active_dialog": {
                "id": local["active_dialog"],
                "title": store.dialog_for(uid, local["active_dialog"])["title"],
            },
            "dialogs": [
                {"id": d["id"], "title": d["title"], "updated_at": d["updated_at"]} for d in dialogs
            ],
            "generation": {
                "busy": uid in controller.jobs,
                "request_id": controller.job_ids.get(uid),
            },
            "retention_days": settings.history_retention_days,
            "support": settings.support_contact,
        }

    @app.post("/miniapp/api/session")
    async def login(body: Login):
        try:
            user = validate_init_data(body.init_data, settings.bot_token.get_secret_value())
        except ValueError as exc:
            raise HTTPException(401, "Откройте меню из личного чата Telegram") from exc
        lock = locks.setdefault(user["id"], asyncio.Lock())
        async with lock:
            token = await get_token(user)
            remote = await gateway.profile(token.get_secret_value())
            local = store.ensure_user(user["id"], user["first_name"])
            if local["account_id"] not in {None, remote["account"]["id"]}:
                raise HTTPException(403, "Привязка аккаунта не совпадает")
            if local["account_id"] is None:
                await gateway.bind(token.get_secret_value(), user["id"], user["username"])
                store.bind_account(user["id"], remote["account"]["id"])
            try:
                bearer, session = sessions.create(
                    body.init_data, user, remote["account"]["id"], token
                )
            except ValueError as exc:
                raise HTTPException(429, "Слишком много открытых панелей") from exc
        return {"session": bearer, "expires_in": max(0, int(session.expires - time.monotonic()))}

    @app.get("/miniapp/api/state")
    async def get_state(auth: Auth):
        session, remote = auth
        return state(session.user["id"], remote)

    @app.get("/miniapp/api/models")
    async def get_models(auth: Auth):
        session, _ = auth
        return {"models": await gateway.models(session.gateway_token.get_secret_value())}

    @app.get("/miniapp/api/account")
    async def get_account(auth: Auth):
        session, _ = auth
        token = session.gateway_token.get_secret_value()
        usage, plans = await asyncio.gather(gateway.usage(token), gateway.plans(token))
        return {
            "summary": usage.get("summary", {}),
            "plans": plans.get("data", [])[:50],
            "payments_enabled": False,
        }

    def check_idle(uid):
        if uid in controller.jobs:
            raise HTTPException(409, "Ответ ещё готовится. Дождитесь его или остановите генерацию.")

    def own_dialog(uid, target):
        if not store.dialog_for(uid, target):
            raise HTTPException(404, "Диалог уже удалён или недоступен")

    @app.post("/miniapp/api/confirm")
    async def confirm(body: Intent, auth: Auth):
        session, _ = auth
        uid = session.user["id"]
        check_idle(uid)
        if body.action != "forget":
            own_dialog(uid, body.target)
        elif body.target:
            raise HTTPException(422, "Для всех диалогов цель не указывается")
        nonce = secrets.token_urlsafe(24)
        session.confirmation = (
            body.action,
            body.target,
            nonce,
            snapshot(uid),
            time.monotonic() + 120,
        )
        return {"confirmation": nonce}

    @app.post("/miniapp/api/action")
    async def action(body: Action, auth: Auth):
        session, remote = auth
        uid = session.user["id"]
        lock = locks.setdefault(uid, asyncio.Lock())
        async with lock:
            signature = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
            prior = session.results.get(body.request_id)
            if prior:
                if not secrets.compare_digest(prior[0], signature):
                    raise HTTPException(409, "Повторный идентификатор другого действия")
                return state(uid, remote)
            # Любые await выполняются ДО проверки активной генерации и изменения Store.
            models = (
                await gateway.models(session.gateway_token.get_secret_value())
                if body.action == "model"
                else []
            )
            if body.action not in {"stop", "export"}:
                check_idle(uid)
            if body.action in DIALOG_ACTIONS:
                own_dialog(uid, body.target)
            if body.action in DESTRUCTIVE:
                record, session.confirmation = session.confirmation, None
                if (
                    not record
                    or record[:2] != (body.action, body.target)
                    or not secrets.compare_digest(record[2], body.confirmation)
                    or record[4] <= time.monotonic()
                    or record[3] != snapshot(uid)
                ):
                    raise HTTPException(409, "Подтверждение устарело. Подтвердите действие снова.")
            try:
                if body.action == "new":
                    store.new_dialog(uid)
                elif body.action == "activate":
                    store.activate_dialog(uid, body.target)
                elif body.action == "rename":
                    store.rename_dialog(uid, body.target, body.value)
                elif body.action == "clear":
                    store.clear_dialog(uid, body.target)
                elif body.action == "delete":
                    store.delete_dialog(uid, body.target)
                elif body.action == "forget":
                    store.forget_dialogs(uid)
                elif body.action == "model":
                    if body.value not in models:
                        raise HTTPException(403, "Модель недоступна вашему тарифу в Telegram")
                    store.set_model(uid, body.value)
                elif body.action == "style":
                    if body.value not in STYLE_LABELS:
                        raise HTTPException(422, "Неизвестный стиль ответа")
                    store.set_style(uid, body.value)
                elif body.action == "stop":
                    job = controller.jobs.get(uid)
                    if not job or body.target != controller.job_ids.get(uid):
                        raise HTTPException(409, "Этот запрос уже завершён")
                    job.cancel()
                elif body.action == "export":
                    # В JSON, не в Telegram; скачивание формируется на клиенте.
                    return {"text": store.export_dialog(uid, body.target)}
            except DialogLimit as exc:
                raise HTTPException(409, "Достигнут лимит диалогов. Удалите ненужный.") from exc
            except ValueError as exc:
                raise HTTPException(422, "Название должно содержать от 1 до 60 символов") from exc
            controller.rename_targets.pop(uid, None)
            controller.confirmations.clear(uid)
            session.confirmation = None
            result = state(uid, remote)
            if len(session.results) >= 64:
                session.results.pop(next(iter(session.results)))
            session.results[body.request_id] = (signature, True)
            return result

    @app.get("/miniapp/health")
    async def health():
        return {"status": "ok"}

    @app.get("/miniapp")
    @app.get("/miniapp/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/miniapp/assets", StaticFiles(directory=STATIC), name="miniapp-assets")
    return app
