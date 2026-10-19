"""Данные допуска: без содержимого сообщений, сырых cookies, IP и OTP."""

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DemoLimit(Base):
    __tablename__ = "demo_limits"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    requests_limit: Mapped[int] = mapped_column(Integer)


class DemoControl(Base):
    __tablename__ = "demo_control"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    concurrent: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=45)
    idle_seconds: Mapped[int] = mapped_column(Integer, default=15)
    network_daily: Mapped[int] = mapped_column(Integer, default=3)
    sessions_hourly: Mapped[int] = mapped_column(Integer, default=20)
    sessions_global_daily: Mapped[int] = mapped_column(Integer, default=1000)
    captcha_minute: Mapped[int] = mapped_column(Integer, default=10)
    otp_email_daily: Mapped[int] = mapped_column(Integer, default=5)
    otp_network_daily: Mapped[int] = mapped_column(Integer, default=20)
    otp_global_daily: Mapped[int] = mapped_column(Integer, default=100)
    otp_verify_minute: Mapped[int] = mapped_column(Integer, default=20)
    http_network_minute: Mapped[int] = mapped_column(Integer, default=120)
    http_global_minute: Mapped[int] = mapped_column(Integer, default=2000)
    captcha_global_minute: Mapped[int] = mapped_column(Integer, default=300)
    otp_verify_global_minute: Mapped[int] = mapped_column(Integer, default=500)


class DemoModelBound(Base):
    __tablename__ = "demo_model_bounds"
    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    # Проверенная верхняя стоимость ПОЛНОГО вызова при заданных пределах.
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    max_input_chars: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class DemoHold(Base):
    __tablename__ = "demo_holds"
    event_id: Mapped[str] = mapped_column(ForeignKey("usage_events.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    day: Mapped[int] = mapped_column(Integer, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires: Mapped[int] = mapped_column(Integer, index=True)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=True)


class WebSession(Base):
    __tablename__ = "web_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[int] = mapped_column(Integer)
    expires: Mapped[int] = mapped_column(Integer, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class WebBucket(Base):
    __tablename__ = "web_buckets"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires: Mapped[int] = mapped_column(Integer, index=True)


class CaptchaUse(Base):
    __tablename__ = "web_captcha_uses"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires: Mapped[int] = mapped_column(Integer, index=True)


class OtpChallenge(Base):
    __tablename__ = "web_otp_challenges"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(320))
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires: Mapped[int] = mapped_column(Integer, index=True)


class WebRequest(Base):
    __tablename__ = "web_requests"
    # session digest + request UUID: одинаковый ID другой сессии не раскрывает ответ.
    id: Mapped[str] = mapped_column(String(110), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("usage_events.id"))
    expires: Mapped[int] = mapped_column(Integer, index=True)


class TelegramLoginChallenge(Base):
    __tablename__ = "web_telegram_login_challenges"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    nonce_hash: Mapped[str] = mapped_column(String(64))
    created: Mapped[int] = mapped_column(Integer)
    expires: Mapped[int] = mapped_column(Integer, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
