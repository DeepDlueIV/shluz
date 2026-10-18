from tests.product.test_demo import demo_client as imported_demo_client
from tests.product.test_demo import start

demo_client = imported_demo_client


def test_stream_uses_lifetime_demo_and_does_not_reset(demo_client):
    c = demo_client
    token = start(c).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    request = {
        "model": "mock-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    for _ in range(3):
        r = c.post("/v1/chat/completions", headers=headers, json=request)
        assert r.status_code == 200 and r.text.endswith("data: [DONE]\n\n")
    assert c.post("/v1/chat/completions", headers=headers, json=request).status_code == 429
    assert c.get("/v1/me", headers=headers).json()["allowance"]["requests"]["remaining"] == 0


def test_stream_does_not_bypass_model_policy(demo_client):
    from app.db.models import ModelPolicy

    c = demo_client
    token = start(c).json()["token"]
    with c.app.state.database.session() as s:
        s.add(ModelPolicy(id="channel:telegram", models_json="[]"))
    r = c.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + token},
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "source": "harness",
        },
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_allowed"


def test_uncertain_stream_holds_credit_reserve_and_one_request(demo_client):
    from decimal import Decimal

    from sqlalchemy import select

    from app.db.models import Plan, Subscription, UsageEvent
    from app.providers.base import ProviderError, StreamChunk

    c = demo_client
    first = start(c).json()
    with c.app.state.database.session() as session:
        plan = Plan(
            code="stream-paid",
            name="Test paid",
            monthly_request_limit=1,
            monthly_credit_limit=10,
            request_credit_reserve=5,
            markup_percent=Decimal("0"),
        )
        session.add(plan)
        session.flush()
        session.add(Subscription(account_id=first["account_id"], plan_id=plan.id))
    provider = c.app.state.provider_registry.provider_for_model("mock-chat")
    calls = []

    async def broken(request):
        calls.append(request)
        yield StreamChunk(content="only a prefix", prompt_tokens=100)
        raise ProviderError("synthetic interruption")

    provider.chat_stream = broken
    headers = {"Authorization": "Bearer " + first["token"]}
    payload = {
        "model": "mock-chat",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    r = c.post("/v1/chat/completions", headers=headers, json=payload)
    assert "stream_interrupted" in r.text and "[DONE]" not in r.text
    assert len(calls) == 1
    account = c.get("/v1/me", headers=headers).json()
    assert account["allowance"]["requests"]["remaining"] == 0
    assert account["allowance"]["credits"]["pending"] == 5
    usage = c.get("/v1/me/usage", headers=headers).json()
    assert usage["summary"]["usage_unknown"] == 1
    assert usage["summary"]["prompt_tokens"] == 0  # known total only, not guessed
    assert usage["data"][0]["prompt_tokens"] is None
    assert usage["data"][0]["charged_usd"] is None
    with c.app.state.database.session() as session:
        rows = list(session.scalars(select(UsageEvent)))
        assert len(rows) == 1 and rows[0].status == "interrupted"
    assert c.post("/v1/chat/completions", headers=headers, json=payload).status_code == 429
    assert len(calls) == 1
