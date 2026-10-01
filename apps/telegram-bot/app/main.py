import asyncio

from aiogram import Bot, Dispatcher

from app.config import Settings


async def main() -> None:
    settings = Settings()
    if not settings.telegram_bot_token:
        raise RuntimeError('Telegram bot token is not configured')

    bot = Bot(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    await dispatcher.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
