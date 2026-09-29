from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, Plan, Subscription, UsageEvent
from app.providers.base import ChatResult
from app.usage.service import (
    CreditLimitExceededError,
    RequestLimitExceededError,
    SpendLimitExceededError,
    SubscriptionRequiredError,
    UsageService,
)

_TEST_PERIOD_START = datetime(2026, 9, 1, tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _service(tmp_path) -> tuple[Database, UsageService]:
    settings = Settings(
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'allowance.db'}"),
        credit_unit_usd=Decimal("0.001"),
        reservation_ttl_seconds=900,
    )
    database = Database(settings)
    database.create_schema()
    return database, UsageService(database, settings)


def _subscribed_account(
    database: Database,
    *,
    request_limit: int | None = 10,
    credit_limit: int | None = 1_000,
    cost_limit: Decimal | None = Decimal("10"),
    reserve: int = 100,
    markup: Decimal = Decimal("25"),
) -> tuple[str, str]:
    with database.session() as session:
        account = Account(display_name="Тестовый пользователь")
        plan = Plan(
            code="pro",
            name="Pro",
            monthly_request_limit=request_limit,
            monthly_credit_limit=credit_limit,
            monthly_cost_limit_usd=cost_limit,
            request_credit_reserve=reserve,
            markup_percent=markup,
        )
        session.add_all([account, plan])
        session.flush()
        session.add(
            Subscription(
                account_id=account.id,
                plan_id=plan.id,
                starts_at=_TEST_PERIOD_START,
            )
        )
        return account.id, plan.id


def test_personal_request_requires_active_subscription(tmp_path):
    database, service = _service(tmp_path)
    with database.session() as session:
        account = Account(display_name="Без тарифа")
        session.add(account)
        session.flush()
        account_id = account.id

    with pytest.raises(SubscriptionRequiredError):
        service.authorize_request(
            account_id=account_id,
            source="harness",
            provider="mock",
            model="mock-chat",
        )


def test_authorize_request_creates_expiring_reservation(tmp_path):
    database, service = _service(tmp_path)
    account_id, plan_id = _subscribed_account(database)
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    reservation = service.authorize_request(
        account_id=account_id,
        source="harness",
        provider="mock",
        model="mock-chat",
        now=now,
    )

    with database.session() as session:
        event = session.get(UsageEvent, reservation.event_id)

    assert event is not None
    assert event.status == "pending"
    assert event.plan_id == plan_id
    assert event.reserved_credits == 100
    assert event.pricing_markup_percent == Decimal("25.00")
    assert event.reservation_expires_at is not None
    assert _as_utc(event.reservation_expires_at) == now + timedelta(seconds=900)


def test_request_credit_and_spend_limits_block_before_provider_call(tmp_path):
    database, service = _service(tmp_path)
    account_id, plan_id = _subscribed_account(
        database,
        request_limit=1,
        credit_limit=100,
        cost_limit=Decimal("0.08"),
        reserve=100,
        markup=Decimal("25"),
    )
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    service.authorize_request(
        account_id=account_id,
        source="site",
        provider="mock",
        model="mock-chat",
        now=now,
    )

    with pytest.raises(RequestLimitExceededError):
        service.authorize_request(
            account_id=account_id,
            source="site",
            provider="mock",
            model="mock-chat",
            now=now,
        )

    with database.session() as session:
        plan = session.get(Plan, plan_id)
        assert plan is not None
        plan.monthly_request_limit = None

    with pytest.raises(CreditLimitExceededError):
        service.authorize_request(
            account_id=account_id,
            source="site",
            provider="mock",
            model="mock-chat",
            now=now,
        )

    with database.session() as session:
        plan = session.get(Plan, plan_id)
        assert plan is not None
        plan.monthly_credit_limit = None

    with pytest.raises(SpendLimitExceededError):
        service.authorize_request(
            account_id=account_id,
            source="site",
            provider="mock",
            model="mock-chat",
            now=now,
        )


def test_expired_pending_reservation_does_not_consume_allowance(tmp_path):
    database, service = _service(tmp_path)
    account_id, plan_id = _subscribed_account(
        database,
        request_limit=1,
        credit_limit=100,
        cost_limit=None,
        reserve=100,
    )
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    with database.session() as session:
        session.add(
            UsageEvent(
                account_id=account_id,
                plan_id=plan_id,
                source="harness",
                provider="mock",
                model="mock-chat",
                status="pending",
                reserved_credits=100,
                reservation_expires_at=now - timedelta(seconds=1),
                created_at=now - timedelta(minutes=20),
            )
        )

    reservation = service.authorize_request(
        account_id=account_id,
        source="harness",
        provider="mock",
        model="mock-chat",
        now=now,
    )

    assert reservation.event_id


def test_success_settlement_records_exact_credits_revenue_and_margin(tmp_path):
    database, service = _service(tmp_path)
    account_id, _ = _subscribed_account(database, markup=Decimal("25"))
    reservation = service.authorize_request(
        account_id=account_id,
        source="harness",
        provider="venice",
        model="model-a",
    )

    event = service.record_success(
        reservation=reservation,
        result=ChatResult(
            content="Готово",
            prompt_tokens=100,
            completion_tokens=20,
            request_id="provider-1",
            cost_usd=Decimal("0.008"),
        ),
    )

    assert event.status == "success"
    assert event.cost_usd == Decimal("0.008")
    assert event.billed_usd == Decimal("0.01000000")
    assert event.internal_credits == 10
    assert event.reserved_credits == 0
    assert event.completed_at is not None


def test_provider_failure_releases_reservation_and_keeps_error_event(tmp_path):
    database, service = _service(tmp_path)
    account_id, _ = _subscribed_account(database)
    reservation = service.authorize_request(
        account_id=account_id,
        source="telegram",
        provider="venice",
        model="model-a",
    )

    event = service.record_failure(reservation, error_code="provider_rate_limit")

    assert event.status == "failed"
    assert event.error_code == "provider_rate_limit"
    assert event.reserved_credits == 0
    assert event.completed_at is not None
    with database.session() as session:
        stored = session.scalar(select(UsageEvent).where(UsageEvent.id == event.id))
    assert stored is not None
    assert stored.status == "failed"
