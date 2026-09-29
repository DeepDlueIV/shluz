import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base

ACTION_LABELS = {
    "user.created": "Пользователь создан",
    "user.archived": "Пользователь перемещён в архив",
    "user.restored": "Пользователь восстановлен",
    "subscription.assigned": "Тариф пользователя изменён",
    "token.created": "Личный доступ выдан",
    "token.revoked": "Личный доступ отозван",
    "plan.created": "Тариф создан",
    "plan.updated": "Тариф изменён",
    "plan.archived": "Тариф перемещён в архив",
    "plan.restored": "Тариф восстановлен",
}
PLAN_FIELDS = (
    "code", "name", "monthly_price_usd", "monthly_cost_limit_usd",
    "monthly_credit_limit", "monthly_request_limit", "request_credit_reserve",
    "markup_percent", "active",
)
_SAFE_FIELDS = set(PLAN_FIELDS) | {
    "before", "after", "role", "status", "source", "account_id", "plan_id",
    "revoked_tokens", "cancelled_subscriptions",
}


class AdminEvent(Base):
    """Журнал изменений владельца; не содержит токенов или текстов переписки."""

    __tablename__ = "admin_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True,
    )
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


def safe_details(values: dict[str, Any]) -> dict[str, Any]:
    """Разрешаем только известные поля, а не произвольный дамп формы или объекта."""
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key not in _SAFE_FIELDS:
            continue
        if isinstance(value, dict):
            result[key] = safe_details(value)
        elif isinstance(value, Decimal):
            result[key] = str(value)
        elif value is None or isinstance(value, (str, int, bool)):
            result[key] = value
    return result


def plan_snapshot(plan) -> dict[str, Any]:
    return safe_details({field: getattr(plan, field) for field in PLAN_FIELDS})


def record_admin_event(
    session: Session, *, actor: str, action: str, target_type: str, target_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Вызывается внутри транзакции изменения: запись и действие сохраняются вместе."""
    if action not in ACTION_LABELS:
        raise ValueError("Unknown administrative action")
    session.add(AdminEvent(
        actor=actor[:200], action=action, target_type=target_type, target_id=target_id,
        details=safe_details(details or {}),
    ))
