"""Создание доступа и привязок атомарно, без изменения существующих квот."""

import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text

from app.db.audit import record_admin_event
from app.db.models import (
    Account,
    ApiToken,
    Identity,
    Plan,
    Subscription,
    TelegramAdmission,
    TelegramInvite,
)
from app.db.repositories import authenticate_api_token
from app.telegram.credentials import decode_session, read_connector_key


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@contextmanager
def transaction(database):
    with database.session() as session:
        # SQLite не поддерживает FOR UPDATE. Блокировка охватывает lookup + insert + redeem.
        if database.engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif database.engine.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(1397247034)"))
        yield session


def identity_for(session, telegram_id):
    return session.scalar(
        select(Identity).where(
            Identity.provider == "telegram",
            Identity.subject == str(telegram_id),
        )
    )


def access_is_active(session, admission):
    if admission is None or admission.status != "approved" or not admission.account_id:
        return False
    account = session.get(Account, admission.account_id)
    if account is None or account.status != "active":
        return False
    identity = identity_for(session, admission.telegram_id)
    if identity is None or identity.account_id != account.id:
        return False
    if admission.legacy_token_id:
        token = session.get(ApiToken, admission.legacy_token_id)
        if (
            token is None
            or token.revoked_at is not None
            or token.account_id != account.id
            or token.source != "telegram"
        ):
            return False
    return True


def authenticate_session(database, token, key_file):
    payload = decode_session(token, read_connector_key(key_file))
    if payload is None:
        return None
    with database.session() as session:
        admission = session.get(TelegramAdmission, payload["access"])
        if (
            not access_is_active(session, admission)
            or admission.telegram_id != payload["sub"]
            or admission.session_version != payload["version"]
        ):
            return None
        return admission.account_id, admission.id, int(admission.telegram_id)


def _active_plan(session, plan_id):
    plan = session.get(Plan, plan_id)
    if plan is None or not plan.active:
        raise HTTPException(422, detail="Выберите действующий тариф")
    return plan


def _grant(session, row, *, plan_id=None, account_id=None, legacy_token_id=None):
    identity = identity_for(session, row.telegram_id)
    linked = identity.account_id if identity else row.account_id
    if account_id and linked and account_id != linked:
        raise HTTPException(409, detail="Этот Telegram уже связан с другим аккаунтом")
    target = account_id or linked
    if target:
        account = session.scalar(select(Account).where(Account.id == target).with_for_update())
        if account is None or account.status != "active" or account.role == "service":
            raise HTTPException(409, detail="Аккаунт недоступен для подключения")
    else:
        account = Account(display_name=row.display_name, role="user", status="active")
        session.add(account)
        session.flush()
    other_identity = session.scalar(
        select(Identity).where(
            Identity.provider == "telegram",
            Identity.account_id == account.id,
            Identity.subject != row.telegram_id,
        )
    )
    if other_identity is not None:
        raise HTTPException(409, detail="Аккаунт уже связан с другим Telegram")
    if identity is None:
        session.add(
            Identity(
                account_id=account.id,
                provider="telegram",
                subject=row.telegram_id,
                username=row.username,
            )
        )
    if plan_id:
        plan = _active_plan(session, plan_id)
        now = datetime.now(UTC)
        current = session.scalar(
            select(Subscription)
            .join(Plan)
            .where(
                Subscription.account_id == account.id,
                Subscription.status == "active",
                Subscription.starts_at <= now,
                or_(Subscription.ends_at.is_(None), Subscription.ends_at > now),
                Plan.active.is_(True),
            )
        )
        # Выдача доступа — не смена подписки. Уже назначенный тариф остаётся прежним.
        if current is None:
            session.add(Subscription(account_id=account.id, plan_id=plan.id))
    row.account_id = account.id
    row.status = "approved"
    row.legacy_token_id = legacy_token_id
    row.session_version += 1
    row.updated_at = datetime.now(UTC)
    session.flush()


def connect(database, payload, *, pending_limit=500):
    with transaction(database) as session:
        row = session.scalar(
            select(TelegramAdmission)
            .where(
                TelegramAdmission.telegram_id == str(payload.telegram_id),
            )
            .with_for_update()
        )
        if row is not None and row.status in {"blocked", "approved"}:
            if row.status == "approved" and not access_is_active(session, row):
                row.status = "blocked"
                row.session_version += 1
            return row
        legacy = None
        if payload.legacy_token is not None:
            legacy = authenticate_api_token(session, payload.legacy_token.get_secret_value())
            if legacy is None or legacy.source != "telegram":
                raise HTTPException(401, detail="Старый Telegram-доступ недействителен")
        if row is None:
            pending = session.scalar(
                select(func.count())
                .select_from(TelegramAdmission)
                .where(
                    TelegramAdmission.status == "pending",
                )
            )
            if pending >= pending_limit and legacy is None and not payload.invite:
                raise HTTPException(429, detail="Приём заявок временно ограничен")
            identity = identity_for(session, payload.telegram_id)
            row = TelegramAdmission(
                telegram_id=str(payload.telegram_id),
                display_name=payload.display_name,
                username=payload.username,
                account_id=identity.account_id if identity else None,
            )
            session.add(row)
            session.flush()
        if legacy:
            _grant(session, row, account_id=legacy.account_id, legacy_token_id=legacy.token_id)
            record_admin_event(
                session,
                actor="telegram-connector",
                action="telegram.imported",
                target_type="account",
                target_id=row.account_id,
                details={"telegram_id": row.telegram_id},
            )
        elif payload.invite:
            invite_hash = sha256(payload.invite.encode("ascii")).hexdigest()
            invitation = session.scalar(
                select(TelegramInvite)
                .where(
                    TelegramInvite.token_hash == invite_hash,
                )
                .with_for_update()
            )
            if (
                invitation is None
                or invitation.revoked
                or utc(invitation.expires_at) <= datetime.now(UTC)
                or invitation.redeemed_telegram_id is not None
            ):
                raise HTTPException(
                    409,
                    detail={
                        "code": "invite_invalid",
                        "message": "Приглашение недействительно или уже использовано",
                    },
                )
            _grant(session, row, plan_id=invitation.plan_id, account_id=invitation.account_id)
            invitation.redeemed_telegram_id = row.telegram_id
            record_admin_event(
                session,
                actor="telegram-connector",
                action="telegram.invite_used",
                target_type="account",
                target_id=row.account_id,
                details={"invite_id": invitation.id},
            )
        return row


def approve(database, admission_id, plan_id, *, actor, account_id=None):
    with transaction(database) as session:
        row = session.scalar(
            select(TelegramAdmission)
            .where(
                TelegramAdmission.id == admission_id,
            )
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, detail="Заявка не найдена")
        if row.status == "approved":
            return
        _grant(session, row, plan_id=plan_id, account_id=account_id)
        record_admin_event(
            session,
            actor=actor,
            action="telegram.approved",
            target_type="account",
            target_id=row.account_id,
            details={"telegram_id": row.telegram_id},
        )


def block(database, admission_id, *, actor):
    with transaction(database) as session:
        row = session.get(TelegramAdmission, admission_id)
        if row is None:
            raise HTTPException(404, detail="Заявка не найдена")
        row.status = "blocked"
        row.session_version += 1
        row.updated_at = datetime.now(UTC)
        record_admin_event(
            session,
            actor=actor,
            action="telegram.blocked",
            target_type="telegram_access",
            target_id=row.id,
            details={},
        )


def issue_invite(database, plan_id, *, actor, account_id=None):
    code = "join_" + secrets.token_urlsafe(24)
    with transaction(database) as session:
        _active_plan(session, plan_id)
        if account_id:
            account = session.get(Account, account_id)
            if account is None or account.status != "active" or account.role == "service":
                raise HTTPException(422, detail="Выберите доступный аккаунт")
        invitation = TelegramInvite(
            token_hash=sha256(code.encode("ascii")).hexdigest(),
            plan_id=plan_id,
            account_id=account_id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        session.add(invitation)
        session.flush()
        record_admin_event(
            session,
            actor=actor,
            action="telegram.invite_created",
            target_type="telegram_invite",
            target_id=invitation.id,
            details={"plan_id": plan_id},
        )
    return code
