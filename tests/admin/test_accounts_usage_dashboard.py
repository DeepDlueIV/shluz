from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Plan, UsageEvent


@pytest.mark.parametrize("path", ["/admin/users", "/admin/plans", "/admin/usage"])
def test_management_pages_require_admin_credentials(client, path):
    response = client.get(path)

    assert response.status_code == 401


def test_users_page_shows_bootstrap_account_and_hides_api_secrets(
    client,
    test_settings,
):
    response = client.get(
        "/admin/users",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert "Пользователи" in response.text
    assert "Bootstrap access" in response.text
    assert "bootstrap" in response.text
    assert "service" in response.text
    assert "active" in response.text
    assert test_settings.bootstrap_api_token.get_secret_value() not in response.text
    assert test_settings.admin_password.get_secret_value() not in response.text


def test_plans_page_has_empty_state_and_can_show_plan(client):
    empty_response = client.get(
        "/admin/plans",
        auth=("test-admin", "test-admin-password"),
    )

    assert empty_response.status_code == 200
    assert "Тарифов пока нет" in empty_response.text

    database = client.app.state.database
    with database.session() as session:
        session.add(
            Plan(
                code="starter",
                name="Стартовый",
                monthly_credit_limit=1000,
                monthly_request_limit=250,
                active=True,
            )
        )

    response = client.get(
        "/admin/plans",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert "Стартовый" in response.text
    assert "starter" in response.text
    assert "1,000" in response.text
    assert "250" in response.text
    assert "Активен" in response.text


def test_usage_page_shows_totals_and_recent_events(client):
    database = client.app.state.database
    with database.session() as session:
        session.add(
            UsageEvent(
                account_id="bootstrap",
                source="harness",
                provider="venice",
                model="venice-uncensored",
                provider_request_id="usage-dashboard-request",
                prompt_tokens=1500,
                completion_tokens=450,
                cost_usd=Decimal("0.125"),
                cost_diem=Decimal("2"),
                status="success",
            )
        )

    response = client.get(
        "/admin/usage",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert "Расход" in response.text
    assert "0.125" in response.text
    assert "2" in response.text
    assert "1,500" in response.text
    assert "450" in response.text
    assert "venice-uncensored" in response.text
    assert "harness" in response.text
    assert "bootstrap" in response.text
    assert "usage-dashboard-request" in response.text

    with database.session() as session:
        event = session.scalar(select(UsageEvent))
    assert event is not None


def test_usage_page_explains_empty_state(client):
    response = client.get(
        "/admin/usage",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert "Расходов пока нет" in response.text


def test_admin_dashboard_links_to_users_plans_and_usage(client):
    response = client.get(
        "/admin",
        auth=("test-admin", "test-admin-password"),
    )

    assert response.status_code == 200
    assert 'href="/admin/users"' in response.text
    assert 'href="/admin/plans"' in response.text
    assert 'href="/admin/usage"' in response.text
