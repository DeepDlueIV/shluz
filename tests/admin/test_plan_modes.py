"""Неактивные поля PAYG не стирают сохранённые параметры подписки."""

import re
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Plan

AUTH = ("test-admin", "test-admin-password")


def csrf(client):
    return re.search(
        r'name="csrf_token" value="([^"]+)"', client.get("/admin/plans", auth=AUTH).text
    ).group(1)


def seed(client):
    with client.app.state.database.session() as s:
        p = Plan(
            code="mode",
            name="Подписка",
            monthly_price_usd=Decimal("9"),
            monthly_request_limit=150,
            monthly_cost_limit_usd=Decimal("5"),
            monthly_credit_limit=800,
            request_credit_reserve=7,
            markup_percent=15,
        )
        s.add(p)
        s.flush()
        return p.id


def test_payg_switch_and_edit_preserve_subscription_values(client):
    pid = seed(client)
    values = {
        "csrf_token": csrf(client),
        "billing_mode": "payg",
        "name": "По факту",
        "code": "mode",
        "markup_percent": "20",
        "active": "on",
    }
    for extra in [
        {},
        {
            "monthly_price_usd": "999",
            "monthly_request_limit": "bad",
            "request_credit_reserve": "-5",
        },
    ]:
        response = client.post(
            f"/admin/plans/{pid}", auth=AUTH, data={**values, **extra}, follow_redirects=False
        )
        assert response.status_code == 303
        with client.app.state.database.session() as s:
            p = s.get(Plan, pid)
            assert (p.billing_mode, p.markup_percent) == ("payg", Decimal("20"))
            assert p.monthly_price_usd == Decimal("9")
            assert p.monthly_request_limit == 150
            assert p.monthly_cost_limit_usd == Decimal("5")
            assert p.monthly_credit_limit == 800 and p.request_credit_reserve == 7


def test_new_payg_ignores_irrelevant_subscription_fields(client):
    r = client.post(
        "/admin/plans",
        auth=AUTH,
        data={
            "csrf_token": csrf(client),
            "code": "payg",
            "name": "Кошелёк",
            "billing_mode": "payg",
            "monthly_price_usd": "not-a-number",
            "monthly_credit_limit": "-8",
            "markup_percent": "12.5",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with client.app.state.database.session() as s:
        p = s.scalar(select(Plan).where(Plan.code == "payg"))
        assert p.billing_mode == "payg" and p.monthly_price_usd == 0
        assert p.monthly_request_limit is None and p.monthly_credit_limit is None
        assert p.markup_percent == Decimal("12.50")


def test_edit_without_mode_keeps_existing_mode_and_unknown_mode_rejected(client):
    pid = seed(client)
    with client.app.state.database.session() as s:
        s.get(Plan, pid).billing_mode = "payg"
    values = {"csrf_token": csrf(client), "code": "mode", "name": "Имя", "active": "on"}
    assert (
        client.post(
            f"/admin/plans/{pid}", auth=AUTH, data=values, follow_redirects=False
        ).status_code
        == 303
    )
    with client.app.state.database.session() as s:
        p = s.get(Plan, pid)
        assert p.billing_mode == "payg" and p.monthly_price_usd == 9
    assert (
        client.post(
            f"/admin/plans/{pid}", auth=AUTH, data={**values, "billing_mode": "bad"}
        ).status_code
        == 422
    )


def test_payg_form_marks_subscription_fields_inactive_and_keeps_values(client):
    pid = seed(client)
    with client.app.state.database.session() as s:
        s.get(Plan, pid).billing_mode = "payg"
    html = client.get("/admin/plans", auth=AUTH).text
    form = re.search(rf'<form[^>]+action="/admin/plans/{pid}"[^>]*>(.*?)</form>', html, re.S).group(
        1
    )
    group = re.search(
        r'<fieldset([^>]*data-plan-section="subscription"[^>]*)>(.*?)</fieldset>', form, re.S
    )
    assert group is not None
    assert "disabled" in group.group(1) and "hidden" in group.group(1)
    assert re.search(r'name="monthly_request_limit"[^>]*value="150"', group.group(2))
    assert 'name="markup_percent"' in form
    assert 'data-plan-section="payg"' in form


def test_help_distinguishes_channel_quotas_wallet_and_unknown_reserve():
    from app.admin.presentation import HELP
    assert "отдельного канала" in HELP["requests"][1]
    assert "100 кредитов = 1 USD" in HELP["credits"][1]
    assert "неизвестном" in HELP["reserve"][1]
    assert "можно архивировать" in HELP["role"][1]
