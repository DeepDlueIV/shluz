"""Регрессии резерва денег/слота и ограниченного JSON-пути демо."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Account, DemoGrant, ModelPolicy, ProductPolicy, UsageEvent
from app.db.repositories import create_api_token
from app.product.service import PolicyError, grant_demo
from app.providers.base import ChatResult, StreamChunk
from app.webaccess.models import DemoControl, DemoHold, DemoModelBound


def account(app, *, provider="mock", model="mock-chat", count=5):
    with app.state.database.session() as s:
        p = s.get(ProductPolicy, "default")
        if p is None:
            p = ProductPolicy(
                id="default", demo_requests=count, demo_models_json='["' + model + '"]'
            )
            s.add(p)
        rule = s.get(ModelPolicy, "channel:web")
        if rule is None:
            s.add(ModelPolicy(id="channel:web", models_json='["' + model + '"]'))
        a = Account(display_name="Guard test")
        s.add(a)
        s.flush()
        grant_demo(s, a.id)
        ident = a.id
    return dict(account_id=ident, source="web", provider=provider, model=model)


def test_global_slot_and_per_account_are_atomic(web_app):
    users = [account(web_app) for _ in range(8)]
    usage = web_app.state.usage_service

    def attempt(args):
        try:
            return usage.authorize_request(**args)
        except PolicyError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [v for v in pool.map(attempt, users) if v is not None]
    assert len(accepted) == 3
    with web_app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(UsageEvent)) == 3
        assert s.scalar(select(func.sum(DemoGrant.requests_used))) == 3
    assert (
        attempt({k: getattr(accepted[0], k) for k in ("account_id", "source", "provider", "model")})
        is None
    )
    for reservation in accepted:
        assert reservation.timeout_seconds == 45 and reservation.idle_seconds == 15
        usage.record_success(reservation=reservation, result=ChatResult("test"))
    assert attempt(users[7]) is not None


def test_paid_demo_disabled_without_budget_and_reviewed_bound(web_app):
    args = account(web_app, provider="priced", model="priced-model")
    usage = web_app.state.usage_service
    with pytest.raises(PolicyError):
        usage.authorize_request(**args)
    with web_app.state.database.session() as s:
        s.add(
            DemoModelBound(
                id="priced:priced-model",
                max_cost_usd=Decimal(".02"),
                max_input_chars=3000,
                max_output_tokens=512,
                reviewed=True,
            )
        )
    with pytest.raises(PolicyError):
        usage.authorize_request(**args)
    with web_app.state.database.session() as s:
        from app.webaccess.security import control

        control(s).daily_usd = Decimal(".03")
    reservation = usage.authorize_request(**args)
    usage.record_failure(reservation, error_code="provider_error")
    # Неизвестный результат не возвращает деньги, хотя освободил слот.
    with pytest.raises(PolicyError):
        usage.authorize_request(**args)
    with web_app.state.database.session() as s:
        hold = s.get(DemoHold, reservation.event_id)
        assert hold.amount == Decimal(".02") and hold.uncertain and not hold.active
        assert s.get(DemoGrant, args["account_id"]).requests_used == 1


def test_known_rejection_refunds_once_and_cost_overrun_trips_fuse(web_app):
    args = account(web_app, provider="priced", model="priced-model")
    with web_app.state.database.session() as s:
        s.add(DemoControl(id="default", daily_usd=Decimal(".1")))
        s.add(
            DemoModelBound(
                id="priced:priced-model",
                max_cost_usd=Decimal(".02"),
                max_input_chars=3000,
                max_output_tokens=512,
                reviewed=True,
            )
        )
    usage = web_app.state.usage_service
    reservation = usage.authorize_request(**args)
    for _ in range(2):
        usage.record_failure(reservation, error_code="provider_balance_exhausted")
    with web_app.state.database.session() as s:
        assert s.get(DemoGrant, args["account_id"]).requests_used == 0
        assert s.get(DemoHold, reservation.event_id).amount == 0
    reservation = usage.authorize_request(**args)
    usage.record_success(reservation=reservation, result=ChatResult("ok", cost_usd=Decimal(".03")))
    with web_app.state.database.session() as s:
        assert s.get(DemoControl, "default").enabled is False


def test_demo_json_also_uses_cancelable_bounded_provider_stream(web_app):
    args = account(web_app)
    with web_app.state.database.session() as s:
        create_api_token(
            s,
            account_id=args["account_id"],
            name="JSON demo",
            source="web",
            raw_token="shluz_" + "r" * 43,
        )
    provider = web_app.state.provider_registry.provider_for_model("mock-chat")

    def must_not_block(request):
        raise AssertionError("Unbounded sync demo generation must not be used")

    async def stream(request):
        assert request.max_completion_tokens == 512
        yield StreamChunk(content="bounded")
        yield StreamChunk(prompt_tokens=3, completion_tokens=1, cost_usd=Decimal("0"))
        yield StreamChunk(done=True)

    provider.chat_completion = must_not_block
    provider.chat_stream = stream
    with TestClient(web_app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer shluz_" + "r" * 43},
            json={"model": "mock-chat", "messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "bounded"
