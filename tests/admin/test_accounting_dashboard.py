from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.models import (
    Account,
    ApiToken,
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
from app.factory import create_app

ADMIN_AUTH = ("test-admin", "test-admin-password")
TOKEN_HASH = "secret-token-hash-that-must-not-be-shown"


def _client_with_accounting_data(test_settings) -> TestClient:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)

    with session_factory() as session:
        account = Account(
            display_name="Владелец сервиса",
            role="admin",
            status="active",
        )
        plan = Plan(
            code="pro",
            name="Профессиональный",
            currency="RUB",
            monthly_price_minor=199000,
            included_credits=Decimal("1000000"),
            is_active=True,
        )
        session.add_all([account, plan])
        session.flush()
        session.add_all(
            [
                Identity(
                    account_id=account.id,
                    kind="telegram",
                    external_id="123456",
                    label="@owner",
                ),
                Subscription(
                    account_id=account.id,
                    plan_id=plan.id,
                    status="active",
                    starts_at=now,
                    ends_at=now + timedelta(days=30),
                ),
                ApiToken(
                    account_id=account.id,
                    name="Основной Harness",
                    token_prefix="shluz_live_abcd",
                    token_hash=TOKEN_HASH,
                    scopes=["chat", "files"],
                    status="active",
                    expires_at=now + timedelta(days=90),
                ),
                UsageEvent(
                    account_id=account.id,
                    channel="harness",
                    provider="venice",
                    model="venice-uncensored",
                    status="succeeded",
                    prompt_tokens=100,
                    completion_tokens=40,
                    cost_usd=Decimal("0.00420000"),
                    billed_credits=Decimal("4.200000"),
                ),
                UsageEvent(
                    account_id=account.id,
                    channel="telegram",
                    provider="venice",
                    model="venice-uncensored",
                    status="failed",
                    error_code="provider_rate_limit",
                ),
            ]
        )
        session.commit()

    return TestClient(create_app(test_settings, session_factory=session_factory))


def test_admin_home_links_to_accounting_sections(client):
    response = client.get("/admin", auth=ADMIN_AUTH)

    assert response.status_code == 200
    assert 'href="/admin/users"' in response.text
    assert 'href="/admin/plans"' in response.text
    assert 'href="/admin/usage"' in response.text
    assert 'href="/admin/tokens"' in response.text


def test_accounting_pages_explain_empty_state(client):
    expected = {
        "/admin/users": "Пользователей пока нет",
        "/admin/plans": "Тарифов пока нет",
        "/admin/usage": "Запросов пока нет",
        "/admin/tokens": "Клиентских токенов пока нет",
    }

    for path, text in expected.items():
        response = client.get(path, auth=ADMIN_AUTH)
        assert response.status_code == 200
        assert text in response.text


def test_users_page_shows_identity_subscription_and_spend(test_settings):
    client = _client_with_accounting_data(test_settings)

    response = client.get("/admin/users", auth=ADMIN_AUTH)

    assert response.status_code == 200
    assert "Владелец сервиса" in response.text
    assert "admin" in response.text
    assert "@owner" in response.text
    assert "Профессиональный" in response.text
    assert "0.00420000" in response.text


def test_plans_page_shows_price_credits_and_subscription_count(test_settings):
    client = _client_with_accounting_data(test_settings)

    response = client.get("/admin/plans", auth=ADMIN_AUTH)

    assert response.status_code == 200
    assert "Профессиональный" in response.text
    assert "pro" in response.text
    assert "1990.00" in response.text
    assert "1000000" in response.text
    assert "1" in response.text


def test_usage_page_shows_cost_tokens_channels_and_failures(test_settings):
    client = _client_with_accounting_data(test_settings)

    response = client.get("/admin/usage", auth=ADMIN_AUTH)

    assert response.status_code == 200
    assert "0.00420000" in response.text
    assert "140" in response.text
    assert "harness" in response.text
    assert "telegram" in response.text
    assert "venice-uncensored" in response.text
    assert "provider_rate_limit" in response.text


def test_tokens_page_shows_only_safe_metadata(test_settings):
    client = _client_with_accounting_data(test_settings)

    response = client.get("/admin/tokens", auth=ADMIN_AUTH)

    assert response.status_code == 200
    assert "Основной Harness" in response.text
    assert "shluz_live_abcd" in response.text
    assert "chat" in response.text
    assert "files" in response.text
    assert "active" in response.text
    assert TOKEN_HASH not in response.text
