"""Личные API-токены из кабинета и authorize-флоу настольных клиентов.

Токен хранится только хешем; сырьё показывается один раз в ответе создания.
Одноразовый код authorize: 60 секунд, одна попытка обмена, привязка к аккаунту
и сессии браузера. Вызовы изменений — внутри product.locked_session.
"""

from __future__ import annotations

import secrets
import time

from sqlalchemy import func, select

from app.db.models import Account, ApiToken
from app.db.repositories import create_api_token, generate_api_token, revoke_api_token
from app.webaccess.models import WebAuthorizeCode
from app.webaccess.security import digest, reject

CODE_TTL_SECONDS = 60
CODE_MAX_ATTEMPTS = 3
MINT_WINDOW_SECONDS = 60
MINT_WINDOW_LIMIT = 5
MAX_ACTIVE_TOKENS = 20
CHERRY_CLIENT = "cherry"
CHERRY_TOKEN_NAME = "Cherry Studio"


def _require_active_account(session, account_id: str) -> Account:
    account = session.get(Account, account_id)
    if account is None or account.status != "active":
        reject("access_unavailable", 401, "Аккаунт недоступен. Войдите заново.")
    return account


def list_own_tokens(session, account_id: str) -> list[dict]:
    rows = session.scalars(
        select(ApiToken)
        .where(ApiToken.account_id == account_id)
        .order_by(ApiToken.created_at.desc(), ApiToken.id)
        .limit(100)
    )
    return [
        {
            "id": row.id,
            "name": row.name,
            "source": row.source,
            "token_prefix": row.token_prefix,
            "created_at": row.created_at.isoformat(),
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        }
        for row in rows
    ]


def create_own_token(session, account_id: str, name: str) -> tuple[ApiToken, str]:
    active = session.scalar(
        select(func.count())
        .select_from(ApiToken)
        .where(ApiToken.account_id == account_id, ApiToken.revoked_at.is_(None))
    )
    if (active or 0) >= MAX_ACTIVE_TOKENS:
        reject(
            "too_many_tokens",
            429,
            f"Активных токенов уже {MAX_ACTIVE_TOKENS}. Отзовите ненужные.",
        )
    raw = generate_api_token()
    token = create_api_token(
        session,
        account_id=account_id,
        name=name.strip()[:100] or CHERRY_TOKEN_NAME,
        raw_token=raw,
        source="harness",
    )
    return token, raw


def revoke_own_token(session, account_id: str, token_id: str) -> bool:
    token = session.get(ApiToken, token_id)
    if token is None or token.account_id != account_id:
        reject("token_not_found", 404, "Токен не найден среди ваших.")
    return revoke_api_token(session, token_id)


def mint_cherry_code(session, settings, account_id: str, session_id: str, port: int):
    """Одноразовый код: 60 секунд, лимит выпуска, привязка к аккаунту и сессии."""
    now = int(time.time())
    minted = session.scalar(
        select(func.count())
        .select_from(WebAuthorizeCode)
        .where(
            WebAuthorizeCode.account_id == account_id,
            WebAuthorizeCode.created >= now - MINT_WINDOW_SECONDS,
        )
    )
    if (minted or 0) >= MINT_WINDOW_LIMIT:
        reject(
            "rate_limited",
            429,
            "Слишком много запросов доступа подряд. Подождите минуту.",
        )
    raw = secrets.token_urlsafe(32)
    code = WebAuthorizeCode(
        id=digest(settings.secret.get_secret_value(), "authorize", raw),
        account_id=account_id,
        session_id=session_id,
        client=CHERRY_CLIENT,
        redirect_port=port,
        created=now,
        expires=now + CODE_TTL_SECONDS,
    )
    session.add(code)
    session.flush()
    redirect = f"http://127.0.0.1:{port}/callback?code={raw}"
    return {"code": raw, "redirect": redirect, "expires_in": CODE_TTL_SECONDS}


def exchange_cherry_code(session, settings, raw_code: str) -> tuple[ApiToken, str]:
    """Обмен кода на персональный токен. Одноразово, с лимитом попыток."""
    key = digest(settings.secret.get_secret_value(), "authorize", raw_code)
    code = session.get(WebAuthorizeCode, key)
    now = int(time.time())
    if code is None or code.used or code.expires <= now:
        reject("code_invalid", 400, "Код недействителен или истёк. Запросите новый.")
    if code.attempts >= CODE_MAX_ATTEMPTS or code.client != CHERRY_CLIENT:
        code.used = True
        reject("code_invalid", 400, "Код недействителен или истёк. Запросите новый.")
    code.attempts += 1
    raw = generate_api_token()
    token = create_api_token(
        session,
        account_id=code.account_id,
        name=CHERRY_TOKEN_NAME,
        raw_token=raw,
        source="harness",
    )
    code.used = True
    return token, raw
