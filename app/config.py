from functools import lru_cache

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
