from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db.models import Account, ApiToken, Identity, Plan, Subscription, UsageEvent


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class IdentityOverview:
    provider: str
    subject: str
    email: str | None
    username: str | None


@dataclass(frozen=True, slots=True)
class AccountOverview:
    id: str
    display_name: str
    role: str
    status: str
    identities: tuple[IdentityOverview, ...]
    plan_code: str | None
    plan_name: str | None
    token_count: int
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    cost_diem: Decimal
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlanOverview:
    id: str
    code: str
    name: str
    monthly_credit_limit: int | None
    monthly_request_limit: int | None
    active: bool
    subscriber_count: int


@dataclass(frozen=True, slots=True)
class UsageBreakdown:
    key: str
    label: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    cost_diem: Decimal


@dataclass(frozen=True, slots=True)
class UsageSummary:
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    cost_diem: Decimal
    by_account: tuple[UsageBreakdown, ...]
    by_model: tuple[UsageBreakdown, ...]
    by_source: tuple[UsageBreakdown, ...]
    recent_events: tuple[UsageEvent, ...]


def list_accounts_overview(session: Session) -> list[AccountOverview]:
    accounts = list(session.scalars(select(Account).order_by(Account.created_at, Account.id)))

    identities_by_account: dict[str, list[IdentityOverview]] = {}
    for identity in session.scalars(select(Identity).order_by(Identity.created_at)):
        identities_by_account.setdefault(identity.account_id, []).append(
            IdentityOverview(
                provider=identity.provider,
                subject=identity.subject,
                email=identity.email,
                username=identity.username,
            )
        )

    token_counts = {
        account_id: int(count)
        for account_id, count in session.execute(
            select(ApiToken.account_id, func.count(ApiToken.id)).group_by(ApiToken.account_id)
        )
    }

    plans_by_account: dict[str, tuple[str, str]] = {}
    subscription_rows = session.execute(
        select(Subscription.account_id, Plan.code, Plan.name)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.status == "active")
        .order_by(desc(Subscription.starts_at))
    )
    for account_id, plan_code, plan_name in subscription_rows:
        plans_by_account.setdefault(account_id, (plan_code, plan_name))

    usage_by_account = {
        account_id: (
            int(request_count),
            int(prompt_tokens or 0),
            int(completion_tokens or 0),
            _decimal(cost_usd),
            _decimal(cost_diem),
        )
        for (
            account_id,
            request_count,
            prompt_tokens,
            completion_tokens,
            cost_usd,
            cost_diem,
        ) in session.execute(
            select(
                UsageEvent.account_id,
                func.count(UsageEvent.id),
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.cost_usd),
                func.sum(UsageEvent.cost_diem),
            ).group_by(UsageEvent.account_id)
        )
    }

    result = []
    for account in accounts:
        plan = plans_by_account.get(account.id)
        usage = usage_by_account.get(
            account.id,
            (0, 0, 0, Decimal("0"), Decimal("0")),
        )
        result.append(
            AccountOverview(
                id=account.id,
                display_name=account.display_name,
                role=account.role,
                status=account.status,
                identities=tuple(identities_by_account.get(account.id, ())),
                plan_code=plan[0] if plan else None,
                plan_name=plan[1] if plan else None,
                token_count=token_counts.get(account.id, 0),
                request_count=usage[0],
                prompt_tokens=usage[1],
                completion_tokens=usage[2],
                cost_usd=usage[3],
                cost_diem=usage[4],
                created_at=account.created_at,
            )
        )
    return result


def list_plans_overview(session: Session) -> list[PlanOverview]:
    subscriber_counts = {
        plan_id: int(count)
        for plan_id, count in session.execute(
            select(Subscription.plan_id, func.count(Subscription.id))
            .where(Subscription.status == "active")
            .group_by(Subscription.plan_id)
        )
    }
    return [
        PlanOverview(
            id=plan.id,
            code=plan.code,
            name=plan.name,
            monthly_credit_limit=plan.monthly_credit_limit,
            monthly_request_limit=plan.monthly_request_limit,
            active=plan.active,
            subscriber_count=subscriber_counts.get(plan.id, 0),
        )
        for plan in session.scalars(select(Plan).order_by(Plan.name, Plan.code))
    ]


def _usage_breakdown(
    session: Session,
    *,
    key_column,
    label_column=None,
) -> tuple[UsageBreakdown, ...]:
    label = label_column if label_column is not None else key_column
    rows = session.execute(
        select(
            key_column,
            label,
            func.count(UsageEvent.id),
            func.sum(UsageEvent.prompt_tokens),
            func.sum(UsageEvent.completion_tokens),
            func.sum(UsageEvent.cost_usd),
            func.sum(UsageEvent.cost_diem),
        )
        .group_by(key_column, label)
        .order_by(desc(func.sum(UsageEvent.cost_usd)), desc(func.count(UsageEvent.id)))
    )
    return tuple(
        UsageBreakdown(
            key=str(key),
            label=str(row_label),
            request_count=int(request_count),
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cost_usd=_decimal(cost_usd),
            cost_diem=_decimal(cost_diem),
        )
        for (
            key,
            row_label,
            request_count,
            prompt_tokens,
            completion_tokens,
            cost_usd,
            cost_diem,
        ) in rows
    )


def get_usage_summary(session: Session, recent_limit: int = 100) -> UsageSummary:
    totals = session.execute(
        select(
            func.count(UsageEvent.id),
            func.sum(UsageEvent.prompt_tokens),
            func.sum(UsageEvent.completion_tokens),
            func.sum(UsageEvent.cost_usd),
            func.sum(UsageEvent.cost_diem),
        )
    ).one()

    by_account = _usage_breakdown(
        session,
        key_column=UsageEvent.account_id,
        label_column=UsageEvent.account_id,
    )
    by_model = _usage_breakdown(
        session,
        key_column=UsageEvent.model,
        label_column=UsageEvent.provider + ": " + UsageEvent.model,
    )
    by_source = _usage_breakdown(
        session,
        key_column=UsageEvent.source,
        label_column=UsageEvent.source,
    )
    recent_events = tuple(
        session.scalars(
            select(UsageEvent)
            .order_by(desc(UsageEvent.created_at), desc(UsageEvent.id))
            .limit(recent_limit)
        )
    )

    return UsageSummary(
        request_count=int(totals[0]),
        prompt_tokens=int(totals[1] or 0),
        completion_tokens=int(totals[2] or 0),
        cost_usd=_decimal(totals[3]),
        cost_diem=_decimal(totals[4]),
        by_account=by_account,
        by_model=by_model,
        by_source=by_source,
        recent_events=recent_events,
    )
