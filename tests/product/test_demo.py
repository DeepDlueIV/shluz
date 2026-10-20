from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import Identity, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token
from app.providers.base import ChatResult


@pytest.fixture
def demo_client(auto_client):
    # Повторно используем изолированную SQLite/PG fixture. Здесь проверяется новый
    # default demo, а существующий onboarding-набор — сохранённый closed-режим.
    from app.db.models import ProductPolicy

    with auto_client.app.state.database.session() as s:
        row = s.get(ProductPolicy, "default")
        if row:
            s.delete(row)
    return auto_client


def start(c, ident=7654321):
    key = c.app.state.settings.telegram_connector_file.read_text().strip()
    return c.post(
        "/internal/telegram/session",
        headers={"Authorization": "Bearer " + key},
        json={"telegram_id": ident, "display_name": "Демо пользователь"},
    )


def chat(c, token, model="mock-chat", **extra):
    return c.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + token},
        json={"model": model, "messages": [{"role": "user", "content": "Hi"}], **extra},
    )


def test_start_grants_one_lifetime_request_without_invite(demo_client):
    c = demo_client
    first = start(c).json()
    assert first["state"] == "active", "Start должен сразу давать демо, без заявки"
    token = first["token"]
    headers = {"Authorization": "Bearer " + token}
    p = c.get("/v1/me", headers=headers).json()
    assert p["plan"]["kind"] == "demo"
    assert p["allowance"]["resets_at"] is None
    assert p["allowance"]["requests"]["remaining"] == 1
    for i in range(1):
        assert chat(c, token).status_code == 200
        assert (
            c.get("/v1/me", headers=headers).json()["allowance"]["requests"]["remaining"] == 0 - i
        )
    denied = chat(c, token)
    assert denied.status_code == 429 and denied.json()["error"]["code"] == "demo_exhausted"
    assert start(c).json()["account_id"] == first["account_id"]
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Identity)) == 1


def test_demo_does_not_reset_next_month_or_different_channel(demo_client):
    c = demo_client
    # Проверяем исторический/настроенный пакет из трёх запросов, не новый default.
    from app.db.models import ProductPolicy

    with c.app.state.database.session() as s:
        s.add(ProductPolicy(id="default", demo_requests=3))
    account = start(c).json()["account_id"]
    service = c.app.state.usage_service
    from app.product.service import DemoExhausted

    for i, channel in enumerate(["telegram", "web", "harness"]):
        reservation = service.authorize_request(
            account_id=account,
            source=channel,
            provider="mock",
            model="mock-chat",
            now=datetime.now(UTC) + timedelta(days=35 * i),
        )
        service.record_success(reservation=reservation, result=ChatResult("ok"))
    with pytest.raises(DemoExhausted):
        service.authorize_request(
            account_id=account,
            source="telegram",
            provider="mock",
            model="mock-chat",
            now=datetime.now(UTC) + timedelta(days=400),
        )


def test_atomic_reservations_cannot_spend_two_demo_requests(demo_client):
    c = demo_client
    account = start(c).json()["account_id"]
    service = c.app.state.usage_service
    from app.product.service import DemoExhausted

    def reserve(_):
        try:
            service.authorize_request(
                account_id=account, source="telegram", provider="mock", model="mock-chat"
            )
            return True
        except DemoExhausted:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 1


def test_channel_and_plan_model_intersection_enforced_on_post(demo_client):
    import json

    from app.db.models import ModelPolicy

    c = demo_client
    first = start(c).json()
    token = first["token"]
    with c.app.state.database.session() as s:
        plan = Plan(code="paid", name="Paid")
        s.add(plan)
        s.flush()
        s.add(Subscription(account_id=first["account_id"], plan_id=plan.id))
        s.add(ModelPolicy(id="plan:" + plan.id, models_json=json.dumps(["mock-chat"])))
        s.add(ModelPolicy(id="channel:telegram", models_json="[]"))
        create_api_token(
            s,
            account_id=first["account_id"],
            source="harness",
            name="test",
            raw_token="shluz_" + "k" * 43,
        )
    assert c.get("/v1/models", headers={"Authorization": "Bearer " + token}).json()["data"] == []
    r = chat(c, token, source="harness")  # Подмена поля в JSON не меняет канал токена.
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"
    assert chat(c, "shluz_" + "k" * 43).status_code == 200
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(UsageEvent)) == 1


def test_paid_plan_preserves_demo_after_expiry_and_start(demo_client):
    c = demo_client
    first = start(c).json()
    token = first["token"]
    assert chat(c, token).status_code == 200
    with c.app.state.database.session() as s:
        plan = Plan(code="paid", name="Paid", monthly_request_limit=10)
        s.add(plan)
        s.flush()
        sub = Subscription(account_id=first["account_id"], plan_id=plan.id)
        s.add(sub)
        s.flush()
        sub_id = sub.id
    assert chat(c, token).status_code == 200
    with c.app.state.database.session() as s:
        s.get(Subscription, sub_id).status = "cancelled"
    start(c)
    p = c.get("/v1/me", headers={"Authorization": "Bearer " + token}).json()
    assert p["allowance"]["requests"]["remaining"] == 0


def test_demo_input_and_output_caps_before_upstream(demo_client):
    c = demo_client
    token = start(c).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    from app.db.models import ProductPolicy

    with c.app.state.database.session() as s:
        p = ProductPolicy(id="default", demo_max_input_chars=100, demo_max_output_tokens=32)
        s.add(p)
    r = c.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "mock-chat", "messages": [{"role": "user", "content": "x" * 101}]},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "input_too_large"
    # Отклонённый до провайдера ввод не списывает демо.
    assert c.get("/v1/me", headers=headers).json()["allowance"]["requests"]["remaining"] == 1
    assert chat(c, token, max_completion_tokens=100000).status_code == 400


def test_block_does_not_regenerate_demo(demo_client):
    c = demo_client
    first = start(c).json()
    from app.telegram.service import block

    block(c.app.state.database, first["request_id"], actor="test")
    assert start(c).json()["state"] == "blocked"


def test_catalog_payment_placeholder_cannot_grant_paid(demo_client):
    c = demo_client
    first = start(c).json()
    headers = {"Authorization": "Bearer " + first["token"]}
    r = c.get("/v1/plans", headers=headers)
    assert r.status_code == 200 and r.json()["payment"]["enabled"] is False
    assert c.post("/v1/pay", headers=headers).status_code == 404


def test_policy_admin_requires_csrf_and_preserves_counter(demo_client):
    from app.admin.routes import _admin_csrf_token

    c = demo_client
    first = start(c).json()
    token = first["token"]
    chat(c, token)
    data = {
        "mode": "demo",
        "demo_requests": "5",
        "demo_models": "mock-chat",
        "demo_max_output_tokens": "512",
        "demo_max_input_chars": "24000",
        "demo_daily_requests": "100",
        "requests_per_minute": "30",
    }
    assert (
        c.post("/admin/access/demo", data=data, auth=("owner", "test-password")).status_code == 403
    )
    data["csrf_token"] = _admin_csrf_token(c.app.state.settings)
    assert (
        c.post(
            "/admin/access/demo", data=data, auth=("owner", "test-password"), follow_redirects=False
        ).status_code
        == 303
    )
    assert (
        c.get("/v1/me", headers={"Authorization": "Bearer " + token}).json()["allowance"][
            "requests"
        ]["remaining"]
        == 0  # Новый лимит действует на новые пакеты, не пополняет использованный.
    )


def test_demo_does_not_spend_paid_quota(demo_client):
    c = demo_client
    who = start(c).json()
    token = who["token"]
    for _ in range(1):
        assert chat(c, token).status_code == 200
    with c.app.state.database.session() as s:
        plan = Plan(code="upgrade", name="Upgrade", monthly_request_limit=10)
        s.add(plan)
        s.flush()
        s.add(Subscription(account_id=who["account_id"], plan_id=plan.id))
    headers = {"Authorization": "Bearer " + token}
    assert c.get("/v1/me", headers=headers).json()["allowance"]["requests"]["remaining"] == 10
    assert chat(c, token).status_code == 200
    assert c.get("/v1/me", headers=headers).json()["allowance"]["requests"]["remaining"] == 9


def test_failure_refund_is_idempotent_but_unknown_does_not_top_up(demo_client):
    from app.db.models import DemoGrant

    c = demo_client
    account = start(c).json()["account_id"]
    service = c.app.state.usage_service
    args = dict(account_id=account, source="telegram", provider="mock", model="mock-chat")
    res = service.authorize_request(**args)
    service.record_failure(res, error_code="provider_balance_exhausted")
    service.record_failure(res, error_code="provider_balance_exhausted")
    with c.app.state.database.session() as s:
        assert s.get(DemoGrant, account).requests_used == 0
    res = service.authorize_request(**args)
    service.record_failure(res, error_code="provider_error")
    with c.app.state.database.session() as s:
        assert s.get(DemoGrant, account).requests_used == 1


def test_global_daily_budget_and_fixed_window_limiter(demo_client):
    from app.db.models import ProductPolicy

    c = demo_client
    a = start(c).json()
    b = start(c, 998877).json()
    with c.app.state.database.session() as s:
        s.add(ProductPolicy(id="default", requests_per_minute=1, demo_daily_requests=2))
    assert chat(c, a["token"]).status_code == 200
    limit = chat(c, a["token"])
    assert limit.status_code == 429 and limit.headers["Retry-After"] == "60"
    assert chat(c, b["token"]).status_code == 200
    fresh = start(c, 112233).json()
    assert chat(c, fresh["token"]).json()["error"]["code"] == "demo_temporarily_unavailable"


def test_user_spending_never_exposes_provider_cost_and_other_accounts(demo_client):
    from decimal import Decimal

    c = demo_client
    a = start(c).json()
    b = start(c, 5566).json()
    res = c.app.state.usage_service.authorize_request(
        account_id=a["account_id"], source="telegram", provider="mock", model="mock-chat"
    )
    c.app.state.usage_service.record_success(
        reservation=res,
        result=ChatResult("answer", prompt_tokens=8, completion_tokens=4, cost_usd=Decimal(".01")),
    )
    data = c.get("/v1/me/usage", headers={"Authorization": "Bearer " + a["token"]}).json()
    assert data["summary"]["prompt_tokens"] == 8
    assert Decimal(data["summary"]["charged_usd"]) == 0  # Demo бесплатно пользователю.
    assert "cost_usd" not in str(data)
    assert (
        c.get("/v1/me/usage", headers={"Authorization": "Bearer " + b["token"]}).json()["data"]
        == []
    )
    assert (
        c.get(
            "/v1/me/usage?limit=100000", headers={"Authorization": "Bearer " + a["token"]}
        ).status_code
        == 422
    )


def test_demo_admin_and_profile_agree(demo_client):
    c = demo_client
    who = start(c).json()
    chat(c, who["token"])
    text = c.get("/admin/users", auth=("owner", "test-password")).text
    assert "Демо" in text and "разовых пробных" in text
    assert "Демо пользователь" in text
    assert "Нет активного тарифа" not in text
