import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def test_single_instance_lock_and_release(tmp_path):
    from telegram_bot.runtime import InstanceLock

    path = tmp_path / "instance.lock"
    with InstanceLock(path), pytest.raises(RuntimeError), InstanceLock(path):
        pass
    with InstanceLock(path):
        pass


def test_healthcheck_fresh_stale_missing_and_corrupt(tmp_path):
    from telegram_bot.healthcheck import is_healthy, write_heartbeat

    path = tmp_path / "heartbeat.json"
    assert not is_healthy(path)
    write_heartbeat(path)
    assert is_healthy(path)
    path.write_text(json.dumps({"pid": os.getpid(), "updated_at": time.time() - 200}))
    assert not is_healthy(path)
    path.write_text("invalid")
    assert not is_healthy(path)


def test_log_filter_never_exposes_api_or_bot_tokens():
    from telegram_bot.runtime import SecretRedaction

    token = "shluz_" + "x" * 43
    bot_token = "123456789:" + "A" * 35
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "request %s https://api.telegram.org/bot%s/sendMessage",
        (token, bot_token),
        None,
    )
    SecretRedaction().filter(record)
    assert token not in record.getMessage()
    assert bot_token not in record.getMessage()


def test_check_command_is_offline_and_does_not_leak_secret(tmp_path):
    from telegram_bot import runtime  # noqa: F401

    path = tmp_path / "access.json"
    path.write_text(json.dumps({"100": "shluz_" + "a" * 43}))
    token = "123456789:" + "A" * 35
    env = {
        **os.environ,
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_ACCESS_FILE": str(path),
        "TELEGRAM_DATABASE_PATH": str(tmp_path / "db.sqlite"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "telegram_bot", "--check"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert token not in result.stdout + result.stderr
    env["TELEGRAM_BOT_TOKEN"] = "invalid-secret-that-must-not-be-printed"
    result = subprocess.run(
        [sys.executable, "-m", "telegram_bot", "--check"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert env["TELEGRAM_BOT_TOKEN"] not in result.stdout + result.stderr


def test_runtime_recovers_jobs_registers_commands_and_cleans_up(tmp_path, monkeypatch):
    from aiogram import Dispatcher

    from telegram_bot.config import BotSettings
    from telegram_bot.healthcheck import heartbeat_path, is_healthy
    from telegram_bot.runtime import run
    from telegram_bot.storage import Store

    path = tmp_path / "access.json"
    path.write_text(json.dumps({"100": "shluz_" + "a" * 43}))
    settings = BotSettings(
        bot_token="123456789:" + "A" * 35,
        access_file=path,
        database_path=tmp_path / "db.sqlite",
        _env_file=None,
    )
    store = Store(settings.database_path)
    store.ensure_user(100, "A")
    turn = store.begin_turn(100, 1, "interrupted", "mock-chat")
    bot = AsyncMock()
    bot.get_webhook_info.return_value.url = ""
    bot.session.close = AsyncMock()

    async def polling(self, passed_bot, **kwargs):
        assert passed_bot is bot
        assert kwargs["allowed_updates"] == ["message", "callback_query"]
        assert kwargs["tasks_concurrency_limit"] > settings.max_parallel_requests
        await asyncio.sleep(0)
        assert is_healthy(heartbeat_path(settings.database_path))
        assert store.turn_status(turn) == "unknown"

    monkeypatch.setattr(Dispatcher, "start_polling", polling)
    asyncio.run(run(settings, bot=bot))
    bot.set_my_commands.assert_awaited()
    bot.session.close.assert_awaited_once()
    assert not is_healthy(heartbeat_path(settings.database_path))


def test_existing_webhook_is_not_deleted_automatically(tmp_path):
    from telegram_bot.config import BotSettings, ConfigurationError
    from telegram_bot.runtime import run

    path = tmp_path / "access.json"
    path.write_text("{}")
    settings = BotSettings(
        bot_token="123456789:" + "A" * 35,
        access_file=path,
        database_path=tmp_path / "db.sqlite",
        _env_file=None,
    )
    bot = AsyncMock()
    bot.get_webhook_info.return_value.url = "https://existing.example/hook"
    bot.session.close = AsyncMock()
    with pytest.raises(ConfigurationError):
        asyncio.run(run(settings, bot=bot))
    bot.delete_webhook.assert_not_awaited()
    bot.session.close.assert_awaited_once()


def test_compose_keeps_bot_secrets_and_storage_separate():
    import yaml

    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "compose.yaml").read_text())
    bot = compose["services"]["telegram_bot"]
    assert bot["profiles"] == ["telegram"]
    assert bot["env_file"] == [{"path": ".env.telegram", "required": False}]
    assert bot["depends_on"]["shluz"]["condition"] == "service_healthy"
    assert "telegram_bot.healthcheck" in str(bot["healthcheck"])
    assert "telegram_data" in str(bot["volumes"])
    assert "COPY telegram_bot ./telegram_bot" in (root / "Dockerfile").read_text()
    assert "secrets" in (root / ".dockerignore").read_text()


def test_failed_second_start_does_not_remove_live_process_heartbeat(tmp_path):
    from telegram_bot.config import BotSettings
    from telegram_bot.healthcheck import heartbeat_path, is_healthy, write_heartbeat
    from telegram_bot.runtime import InstanceLock, run

    access = tmp_path / "access.json"
    access.write_text("{}")
    settings = BotSettings(access_file=access, database_path=tmp_path / "db.sqlite", _env_file=None)
    heartbeat = heartbeat_path(settings.database_path)
    bot = AsyncMock()
    bot.session.close = AsyncMock()
    with InstanceLock(settings.database_path.with_suffix(".lock")):
        write_heartbeat(heartbeat)
        with pytest.raises(RuntimeError):
            asyncio.run(run(settings, bot=bot))
        assert is_healthy(heartbeat)
    bot.session.close.assert_awaited_once()


def test_instance_lock_is_held_until_controller_finishes_shutdown(tmp_path, monkeypatch):
    from aiogram import Dispatcher

    from telegram_bot.config import BotSettings
    from telegram_bot.controller import Controller
    from telegram_bot.runtime import InstanceLock, run

    access = tmp_path / "access.json"
    access.write_text("{}")
    settings = BotSettings(access_file=access, database_path=tmp_path / "db.sqlite", _env_file=None)
    bot = AsyncMock()
    bot.get_webhook_info.return_value.url = ""
    bot.session.close = AsyncMock()
    real_close = Controller.close

    async def close(self):
        with (
            pytest.raises(RuntimeError),
            InstanceLock(settings.database_path.with_suffix(".lock")),
        ):
            pass
        await real_close(self)

    monkeypatch.setattr(Controller, "close", close)
    monkeypatch.setattr(Dispatcher, "start_polling", AsyncMock())
    asyncio.run(run(settings, bot=bot))


def test_automatic_mode_accepts_missing_legacy_json(tmp_path):
    from telegram_bot.config import AccessRegistry
    registry = AccessRegistry(tmp_path / "no-file.json", optional=True)
    assert registry.token_for(123) is None


def test_session_secret_redaction_covers_full_signed_value():
    from telegram_bot.runtime import SecretRedaction
    token = "shluz_tg." + "payload" * 20 + "." + "a" * 64
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, token, (), None)
    SecretRedaction().filter(record)
    assert "payload" not in record.getMessage()
    assert "a" * 64 not in record.getMessage()


def test_optional_legacy_directory_supports_notepad_bom(tmp_path):
    from telegram_bot.config import AccessRegistry
    path = tmp_path / "legacy"
    path.mkdir()
    registry = AccessRegistry(path, optional=True)
    assert registry.token_for(123) is None
    (path / "telegram-access.json").write_text(
        json.dumps({"123": "shluz_" + "a" * 43}), encoding="utf-8-sig",
    )
    assert registry.token_for(123).get_secret_value() == "shluz_" + "a" * 43


def test_compose_auto_onboarding_generates_shared_key_without_per_user_file():
    import yaml
    root = Path(__file__).resolve().parents[2]
    for filename, volume in [("compose.yaml", "telegram_connector"),
                             ("compose.owner.telegram.yaml", "owner_telegram_connector")]:
        compose = yaml.safe_load((root / filename).read_text())
        bot = compose["services"]["telegram_bot"]
        gateway = compose["services"]["shluz"]
        assert bot["environment"]["TELEGRAM_CONNECTOR_FILE"] == "/run/shluz-connector/key"
        assert (
            gateway["environment"]["SHLUZ_TELEGRAM_CONNECTOR_FILE"]
            == "/app/telegram-connector/key"
        )
        assert volume + ":/run/shluz-connector:ro" in bot["volumes"]
        legacy = next(v for v in bot["volumes"] if isinstance(v, dict))
        assert legacy["bind"]["create_host_path"] is True
        assert legacy["read_only"] is True


def test_automatic_startup_registers_bot_and_needs_no_access_file(tmp_path, monkeypatch):
    from aiogram import Dispatcher

    from app.telegram.credentials import ensure_connector_key
    from telegram_bot.config import BotSettings
    from telegram_bot.gateway import GatewayClient
    from telegram_bot.runtime import run

    key = tmp_path / "key"
    ensure_connector_key(key)
    settings = BotSettings(_env_file=None, connector_file=key,
                           access_file=tmp_path / "absent.json",
                           database_path=tmp_path / "bot.sqlite")
    bot = AsyncMock()
    bot.get_webhook_info.return_value.url = ""
    bot.get_me.return_value.username = "auto_test_bot"
    register = AsyncMock()
    monkeypatch.setattr(GatewayClient, "register_bot", register)
    monkeypatch.setattr(Dispatcher, "start_polling", AsyncMock())
    asyncio.run(run(settings, bot=bot))
    register.assert_awaited_once_with("auto_test_bot")
    bot.session.close.assert_awaited_once()
    assert not settings.access_file.exists()
