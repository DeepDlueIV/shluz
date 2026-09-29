from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    service_name: str = "shluz"
    service_version: str = "0.1.0"
    environment: str = "development"
    bootstrap_api_token: SecretStr = SecretStr("dev-token")
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("change-me")
    database_url: SecretStr = SecretStr("sqlite+pysqlite:///./data/shluz.db")

    active_provider: Literal["mock", "venice"] = "mock"
    venice_api_key: SecretStr | None = None
    venice_base_url: str = "https://api.venice.ai/api/v1"
    venice_timeout_seconds: float = 30.0
    venice_analytics_lookback: str = "7d"

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
