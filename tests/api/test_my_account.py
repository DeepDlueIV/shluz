from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Account, Identity, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token


def provision(client, *, source="telegram", token="shluz_" + "a" * 43, with_plan=True):
    with client.app.state.database.session() as session:
        account = Account(display_name="Закрытая бета")
        session.add(account)
        session.flush()
        if with_plan:
            plan = Plan(
                code=token[-8:],
                name="Тестовый",
                monthly_request_limit=10,
                monthly_credit_limit=100,
                request_credit_reserve=5,
            )
            session.add(plan)
            session.flush()
            session.add(Subscription(account_id=account.id, plan_id=plan.id))
        create_api_token(
            session, account_id=account.id, name="Beta", raw_token=token, source=source
        )
        return account.id, {"Authorization": f"Bearer {token}"}


def test_me_requires_personal_token(client, authorized_headers):
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers=authorized_headers).status_code == 403


def test_me_uses_authoritative_limits_and_hides_internal_costs(client):
    account_id, headers = provision(client)
    now = datetime.now(UTC)
    with client.app.state.database.session() as session:
        session.add_all(
            [
                UsageEvent(
                    account_id=account_id,
                    source="telegram",
                    provider="mock",
                    model="mock-chat",
                    status="success",
                    internal_credits=7,
                ),
                UsageEvent(
                    account_id=account_id,
                    source="harness",
                    provider="mock",
                    model="mock-chat",
                    status="pending",
                    reserved_credits=5,
                    reservation_expires_at=now + timedelta(minutes=2),
                ),
                UsageEvent(
                    account_id=account_id,
                    source="telegram",
                    provider="mock",
                    model="mock-chat",
                    status="failed",
                    internal_credits=99,
                ),
                UsageEvent(
                    account_id=account_id,
                    source="telegram",
                    provider="mock",
                    model="mock-chat",
                    status="pending",
                    reserved_credits=50,
                    reservation_expires_at=now - timedelta(minutes=2),
                ),
            ]
        )
    result = client.get("/v1/me", headers=headers)
    assert result.status_code == 200
    data = result.json()
    assert data["account"]["id"] == account_id
    assert data["channel"] == "telegram"
    assert data["plan"]["name"] == "Тестовый"
    assert data["allowance"]["scope"] == "account"
    assert data["allowance"]["requests"] == {"limit": 10, "used": 1, "pending": 1, "remaining": 8}
    assert data["allowance"]["credits"] == {"limit": 100, "used": 7, "pending": 5, "remaining": 88}
    assert "cost_usd" not in result.text
    assert "token_hash" not in result.text
    assert "markup" not in result.text
    assert result.headers["cache-control"] == "no-store"


def test_me_without_subscription_is_not_free_access(client):
    _, headers = provision(client, with_plan=False)
    result = client.get("/v1/me", headers=headers)
    assert result.status_code == 200
    assert result.json()["plan"] is None
    assert (
        client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "mock-chat", "messages": [{"role": "user", "content": "hi"}]},
        ).status_code
        == 402
    )


def test_bind_telegram_identity_is_idempotent_and_conflicts_are_safe(client):
    account_id, headers = provision(client)
    payload = {"telegram_id": 123456789, "username": "beta"}
    for _ in range(2):
        result = client.post("/v1/me/telegram", headers=headers, json=payload)
        assert result.status_code == 200
    with client.app.state.database.session() as session:
        identities = list(session.scalars(select(Identity)))
        assert len(identities) == 1
        assert identities[0].account_id == account_id
        assert identities[0].subject == "123456789"
    _, other = provision(client, token="shluz_" + "b" * 43)
    assert client.post("/v1/me/telegram", headers=other, json=payload).status_code == 409


def test_bind_requires_telegram_channel_and_positive_integer(client, authorized_headers):
    _, headers = provision(client, source="harness")
    assert (
        client.post("/v1/me/telegram", headers=headers, json={"telegram_id": 1}).status_code == 403
    )
    assert (
        client.post(
            "/v1/me/telegram", headers=authorized_headers, json={"telegram_id": 1}
        ).status_code
        == 403
    )
    _, headers = provision(client, token="shluz_" + "b" * 43)
    for invalid in (0, -1, True, "1", 1.5):
        assert (
            client.post(
                "/v1/me/telegram", headers=headers, json={"telegram_id": invalid}
            ).status_code
            == 422
        )
