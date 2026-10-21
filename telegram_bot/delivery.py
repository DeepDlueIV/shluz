"""Доставка уже полученного ответа. Ошибки Telegram не запускают модель повторно."""

import asyncio
import html
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from weakref import WeakValueDictionary

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, LinkPreviewOptions, Message

from telegram_bot import ui
from telegram_bot.presentation import render_answer, split_text

if TYPE_CHECKING:
    from telegram_bot.storage import Store

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class Delivery:
    def __init__(
        self, *, max_parts: int = 6, sleep=asyncio.sleep, store: "Store | None" = None
    ) -> None:
        self.max_parts = max_parts
        self.sleep = sleep
        self.store = store
        self.screen_ids: dict[tuple[int, int], int] = {}
        self.screen_locks: WeakValueDictionary[tuple[int, int], asyncio.Lock] = (
            WeakValueDictionary()
        )

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

    async def screen(self, message: Message, text: str, markup=None):
        """Одна панель на личный чат: источник кнопки не становится целью редактирования."""
        key = (message.bot.id, message.chat.id)
        lock = self.screen_locks.setdefault(key, asyncio.Lock())
        async with lock:
            panel_id = self.store.screen_id(*key) if self.store else self.screen_ids.get(key)
            if panel_id is not None:
                try:
                    sent = await self.call(
                        lambda: message.bot.edit_message_text(
                            text,
                            chat_id=message.chat.id,
                            message_id=panel_id,
                            parse_mode="HTML",
                            reply_markup=markup,
                            link_preview_options=NO_PREVIEW,
                        )
                    )
                except TelegramBadRequest as exc:
                    reason = exc.message.lower()
                    if "message is not modified" in reason:
                        sent = message.model_copy(
                            update={"message_id": panel_id, "text": text, "reply_markup": markup}
                        )
                    elif any(
                        reason_part in reason
                        for reason_part in (
                            "message to edit not found",
                            "message can't be edited",
                        )
                    ):
                        # Заменяем только заведомо недоступную панель, не ответы или сообщения.
                        # Сетевой сбой/неверный HTML сюда не попадают: новая копия их не исправит.
                        with suppress(TelegramAPIError):
                            await self.call(
                                lambda: message.bot.edit_message_reply_markup(
                                    chat_id=message.chat.id, message_id=panel_id, reply_markup=None
                                )
                            )
                        sent = await self.say(message, text, markup)
                    else:
                        raise
            else:
                sent = await self.say(message, text, markup)
            if isinstance(sent, Message):
                self.screen_ids[key] = sent.message_id
                if self.store:
                    self.store.set_screen_id(*key, sent.message_id)
            return sent

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

    async def waiting(self, message: Message, *, request_id: str | None = None) -> Message:
        with suppress(TelegramAPIError):
            await message.bot.send_chat_action(message.chat.id, "typing")
        markup = ui.cancel_controls(request_id)
        sent = await self.say(message, "⏳ Готовлю ответ…", markup)
        return sent.model_copy(update={"reply_markup": markup})

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
                    await self.edit(message, waiting, status, waiting.reply_markup)
            except TelegramRetryAfter as exc:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=exc.retry_after + 1)
            except TelegramAPIError:
                pass

    async def answer(
        self,
        message: Message,
        waiting: Message,
        answer: str,
        *,
        turn_id: str | None = None,
    ) -> None:
        controls = ui.answer_controls(turn_id)
        parts = split_text(answer)
        if len(parts) > self.max_parts:
            await self.edit(
                message, waiting, "📄 Ответ готов. Он большой — отправляю полный текст файлом."
            )
            await self.call(
                lambda: message.answer_document(
                    BufferedInputFile(answer.encode("utf-8"), filename="shluz-answer.txt"),
                    caption="Полный ответ без сокращений · UTF-8",
                    reply_markup=controls,
                )
            )
            return
        for index, part in enumerate(parts):
            markup = controls if index == len(parts) - 1 else None
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

    async def answer_file(self, message: Message, text: str, turn_id: str) -> None:
        await self.call(
            lambda: message.answer_document(
                BufferedInputFile(text.encode("utf-8"), filename=f"shluz-answer-{turn_id[:8]}.txt"),
                caption="Сохранённый ответ · повторного обращения к модели нет",
                reply_markup=ui.answer_controls(turn_id),
            )
        )


class StreamPreview:
    """Один редактируемый префикс; Telegram rate limits не тормозят чтение модели."""

    def __init__(self, delivery, message, waiting, interval=1.2):
        self.delivery, self.message, self.waiting = delivery, message, waiting
        self.interval = interval
        self.latest = self.shown = ""
        self.changed = asyncio.Event()
        self.task = None

    async def push(self, text):
        self.latest = text
        self.changed.set()
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def run(self):
        last = 0.0
        while True:
            await self.changed.wait()
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - last)))
            self.changed.clear()
            prefix = split_text(self.latest, limit=3200)[0]
            # При длинном ответе не рассылаем десятки промежуточных сообщений.
            text = html.escape(prefix) + (
                "\n\n⏳ Ответ продолжается. Полный текст придёт после завершения."
                if len(prefix) < len(self.latest)
                else "\n\n▌"
            )
            if text == self.shown:
                continue
            try:
                await self.delivery.edit(
                    self.message, self.waiting, text, self.waiting.reply_markup
                )
                self.shown = text
            except TelegramAPIError:
                # Итоговую доставку выполнит обычный Delivery.answer; POST не повторяем.
                pass
            last = time.monotonic()

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    def interrupted(self, warning):
        if not self.latest:
            return warning
        prefix = split_text(self.latest, limit=2800)[0]
        return html.escape(prefix) + "\n\n" + warning
