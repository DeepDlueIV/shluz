"""Доставка уже полученного ответа. Ошибки Telegram не запускают модель повторно."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, LinkPreviewOptions, Message

from telegram_bot import ui
from telegram_bot.presentation import render_answer, split_text

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class Delivery:
    def __init__(self, *, max_parts: int = 6, sleep=asyncio.sleep) -> None:
        self.max_parts = max_parts
        self.sleep = sleep

    async def call(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        for attempt in range(2):
            try:
                return await operation()
            except TelegramRetryAfter as exc:
                if attempt or exc.retry_after > 30:
                    raise
                await self.sleep(exc.retry_after + 0.1)
        raise RuntimeError("Unreachable delivery state")

    async def say(self, message: Message, text: str, markup=None, *, plain: bool = False):
        return await self.call(
            lambda: message.answer(
                text,
                parse_mode=None if plain else "HTML",
                reply_markup=markup,
                link_preview_options=NO_PREVIEW,
            )
        )

    async def edit(
        self,
        origin: Message,
        waiting: Message,
        text: str,
        markup=None,
        *,
        plain_text: str | None = None,
    ):
        try:
            return await self.call(
                lambda: origin.bot.edit_message_text(
                    text,
                    chat_id=origin.chat.id,
                    message_id=waiting.message_id,
                    parse_mode="HTML",
                    reply_markup=markup,
                    link_preview_options=NO_PREVIEW,
                )
            )
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return waiting
            if plain_text is None:
                raise
            # Удалённое сообщение ожидания или неожиданный parse error: новый обычный текст.
            return await self.say(origin, plain_text, markup, plain=True)

    async def waiting(self, message: Message) -> Message:
        with suppress(TelegramAPIError):
            await message.bot.send_chat_action(message.chat.id, "typing")
        return await self.say(message, "⏳ Готовлю ответ…", ui.cancel_controls())

    async def animate(self, message: Message, waiting: Message, stop: asyncio.Event) -> None:
        tick = 0
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=4)
                return
            except TimeoutError:
                pass
            try:
                await message.bot.send_chat_action(message.chat.id, "typing")
                tick += 1
                if tick % 2 == 0:
                    status = "⌛ Ответ ещё готовится…" if tick % 4 else "⏳ Готовлю ответ…"
                    await self.edit(message, waiting, status, ui.cancel_controls())
            except TelegramRetryAfter as exc:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=exc.retry_after + 1)
            except TelegramAPIError:
                pass

    async def answer(self, message: Message, waiting: Message, answer: str) -> None:
        parts = split_text(answer)
        if len(parts) > self.max_parts:
            await self.edit(
                message, waiting, "📄 Ответ готов. Он большой — отправляю полный текст файлом."
            )
            await self.call(
                lambda: message.answer_document(
                    BufferedInputFile(answer.encode("utf-8"), filename="shluz-answer.txt"),
                    caption="Полный ответ без сокращений · UTF-8",
                    reply_markup=ui.answer_controls(),
                )
            )
            return
        for index, part in enumerate(parts):
            markup = ui.answer_controls() if index == len(parts) - 1 else None
            if index == 0:
                await self.edit(message, waiting, render_answer(part), markup, plain_text=part)
            else:
                await self.sleep(1.1)
                try:
                    await self.say(message, render_answer(part), markup)
                except TelegramBadRequest:
                    await self.say(message, part, markup, plain=True)

    async def export(self, message: Message, text: str) -> None:
        await self.call(
            lambda: message.answer_document(
                BufferedInputFile(text.encode("utf-8"), filename="shluz-dialog.txt"),
                caption="История текущего диалога · UTF-8",
                reply_markup=ui.back(),
            )
        )
