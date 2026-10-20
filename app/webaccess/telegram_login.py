"""Официальная Telegram Login JS library → RS256 ID-token → серверная сессия.

Не принимаем непроверенный user/Telegram ID и не требуем BotFather token в браузере.
JWKS читается только у Telegram; cookie/nonce/CSRF связывают вход с его инициатором.
"""

import asyncio
import json
import secrets
import time

import httpx
import jwt
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from app.db.models import Account, DemoGrant, Identity
from app.product.service import grant_demo, locked_session, policy
from app.telegram.service import transaction
from app.webaccess.models import DemoHold, TelegramLoginChallenge
from app.webaccess.security import (
    client_network,
    consume,
    control,
    current_session,
    digest,
    new_session,
    reject,
    require_enabled,
    set_session_cookie,
)

ISSUER = "https://oauth.telegram.org"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
router = APIRouter(prefix="/v1/web/auth/telegram", tags=["web-access"])


class TelegramUnavailable(Exception):
    pass


class TelegramVerifier:
    """Маленький ограниченный cache только публичных ключей; не кеш пользователей."""

    def __init__(self):
        self.keys = {}
        self.checked_at = -1000.0
        self.refresh_lock = asyncio.Lock()

    async def key_for(self, kid):
        now = time.monotonic()
        if kid in self.keys and now - self.checked_at < 300:
            return self.keys[kid]
        async with self.refresh_lock:
            now = time.monotonic()
            if kid in self.keys and now - self.checked_at < 300:
                return self.keys[kid]
            if now - self.checked_at < 30:
                if not self.keys:
                    raise TelegramUnavailable()
                return None
            # Неизвестные kid не создают бесконечные сетевые запросы.
            self.checked_at = now
            self.keys = {}  # Не продлеваем старые ключи при ошибке обновления.
            try:
                async with (
                    asyncio.timeout(8),
                    httpx.AsyncClient(timeout=6, trust_env=False, follow_redirects=False) as c,
                    c.stream("GET", JWKS_URL) as r,
                ):
                    if r.status_code != 200:
                        raise TelegramUnavailable()
                    raw = bytearray()
                    async for chunk in r.aiter_bytes(chunk_size=4096):
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise TelegramUnavailable()
                body = json.loads(raw)
                rows = body.get("keys")
                if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
                    raise TelegramUnavailable()
                parsed = {}
                for item in rows:
                    if not isinstance(item, dict) or item.get("kty") != "RSA":
                        continue
                    if item.get("alg", "RS256") != "RS256" or item.get("use", "sig") != "sig":
                        continue
                    name = item.get("kid")
                    if not isinstance(name, str) or not 1 <= len(name) <= 128 or name in parsed:
                        raise TelegramUnavailable()
                    key = jwt.PyJWK.from_dict(item, algorithm="RS256").key
                    if not 2048 <= key.key_size <= 8192:
                        raise TelegramUnavailable()
                    parsed[name] = key
                if not parsed:
                    raise TelegramUnavailable()
                self.keys = parsed
            except (httpx.HTTPError, ValueError, TypeError, AttributeError,
                    jwt.PyJWTError, TimeoutError) as exc:
                raise TelegramUnavailable() from exc
            return self.keys.get(kid)

    async def verify(self, token, client_id, nonce_hash, secret, created):
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (
                header.get("alg") != "RS256" or header.get("crit")
                or not isinstance(kid, str) or not 1 <= len(kid) <= 128
            ):
                return None
            key = await self.key_for(kid)
            if key is None:
                return None
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=ISSUER, audience=client_id,
                leeway=30,
                options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce", "id"],
                         "strict_aud": True},
            )
            now = int(time.time())
            nonce, user_id = claims["nonce"], claims["id"]
            if (
                type(user_id) is not int or not 0 < user_id < 2**63
                or not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255
                or type(claims["iat"]) is not int or type(claims["exp"]) is not int
                or not created - 30 <= claims["iat"] <= now + 30
                or claims["exp"] <= claims["iat"]
                or not isinstance(nonce, str) or not 32 <= len(nonce) <= 128
                or not secrets.compare_digest(digest(secret, "telegram-nonce", nonce), nonce_hash)
            ):
                return None
            # Telegram profile id соответствует Bot API. OIDC sub может отличаться.
            name = claims.get("name")
            return {"id": user_id, "name": name[:200] if isinstance(name, str) and name else
                    "Пользователь Telegram"}
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, UnicodeError):
            return None


class LoginResult(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    challenge_id: str = Field(min_length=32, max_length=128)
    id_token: str = Field(min_length=32, max_length=16384)


def settings_for(request):
    settings = require_enabled(request)
    if not settings.telegram_client_id:
        reject("telegram_login_not_configured", 503, "Вход через Telegram пока не подключён.")
    return settings


@router.post("/start")
def start_login(request: Request):
    settings, db = settings_for(request), request.app.state.database
    _, network = client_network(request, settings)
    key, now = settings.secret.get_secret_value(), int(time.time())
    raw, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with locked_session(db) as s:
        browser = current_session(request, s, csrf=True)
        cfg = control(s)
        consume(s, "tg-login-start:" + network, cfg.otp_verify_minute, 60, now)
        consume(s, "tg-login-start:global", cfg.otp_verify_global_minute, 60, now)
        for old in s.scalars(select(TelegramLoginChallenge).where(
            TelegramLoginChallenge.session_id == browser.id,
            TelegramLoginChallenge.used.is_(False),
        )):
            old.used = True
        s.add(TelegramLoginChallenge(
            id=digest(key, "telegram-flow", raw), session_id=browser.id,
            nonce_hash=digest(key, "telegram-nonce", nonce), created=now,
            expires=now + 300, attempts=0, used=False,
        ))
    return {"challenge_id": raw, "client_id": settings.telegram_client_id,
            "nonce": nonce, "scope": ["profile"], "expires_in": 300}


def valid_flow(row, browser, now):
    return bool(row and row.session_id == browser.id and not row.used and row.expires > now)


def account_for(s, browser, user):
    identity = s.scalar(select(Identity).where(
        Identity.provider == "telegram", Identity.subject == str(user["id"]),
    ))
    guest = s.get(Account, browser.account_id) if browser.account_id else None
    if guest and guest.status != "active":
        reject("access_unavailable", 401)
    if guest and guest.role == "guest" and s.scalar(select(DemoHold.event_id).where(
        DemoHold.account_id == guest.id, DemoHold.active.is_(True),
        DemoHold.expires > int(time.time()),
    ).limit(1)):
        reject("demo_in_progress", 409, "Дождитесь окончания демо и повторите вход.")
    if identity:
        account = s.get(Account, identity.account_id)
        if not account or account.status != "active" or account.role == "service":
            reject("access_unavailable", 401)
        if guest and guest.role == "guest" and guest.id != account.id:
            old = s.get(DemoGrant, guest.id)
            if old:
                grant_demo(s, account.id).requests_used += old.requests_used
            guest.status = "inactive"
    else:
        if guest and guest.role == "guest":
            account = guest
            account.role, account.display_name = "user", user["name"]
        else:
            account = Account(display_name=user["name"])
            s.add(account)
            s.flush()
        s.add(Identity(provider="telegram", subject=str(user["id"]), account_id=account.id))
        if policy(s).mode == "demo":
            grant_demo(s, account.id)
    # TelegramAdmission и подписки не меняем: вход на сайт не разблокирует канал бота.
    return account


@router.post("/verify")
async def verify_login(payload: LoginResult, request: Request, response: Response):
    settings, db = settings_for(request), request.app.state.database
    key = settings.secret.get_secret_value()
    _, network = client_network(request, settings)
    flow_id, now = digest(key, "telegram-flow", payload.challenge_id), int(time.time())
    with locked_session(db) as s:
        browser = current_session(request, s, csrf=True)
        cfg = control(s)
        consume(s, "tg-login-verify:" + network, cfg.otp_verify_minute, 60, now)
        consume(s, "tg-login-verify:global", cfg.otp_verify_global_minute, 60, now)
        row = s.get(TelegramLoginChallenge, flow_id)
        valid = valid_flow(row, browser, now) and row.attempts < 5
        if valid:
            row.attempts += 1
    if not valid:
        reject("telegram_login_expired", 401, "Начните вход через Telegram заново.")
    try:
        user = await request.app.state.telegram_login.verify(
            payload.id_token, settings.telegram_client_id, row.nonce_hash, key, row.created,
        )
    except TelegramUnavailable:
        reject("telegram_login_unavailable", 503, "Telegram временно недоступен.")
    if user is None:
        reject("telegram_login_invalid", 401, "Не удалось подтвердить вход через Telegram.")
    # Один порядок блокировок: сначала Telegram identity, затем демо/сессии.
    # Никакие внешние запросы не выполняются под SQL-блокировками.
    with transaction(db) as s:
        if db.engine.dialect.name == "postgresql":
            s.execute(text("SELECT pg_advisory_xact_lock(1397247035)"))
        browser = current_session(request, s, csrf=True)
        row = s.get(TelegramLoginChallenge, flow_id)
        if not valid_flow(row, browser, int(time.time())):
            reject("telegram_login_expired", 401, "Начните вход через Telegram заново.")
        account = account_for(s, browser, user)
        row.used, browser.revoked = True, True
        raw, new = new_session(s, settings, account.id, True)
    set_session_cookie(response, raw, settings, True)
    return {"authenticated": True, "account_id": new.account_id,
            "csrf_token": digest(key, "csrf", raw)}
