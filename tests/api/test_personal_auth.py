from datetime import UTC, datetime

from sqlalchemy import desc, select

from app.db.models import Account, ApiToken, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token


def _issue_token(client, *, source: str = "harness") -> tuple[str, str, str]:
    raw_token = f"shluz_test_{source}_personal_token"
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name=f"Пользователь {source}")
        plan = Plan(code=f"plan-{source}", name=f"Тариф {source}")
        session.add_all([account, plan])
        session.flush()
        session.add(Subscription(account_id=account.id, plan_id=plan.id))
        token = create_api_token(
            session,
            account_id=account.id,
            name=f"Доступ {source}",
            source=source,
            raw_token=raw_token,
        )
        return raw_token, account.id, token.id


def test_personal_token_allows_chat_and_attributes_usage(client):
    raw_token, account_id, token_id = _issue_token(client, source="harness")

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={
            "model": "mock-chat",
            "messages": [{"role": "user", "content": "Привет"}],
        },
    )

    assert response.status_code == 200

    database = client.app.state.database
    with database.session() as session:
        event = session.scalar(select(UsageEvent).order_by(desc(UsageEvent.created_at)))
        token = session.get(ApiToken, token_id)

    assert event is not None
    assert event.account_id == account_id
    assert event.source == "harness"
    assert token is not None
    assert token.last_used_at is not None


def test_invalid_personal_token_is_rejected(client):
    response = client.get(
        "/v1/models",
        headers={"Authorization": "Bearer shluz_unknown_personal_token"},
    )

    assert response.status_code == 401


def test_revoked_personal_token_is_rejected(client):
    raw_token, _, token_id = _issue_token(client, source="site")
    database = client.app.state.database
    with database.session() as session:
        token = session.get(ApiToken, token_id)
        assert token is not None
        token.revoked_at = datetime.now(UTC)

    response = client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 401


def test_inactive_account_token_is_rejected(client):
    raw_token, account_id, _ = _issue_token(client, source="telegram")
    database = client.app.state.database
    with database.session() as session:
        account = session.get(Account, account_id)
        assert account is not None
        account.status = "disabled"

    response = client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {raw_token}"},
    )

    assert response.status_code == 401
