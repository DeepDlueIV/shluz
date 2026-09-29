import re
from hashlib import sha256

from sqlalchemy import select

from app.db.models import Account, ApiToken
from app.db.repositories import create_api_token

_ADMIN_AUTH = ("test-admin", "test-admin-password")
_CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')
_TOKEN_PATTERN = re.compile(r"shluz_[A-Za-z0-9_-]{30,}")


def _csrf_token(client) -> str:
    response = client.get("/admin/users", auth=_ADMIN_AUTH)
    assert response.status_code == 200
    match = _CSRF_PATTERN.search(response.text)
    assert match is not None
    return match.group(1)


def test_admin_rejects_user_mutation_without_valid_csrf(client):
    response = client.post(
        "/admin/users",
        auth=_ADMIN_AUTH,
        data={
            "csrf_token": "wrong",
            "display_name": "Новый пользователь",
            "role": "user",
            "status": "active",
        },
    )

    assert response.status_code == 403


def test_admin_can_create_user_visually(client):
    csrf_token = _csrf_token(client)

    response = client.post(
        "/admin/users",
        auth=_ADMIN_AUTH,
        data={
            "csrf_token": csrf_token,
            "display_name": "Пользователь сайта",
            "role": "user",
            "status": "active",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    database = client.app.state.database
    with database.session() as session:
        account = session.scalar(
            select(Account).where(Account.display_name == "Пользователь сайта")
        )
    assert account is not None
    assert account.role == "user"
    assert account.status == "active"


def test_admin_issues_token_once_and_later_shows_only_safe_prefix(client):
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Профессиональный пользователь")
        session.add(account)
        session.flush()
        account_id = account.id

    csrf_token = _csrf_token(client)
    response = client.post(
        f"/admin/users/{account_id}/tokens",
        auth=_ADMIN_AUTH,
        data={
            "csrf_token": csrf_token,
            "name": "Основной Harness",
            "source": "harness",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    match = _TOKEN_PATTERN.search(response.text)
    assert match is not None
    raw_token = match.group(0)

    with database.session() as session:
        stored = session.scalar(select(ApiToken).where(ApiToken.account_id == account_id))
    assert stored is not None
    assert stored.token_hash == sha256(raw_token.encode("utf-8")).hexdigest()
    assert stored.token_prefix == raw_token[:8]
    assert stored.source == "harness"
    assert raw_token not in stored.token_hash

    users_page = client.get("/admin/users", auth=_ADMIN_AUTH)
    assert users_page.status_code == 200
    assert raw_token not in users_page.text
    assert stored.token_prefix in users_page.text
    assert "Основной Harness" in users_page.text
    assert "harness" in users_page.text


def test_admin_can_revoke_personal_token(client):
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Пользователь Telegram")
        session.add(account)
        session.flush()
        token = create_api_token(
            session,
            account_id=account.id,
            name="Telegram",
            source="telegram",
            raw_token="shluz_token_to_revoke",
        )
        token_id = token.id

    csrf_token = _csrf_token(client)
    response = client.post(
        f"/admin/tokens/{token_id}/revoke",
        auth=_ADMIN_AUTH,
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with database.session() as session:
        revoked = session.get(ApiToken, token_id)
    assert revoked is not None
    assert revoked.revoked_at is not None
