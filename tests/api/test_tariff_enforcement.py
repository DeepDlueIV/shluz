from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import desc, select

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token
from app.factory import create_app
from app.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    ProviderRateLimitError,
)
from app.providers.registry import ProviderRegistry


def _personal_access(client, *, with_plan: bool = True, **plan_fields):
    database = client.app.state.database
    raw_token = "shluz_tariff_test_token"
    with database.session() as session:
        account = Account(display_name="Тарифный пользователь")
        session.add(account)
        session.flush()
        plan = None
        if with_plan:
            plan = Plan(code="api-plan", name="API plan", **plan_fields)
            session.add(plan)
            session.flush()
            session.add(Subscription(account_id=account.id, plan_id=plan.id))
        create_api_token(
            session,
            account_id=account.id,
            name="Harness",
            source="harness",
            raw_token=raw_token,
        )
        return raw_token, account.id, plan.id if plan is not None else None


def _chat(client, token: str):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )


def test_personal_chat_requires_subscription(client):
    token, _, _ = _personal_access(client, with_plan=False)

    response = _chat(client, token)

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "subscription_required"


def test_request_limit_is_returned_as_openai_style_429(client):
    token, account_id, plan_id = _personal_access(
        client,
        monthly_request_limit=1,
    )
    database = client.app.state.database
    with database.session() as session:
        session.add(
            UsageEvent(
                account_id=account_id,
                plan_id=plan_id,
                source="harness",
                provider="mock",
                model="mock-chat",
                status="success",
                created_at=datetime.now(UTC),
            )
        )

    response = _chat(client, token)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "request_limit_exceeded"


def test_credit_and_spend_limits_return_payment_required(client):
    token, account_id, plan_id = _personal_access(
        client,
        monthly_credit_limit=10,
        monthly_cost_limit_usd=Decimal("0.01"),
    )
    database = client.app.state.database
    with database.session() as session:
        session.add(
            UsageEvent(
                account_id=account_id,
                plan_id=plan_id,
                source="harness",
                provider="mock",
                model="mock-chat",
                status="success",
                internal_credits=10,
                cost_usd=Decimal("0.01"),
                created_at=datetime.now(UTC),
            )
        )

    credit_response = _chat(client, token)
    assert credit_response.status_code == 402
    assert credit_response.json()["error"]["code"] == "credit_limit_exceeded"

    with database.session() as session:
        plan = session.get(Plan, plan_id)
        assert plan is not None
        plan.monthly_credit_limit = None

    spend_response = _chat(client, token)
    assert spend_response.status_code == 402
    assert spend_response.json()["error"]["code"] == "spend_limit_exceeded"


def test_successful_personal_chat_settles_reserved_event(client):
    token, account_id, plan_id = _personal_access(
        client,
        request_credit_reserve=25,
        markup_percent=Decimal("30"),
    )

    response = _chat(client, token)

    assert response.status_code == 200
    database = client.app.state.database
    with database.session() as session:
        event = session.scalar(
            select(UsageEvent)
            .where(UsageEvent.account_id == account_id)
            .order_by(desc(UsageEvent.created_at))
        )
    assert event is not None
    assert event.plan_id == plan_id
    assert event.status == "success"
    assert event.reserved_credits == 0
    assert event.completed_at is not None


class FailingProvider:
    @property
    def name(self) -> str:
        return "failing"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="failing-chat", provider=self.name)]

    def chat_completion(self, request: ChatRequest) -> ChatResult:
        raise ProviderRateLimitError("slow down")


def test_provider_failure_releases_api_reservation(tmp_path):
    settings = Settings(
        bootstrap_api_token=SecretStr("bootstrap"),
        admin_password=SecretStr("admin-password"),
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'failure.db'}"),
    )
    database = Database(settings)
    app = create_app(
        settings,
        registry=ProviderRegistry([FailingProvider()]),
        database=database,
    )
    client = TestClient(app)
    raw_token = "shluz_failure_token"
    with database.session() as session:
        account = Account(display_name="Failure user")
        plan = Plan(code="failure", name="Failure")
        session.add_all([account, plan])
        session.flush()
        session.add(Subscription(account_id=account.id, plan_id=plan.id))
        create_api_token(
            session,
            account_id=account.id,
            name="Harness",
            source="harness",
            raw_token=raw_token,
        )
        account_id = account.id

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "model": "failing-chat",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )

    assert response.status_code == 429
    with database.session() as session:
        event = session.scalar(
            select(UsageEvent)
            .where(UsageEvent.account_id == account_id)
            .order_by(desc(UsageEvent.created_at))
        )
    assert event is not None
    assert event.status == "failed"
    assert event.error_code == "provider_rate_limit"
    assert event.reserved_credits == 0
