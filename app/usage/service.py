from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import and_, func, or_, select

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, Plan, Subscription, UsageEvent
from app.db.repositories import ensure_bootstrap_account
from app.providers.base import ChatResult


class UsageAuthorizationError(RuntimeError):
    """A tariff rule rejected a request before provider execution."""


class SubscriptionRequiredError(UsageAuthorizationError):
    """The account does not have an active subscription."""


class RequestLimitExceededError(UsageAuthorizationError):
    """The monthly request limit has been exhausted."""


class CreditLimitExceededError(UsageAuthorizationError):
    """The monthly internal-credit limit has been exhausted."""


class SpendLimitExceededError(UsageAuthorizationError):
    """The monthly provider-cost limit has been exhausted."""


@dataclass(frozen=True, slots=True)
class UsageReservation:
    event_id: str
    account_id: str
    source: str
    provider: str
    model: str
    plan_id: str | None
    markup_percent: Decimal
    bootstrap: bool = False


def _month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def _enabled_numeric_limit(value: int | Decimal | None) -> bool:
    return value is not None and value > 0


class UsageService:
    """Reserve allowance and persist cost without storing conversation text."""

    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self._database = database
        self._settings = settings or Settings()
        if self._settings.credit_unit_usd <= 0:
            raise ValueError("credit_unit_usd must be greater than zero")
        if self._settings.reservation_ttl_seconds <= 0:
            raise ValueError("reservation_ttl_seconds must be greater than zero")

    def authorize_request(
        self,
        *,
        account_id: str,
        source: str,
        provider: str,
        model: str,
        now: datetime | None = None,
    ) -> UsageReservation:
        resolved_now = now or datetime.now(UTC)
        expires_at = resolved_now + timedelta(
            seconds=self._settings.reservation_ttl_seconds
        )
        bootstrap = account_id == "bootstrap"

        with self._database.session() as session:
            plan: Plan | None = None
            if bootstrap:
                ensure_bootstrap_account(session)
            else:
                account = session.scalar(
                    select(Account)
                    .where(Account.id == account_id, Account.status == "active")
                    .with_for_update()
                )
                if account is None:
                    raise SubscriptionRequiredError("Active account is required")

                subscription_row = session.execute(
                    select(Subscription, Plan)
                    .join(Plan, Plan.id == Subscription.plan_id)
                    .where(
                        Subscription.account_id == account_id,
                        Subscription.status == "active",
                        Subscription.starts_at <= resolved_now,
                        or_(
                            Subscription.ends_at.is_(None),
                            Subscription.ends_at > resolved_now,
                        ),
                        Plan.active.is_(True),
                    )
                    .order_by(Subscription.starts_at.desc(), Subscription.created_at.desc())
                ).first()
                if subscription_row is None:
                    raise SubscriptionRequiredError("Active subscription is required")
                _, plan = subscription_row
                self._check_limits(session, account_id, plan, resolved_now)

            reserve = plan.request_credit_reserve if plan is not None else 0
            markup = plan.markup_percent if plan is not None else Decimal("0")
            event = UsageEvent(
                account_id=account_id,
                plan_id=plan.id if plan is not None else None,
                source=source,
                provider=provider,
                model=model,
                status="pending",
                reserved_credits=reserve,
                pricing_markup_percent=markup,
                reservation_expires_at=expires_at,
                created_at=resolved_now,
            )
            session.add(event)
            session.flush()
            return UsageReservation(
                event_id=event.id,
                account_id=account_id,
                source=source,
                provider=provider,
                model=model,
                plan_id=event.plan_id,
                markup_percent=markup,
                bootstrap=bootstrap,
            )

    def _check_limits(
        self,
        session,
        account_id: str,
        plan: Plan,
        now: datetime,
    ) -> None:
        period_start = _month_start(now)
        active_pending = and_(
            UsageEvent.status == "pending",
            UsageEvent.reservation_expires_at.is_not(None),
            UsageEvent.reservation_expires_at > now,
        )
        common = (
            UsageEvent.account_id == account_id,
            UsageEvent.created_at >= period_start,
        )

        request_count = int(
            session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(*common, or_(UsageEvent.status == "success", active_pending))
            )
            or 0
        )
        if (
            _enabled_numeric_limit(plan.monthly_request_limit)
            and request_count >= plan.monthly_request_limit
        ):
            raise RequestLimitExceededError("Monthly request limit exceeded")

        successful_credits = int(
            session.scalar(
                select(func.coalesce(func.sum(UsageEvent.internal_credits), 0)).where(
                    *common,
                    UsageEvent.status == "success",
                )
            )
            or 0
        )
        pending_credits = int(
            session.scalar(
                select(func.coalesce(func.sum(UsageEvent.reserved_credits), 0)).where(
                    *common,
                    active_pending,
                )
            )
            or 0
        )
        used_credits = successful_credits + pending_credits
        reserve = max(plan.request_credit_reserve, 0)
        if _enabled_numeric_limit(plan.monthly_credit_limit):
            if used_credits >= plan.monthly_credit_limit:
                raise CreditLimitExceededError("Monthly credit limit exceeded")
            if reserve and used_credits + reserve > plan.monthly_credit_limit:
                raise CreditLimitExceededError("Not enough credits for request reservation")

        successful_cost = Decimal(
            session.scalar(
                select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
                    *common,
                    UsageEvent.status == "success",
                )
            )
            or 0
        )
        if _enabled_numeric_limit(plan.monthly_cost_limit_usd):
            if successful_cost >= plan.monthly_cost_limit_usd:
                raise SpendLimitExceededError("Monthly provider cost limit exceeded")
            reserve_billed = Decimal(reserve) * self._settings.credit_unit_usd
            multiplier = Decimal("1") + max(plan.markup_percent, Decimal("0")) / Decimal(
                "100"
            )
            reserve_cost = reserve_billed / multiplier if multiplier > 0 else reserve_billed
            if reserve and successful_cost + reserve_cost > plan.monthly_cost_limit_usd:
                raise SpendLimitExceededError("Not enough provider budget for reservation")

    def record_success(
        self,
        *,
        result: ChatResult,
        reservation: UsageReservation | None = None,
        account_id: str | None = None,
        source: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> UsageEvent:
        """Settle a reservation or retain the legacy direct-recording path."""

        if reservation is None:
            if not all((account_id, source, provider, model)):
                raise ValueError("Direct usage recording requires attribution fields")
            return self._record_direct_success(
                account_id=account_id or "",
                source=source or "",
                provider=provider or "",
                model=model or "",
                result=result,
            )

        with self._database.session() as session:
            event = session.get(UsageEvent, reservation.event_id)
            if event is None:
                raise LookupError("Usage reservation not found")

            billed_usd, credits = self._calculate_charge(
                result.cost_usd,
                reservation.markup_percent,
            )
            event.provider_request_id = result.request_id
            event.prompt_tokens = result.prompt_tokens
            event.completion_tokens = result.completion_tokens
            event.cost_usd = result.cost_usd
            event.cost_diem = result.cost_diem
            event.billed_usd = billed_usd
            event.internal_credits = credits
            event.reserved_credits = 0
            event.status = "success"
            event.error_code = None
            event.completed_at = datetime.now(UTC)
            session.flush()
            return event

    def record_failure(
        self,
        reservation: UsageReservation,
        *,
        error_code: str,
    ) -> UsageEvent:
        with self._database.session() as session:
            event = session.get(UsageEvent, reservation.event_id)
            if event is None:
                raise LookupError("Usage reservation not found")
            event.status = "failed"
            event.error_code = error_code
            event.reserved_credits = 0
            event.completed_at = datetime.now(UTC)
            session.flush()
            return event

    def _calculate_charge(
        self,
        cost_usd: Decimal,
        markup_percent: Decimal,
    ) -> tuple[Decimal, int]:
        safe_cost = max(cost_usd, Decimal("0"))
        safe_markup = max(markup_percent, Decimal("0"))
        billed_usd = (
            safe_cost * (Decimal("1") + safe_markup / Decimal("100"))
        ).quantize(Decimal("0.00000001"))
        if billed_usd == 0:
            return billed_usd, 0
        credits = int(
            (billed_usd / self._settings.credit_unit_usd).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        return billed_usd, credits

    def _record_direct_success(
        self,
        *,
        account_id: str,
        source: str,
        provider: str,
        model: str,
        result: ChatResult,
    ) -> UsageEvent:
        with self._database.session() as session:
            if result.request_id:
                existing = session.scalar(
                    select(UsageEvent).where(
                        UsageEvent.provider == provider,
                        UsageEvent.provider_request_id == result.request_id,
                    )
                )
                if existing is not None:
                    return existing

            if account_id == "bootstrap":
                ensure_bootstrap_account(session)

            event = UsageEvent(
                account_id=account_id,
                source=source,
                provider=provider,
                model=model,
                provider_request_id=result.request_id,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd,
                cost_diem=result.cost_diem,
                billed_usd=result.cost_usd,
                status="success",
                completed_at=datetime.now(UTC),
            )
            session.add(event)
            session.flush()
            return event
