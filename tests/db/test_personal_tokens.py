from datetime import UTC, datetime

from pydantic import SecretStr

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, ApiToken
from app.db.repositories import (
    authenticate_api_token,
    create_api_token,
    generate_api_token,
    revoke_api_token,
)


def _database(tmp_path) -> Database:
    database = Database(
        Settings(database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'tokens.db'}"))
    )
    database.create_schema()
    return database


def test_generated_token_has_public_prefix_and_is_unique():
    first = generate_api_token()
    second = generate_api_token()

    assert first.startswith("shluz_")
    assert second.startswith("shluz_")
    assert first != second
    assert len(first) >= 40


def test_personal_token_authenticates_active_account_and_tracks_source(tmp_path):
    database = _database(tmp_path)
    raw_token = "shluz_personal_token_for_harness"

    with database.session() as session:
        account = Account(display_name="Тестовый пользователь")
        session.add(account)
        session.flush()
        stored = create_api_token(
            session,
            account_id=account.id,
            name="Рабочий Harness",
            source="harness",
            raw_token=raw_token,
        )
        account_id = account.id
        token_id = stored.id

    with database.session() as session:
        authenticated = authenticate_api_token(session, raw_token)

    assert authenticated is not None
    assert authenticated.account_id == account_id
    assert authenticated.token_id == token_id
    assert authenticated.source == "harness"

    with database.session() as session:
        refreshed = session.get(ApiToken, token_id)

    assert refreshed is not None
    assert refreshed.last_used_at is not None


def test_revoked_token_and_inactive_account_do_not_authenticate(tmp_path):
    database = _database(tmp_path)

    with database.session() as session:
        revoked_account = Account(display_name="Отозванный")
        inactive_account = Account(display_name="Заблокированный", status="disabled")
        session.add_all([revoked_account, inactive_account])
        session.flush()
        revoked = create_api_token(
            session,
            account_id=revoked_account.id,
            name="Отозванный токен",
            source="site",
            raw_token="shluz_revoked_token",
        )
        create_api_token(
            session,
            account_id=inactive_account.id,
            name="Токен заблокированного аккаунта",
            source="telegram",
            raw_token="shluz_inactive_account_token",
        )
        revoked_id = revoked.id

    with database.session() as session:
        assert revoke_api_token(session, revoked_id, revoked_at=datetime.now(UTC)) is True

    with database.session() as session:
        assert authenticate_api_token(session, "shluz_revoked_token") is None
        assert authenticate_api_token(session, "shluz_inactive_account_token") is None
