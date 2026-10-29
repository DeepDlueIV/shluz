"""Доставка уже полученного ответа. Ошибки Telegram не запускают модель повторно."""

import asyncio
import html
import logging
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

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class Delivery:
    def __init__(
        self,
        *,
        max_parts: int = 6,
        sleep=asyncio.sleep,
        store: "Store | None" = None,
        clean_chat: bool = False,
    ) -> None:
        self.clean_chat = clean_chat
        self.max_parts = max_parts
        self.sleep = sleep
        self.store = store
        self.screen_ids: dict[tuple[int, int], int] = {}
        self.latest_ids: dict[tuple[int, int], int] = {}
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

    def observe_message(self, message: Message, message_id: int | None = None) -> None:
        key = (message.bot.id, message.chat.id)
        message_id = message.message_id if message_id is None else message_id
        if message_id > self.latest_ids.get(key, 0):
            self.latest_ids[key] = message_id
            if self.store:
                self.store.note_message(*key, message_id)

    async def say(self, message: Message, text: str, markup=None, *, plain: bool = False):
        markup = None if self.clean_chat else markup
        sent = await self.call(
            lambda: message.answer(
                text,
                parse_mode=None if plain else "HTML",
                reply_markup=markup,
                link_preview_options=NO_PREVIEW,
            )
        )
        if isinstance(sent, Message):
            self.observe_message(message, sent.message_id)
        return sent

    async def retire(self, message: Message, message_id: int) -> None:
        """Убираем только известную панель/индикатор бота, никогда пользовательский ответ."""
        try:
            await self.call(lambda: message.bot.delete_message(message.chat.id, message_id))
            return
        except TelegramBadRequest as exc:
            if "message to delete not found" in exc.message.lower():
                return
        except TelegramAPIError:
            pass
        # Telegram не разрешает удалять сообщения старше 48 часов. Хотя бы убираем кнопки.
        with suppress(TelegramAPIError):
            await self.call(
                lambda: message.bot.edit_message_reply_markup(
                    chat_id=message.chat.id, message_id=message_id, reply_markup=None
                )
            )

    async def screen(self, message: Message, text: str, markup=None):
        """Навигация на месте; после переписки открываем панель внизу и убираем прежнюю."""
        if self.clean_chat:
            return await self.say(message, text)
        key = (message.bot.id, message.chat.id)
        self.observe_message(message)
        lock = self.screen_locks.setdefault(key, asyncio.Lock())
        async with lock:
            panel_id = self.store.screen_id(*key) if self.store else self.screen_ids.get(key)
            latest = self.store.last_message_id(*key) if self.store else self.latest_ids.get(key, 0)
            sent = None
            if panel_id is not None and latest <= panel_id:
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
                        return message.model_copy(
                            update={"message_id": panel_id, "text": text, "reply_markup": markup}
                        )
                    if not any(
                        part in reason
                        for part in (
                            "message to edit not found",
                            "message can't be edited",
                        )
                    ):
                        raise
            if sent is None:
                # Сначала успешно отправить и сохранить новую панель. При сетевом сбое
                # старую не удаляем и sendMessage автоматически не повторяем.
                sent = await self.say(message, text, markup)
            if isinstance(sent, Message):
                self.screen_ids[key] = sent.message_id
                if self.store:
                    self.store.set_screen_id(*key, sent.message_id)
                if panel_id is not None and panel_id != sent.message_id:
                    await self.retire(message, panel_id)
            return sent

    async def finish(
        self,
        message: Message,
        waiting: Message,
        text: str,
        markup=None,
        *,
        draft: bool = False,
        plain_text: str | None = None,
    ):
        markup = None if self.clean_chat else markup
        if not draft:
            return await self.edit(message, waiting, text, markup, plain_text=plain_text)
        # Черновик временный: завершить именно sendMessage, а не редактированием индикатора.
        try:
            sent = await self.say(message, text, markup)
        except TelegramBadRequest:
            if plain_text is None:
                raise
            sent = await self.say(message, plain_text, markup, plain=True)
        await self.retire(message, waiting.message_id)
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
        markup = None if self.clean_chat else markup
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
        markup = None if self.clean_chat else ui.cancel_controls(request_id)
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
        draft: bool = False,
    ) -> None:
        controls = None if self.clean_chat else ui.answer_controls(turn_id)
        parts = split_text(answer)
        if len(parts) > self.max_parts:
            await self.finish(
                message,
                waiting,
                "📄 Ответ готов. Он большой — отправляю полный текст файлом.",
                draft=draft,
            )
            sent = await self.call(
                lambda: message.answer_document(
                    BufferedInputFile(answer.encode("utf-8"), filename="shluz-answer.txt"),
                    caption="Полный ответ без сокращений · UTF-8",
                    reply_markup=controls,
                )
            )
            self.observe_message(message, sent.message_id)
            return
        for index, part in enumerate(parts):
            markup = controls if index == len(parts) - 1 else None
            if index == 0:
                await self.finish(
                    message, waiting, render_answer(part), markup, draft=draft, plain_text=part
                )
            else:
                await self.sleep(1.1)
                try:
                    await self.say(message, render_answer(part), markup)
                except TelegramBadRequest:
                    await self.say(message, part, markup, plain=True)

    async def export(self, message: Message, text: str) -> None:
        sent = await self.call(
            lambda: message.answer_document(
                BufferedInputFile(text.encode("utf-8"), filename="shluz-dialog.txt"),
                caption="История текущего диалога · UTF-8",
                reply_markup=ui.back(),
            )
        )

        self.observe_message(message, sent.message_id)

    async def answer_file(self, message: Message, text: str, turn_id: str) -> None:
        sent = await self.call(
            lambda: message.answer_document(
                BufferedInputFile(text.encode("utf-8"), filename=f"shluz-answer-{turn_id[:8]}.txt"),
                caption="Сохранённый ответ · повторного обращения к модели нет",
                reply_markup=ui.answer_controls(turn_id),
            )
        )

        self.observe_message(message, sent.message_id)


class StreamPreview:
    """Нативная анимация Telegram; чтение SSE не ждёт обновлений интерфейса."""

    def __init__(self, delivery, message, waiting, interval=0.35):
        self.delivery, self.message, self.waiting = delivery, message, waiting
        self.interval = interval
        self.latest = self.shown = ""
        self.changed = asyncio.Event()
        self.task = None
        self.draft_used = False
        self.use_draft = message.chat.type == "private"
        self.draft_id = waiting.message_id  # Стабилен внутри ответа, ненулевой, уникален в чате.
        self.retry_at = 0.0

    async def push(self, text):
        self.latest = text
        self.changed.set()
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def run(self):
        last = 0.0
        while True:
            # Черновик живёт 30 секунд: поддерживаем его при паузе поставщика.
            with suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout=10)
            interval = self.interval if self.use_draft else max(1.2, self.interval)
            await asyncio.sleep(
                max(0, last + interval - time.monotonic(), self.retry_at - time.monotonic())
            )
            self.changed.clear()
            if not self.latest:
                continue
            prefix = split_text(self.latest, limit=3800)[0]
            text = prefix + ("\n\nОтвет продолжается…" if len(prefix) < len(self.latest) else "")
            if not self.use_draft and text == self.shown:
                continue
            try:
                if self.use_draft:
                    try:
                        await self.message.bot.send_message_draft(
                            chat_id=self.message.chat.id,
                            message_thread_id=self.message.message_thread_id,
                            draft_id=self.draft_id,
                            text=text,
                            parse_mode=None,
                        )
                        self.draft_used = True
                    except TelegramBadRequest as exc:
                        if not any(
                            part in exc.message.lower()
                            for part in (
                                "method not found",
                                "not supported",
                                "unsupported",
                                "not available",
                                "forum topic mode",
                            )
                        ):
                            raise
                        self.use_draft = False
                        await self.delivery.edit(
                            self.message, self.waiting, html.escape(text), self.waiting.reply_markup
                        )
                else:
                    await self.delivery.edit(
                        self.message, self.waiting, html.escape(text), self.waiting.reply_markup
                    )
                self.shown = text
            except TelegramRetryAfter as exc:
                self.retry_at = time.monotonic() + exc.retry_after + 0.1
            except TelegramAPIError as exc:
                # Ошибка Telegram не объявляется ошибкой модели и не повторяет платный POST.
                logger.info("Предпросмотр Telegram временно недоступен: %s", type(exc).__name__)
                self.retry_at = time.monotonic() + 3
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
