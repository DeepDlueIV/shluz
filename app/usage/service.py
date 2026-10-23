from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import and_, func, or_, select

from app.billing import service as billing
from app.config import Settings
from app.db.database import Database
from app.db.models import Account, DemoGrant, Plan, UsageEvent
from app.db.repositories import ensure_bootstrap_account
from app.product import service as product
from app.providers.base import ChatResult
from app.webaccess.demo import reserve_protection, settle_protection


class UsageAuthorizationError(RuntimeError):
    """A tariff rule rejected a request before provider execution."""


class SubscriptionRequiredError(UsageAuthorizationError):
    pass


class RequestLimitExceededError(UsageAuthorizationError):
    pass


class CreditLimitExceededError(UsageAuthorizationError):
    pass


class SpendLimitExceededError(UsageAuthorizationError):
    pass


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
    demo: bool = False
    max_output_tokens: int | None = None
    timeout_seconds: int | None = None
    idle_seconds: int | None = None


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
        input_chars: int = 0,
        requested_output_tokens: int | None = None,
        web_context=None,
        uses_tools: bool = False,
    ) -> UsageReservation:
        resolved_now = now or datetime.now(UTC)
        expires_at = resolved_now + timedelta(seconds=self._settings.reservation_ttl_seconds)
        bootstrap = account_id == "bootstrap"
        with product.locked_session(self._database) as session:
            is_demo = False
            output_limit = requested_output_tokens
            plan: Plan | None = None
            quota_scope = "*"
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
                subscription_row = product.active_subscription(
                    session, account_id, resolved_now, source
                )
                grant = session.get(DemoGrant, account_id)
                if subscription_row is None and grant is None:
                    raise SubscriptionRequiredError("Active subscription is required")
                allowed = product.allowed_models(session, account_id, source, resolved_now)
                if allowed is not None and model not in allowed:
                    raise product.PolicyError()
                config = product.policy(session)
                product.limit_rate(session, account_id, config, resolved_now)
                if subscription_row:
                    subscription, plan = subscription_row
                    quota_scope = subscription.source
                    if plan.billing_mode == "subscription":
                        self._check_limits(session, account_id, plan, resolved_now, quota_scope)
                    elif plan.billing_mode != "payg":
                        raise SubscriptionRequiredError("Unknown billing mode")
                    output_limit = min(requested_output_tokens or 4096, 4096)
                    if input_chars > 200000:
                        raise product.InputTooLarge()
                else:
                    if uses_tools:
                        raise product.DemoToolsNotAllowed()
                    if input_chars > config.demo_max_input_chars or (
                        requested_output_tokens is not None
                        and requested_output_tokens > config.demo_max_output_tokens
                    ):
                        raise product.InputTooLarge()
                    output_limit = requested_output_tokens or config.demo_max_output_tokens
                    is_demo = product.reserve_demo(session, account_id, config, resolved_now)
            reserve = (
                plan.request_credit_reserve
                if plan is not None and plan.billing_mode == "subscription"
                else 0
            )
            markup = plan.markup_percent if plan is not None else Decimal("0")
            event = UsageEvent(
                account_id=account_id,
                plan_id=plan.id if plan is not None else None,
                source=source,
                provider=provider,
                model=model,
                status="pending",
                quota_kind="demo"
                if is_demo
                else ("payg" if plan and plan.billing_mode == "payg" else "monthly"),
                quota_scope=quota_scope,
                reserved_credits=reserve,
                pricing_markup_percent=markup,
                reservation_expires_at=expires_at,
                created_at=resolved_now,
            )
            session.add(event)
            session.flush()
            total_timeout = idle_timeout = None
            if is_demo:
                total_timeout, idle_timeout = reserve_protection(
                    session, event, config, resolved_now
                )
            if plan and plan.billing_mode == "payg":
                billing.reserve(session, event, input_chars, output_limit)
            # Серверный callback не является параметром JSON. Cookie, IP, повторный ID,
            # слот и деньги проверяются под ТОЙ ЖЕ блокировкой, до upstream.
            if web_context is not None:
                web_context(session, event)
            return UsageReservation(
                event_id=event.id,
                account_id=account_id,
                source=source,
                provider=provider,
                model=model,
                plan_id=event.plan_id,
                markup_percent=markup,
                bootstrap=bootstrap,
                demo=is_demo,
                max_output_tokens=output_limit,
                timeout_seconds=total_timeout,
                idle_seconds=idle_timeout,
            )

    def _check_limits(self, session, account_id: str, plan: Plan, now: datetime, scope="*") -> None:
        period_start = _month_start(now)
        active_pending = or_(
            and_(UsageEvent.status == "pending", UsageEvent.reservation_expires_at > now),
            and_(UsageEvent.status == "interrupted", UsageEvent.cost_known.is_(False)),
        )
        common = (
            UsageEvent.account_id == account_id,
            UsageEvent.created_at >= period_start,
            UsageEvent.quota_kind == "monthly",
            UsageEvent.quota_scope == scope,
        )
        request_count = int(
            session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    *common, or_(UsageEvent.status.in_(["success", "interrupted"]), active_pending)
                )
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
                    *common, UsageEvent.status.in_(["success", "interrupted"])
                )
            )
            or 0
        )
        pending_credits = int(
            session.scalar(
                select(func.coalesce(func.sum(UsageEvent.reserved_credits), 0)).where(
                    *common, active_pending
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
                    *common, UsageEvent.status.in_(["success", "interrupted"])
                )
            )
            or 0
        )
        if _enabled_numeric_limit(plan.monthly_cost_limit_usd):
            multiplier = Decimal("1") + max(plan.markup_percent, Decimal("0")) / Decimal("100")
            pending_billed = Decimal(pending_credits) * self._settings.credit_unit_usd
            pending_cost = pending_billed / multiplier if multiplier > 0 else pending_billed
            committed_cost = successful_cost + pending_cost
            if committed_cost >= plan.monthly_cost_limit_usd:
                raise SpendLimitExceededError("Monthly provider cost limit exceeded")
            reserve_billed = Decimal(reserve) * self._settings.credit_unit_usd
            reserve_cost = reserve_billed / multiplier if multiplier > 0 else reserve_billed
            if reserve and committed_cost + reserve_cost > plan.monthly_cost_limit_usd:
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
        timing: dict | None = None,
    ) -> UsageEvent:
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
        with product.locked_session(self._database) as session:
            event = session.get(UsageEvent, reservation.event_id)
            if event is None:
                raise LookupError("Usage reservation not found")
            if event.status != "pending":
                return event
            billed_usd, credits = self._calculate_charge(
                result.cost_usd if not reservation.demo else Decimal("0"),
                reservation.markup_percent,
            )
            if timing:
                for name in ("duration_ms", "first_token_ms", "streamed"):
                    setattr(event, name, timing[name])
            event.usage_known = result.usage_known
            event.provider_request_id = result.request_id
            event.prompt_tokens = result.prompt_tokens
            event.completion_tokens = result.completion_tokens
            event.cost_known = result.cost_known
            event.cost_usd = result.cost_usd
            event.cost_diem = result.cost_diem
            event.billed_usd = billed_usd
            event.internal_credits = credits
            event.reserved_credits = 0
            event.status = "success"
            event.error_code = None
            event.reservation_expires_at = None
            event.completed_at = datetime.now(UTC)
            settle_protection(session, event, result=result)
            billing.settle(session, event, result=result)
            session.flush()
            return event

    def record_failure(
        self,
        reservation: UsageReservation,
        *,
        error_code: str,
        timing: dict | None = None,
        partial: ChatResult | None = None,
    ) -> UsageEvent:
        with product.locked_session(self._database) as session:
            event = session.get(UsageEvent, reservation.event_id)
            if event is None:
                raise LookupError("Usage reservation not found")
            if event.status != "pending":
                return event
            definite = error_code in {
                "provider_authentication_error",
                "provider_access_denied",
                "provider_balance_exhausted",
                "provider_rate_limit",
            }
            if reservation.demo and definite:
                grant = session.get(DemoGrant, reservation.account_id)
                if grant:
                    grant.requests_used = max(0, grant.requests_used - 1)
            if timing:
                for name in ("duration_ms", "first_token_ms", "streamed"):
                    setattr(event, name, timing[name])
            event.status = "interrupted" if partial is not None else "failed"
            if partial is not None:
                event.prompt_tokens = partial.prompt_tokens
                event.completion_tokens = partial.completion_tokens
                event.usage_known = partial.usage_known
                event.cost_known = partial.cost_known
                event.cost_usd = partial.cost_usd
                event.cost_diem = partial.cost_diem
                event.billed_usd, event.internal_credits = self._calculate_charge(
                    partial.cost_usd if not reservation.demo else Decimal("0"),
                    reservation.markup_percent,
                )
            event.error_code = error_code
            if partial is None or partial.cost_known:
                event.reserved_credits = 0
            event.reservation_expires_at = None
            event.completed_at = datetime.now(UTC)
            settle_protection(session, event, result=partial, definite=definite)
            billing.settle(session, event, result=partial, definite=definite)
            session.flush()
            return event

    def _calculate_charge(self, cost_usd: Decimal, markup_percent: Decimal) -> tuple[Decimal, int]:
        safe_cost = max(cost_usd, Decimal("0"))
        safe_markup = max(markup_percent, Decimal("0"))
        billed_usd = (safe_cost * (Decimal("1") + safe_markup / Decimal("100"))).quantize(
            Decimal("0.00000001")
        )
        if billed_usd == 0:
            return billed_usd, 0
        credits = int(
            (billed_usd / self._settings.credit_unit_usd).to_integral_value(rounding=ROUND_CEILING)
        )
        return billed_usd, credits

    def _record_direct_success(
        self, *, account_id: str, source: str, provider: str, model: str, result: ChatResult
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
