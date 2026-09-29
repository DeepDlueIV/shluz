import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Account, Plan, Subscription, UsageEvent

_ADMIN_AUTH = ("test-admin", "test-admin-password")
_CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf_token(client, path: str = "/admin/plans") -> str:
    response = client.get(path, auth=_ADMIN_AUTH)
    assert response.status_code == 200
    match = _CSRF_PATTERN.search(response.text)
    assert match is not None
    return match.group(1)


def test_admin_can_create_and_update_tariff(client):
    csrf_token = _csrf_token(client)
    response = client.post(
        "/admin/plans",
        auth=_ADMIN_AUTH,
        data={
            "csrf_token": csrf_token,
            "code": "pro",
            "name": "Pro",
            "monthly_price_usd": "29.00",
            "monthly_cost_limit_usd": "8.50",
            "monthly_credit_limit": "20000",
            "monthly_request_limit": "500",
            "request_credit_reserve": "250",
            "markup_percent": "40",
            "active": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    database = client.app.state.database
    with database.session() as session:
        plan = session.scalar(select(Plan).where(Plan.code == "pro"))
        assert plan is not None
        plan_id = plan.id
        assert plan.monthly_price_usd == Decimal("29.00")
        assert plan.monthly_cost_limit_usd == Decimal("8.50000000")
        assert plan.request_credit_reserve == 250
        assert plan.markup_percent == Decimal("40.00")

    csrf_token = _csrf_token(client)
    update = client.post(
        f"/admin/plans/{plan_id}",
        auth=_ADMIN_AUTH,
        data={
            "csrf_token": csrf_token,
            "code": "pro",
            "name": "Pro Plus",
            "monthly_price_usd": "39.00",
            "monthly_cost_limit_usd": "12",
            "monthly_credit_limit": "30000",
            "monthly_request_limit": "800",
            "request_credit_reserve": "300",
            "markup_percent": "50",
        },
        follow_redirects=False,
    )

    assert update.status_code == 303
    with database.session() as session:
        plan = session.get(Plan, plan_id)
        assert plan is not None
        assert plan.name == "Pro Plus"
        assert plan.monthly_price_usd == Decimal("39.00")
        assert plan.active is False


def test_admin_rejects_negative_tariff_values(client):
    csrf_token = _csrf_token(client)

    response = client.post(
        "/admin/plans",
        auth=_ADMIN_AUTH,
        data={
            "csrf_token": csrf_token,
            "code": "bad",
            "name": "Bad",
            "monthly_price_usd": "-1",
            "monthly_cost_limit_usd": "",
            "monthly_credit_limit": "",
            "monthly_request_limit": "",
            "request_credit_reserve": "0",
            "markup_percent": "0",
            "active": "on",
        },
    )

    assert response.status_code == 422


def test_admin_can_assign_and_remove_plan_from_user(client):
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Пользователь тарифа")
        plan = Plan(code="starter", name="Starter")
        session.add_all([account, plan])
        session.flush()
        account_id = account.id
        plan_id = plan.id

    csrf_token = _csrf_token(client, "/admin/users")
    assign = client.post(
        f"/admin/users/{account_id}/plan",
        auth=_ADMIN_AUTH,
        data={"csrf_token": csrf_token, "plan_id": plan_id},
        follow_redirects=False,
    )

    assert assign.status_code == 303
    with database.session() as session:
        active = session.scalar(
            select(Subscription).where(
                Subscription.account_id == account_id,
                Subscription.status == "active",
            )
        )
    assert active is not None
    assert active.plan_id == plan_id

    csrf_token = _csrf_token(client, "/admin/users")
    remove = client.post(
        f"/admin/users/{account_id}/plan",
        auth=_ADMIN_AUTH,
        data={"csrf_token": csrf_token, "plan_id": ""},
        follow_redirects=False,
    )

    assert remove.status_code == 303
    with database.session() as session:
        active = session.scalar(
            select(Subscription).where(
                Subscription.account_id == account_id,
                Subscription.status == "active",
            )
        )
    assert active is None


def test_admin_pages_show_current_allowance_revenue_and_margin(client):
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Маржинальный пользователь")
        plan = Plan(
            code="margin",
            name="Margin",
            monthly_request_limit=10,
            monthly_credit_limit=100,
            monthly_cost_limit_usd=Decimal("1"),
        )
        session.add_all([account, plan])
        session.flush()
        session.add(Subscription(account_id=account.id, plan_id=plan.id))
        session.add(
            UsageEvent(
                account_id=account.id,
                plan_id=plan.id,
                source="harness",
                provider="venice",
                model="model-a",
                status="success",
                internal_credits=25,
                cost_usd=Decimal("0.02000000"),
                billed_usd=Decimal("0.03000000"),
                prompt_tokens=100,
                completion_tokens=20,
                created_at=datetime.now(UTC),
            )
        )

    users_page = client.get("/admin/users", auth=_ADMIN_AUTH)
    usage_page = client.get("/admin/usage", auth=_ADMIN_AUTH)

    assert users_page.status_code == 200
    assert "Маржинальный пользователь" in users_page.text
    assert "75" in users_page.text
    assert "0.01000000" in users_page.text
    assert usage_page.status_code == 200
    assert "Расчётная выручка" in usage_page.text
    assert "0.03000000" in usage_page.text
    assert "0.01000000" in usage_page.text
