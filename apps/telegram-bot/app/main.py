import asyncio

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.config import Settings

router = Router()


@router.message(CommandStart())
async def start(message: Message, settings: Settings) -> None:
    if message.from_user is None or message.from_user.id not in settings.allowed_ids():
        await message.answer(
            "Доступ к закрытому тесту ограничен.\n"
            f"Ваш Telegram ID: {message.from_user.id if message.from_user else 'unknown'}"
        )
        return
    await message.answer("Добро пожаловать в Shluz. Напишите сообщение для нового диалога.")


@router.message()
async def fallback(message: Message, settings: Settings) -> None:
    if message.from_user is None or message.from_user.id not in settings.allowed_ids():
        await message.answer("Этот бот доступен только участникам закрытого теста.")
        return
    await message.answer("Подключение к шлюзу будет выполнено следующим этапом.")


async def main() -> None:
    settings = Settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Telegram bot token is not configured")

    bot = Bot(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
