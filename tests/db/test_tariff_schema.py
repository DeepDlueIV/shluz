from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import SecretStr
from sqlalchemy import inspect

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, Plan, UsageEvent


def _database(tmp_path) -> Database:
    settings = Settings(
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'tariffs.db'}")
    )
    database = Database(settings)
    database.create_schema()
    return database


def test_billing_settings_have_safe_defaults():
    settings = Settings()

    assert settings.credit_unit_usd == Decimal("0.001")
    assert settings.reservation_ttl_seconds == 900


def test_plan_persists_pricing_and_limit_fields(tmp_path):
    database = _database(tmp_path)

    with database.session() as session:
        plan = Plan(
            code="pro",
            name="Pro",
            monthly_price_usd=Decimal("29.00"),
            monthly_cost_limit_usd=Decimal("8.50"),
            monthly_credit_limit=20_000,
            monthly_request_limit=500,
            request_credit_reserve=250,
            markup_percent=Decimal("40.00"),
        )
        session.add(plan)
        session.flush()
        plan_id = plan.id

    with database.session() as session:
        stored = session.get(Plan, plan_id)

    assert stored is not None
    assert stored.monthly_price_usd == Decimal("29.00")
    assert stored.monthly_cost_limit_usd == Decimal("8.50000000")
    assert stored.request_credit_reserve == 250
    assert stored.markup_percent == Decimal("40.00")


def test_usage_event_persists_reservation_and_historical_billing(tmp_path):
    database = _database(tmp_path)
    expires_at = datetime.now(UTC) + timedelta(minutes=15)

    with database.session() as session:
        account = Account(display_name="Пользователь")
        plan = Plan(code="starter", name="Starter")
        session.add_all([account, plan])
        session.flush()
        event = UsageEvent(
            account_id=account.id,
            plan_id=plan.id,
            source="harness",
            provider="mock",
            model="mock-chat",
            status="pending",
            reserved_credits=100,
            reservation_expires_at=expires_at,
            billed_usd=Decimal("0.01200000"),
            pricing_markup_percent=Decimal("20.00"),
        )
        session.add(event)
        session.flush()
        event_id = event.id

    with database.session() as session:
        stored = session.get(UsageEvent, event_id)

    assert stored is not None
    assert stored.plan_id == plan.id
    assert stored.reserved_credits == 100
    assert stored.reservation_expires_at is not None
    assert stored.billed_usd == Decimal("0.01200000")
    assert stored.pricing_markup_percent == Decimal("20.00")


def test_database_contains_billing_columns(tmp_path):
    database = _database(tmp_path)
    inspector = inspect(database.engine)

    plan_columns = {column["name"] for column in inspector.get_columns("plans")}
    usage_columns = {column["name"] for column in inspector.get_columns("usage_events")}

    assert {
        "monthly_price_usd",
        "monthly_cost_limit_usd",
        "request_credit_reserve",
        "markup_percent",
    } <= plan_columns
    assert {
        "plan_id",
        "reserved_credits",
        "reservation_expires_at",
        "completed_at",
        "billed_usd",
        "pricing_markup_percent",
    } <= usage_columns
