from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Account, ApiToken, Identity, Plan, Subscription, UsageEvent


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def _remaining_int(limit: int | None, used: int) -> int | None:
    if limit is None or limit <= 0:
        return None
    return max(limit - used, 0)


def _remaining_decimal(limit: Decimal | None, used: Decimal) -> Decimal | None:
    if limit is None or limit <= 0:
        return None
    return max(limit - used, Decimal("0"))


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
    plan_id: str | None
    plan_code: str | None
    plan_name: str | None
    plan_active: bool | None
    monthly_request_limit: int | None
    monthly_credit_limit: int | None
    monthly_cost_limit_usd: Decimal | None
    token_count: int
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    cost_diem: Decimal
    billed_usd: Decimal
    margin_usd: Decimal
    internal_credits: int
    month_request_count: int
    month_internal_credits: int
    month_cost_usd: Decimal
    month_billed_usd: Decimal
    month_margin_usd: Decimal
    remaining_requests: int | None
    remaining_credits: int | None
    remaining_cost_usd: Decimal | None
    blocked_reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlanOverview:
    id: str
    code: str
    name: str
    monthly_price_usd: Decimal
    monthly_cost_limit_usd: Decimal | None
    monthly_credit_limit: int | None
    monthly_request_limit: int | None
    request_credit_reserve: int
    markup_percent: Decimal
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
    billed_usd: Decimal
    margin_usd: Decimal
    internal_credits: int


@dataclass(frozen=True, slots=True)
class UsageSummary:
    request_count: int
    success_count: int
    pending_count: int
    failed_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    cost_diem: Decimal
    billed_usd: Decimal
    margin_usd: Decimal
    internal_credits: int
    by_account: tuple[UsageBreakdown, ...]
    by_model: tuple[UsageBreakdown, ...]
    by_source: tuple[UsageBreakdown, ...]
    recent_events: tuple[UsageEvent, ...]


def list_accounts_overview(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[AccountOverview]:
    resolved_now = now or datetime.now(UTC)
    period_start = _month_start(resolved_now)
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

    plans_by_account: dict[str, Plan] = {}
    subscription_rows = session.execute(
        select(Subscription, Plan)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(
            Subscription.status == "active",
            Subscription.starts_at <= resolved_now,
            or_(Subscription.ends_at.is_(None), Subscription.ends_at > resolved_now),
        )
        .order_by(desc(Subscription.starts_at), desc(Subscription.created_at))
    )
    for subscription, plan in subscription_rows:
        plans_by_account.setdefault(subscription.account_id, plan)

    all_usage_by_account = {
        account_id: (
            int(request_count),
            int(prompt_tokens or 0),
            int(completion_tokens or 0),
            _decimal(cost_usd),
            _decimal(cost_diem),
            _decimal(billed_usd),
            int(internal_credits or 0),
        )
        for (
            account_id,
            request_count,
            prompt_tokens,
            completion_tokens,
            cost_usd,
            cost_diem,
            billed_usd,
            internal_credits,
        ) in session.execute(
            select(
                UsageEvent.account_id,
                func.count(UsageEvent.id),
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
                func.sum(UsageEvent.cost_usd),
                func.sum(UsageEvent.cost_diem),
                func.sum(UsageEvent.billed_usd),
                func.sum(UsageEvent.internal_credits),
            ).group_by(UsageEvent.account_id)
        )
    }

    month_usage: dict[str, dict[str, object]] = {}
    month_events = session.scalars(
        select(UsageEvent).where(UsageEvent.created_at >= period_start)
    )
    for event in month_events:
        totals = month_usage.setdefault(
            event.account_id,
            {
                "requests": 0,
                "credits": 0,
                "cost": Decimal("0"),
                "billed": Decimal("0"),
            },
        )
        if event.status == "success":
            totals["requests"] = int(totals["requests"]) + 1
            totals["credits"] = int(totals["credits"]) + event.internal_credits
            totals["cost"] = _decimal(totals["cost"]) + _decimal(event.cost_usd)
            totals["billed"] = _decimal(totals["billed"]) + _decimal(event.billed_usd)
        elif event.status == "pending":
            expires_at = _as_utc(event.reservation_expires_at)
            if expires_at is not None and expires_at > resolved_now:
                totals["requests"] = int(totals["requests"]) + 1
                totals["credits"] = int(totals["credits"]) + event.reserved_credits

    result = []
    for account in accounts:
        plan = plans_by_account.get(account.id)
        usage = all_usage_by_account.get(
            account.id,
            (0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), 0),
        )
        month = month_usage.get(
            account.id,
            {
                "requests": 0,
                "credits": 0,
                "cost": Decimal("0"),
                "billed": Decimal("0"),
            },
        )
        month_requests = int(month["requests"])
        month_credits = int(month["credits"])
        month_cost = _decimal(month["cost"])
        month_billed = _decimal(month["billed"])
        remaining_requests = _remaining_int(
            plan.monthly_request_limit if plan else None,
            month_requests,
        )
        remaining_credits = _remaining_int(
            plan.monthly_credit_limit if plan else None,
            month_credits,
        )
        remaining_cost = _remaining_decimal(
            plan.monthly_cost_limit_usd if plan else None,
            month_cost,
        )

        blocked_reason = None
        if account.status != "active":
            blocked_reason = "Аккаунт заблокирован"
        elif account.id != "bootstrap" and plan is None:
            blocked_reason = "Нет активного тарифа"
        elif plan is not None and not plan.active:
            blocked_reason = "Тариф отключён"
        elif remaining_requests == 0:
            blocked_reason = "Исчерпан лимит запросов"
        elif remaining_credits == 0:
            blocked_reason = "Исчерпаны внутренние кредиты"
        elif remaining_cost == 0:
            blocked_reason = "Исчерпан лимит себестоимости"

        billed = usage[5]
        cost = usage[3]
        result.append(
            AccountOverview(
                id=account.id,
                display_name=account.display_name,
                role=account.role,
                status=account.status,
                identities=tuple(identities_by_account.get(account.id, ())),
                plan_id=plan.id if plan else None,
                plan_code=plan.code if plan else None,
                plan_name=plan.name if plan else None,
                plan_active=plan.active if plan else None,
                monthly_request_limit=plan.monthly_request_limit if plan else None,
                monthly_credit_limit=plan.monthly_credit_limit if plan else None,
                monthly_cost_limit_usd=plan.monthly_cost_limit_usd if plan else None,
                token_count=token_counts.get(account.id, 0),
                request_count=usage[0],
                prompt_tokens=usage[1],
                completion_tokens=usage[2],
                cost_usd=cost,
                cost_diem=usage[4],
                billed_usd=billed,
                margin_usd=billed - cost,
                internal_credits=usage[6],
                month_request_count=month_requests,
                month_internal_credits=month_credits,
                month_cost_usd=month_cost,
                month_billed_usd=month_billed,
                month_margin_usd=month_billed - month_cost,
                remaining_requests=remaining_requests,
                remaining_credits=remaining_credits,
                remaining_cost_usd=remaining_cost,
                blocked_reason=blocked_reason,
                created_at=account.created_at,
            )
        )
    return result


def list_plans_overview(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[PlanOverview]:
    resolved_now = now or datetime.now(UTC)
    subscriber_counts = {
        plan_id: int(count)
        for plan_id, count in session.execute(
            select(Subscription.plan_id, func.count(Subscription.id))
            .where(
                Subscription.status == "active",
                Subscription.starts_at <= resolved_now,
                or_(Subscription.ends_at.is_(None), Subscription.ends_at > resolved_now),
            )
            .group_by(Subscription.plan_id)
        )
    }
    return [
        PlanOverview(
            id=plan.id,
            code=plan.code,
            name=plan.name,
            monthly_price_usd=_decimal(plan.monthly_price_usd),
            monthly_cost_limit_usd=(
                _decimal(plan.monthly_cost_limit_usd)
                if plan.monthly_cost_limit_usd is not None
                else None
            ),
            monthly_credit_limit=plan.monthly_credit_limit,
            monthly_request_limit=plan.monthly_request_limit,
            request_credit_reserve=plan.request_credit_reserve,
            markup_percent=_decimal(plan.markup_percent),
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
            func.sum(UsageEvent.billed_usd),
            func.sum(UsageEvent.internal_credits),
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
            billed_usd=_decimal(billed_usd),
            margin_usd=_decimal(billed_usd) - _decimal(cost_usd),
            internal_credits=int(internal_credits or 0),
        )
        for (
            key,
            row_label,
            request_count,
            prompt_tokens,
            completion_tokens,
            cost_usd,
            cost_diem,
            billed_usd,
            internal_credits,
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
            func.sum(UsageEvent.billed_usd),
            func.sum(UsageEvent.internal_credits),
        )
    ).one()
    status_counts = {
        status: int(count)
        for status, count in session.execute(
            select(UsageEvent.status, func.count(UsageEvent.id)).group_by(UsageEvent.status)
        )
    }

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
    cost = _decimal(totals[3])
    billed = _decimal(totals[5])

    return UsageSummary(
        request_count=int(totals[0]),
        success_count=status_counts.get("success", 0),
        pending_count=status_counts.get("pending", 0),
        failed_count=status_counts.get("failed", 0),
        prompt_tokens=int(totals[1] or 0),
        completion_tokens=int(totals[2] or 0),
        cost_usd=cost,
        cost_diem=_decimal(totals[4]),
        billed_usd=billed,
        margin_usd=billed - cost,
        internal_credits=int(totals[6] or 0),
        by_account=by_account,
        by_model=by_model,
        by_source=by_source,
        recent_events=recent_events,
    )
