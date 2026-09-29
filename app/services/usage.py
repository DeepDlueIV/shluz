from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.db.models import UsageEvent
from app.db.session import SessionFactory
from app.providers.base import ChatResult


class UsageStorageError(RuntimeError):
    """Usage accounting storage is unavailable or inconsistent."""


class UsageService:
    """Persist the lifecycle and real provider cost of every model call."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def begin(
        self,
        *,
        provider: str,
        model: str,
        channel: str = "api",
        account_id: UUID | None = None,
    ) -> UUID:
        try:
            with self._session_factory() as session:
                event = UsageEvent(
                    account_id=account_id,
                    channel=channel,
                    provider=provider,
                    model=model,
                    status="pending",
                )
                session.add(event)
                session.commit()
                return event.id
        except (SQLAlchemyError, OSError) as exc:
            raise UsageStorageError("Unable to begin usage accounting") from exc

    def succeed(self, event_id: UUID, result: ChatResult) -> None:
        self._update(
            event_id,
            status="succeeded",
            provider_request_id=result.request_id,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            error_code=None,
        )

    def fail(self, event_id: UUID, *, error_code: str) -> None:
        self._update(event_id, status="failed", error_code=error_code)

    def _update(self, event_id: UUID, **values: object) -> None:
        try:
            with self._session_factory() as session:
                event = session.get(UsageEvent, event_id)
                if event is None:
                    raise UsageStorageError(f"Usage event not found: {event_id}")
                for name, value in values.items():
                    setattr(event, name, value)
                session.commit()
        except UsageStorageError:
            raise
        except (SQLAlchemyError, OSError) as exc:
            raise UsageStorageError("Unable to update usage accounting") from exc
