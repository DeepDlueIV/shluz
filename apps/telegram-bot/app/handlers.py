from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.config import Settings

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    settings = Settings()
    if message.from_user is None or message.from_user.id not in settings.allowed_ids():
        await message.answer(
            'Доступ к закрытому тесту не включён. Ваш Telegram ID: '
            f'{message.from_user.id if message.from_user else "unknown"}'
        )
        return
    await message.answer('Добро пожаловать в Shluz. Напишите сообщение для нового диалога.')
