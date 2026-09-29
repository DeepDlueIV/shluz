from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base

SessionFactory = sessionmaker[Session]
REQUIRED_TABLES = frozenset(
    {
        "accounts",
        "account_balances",
        "identities",
        "plans",
        "subscriptions",
        "api_tokens",
        "usage_events",
        "audit_events",
    }
)


def create_database_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a portable SQLAlchemy engine for SQLite or PostgreSQL."""

    url = make_url(database_url)
    engine_options: dict[str, object] = {
        "echo": echo,
        "pool_pre_ping": True,
    }

    if url.get_backend_name() == "sqlite":
        engine_options["connect_args"] = {"check_same_thread": False}
        if url.database == ":memory:":
            engine_options["poolclass"] = StaticPool
        elif url.database:
            Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, **engine_options)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> SessionFactory:
    """Create independent sessions for concurrent requests."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


def initialize_database(engine: Engine) -> None:
    """Create local SQLite tables or validate a managed PostgreSQL schema."""

    from app.db import models as _models  # noqa: F401

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
        return

    existing_tables = set(inspect(engine).get_table_names(schema="public"))
    missing_tables = sorted(REQUIRED_TABLES - existing_tables)
    if missing_tables:
        missing = ", ".join(missing_tables)
        raise RuntimeError(
            "Database migration is required before startup; missing tables: " + missing
        )
