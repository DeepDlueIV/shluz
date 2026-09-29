from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import models as _models  # noqa: F401
from app.db.base import Base


class Database:
    """Own the SQLAlchemy engine and transaction boundaries."""

    def __init__(self, settings: Settings) -> None:
        raw_url = settings.database_url.get_secret_value()
        url = make_url(raw_url)
        engine_options: dict[str, object] = {"pool_pre_ping": True}

        if url.get_backend_name() == "sqlite":
            database_path = url.database
            engine_options["connect_args"] = {"check_same_thread": False}
            if database_path in {None, "", ":memory:"}:
                engine_options["poolclass"] = StaticPool
            else:
                Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

        self.engine: Engine = create_engine(raw_url, **engine_options)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def create_schema(self) -> None:
        """Create missing tables for local development and tests."""

        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Open one transaction and commit or roll it back as a unit."""

        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
