from hashlib import sha256

from app.db.database import Database
from app.db.models import Account, ApiToken
from app.db.repositories import create_api_token, ensure_bootstrap_account
from pydantic import SecretStr
from sqlalchemy import func, inspect, select

from app.config import Settings


def _database(tmp_path) -> Database:
    settings = Settings(
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'shluz-test.db'}")
    )
    database = Database(settings)
    database.create_schema()
    return database


def test_database_creates_expected_tables(tmp_path):
    database = _database(tmp_path)

    table_names = set(inspect(database.engine).get_table_names())

    assert {
        "accounts",
        "identities",
        "plans",
        "subscriptions",
        "api_tokens",
        "usage_events",
    } <= table_names


def test_bootstrap_account_is_created_once(tmp_path):
    database = _database(tmp_path)

    with database.session() as session:
        first = ensure_bootstrap_account(session)
        second = ensure_bootstrap_account(session)
        account_count = session.scalar(select(func.count()).select_from(Account))

    assert first.id == "bootstrap"
    assert second.id == "bootstrap"
    assert account_count == 1
    assert first.role == "service"
    assert first.status == "active"


def test_api_token_stores_only_hash_and_safe_prefix(tmp_path):
    database = _database(tmp_path)
    raw_token = "shluz_test_super_secret_token_value"

    with database.session() as session:
        account = ensure_bootstrap_account(session)
        token = create_api_token(
            session,
            account_id=account.id,
            name="Тестовый токен",
            raw_token=raw_token,
        )
        token_id = token.id

    with database.session() as session:
        stored = session.get(ApiToken, token_id)

    assert stored is not None
    assert stored.token_hash == sha256(raw_token.encode("utf-8")).hexdigest()
    assert stored.token_prefix == "shluz_te"
    assert raw_token not in stored.token_hash
    assert not hasattr(stored, "raw_token")
