"""Запуск polling-клиента: отдельные секреты, один процесс, корректное завершение."""

import argparse
import asyncio
import logging
import os
import re
from contextlib import suppress
from pathlib import Path

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import ValidationError

from telegram_bot.config import AccessRegistry, BotSettings, ConfigurationError
from telegram_bot.controller import Controller, create_dispatcher
from telegram_bot.gateway import GatewayClient
from telegram_bot.healthcheck import heartbeat_path, write_heartbeat
from telegram_bot.storage import Store

logger = logging.getLogger(__name__)


class SecretRedaction(logging.Filter):
    pattern = re.compile(r"shluz_[A-Za-z0-9_-]+|(?:bot)?\d{3,}(?::|%3[Aa])[A-Za-z0-9_-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        if record.exc_info and record.exc_info[0]:
            text += " [" + record.exc_info[0].__name__ + "]"
        record.msg = self.pattern.sub("[SECRET]", text)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(SecretRedaction())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = self.path.open("a+", encoding="utf-8")
        self.path.chmod(0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    self.handle.write("0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Другой процесс уже использует базу Telegram") from exc
        return self

    def __exit__(self, *args):
        if self.handle:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


COMMANDS = [
    ("start", "Начать работу"),
    ("menu", "Главное меню"),
    ("new", "Новый диалог"),
    ("models", "Выбрать модель"),
    ("history", "Мои диалоги"),
    ("plan", "Тариф и лимиты"),
    ("profile", "Мой профиль"),
    ("settings", "Настройки ответов"),
    ("export", "Экспорт текущего диалога"),
    ("clear", "Очистить текущий диалог"),
    ("delete", "Удалить текущий диалог"),
    ("cancel", "Остановить ожидание ответа"),
    ("privacy", "Как хранятся данные"),
    ("help", "Помощь"),
]


async def maintenance(store: Store, settings: BotSettings) -> None:
    count = 0
    while True:
        write_heartbeat(heartbeat_path(settings.database_path))
        if count % 720 == 0:
            store.cleanup(retention_days=settings.history_retention_days)
        count += 1
        await asyncio.sleep(5)


async def run(settings: BotSettings, *, bot: Bot | None = None) -> None:
    access = AccessRegistry(settings.access_file)
    resolved_bot = bot or Bot(token=settings.bot_token.get_secret_value())
    heartbeat = heartbeat_path(settings.database_path)
    try:
        with InstanceLock(settings.database_path.with_suffix(".lock")):
            controller, upkeep = None, None
            try:
                store = Store(
                    settings.database_path,
                    max_dialogs=settings.max_dialogs,
                    max_saved_turns=settings.max_saved_turns,
                )
                recovered = store.recover_pending()
                if recovered:
                    logger.warning(
                        "Неподтверждённых запросов после перезапуска: %s; повтора не будет",
                        recovered,
                    )
                gateway = GatewayClient(
                    settings.gateway_url,
                    timeout=settings.request_timeout,
                    max_response_chars=settings.max_response_chars,
                )
                controller = Controller(settings, access, store, gateway)
                webhook = await resolved_bot.get_webhook_info()
                if webhook.url:
                    raise ConfigurationError(
                        "У бота настроен webhook. Для polling используйте отдельного бота "
                        "или отключите webhook самостоятельно."
                    )
                await resolved_bot.set_my_commands(
                    [
                        BotCommand(command=command, description=description)
                        for command, description in COMMANDS
                    ],
                    scope=BotCommandScopeAllPrivateChats(),
                )
                upkeep = asyncio.create_task(maintenance(store, settings))
                dispatcher = create_dispatcher(controller)
                await dispatcher.start_polling(
                    resolved_bot,
                    allowed_updates=["message", "callback_query"],
                    handle_as_tasks=True,
                    tasks_concurrency_limit=settings.max_parallel_requests * 4 + 16,
                    close_bot_session=False,
                )
            finally:
                if upkeep:
                    upkeep.cancel()
                    with suppress(asyncio.CancelledError):
                        await upkeep
                try:
                    if controller:
                        await controller.close()
                finally:
                    heartbeat.unlink(missing_ok=True)
    finally:
        await resolved_bot.session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Shluz Telegram bot")
    parser.add_argument(
        "--check", action="store_true", help="Проверить настройки без сетевых запросов"
    )
    args = parser.parse_args()
    configure_logging()
    try:
        settings = BotSettings()
        validate_token(settings.bot_token.get_secret_value())
        AccessRegistry(settings.access_file)
        if args.check:
            print("Конфигурация Telegram проверена. Сетевые запросы не выполнялись.")
            return 0
        asyncio.run(run(settings))
        return 0
    except (ValidationError, TokenValidationError):
        logger.error(
            "Проверьте TELEGRAM_BOT_TOKEN и настройки .env.telegram; секреты не выводятся."
        )
        return 2
    except ConfigurationError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error(
            "Бот остановлен: %s. Проверьте конфигурацию и доступность сервисов.", type(exc).__name__
        )
        return 1
