import asyncio
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

from telegram_bot.main import send_to_shluz

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "🤖 Добро пожаловать в Shluz AI.\n\n"
        "Отправьте сообщение, и я передам его модели."
    )


@router.message()
async def chat(message: Message) -> None:
    waiting = await message.answer("⏳ Думаю...")
    try:
        answer = await send_to_shluz(
            [{"role": "user", "content": message.text or ""}]
        )
        await waiting.edit_text(answer[:4000])
    except Exception:
        await waiting.edit_text(
            "⚠️ Не удалось получить ответ. Попробуйте позже."
        )


async def run() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    bot = Bot(token=token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run())
