from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Account,
    ApiToken,
    AuditEvent,
    Identity,
    Plan,
    Subscription,
    UsageEvent,
)
from app.db.session import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)


def _session_factory():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    initialize_database(engine)
    return create_session_factory(engine)


def test_core_entities_are_persisted_with_exact_money_values():
    session_factory = _session_factory()
    now = datetime.now(UTC)

    with session_factory() as session:
        account = Account(
            display_name="Тестовый пользователь",
            role="admin",
            status="active",
        )
        session.add(account)
        session.flush()

        identity = Identity(
            account_id=account.id,
            kind="telegram",
            external_id="123456789",
            label="@test_user",
        )
        plan = Plan(
            code="pro",
            name="Pro",
            currency="RUB",
            monthly_price_minor=1990_00,
            included_credits=Decimal("1000000.000000"),
            is_active=True,
        )
        session.add_all([identity, plan])
        session.flush()

        subscription = Subscription(
            account_id=account.id,
            plan_id=plan.id,
            status="active",
            starts_at=now,
            ends_at=now + timedelta(days=30),
        )
        api_token = ApiToken(
            account_id=account.id,
            name="Harness",
            token_prefix="shluz_live_abcd",
            token_hash="hash-not-a-secret",
            scopes=["chat", "files"],
            status="active",
            expires_at=now + timedelta(days=90),
        )
        usage = UsageEvent(
            account_id=account.id,
            channel="harness",
            provider="venice",
            model="venice-uncensored",
            provider_request_id="chatcmpl-test",
            status="succeeded",
            prompt_tokens=120,
            completion_tokens=30,
            cost_usd=Decimal("0.00123456"),
            billed_credits=Decimal("1.250000"),
        )
        audit = AuditEvent(
            actor_account_id=account.id,
            action="subscription.created",
            object_type="subscription",
            object_id="pending",
            details={"source": "test"},
        )
        session.add_all([subscription, api_token, usage, audit])
        session.commit()

    with session_factory() as session:
        stored_usage = session.scalar(select(UsageEvent))
        stored_token = session.scalar(select(ApiToken))

        assert session.scalar(select(func.count(Account.id))) == 1
        assert session.scalar(select(func.count(Identity.id))) == 1
        assert session.scalar(select(func.count(Plan.id))) == 1
        assert session.scalar(select(func.count(Subscription.id))) == 1
        assert session.scalar(select(func.count(ApiToken.id))) == 1
        assert session.scalar(select(func.count(UsageEvent.id))) == 1
        assert session.scalar(select(func.count(AuditEvent.id))) == 1
        assert stored_usage is not None
        assert stored_usage.cost_usd == Decimal("0.00123456")
        assert stored_usage.billed_credits == Decimal("1.250000")
        assert stored_token is not None
        assert stored_token.scopes == ["chat", "files"]


def test_identity_source_and_external_id_are_unique_together():
    session_factory = _session_factory()

    with session_factory() as session:
        first = Account(display_name="Первый")
        second = Account(display_name="Второй")
        session.add_all([first, second])
        session.flush()
        session.add(
            Identity(account_id=first.id, kind="telegram", external_id="42")
        )
        session.commit()

    with session_factory() as session:
        session.add(
            Identity(account_id=second.id, kind="telegram", external_id="42")
        )
        with pytest.raises(IntegrityError):
            session.commit()
