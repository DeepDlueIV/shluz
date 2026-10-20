from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.audit import record_admin_event
from app.db.models import Account, ApiToken, Plan, Subscription, TelegramAdmission


def ensure_plan_unused(session: Session, plan_id: str) -> None:
    count = (
        session.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(
                Subscription.plan_id == plan_id,
                Subscription.status == "active",
                or_(Subscription.ends_at.is_(None), Subscription.ends_at > datetime.now(UTC)),
            )
        )
        or 0
    )
    if count:
        raise HTTPException(
            status_code=409,
            detail=f"У тарифа есть действующие подписки: {count}. Сначала назначьте другой тариф.",
        )


def change_account_archive(session: Session, account_id: str, actor: str, archive: bool) -> None:
    account = session.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if account.id == "bootstrap" or account.role != "user":
        raise HTTPException(
            status_code=409,
            detail="Технические и административные аккаунты нельзя архивировать",
        )
    target_status = "archived" if archive else "active"
    if account.status == target_status:
        return
    before = account.status
    revoked = 0
    cancelled = 0
    if archive:
        from app.telegram.service import access_is_active

        admissions = list(
            session.scalars(
                select(TelegramAdmission).where(
                    TelegramAdmission.account_id == account_id,
                )
            )
        )
        recoverable = {row.id for row in admissions if access_is_active(session, row)}
        now = datetime.now(UTC)
        for token in session.scalars(
            select(ApiToken).where(
                ApiToken.account_id == account_id,
                ApiToken.revoked_at.is_(None),
            )
        ):
            token.revoked_at = now
            revoked += 1
        for admission in admissions:
            if admission.status == "approved":
                admission.status = "archived" if admission.id in recoverable else "blocked"
            admission.session_version += 1
        for subscription in session.scalars(
            select(Subscription).where(
                Subscription.account_id == account_id,
                Subscription.status == "active",
            )
        ):
            subscription.status = "archived"
            cancelled += 1
    else:
        # Возвращаем только приостановленное архивом. Отозванные ключи не оживляем.
        for admission in session.scalars(
            select(TelegramAdmission).where(
                TelegramAdmission.account_id == account_id,
                TelegramAdmission.status == "archived",
            )
        ):
            admission.status = "approved"
            admission.legacy_token_id = None
            admission.session_version += 1
        for subscription in session.scalars(
            select(Subscription).where(
                Subscription.account_id == account_id,
                Subscription.status == "archived",
            )
        ):
            subscription.status = "active"  # Исходный ends_at сохранён, срок не продлевается.
    account.status = target_status
    record_admin_event(
        session,
        actor=actor,
        action="user.archived" if archive else "user.restored",
        target_type="account",
        target_id=account_id,
        details={
            "before": {"status": before},
            "after": {"status": target_status},
            "revoked_tokens": revoked,
            "cancelled_subscriptions": cancelled,
        },
    )


def change_plan_archive(session: Session, plan_id: str, actor: str, archive: bool) -> None:
    plan = session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
    if plan is None:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    if archive:
        ensure_plan_unused(session, plan_id)
    if plan.active == (not archive):
        return
    before = plan.active
    plan.active = not archive
    record_admin_event(
        session,
        actor=actor,
        action="plan.archived" if archive else "plan.restored",
        target_type="plan",
        target_id=plan_id,
        details={"before": {"active": before}, "after": {"active": plan.active}},
    )
