"""Один денежный баланс, журнал начислений и неистекающие финансовые резервы."""

import time
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db.base import Base


class FixedUSD(TypeDecorator):
    """Целые 1e-8 USD в SQLite и PostgreSQL: без потери точности binary float."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = Decimal(str(value)) * 100_000_000
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("USD precision exceeds eight decimal places")
        if abs(value) > 9_000_000_000_000_000_000:
            raise ValueError("USD amount exceeds storage bound")
        return int(value)

    def process_result_value(self, value, dialect):
        return Decimal(value) / 100_000_000 if value is not None else None


class Wallet(Base):
    __tablename__ = "wallets"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    balance_usd: Mapped[Decimal] = mapped_column(FixedUSD(), default=Decimal("0"))
    display_unit: Mapped[str] = mapped_column(String(16), default="usd")


class WalletEntry(Base):
    __tablename__ = "wallet_entries"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount_usd: Mapped[Decimal] = mapped_column(FixedUSD())
    kind: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(200), default="")
    actor: Mapped[str] = mapped_column(String(200), default="gateway")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class WalletHold(Base):
    __tablename__ = "wallet_holds"
    event_id: Mapped[str] = mapped_column(ForeignKey("usage_events.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount_usd: Mapped[Decimal] = mapped_column(FixedUSD())
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    rule_id: Mapped[str] = mapped_column(String(300))


class WalletModelBound(Base):
    __tablename__ = "wallet_model_bounds"
    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    max_cost_usd: Mapped[Decimal] = mapped_column(FixedUSD())
    max_input_chars: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    expires: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()) + 86400)
