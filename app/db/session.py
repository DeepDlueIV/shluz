from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base

SessionFactory = sessionmaker[Session]


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
    """Create missing tables for the current development milestone."""

    from app.db import models as _models  # noqa: F401

    Base.metadata.create_all(engine)
