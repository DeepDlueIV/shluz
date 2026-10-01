"""Личный кабинет клиента: только собственные данные, без себестоимости и секретов."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.auth import AuthenticatedPrincipal, require_api_token
from app.api.dependencies import get_database
from app.db.database import Database
from app.db.models import Account, Identity, Plan, Subscription, UsageEvent

router = APIRouter()
Principal = Annotated[AuthenticatedPrincipal, Depends(require_api_token)]
Db = Annotated[Database, Depends(get_database)]


def _personal(principal: AuthenticatedPrincipal) -> None:
    if principal.bootstrap or not principal.token_id:
        raise HTTPException(403, detail="Personal access token required")


def _allowance(limit: int | None, used: int, pending: int) -> dict:
    enabled = limit if limit is not None and limit > 0 else None
    return {
        "limit": enabled,
        "used": used,
        "pending": pending,
        "remaining": max(enabled - used - pending, 0) if enabled is not None else None,
    }


@router.get("/me")
def my_account(principal: Principal, database: Db, response: Response) -> dict:
    _personal(principal)
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    next_month = datetime(
        now.year + (now.month == 12),
        now.month % 12 + 1,
        1,
        tzinfo=UTC,
    )
    with database.session() as session:
        account = session.get(Account, principal.account_id)
        if account is None or account.status != "active":
            raise HTTPException(401, detail="Inactive account")
        # Выбор тарифа совпадает с UsageService. Текущий шлюз считает общую квоту аккаунта.
        row = session.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.account_id == account.id,
                Subscription.status == "active",
                Subscription.starts_at <= now,
                or_(Subscription.ends_at.is_(None), Subscription.ends_at > now),
                Plan.active.is_(True),
            )
            .order_by(Subscription.starts_at.desc(), Subscription.created_at.desc())
        ).first()
        subscription, plan = row if row else (None, None)
        common = (UsageEvent.account_id == account.id, UsageEvent.created_at >= month_start)
        success = UsageEvent.status == "success"
        pending = and_(UsageEvent.status == "pending", UsageEvent.reservation_expires_at > now)

        def total(column, condition) -> int:
            return int(
                session.scalar(
                    select(func.coalesce(func.sum(column), 0)).where(
                        *common,
                        condition,
                    )
                )
                or 0
            )

        def count(condition) -> int:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(UsageEvent)
                    .where(
                        *common,
                        condition,
                    )
                )
                or 0
            )

        ends_at = subscription.ends_at if subscription else None
        if ends_at is not None and ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=UTC)
        return {
            "account": {
                "id": account.id,
                "display_name": account.display_name,
                "status": account.status,
            },
            "channel": principal.source,
            "plan": {
                "id": plan.id,
                "code": plan.code,
                "name": plan.name,
                "scope": "account",
                "ends_at": ends_at.isoformat() if ends_at else None,
            }
            if plan
            else None,
            "allowance": {
                "scope": "account",
                "resets_at": next_month.isoformat(),
                "requests": _allowance(
                    plan.monthly_request_limit if plan else None,
                    count(success),
                    count(pending),
                ),
                "credits": _allowance(
                    plan.monthly_credit_limit if plan else None,
                    total(UsageEvent.internal_credits, success),
                    total(UsageEvent.reserved_credits, pending),
                ),
            },
        }


class TelegramIdentityInput(BaseModel):
    telegram_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    username: str | None = Field(default=None, max_length=200)


@router.post("/me/telegram")
def bind_telegram(
    payload: TelegramIdentityInput,
    principal: Principal,
    database: Db,
    response: Response,
) -> dict:
    """Привязка от доверенного бота с персональным токеном, не публичный Telegram Login."""
    _personal(principal)
    if principal.source != "telegram":
        raise HTTPException(403, detail="Telegram channel token required")
    response.headers["Cache-Control"] = "no-store"
    try:
        with database.session() as session:
            account = session.scalar(
                select(Account)
                .where(
                    Account.id == principal.account_id,
                    Account.status == "active",
                )
                .with_for_update()
            )
            if account is None:
                raise HTTPException(401, detail="Inactive account")
            identity = session.scalar(
                select(Identity).where(
                    Identity.provider == "telegram",
                    Identity.subject == str(payload.telegram_id),
                )
            )
            if identity is not None and identity.account_id != account.id:
                raise HTTPException(409, detail="Telegram identity is already bound")
            if identity is None:
                identity = Identity(
                    account_id=account.id, provider="telegram", subject=str(payload.telegram_id)
                )
                session.add(identity)
            identity.username = payload.username
            session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Telegram identity is already bound") from exc
    return {"account_id": principal.account_id, "telegram_id": payload.telegram_id}
