from sqlalchemy import select

from app.db.database import Database
from app.db.models import UsageEvent
from app.db.repositories import ensure_bootstrap_account
from app.providers.base import ChatResult


class UsageService:
    """Persist provider cost and token usage without storing conversation text."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def record_success(
        self,
        *,
        account_id: str,
        source: str,
        provider: str,
        model: str,
        result: ChatResult,
    ) -> UsageEvent:
        with self._database.session() as session:
            if result.request_id:
                existing = session.scalar(
                    select(UsageEvent).where(
                        UsageEvent.provider == provider,
                        UsageEvent.provider_request_id == result.request_id,
                    )
                )
                if existing is not None:
                    return existing

            if account_id == "bootstrap":
                ensure_bootstrap_account(session)

            event = UsageEvent(
                account_id=account_id,
                source=source,
                provider=provider,
                model=model,
                provider_request_id=result.request_id,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd,
                cost_diem=result.cost_diem,
                status="success",
            )
            session.add(event)
            session.flush()
            return event
