import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default="user", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
    )


class Identity(Base):
    __tablename__ = "identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_identities_provider_subject"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    monthly_price_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    monthly_cost_limit_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8),
        nullable=True,
    )
    monthly_credit_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_request_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_credit_reserve: Mapped[int] = mapped_column(Integer, default=0)
    markup_percent: Mapped[Decimal] = mapped_column(
        Numeric(7, 2),
        default=Decimal("0"),
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_account_status", "account_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="active")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32), default="harness", index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_request_id",
            name="uq_usage_provider_request",
        ),
        Index("ix_usage_events_account_created", "account_id", "created_at"),
        Index("ix_usage_events_provider_model", "provider", "model"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        index=True,
    )
    quota_kind: Mapped[str] = mapped_column(String(16), default="monthly", server_default="monthly")
    cost_known: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), default="bootstrap", index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(200), index=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=Decimal("0"),
    )
    cost_diem: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=Decimal("0"),
    )
    billed_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        default=Decimal("0"),
    )
    pricing_markup_percent: Mapped[Decimal] = mapped_column(
        Numeric(7, 2),
        default=Decimal("0"),
    )
    internal_credits: Mapped[int] = mapped_column(Integer, default=0)
    reserved_credits: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        index=True,
    )


class TelegramAdmission(Base):
    """Разрешение на канал, а не отдельный финансовый аккаунт."""

    __tablename__ = "telegram_admissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    telegram_id: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # Отсутствие/удаление старого ключа должно закрывать доступ, а не снимать проверку.
    legacy_token_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class TelegramInvite(Base):
    __tablename__ = "telegram_invites"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_telegram_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class TelegramIntegration(Base):
    __tablename__ = "telegram_integration"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    username: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class ProductPolicy(Base):
    """Настройки демо и допуска. Не содержат ключей поставщика."""

    __tablename__ = "product_policy"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    mode: Mapped[str] = mapped_column(String(16), default="demo")
    demo_requests: Mapped[int] = mapped_column(Integer, default=3)
    demo_models_json: Mapped[str] = mapped_column(String(22000), default='["mock-chat"]')
    demo_max_input_chars: Mapped[int] = mapped_column(Integer, default=24000)
    demo_max_output_tokens: Mapped[int] = mapped_column(Integer, default=512)
    demo_daily_requests: Mapped[int] = mapped_column(Integer, default=100)
    requests_per_minute: Mapped[int] = mapped_column(Integer, default=30)


class DemoGrant(Base):
    """Один пожизненный пакет на аккаунт; месяц и новый канал его не обновляют."""

    __tablename__ = "demo_grants"
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), primary_key=True
    )
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class ModelPolicy(Base):
    """Пересечение channel:* и plan:*; отсутствие plan:* наследует канал."""

    __tablename__ = "model_policies"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    models_json: Mapped[str] = mapped_column(String(22000), default="[]")
