"""Явные сетевые/CSRF границы и атомарные SQL-счётчики, без fingerprint-трекера."""

import hashlib
import hmac
import ipaddress
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.product.service import locked_session
from app.webaccess.models import (
    CaptchaUse,
    DemoControl,
    OtpChallenge,
    WebBucket,
    WebRequest,
    WebSession,
)


def reject(code, status=403, message="Запрос отклонён. Попробуйте позже."):
    raise HTTPException(status, detail={"code": code, "message": message})


def digest(key, purpose, value):
    return hmac.new(key.encode(), (purpose + "\0" + value).encode(), hashlib.sha256).hexdigest()


def control(session):
    row = session.get(DemoControl, "default")
    if row is None:
        row = DemoControl(id="default")
        session.add(row)
        session.flush()
    return row


def consume(session, key, limit, seconds, now=None):
    """Вызывается только под общей короткой SQL-блокировкой."""
    now = int(time.time()) if now is None else now
    row = session.get(WebBucket, key)
    if row and row.expires <= now:
        session.delete(row)
        session.flush()
        row = None
    if limit <= 0 or (row and row.count >= limit):
        reject("too_many_requests", 429, "Слишком много попыток. Попробуйте позже.")
    if row:
        row.count += 1
    else:
        session.add(WebBucket(id=key, count=1, expires=now + seconds))
    session.flush()


def prune(session, now, limit=100):
    # Лимит работы уборки: не сканируем/не удаляем всю таблицу при каждом запросе.
    for model in (WebBucket, CaptchaUse, OtpChallenge, WebRequest):
        ids = list(session.scalars(select(model.id).where(model.expires <= now).limit(limit)))
        if ids:
            session.execute(delete(model).where(model.id.in_(ids)))
    ids = list(session.scalars(select(WebSession.id).where(WebSession.expires <= now).limit(limit)))
    if ids:
        session.execute(delete(WebSession).where(WebSession.id.in_(ids)))


def normalized_ip(value):
    ip = ipaddress.ip_address(value.strip())
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def client_network(request, settings):
    try:
        peer = normalized_ip(request.client.host)
        trusted = [ipaddress.ip_network(v, strict=False) for v in settings.trusted_proxies]
        if any(peer in n for n in trusted):
            forwarded = request.headers.get("x-forwarded-for", "")
            if len(forwarded) > 1024:
                raise ValueError()
            chain = [normalized_ip(x) for x in forwarded.split(",")] if forwarded else []
            chain.append(peer)
            while len(chain) > 1 and any(chain[-1] in n for n in trusted):
                chain.pop()
            peer = chain[-1]
        # Все остальные заголовки (включая CF-Connecting-IP) не являются доказательством IP.
        prefix = ipaddress.ip_network(f"{peer}/{64 if peer.version == 6 else 32}", strict=False)
        key = settings.secret.get_secret_value()
        return str(peer), digest(key, "network", str(prefix))
    except (ValueError, AttributeError):
        reject("invalid_network", 400, "Не удалось проверить сетевое подключение.")


def require_enabled(request):
    settings = request.app.state.web_settings
    if not settings.enabled:
        reject("web_not_configured", 503, "Вход на сайте пока не подключён.")
    return settings


def require_origin(request, settings):
    if request.headers.get("origin") not in settings.origins:
        reject("origin_rejected")
    if request.headers.get("sec-fetch-site") == "cross-site":
        reject("cross_site_rejected")


def current_session(request, session, *, csrf=False):
    settings = require_enabled(request)
    if csrf:
        require_origin(request, settings)
    raw = request.cookies.get(settings.cookie_name, "")
    if not 32 <= len(raw) <= 128:
        reject("session_required", 401, "Обновите страницу и повторите попытку.")
    key = settings.secret.get_secret_value()
    row = session.get(WebSession, digest(key, "session", raw))
    if row is None or row.revoked or row.expires <= int(time.time()):
        reject("session_expired", 401, "Сессия истекла. Войдите ещё раз.")
    if csrf and not secrets.compare_digest(
        request.headers.get("x-csrf-token", "").encode(), digest(key, "csrf", raw).encode()
    ):
        reject("csrf_rejected")
    return row


def new_session(session, settings, account_id=None, authenticated=False):
    raw = secrets.token_urlsafe(32)
    now = int(time.time())
    row = WebSession(
        id=digest(settings.secret.get_secret_value(), "session", raw),
        account_id=account_id,
        authenticated=authenticated,
        created=now,
        expires=now + (604800 if authenticated else 2592000),
    )
    session.add(row)
    session.flush()
    return raw, row


def set_session_cookie(response, raw, settings, authenticated=False):
    response.set_cookie(
        settings.cookie_name,
        raw,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="strict",
        path="/",
        max_age=604800 if authenticated else 2592000,
    )
    response.headers["Cache-Control"] = "no-store"


async def verify_captcha(request, token, action):
    settings = require_enabled(request)
    if not token or len(token) > 2048:
        reject("verification_required", 403, "Подтвердите, что вы не робот.")
    ip, network = client_network(request, settings)
    db = request.app.state.database
    key = settings.secret.get_secret_value()
    token_id = digest(key, "captcha", token)
    now = int(time.time())
    with locked_session(db) as session:
        cfg = control(session)
        consume(session, "captcha:" + network, cfg.captcha_minute, 60, now)
        consume(session, "captcha:global", cfg.captcha_global_minute, 60, now)
        prune(session, now)
        if session.get(CaptchaUse, token_id):
            reject("verification_reused", 403, "Проверка уже использована. Повторите её.")
        session.add(CaptchaUse(id=token_id, expires=now + 600))
    # Не повторяем неоднозначный запрос к проверяющему сервису автоматически.
    import asyncio

    try:
        async with asyncio.timeout(10):
            valid = await request.app.state.web_captcha.verify(token, ip, action)
    except TimeoutError:
        valid = False
    if not valid:
        reject("verification_failed", 403, "Не удалось подтвердить проверку. Повторите её.")
    return network
