"""Настройки клиента и закрытый список персональных ключей. Секреты не попадают в БД."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):
    pass


class BotSettings(BaseSettings):
    bot_token: SecretStr = SecretStr("")
    gateway_url: str = "http://shluz:8000"
    access_file: Path = Path("secrets/telegram-access.json")
    database_path: Path = Path("data/telegram.sqlite")
    default_model: str = "mock-chat"
    request_timeout: float = Field(default=120, ge=5, le=600)
    context_char_limit: int = Field(default=24000, ge=1000, le=200000)
    max_input_chars: int = Field(default=8000, ge=100, le=20000)
    max_response_chars: int = Field(default=100000, ge=4000, le=1000000)
    max_parallel_requests: int = Field(default=4, ge=1, le=32)
    cooldown_seconds: float = Field(default=2, ge=0, le=60)
    history_retention_days: int = Field(default=30, ge=1, le=365)
    max_dialogs: int = Field(default=20, ge=2, le=100)
    max_saved_turns: int = Field(default=200, ge=10, le=1000)
    max_text_parts: int = Field(default=6, ge=1, le=15)
    support_contact: str = "Обратитесь к администратору тестирования."
    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_",
        env_file=".env.telegram",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("gateway_url")
    @classmethod
    def validate_gateway_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
        ):
            raise ValueError("Укажите HTTP(S) адрес шлюза без секретов и параметров")
        return value.rstrip("/")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("Повторяющийся Telegram ID в списке доступа")
        result[key] = value
    return result


class AccessRegistry:
    """Список перечитывается на каждом событии: удалённый доступ закрывается сразу."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._load()

    def _load(self) -> dict[int, SecretStr]:
        try:
            if self.path.stat().st_size > 262144:
                raise ConfigurationError("Слишком большой список доступа")
            data = json.loads(
                self.path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
            )
            if not isinstance(data, dict):
                raise ConfigurationError("Список доступа должен быть JSON-объектом")
            result: dict[int, SecretStr] = {}
            seen = set()
            for key, token in data.items():
                if (
                    not key.isascii()
                    or not key.isdigit()
                    or str(int(key)) != key
                    or not 0 < int(key) <= 2**63 - 1
                ):
                    raise ConfigurationError("Telegram ID должен быть положительным целым числом")
                if (
                    not isinstance(token, str)
                    or not token.startswith("shluz_")
                    or len(token) < 25
                    or any(c.isspace() for c in token)
                ):
                    raise ConfigurationError("Нужен отдельный персональный токен Shluz")
                if token in seen:
                    raise ConfigurationError("Разным Telegram ID нельзя выдавать общий токен")
                seen.add(token)
                result[int(key)] = SecretStr(token)
            return result
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise ConfigurationError("Не удалось безопасно прочитать список доступа") from exc

    def token_for(self, user_id: int) -> SecretStr | None:
        try:
            return self._load().get(user_id)
        except ConfigurationError:
            # При ошибке файла старые разрешения не кешируются: доступ закрыт.
            return None
