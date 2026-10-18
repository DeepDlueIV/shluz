from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    service_name: str = "shluz"
    service_version: str = "0.1.0"
    build_sha: str = Field(default="", pattern=r"^(?:[a-f0-9]{40})?$")
    environment: str = "development"
    bootstrap_api_token: SecretStr = SecretStr("dev-token")
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("change-me")
    database_url: SecretStr = SecretStr("sqlite+pysqlite:///./data/shluz.db")

    telegram_connector_file: Path | None = None
    telegram_pending_limit: int = Field(default=500, ge=1, le=10000)

    active_provider: Literal["mock", "venice"] = "mock"
    venice_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("SHLUZ_VENICE_API_KEY", "VENICE_API_KEY", "venice_api_key"),
    )
    venice_base_url: str = "https://api.venice.ai/api/v1"
    venice_timeout_seconds: float = 30.0
    venice_analytics_lookback: str = "7d"

    stream_timeout_seconds: float = Field(default=120, ge=5, le=600)
    stream_idle_timeout_seconds: float = Field(default=30, ge=1, le=120)
    otlp_endpoint: str | None = None

    credit_unit_usd: Decimal = Decimal("0.001")
    reservation_ttl_seconds: int = 900

    model_config = SettingsConfigDict(
        env_prefix="SHLUZ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings object for the process."""

    return Settings()
