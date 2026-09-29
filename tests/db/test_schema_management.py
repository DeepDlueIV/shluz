from types import SimpleNamespace

import pytest

import app.db.session as db_session
from app.db.session import REQUIRED_TABLES, initialize_database


def _postgres_engine():
    return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))


def test_managed_postgres_schema_is_validated_without_auto_creation(monkeypatch):
    created_with = []
    monkeypatch.setattr(
        db_session.Base.metadata,
        "create_all",
        lambda engine: created_with.append(engine),
    )
    monkeypatch.setattr(
        db_session,
        "inspect",
        lambda engine: SimpleNamespace(
            get_table_names=lambda schema=None: list(REQUIRED_TABLES)
        ),
    )

    initialize_database(_postgres_engine())

    assert created_with == []


def test_managed_postgres_requires_migration_when_tables_are_missing(monkeypatch):
    monkeypatch.setattr(
        db_session,
        "inspect",
        lambda engine: SimpleNamespace(
            get_table_names=lambda schema=None: ["accounts", "plans"]
        ),
    )

    with pytest.raises(RuntimeError, match="migration"):
        initialize_database(_postgres_engine())
