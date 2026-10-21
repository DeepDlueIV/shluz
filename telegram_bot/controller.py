"""Сценарии закрытого бота: меню, диалоги, ограничения, отмена и защита от повторов."""

import asyncio
import html
import logging
import time
import uuid
from contextlib import suppress

from aiogram import BaseMiddleware, Dispatcher, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from pydantic import SecretStr

from telegram_bot import ui
from telegram_bot.config import AccessRegistry, BotSettings
from telegram_bot.delivery import Delivery, StreamPreview
from telegram_bot.features import Confirmations, handle_action
from telegram_bot.gateway import ERROR_TEXT, GatewayClient, GatewayError
from telegram_bot.presentation import prepare_messages
from telegram_bot.storage import DialogLimit, Store

logger = logging.getLogger(__name__)


class ClosedAccessMiddleware(BaseMiddleware):
    def __init__(self, controller: "Controller") -> None:
        self.controller = controller

    async def __call__(self, handler, event: Update, data):
        callback = event.callback_query
        message = event.message or (callback.message if callback else None)
        sender = callback.from_user if callback else (message.from_user if message else None)
        if not isinstance(message, Message) or sender is None or sender.is_bot:
            if callback:
                with suppress(TelegramAPIError):
                    await callback.answer("Откройте личный чат с ботом.")
            return None
        if message.chat.type != "private" or message.chat.id != sender.id:
            if callback:
                await callback.answer("Работаю только в личном чате.", show_alert=True)
            return None
        self.controller.delivery.observe_message(message)
        token = self.controller.access.token_for(sender.id)
        if self.controller.settings.connector_file:
            start_parts = (message.text or "").split(maxsplit=1) if not callback else []
            invite = None
            if start_parts and start_parts[0].split("@")[0] == "/start" and len(start_parts) == 2:
                candidate = start_parts[1].strip()
                if candidate.startswith("join_"):
                    invite = candidate
            try:
                admission = await self.controller.gateway.onboard(
                    sender.id,
                    sender.full_name,
                    sender.username,
                    invite=invite,
                    legacy_token=token,
                )
            except GatewayError as exc:
                if callback:
                    with suppress(TelegramAPIError):
                        await callback.answer()
                await self.controller.delivery.screen(message, exc.user_message())
                return None
            if admission["state"] != "active":
                if callback:
                    with suppress(TelegramAPIError):
                        await callback.answer()
                pending = admission["state"] == "pending"
                await self.controller.delivery.screen(
                    message,
                    (
                        "📨 <b>Заявка отправлена</b>\n\n"
                        "Сейчас идёт закрытое тестирование. Администратор увидит вашу заявку "
                        "в панели. После одобрения нажмите «Проверить доступ» или напишите мне.\n\n"
                        "Никаких ключей, ID или файлов отправлять не нужно."
                    )
                    if pending
                    else ERROR_TEXT["onboarding_blocked"],
                    InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Проверить доступ", callback_data="access_check"
                                ),
                            ]
                        ]
                    )
                    if pending
                    else None,
                )
                return None
            token = SecretStr(admission["token"])
        if token is None:
            if callback:
                await callback.answer("Доступ к закрытой бете не выдан.", show_alert=True)
            else:
                await self.controller.delivery.screen(
                    message,
                    "🔒 <b>Закрытое тестирование Shluz</b>\n\n"
                    f"Ваш Telegram ID: <code>{sender.id}</code>\n"
                    "Передайте этот ID администратору для подключения. "
                    "API-ключи сюда присылать не нужно.",
                )
            return None
        if not self.controller.store.claim_update(event.update_id):
            if callback:
                with suppress(TelegramAPIError):
                    await callback.answer()
            return None
        self.controller.store.ensure_user(sender.id, sender.full_name)
        data["gateway_token"] = token
        data["telegram_update_id"] = event.update_id
        try:
            return await handler(event, data)
        except GatewayError as exc:
            await self.controller.delivery.screen(message, exc.user_message(), ui.back())
        except DialogLimit:
            await self.controller.delivery.screen(
                message,
                "🗂 Достигнут лимит диалогов. Удалите ненужный через «Настройки диалога».",
                ui.back(),
            )
        except TelegramAPIError as exc:
            # sendMessage мог дойти до Telegram без ответа нам: не создаём дубликат меню.
            logger.warning("Доставка меню Telegram не подтверждена: %s", type(exc).__name__)
        except Exception as exc:
            # Не логируем тело сообщения, токены, URL запроса или traceback с секретами.
            logger.warning("Ошибка обработки Telegram: %s", type(exc).__name__)
            with suppress(TelegramAPIError):
                await self.controller.delivery.screen(
                    message,
                    "⚠️ Не удалось выполнить действие. Откройте главное меню кнопкой ниже.",
                    ui.back(),
                )
        return None


class Controller:
    def __init__(
        self,
        settings: BotSettings,
        access: AccessRegistry,
        store: Store,
        gateway: GatewayClient,
        *,
        sleep=asyncio.sleep,
    ) -> None:
        self.settings, self.access, self.store, self.gateway = settings, access, store, gateway
        self.delivery = Delivery(max_parts=settings.max_text_parts, sleep=sleep, store=store)
        self.jobs: dict[int, asyncio.Task] = {}
        self.job_ids: dict[int, str] = {}
        self.last_request: dict[int, float] = {}
        self.shutting_down = False
        self.confirmations = Confirmations()
        self.rename_targets: dict[int, tuple[str, float]] = {}

    async def linked_profile(self, message: Message, token: str, *, user=None) -> dict:
        sender = user or message.from_user
        remote = await self.gateway.profile(token)
        local = self.store.profile(sender.id)
        if local["account_id"] != remote["account"]["id"]:
            if local["account_id"] is not None:
                raise GatewayError("identity_conflict")
            await self.gateway.bind(token, sender.id, sender.username)
            self.store.bind_account(sender.id, remote["account"]["id"])
        return remote

    async def show_menu(self, message: Message, user_id: int, remote: dict | None = None) -> None:
        profile = self.store.profile(user_id)
        model = html.escape(profile["model"] or "будет выбрана при первом сообщении")
        dialog = self.store.dialog_for(user_id, profile["active_dialog"])
        title = html.escape(dialog["title"] if dialog else "Новый диалог")
        text = (
            "<b>🧪 Shluz · тестовая версия</b>\n\n"
            f"Сейчас открыт: <b>{title}</b>\n"
            "Просто напишите сообщение, чтобы продолжить этот диалог.\n"
            f"Модель: <code>{model}</code>\n\n"
            "«Новый чат» начинает отдельный диалог. Предыдущие остаются в «Диалогах»."
        )
        if remote and remote.get("plan") and remote["plan"].get("kind") == "demo":
            quota = remote["allowance"]["requests"]
            text += f"\n\n🎁 Демо: осталось {quota['remaining']} из {quota['limit']} запросов."
        if profile["model"] == "mock-chat":
            text += "\n\n🧩 mock-chat — тестовый ответ, не настоящая нейросеть."
        await self.delivery.screen(message, text, ui.menu())

    async def on_message(
        self, message: Message, gateway_token: SecretStr, telegram_update_id: int
    ) -> None:
        user_id = message.from_user.id
        self.confirmations.clear(user_id)
        text = message.text
        if text is None or not text.strip():
            await self.delivery.screen(
                message,
                "📝 Пока поддерживаются только текстовые сообщения. "
                "Файлы, фото и голосовые модели не отправляются.",
                ui.back(),
            )
            return
        if text.startswith("/"):
            self.rename_targets.pop(user_id, None)
            command = text.split()[0].split("@")[0][1:].lower()
            parts = text.split(maxsplit=1)
            value = parts[1].strip() if len(parts) > 1 else ""
            await self.action(
                message,
                user_id,
                gateway_token.get_secret_value(),
                command,
                value,
                update_id=telegram_update_id,
            )
            return
        pending_name = self.rename_targets.get(user_id)
        if pending_name is not None:
            target, expires = pending_name
            if expires <= time.monotonic():
                self.rename_targets.pop(user_id, None)
                await self.delivery.screen(
                    message,
                    "Запрос названия устарел. Нажмите «Название диалога» ещё раз.",
                    ui.keyboard([[("✏️ Название диалога", "rename")], [("☰ Меню", "menu")]]),
                )
                return
            try:
                changed = self.store.rename_dialog(user_id, target, text)
            except ValueError:
                await self.delivery.screen(
                    message,
                    "Название должно содержать от 1 до 60 символов. Попробуйте ещё раз.",
                    ui.keyboard([[("Отмена", "rename_cancel")]]),
                )
                return
            self.rename_targets.pop(user_id, None)
            dialog = self.store.dialog_for(user_id, target) if changed else None
            await self.delivery.screen(
                message,
                f"✅ Название: {html.escape(dialog['title'])}"
                if dialog
                else "Диалог уже удалён или недоступен.",
                ui.back(),
            )
            return
        if len(text) > self.settings.max_input_chars:
            await self.delivery.screen(
                message,
                "📏 Сообщение слишком большое. Разделите его на несколько более коротких запросов.",
                ui.back(),
            )
            return
        await self.generate(message, gateway_token, telegram_update_id)

    async def on_callback(
        self, callback: CallbackQuery, gateway_token: SecretStr, telegram_update_id: int
    ) -> None:
        action, _, value = (callback.data or "").partition(":")
        self.rename_targets.pop(callback.from_user.id, None)
        if action not in {"retry_yes", "forget_yes", "clear_yes", "delete_yes"}:
            self.confirmations.clear(callback.from_user.id)
        if action in {"up", "down"}:
            saved = self.store.rate_answer(
                callback.from_user.id, value, 1 if action == "up" else -1
            )
            await callback.answer(
                "Спасибо, оценка сохранена." if saved else "Ответ уже недоступен."
            )
            return
        await callback.answer()
        await self.action(
            callback.message,
            callback.from_user.id,
            gateway_token.get_secret_value(),
            action,
            value,
            user=callback.from_user,
            update_id=telegram_update_id,
        )

    async def action(
        self,
        message: Message,
        user_id: int,
        token: str,
        action: str,
        value: str = "",
        *,
        user=None,
        update_id: int | None = None,
    ) -> None:
        local = self.store.profile(user_id)
        busy_actions = {
            "new",
            "clear",
            "delete",
            "delete_turn",
            "clear_yes",
            "delete_yes",
            "dialog",
            "model",
            "style",
            "start",
            "rename",
            "retry",
            "retry_yes",
            "forget",
            "forget_yes",
        }
        if action in busy_actions and user_id in self.jobs:
            await self.delivery.screen(
                message,
                "⏳ Ответ уже готовится. Дождитесь его или нажмите «Остановить ожидание».",
                ui.cancel_controls(self.job_ids.get(user_id)),
            )
            return
        if await handle_action(
            self,
            message,
            user_id,
            token,
            action,
            value,
            user=user,
            update_id=update_id,
        ):
            return
        if action in {"start", "menu", "access_check"}:
            remote = await self.linked_profile(message, token, user=user)
            if action in {"start", "access_check"} and local["model"] is None:
                models = await self.gateway.models(token)
                selected = (
                    self.settings.default_model
                    if self.settings.default_model in models
                    else (models[0] if models else None)
                )
                if selected:
                    self.store.set_model(user_id, selected)
            await self.show_menu(message, user_id, remote)
        elif action == "new":
            self.store.new_dialog(user_id)
            await self.delivery.screen(
                message, "✨ Новый диалог открыт. О чём поговорим?", ui.answer_controls()
            )
        elif action in {"plan", "balance", "limits", "profile", "stats"}:
            remote = await self.linked_profile(message, token, user=user)
            usage = await self.gateway.usage(token)
            summary = usage.get("summary", {})
            charged = summary.get("charged_usd")
            money_text = html.escape(str(charged)) + " USD" if charged is not None else "уточняется"
            expenses = (
                f"\n\n<b>Расход за текущий месяц (UTC)</b>\n"
                f"Входные токены: {summary.get('prompt_tokens', 0)}\n"
                f"Выходные токены: {summary.get('completion_tokens', 0)}\n"
                f"Списано: {money_text}"
            )
            if summary.get("usage_unknown"):
                expenses += (
                    f"\n⚠️ Статистика {summary['usage_unknown']} прерванных запросов "
                    "не подтверждена провайдером."
                )
            await self.delivery.screen(
                message,
                ui.profile_text(remote, local) + expenses,
                ui.keyboard([[("💳 Тарифы и оплата", "tariffs")], [("☰ Меню", "menu")]]),
            )
        elif action == "models":
            models = await self.gateway.models(token)
            offset = int(value) if value.isdigit() else 0
            offset = min(max(0, offset), max(0, len(models) - 1))
            await self.delivery.screen(
                message,
                "<b>🤖 Выберите модель</b>\n"
                + (
                    ("Только модели вашего тарифа и Telegram. Смена модели сохраняет контекст.")
                    if models
                    else "Для этого канала пока нет разрешённых моделей. Меню и история доступны."
                ),
                ui.models_controls(models, local["model"], offset),
            )
        elif action == "model":
            models = await self.gateway.models(token)
            matches = [model for model in models if ui.model_key(model) == value]
            if len(matches) != 1:
                await self.delivery.screen(
                    message, "🤖 Список изменился. Откройте «Модели» ещё раз.", ui.back()
                )
                return
            self.store.set_model(user_id, matches[0])
            await self.delivery.screen(
                message,
                f"✅ Выбрана модель <code>{html.escape(matches[0])}</code>.",
                ui.answer_controls(),
            )
        elif action in {"settings", "rename_cancel"}:
            await self.delivery.screen(
                message,
                "<b>⚙️ Настройки диалога</b>\n"
                f"История: {self.settings.history_retention_days} дней.\n\n"
                "Выберите стиль ответа или действие с текущим диалогом.",
                ui.settings_controls(local["style"]),
            )
        elif action == "style":
            if value not in ui.STYLE_LABELS:
                await self.delivery.screen(
                    message, "Кнопка устарела. Откройте «Настройки диалога».", ui.back()
                )
                return
            self.store.set_style(user_id, value)
            await self.delivery.screen(
                message, f"✅ Стиль ответа: {ui.STYLE_LABELS[value]}.", ui.settings_controls(value)
            )
        elif action == "history":
            offset = min(int(value), self.settings.max_dialogs) if value.isdigit() else 0
            dialogs = self.store.dialogs(user_id, offset=offset, limit=9)
            await self.delivery.screen(
                message,
                "<b>🗂 Ваши диалоги</b>\nВыберите диалог для продолжения.",
                ui.history_controls(dialogs, local["active_dialog"], offset),
            )
        elif action == "dialog":
            if not self.store.activate_dialog(user_id, value):
                await self.delivery.screen(
                    message, "Этот диалог недоступен. Откройте «Диалоги».", ui.back()
                )
                return
            history = self.store.history(user_id)
            dialog = self.store.dialog_for(user_id, value)
            title = html.escape(dialog["title"] if dialog else "Новый диалог")
            text = f"💬 Сейчас открыт: <b>{title}</b>\nПросто напишите сообщение для продолжения."
            if history:
                excerpt = history[-1]["content"][:700]
                text += "\n\n<b>Начало последнего ответа:</b>\n" + html.escape(excerpt)
                text += "\n\nПолная история — «Настройки диалога» → «Экспорт»."
            await self.delivery.screen(message, text, ui.answer_controls())
        elif action in {"clear", "delete", "delete_turn"}:
            target = local["active_dialog"]
            if action == "delete_turn":
                turn = self.store.turn_for(user_id, value)
                if not turn:
                    await self.delivery.screen(
                        message,
                        "Диалог этого ответа уже удалён или недоступен.",
                        ui.keyboard([[("🗂 Диалоги", "history:0")], [("☰ Меню", "menu")]]),
                    )
                    return
                target = turn["dialog_id"]
                action = "delete"
            dialog = self.store.dialog_for(user_id, target)
            if not dialog:
                await self.delivery.screen(message, "Диалог уже недоступен.", ui.back())
                return
            nonce = self.confirmations.issue(user_id, action, target)
            verb = "Очистить историю диалога" if action == "clear" else "Удалить диалог"
            await self.delivery.screen(
                message,
                f"<b>🗑 {verb} «{html.escape(dialog['title'])}»?</b>\n\n"
                "История этого диалога в боте будет удалена. Отменить удаление нельзя.\n"
                "Сообщения в Telegram останутся. Баланс и тариф не изменятся.",
                ui.keyboard(
                    [
                        [("Да, удалить", f"{action}_yes:{nonce}")],
                        [("Отмена", "menu")],
                    ]
                ),
            )
        elif action in {"clear_yes", "delete_yes"}:
            target = self.confirmations.take(user_id, action.removesuffix("_yes"), value)
            if target is None:
                await self.delivery.screen(
                    message,
                    "Подтверждение устарело. Откройте «Настройки диалога» ещё раз.",
                    ui.back(),
                )
                return
            change = self.store.clear_dialog if action == "clear_yes" else self.store.delete_dialog
            if change(user_id, target):
                await self.delivery.screen(
                    message, "✅ История выбранного диалога удалена.", ui.answer_controls()
                )
            else:
                await self.delivery.screen(message, "Диалог уже удалён или недоступен.", ui.back())
        elif action == "export":
            if not self.store.history(user_id):
                await self.delivery.screen(
                    message, "📄 В текущем диалоге пока нет завершённых ответов.", ui.back()
                )
                return
            await self.delivery.export(message, self.store.export_dialog(user_id))
        elif action == "cancel":
            if user is not None and (not value or self.job_ids.get(user_id) != value):
                await self.delivery.screen(
                    message,
                    "Эта кнопка относится к завершённому запросу. Текущий не остановлен.",
                    ui.back(),
                )
                return
            job = self.jobs.get(user_id)
            if job:
                job.cancel()
            else:
                await self.delivery.screen(
                    message, "Сейчас нет запроса, которого нужно ждать.", ui.back()
                )
        elif action in {"faq", "help", "privacy"}:
            await self.delivery.screen(
                message,
                "<b>❓ FAQ</b>\n\n"
                "Здесь появятся ответы на частые вопросы. Пока можно обратиться в поддержку."
                '\n\n<a href="https://github.com/DeepDlueIV/shluz/blob/'
                'implementation/telegram-beta-complete/docs/telegram-privacy.md">'
                "Конфиденциальность</a>",
                ui.keyboard([[("🛟 Поддержка", "support")], [("☰ Меню", "menu")]]),
            )

        else:
            await self.delivery.screen(
                message,
                "Неизвестная команда или устаревшая кнопка. Нажмите «Главное меню».",
                ui.back(),
            )

    async def generate(
        self,
        message: Message,
        secret: SecretStr,
        update_id: int,
        *,
        user=None,
        text: str | None = None,
        retry_of: str | None = None,
        model_override: str | None = None,
    ) -> None:
        sender = user or message.from_user
        user_id = sender.id
        text = message.text if text is None else text
        if user_id in self.jobs:
            await self.delivery.screen(
                message,
                "⏳ Ответ уже готовится. Дождитесь его или нажмите «Остановить ожидание».",
                ui.cancel_controls(self.job_ids.get(user_id)),
            )
            return
        if len(self.jobs) >= self.settings.max_parallel_requests:
            await self.delivery.screen(
                message, "⏳ Сейчас все места обработки заняты. Попробуйте чуть позже.", ui.back()
            )
            return
        if time.monotonic() - self.last_request.get(user_id, 0) < self.settings.cooldown_seconds:
            await self.delivery.screen(
                message, "⏳ Слишком быстро. Подождите пару секунд перед новым запросом.", ui.back()
            )
            return
        task = asyncio.current_task()
        self.jobs[user_id] = task
        self.job_ids[user_id] = uuid.uuid4().hex
        waiting, spinner, turn_id = None, None, None
        preview = None
        stop = asyncio.Event()

        async def stop_animation() -> None:
            if preview:
                await preview.close()
            stop.set()
            if spinner:
                spinner.cancel()
                with suppress(asyncio.CancelledError):
                    await spinner

        try:
            token = secret.get_secret_value()
            remote = await self.linked_profile(message, token, user=sender)
            if remote["plan"] is None:
                raise GatewayError("subscription_required")
            models = await self.gateway.models(token)
            if not models:
                raise GatewayError("model_not_allowed")
            local = self.store.profile(user_id)
            model = model_override or local["model"]
            if model is None:
                model = (
                    self.settings.default_model
                    if self.settings.default_model in models
                    else models[0]
                )
                self.store.set_model(user_id, model)
            if model not in models:
                raise GatewayError("model_not_found")
            history = self.store.history(user_id)
            if retry_of:
                if self.store.retry_source(user_id, retry_of) is None:
                    raise GatewayError("retry_unavailable")
                history = history[:-2]
            messages, trimmed = prepare_messages(
                history,
                text,
                style=local["style"],
                budget=self.settings.context_char_limit,
            )
            if not self.settings.connector_file:
                latest = self.access.token_for(user_id)
                if latest is None or latest.get_secret_value() != token:
                    raise GatewayError("unauthorized")
            # В автоматическом режиме статус доступа проверяется самим gateway при POST.
            waiting = await self.delivery.waiting(message, request_id=self.job_ids[user_id])
            turn_id = self.store.begin_turn(
                user_id,
                update_id,
                text,
                model,
                replaces_turn_id=retry_of,
            )
            if turn_id is None:
                await self.delivery.edit(
                    message, waiting, "Этот запрос уже обработан. Откройте «Диалоги»."
                )
                return
            self.last_request[user_id] = time.monotonic()
            spinner = asyncio.create_task(self.delivery.animate(message, waiting, stop))
            preview = StreamPreview(self.delivery, message, waiting)

            async def show_delta(text):
                # Остановить старую анимацию до первого фрагмента, иначе она затрёт текст.
                stop.set()
                if spinner and not spinner.done():
                    spinner.cancel()
                    with suppress(asyncio.CancelledError):
                        await spinner
                await preview.push(text)

            answer = await self.gateway.chat(token, model, messages, turn_id, on_delta=show_delta)
            self.store.complete_turn(turn_id, answer)
            await stop_animation()
            await self.delivery.answer(
                message, waiting, answer, turn_id=turn_id, draft=preview.draft_used
            )
            if remote["plan"].get("kind") == "demo":
                updated = await self.gateway.profile(token)
                left = updated["allowance"]["requests"]["remaining"]
                await self.delivery.say(
                    message,
                    f"🎁 Демо: осталось {left} запросов.",
                    ui.keyboard([[("💳 Тарифы и оплата", "tariffs")]]) if left == 0 else None,
                )
            if trimmed:
                await self.delivery.say(
                    message,
                    "ℹ️ В запрос вошла только последняя часть длинной истории. "
                    "Полная история — «Настройки диалога» → «Экспорт».",
                )
        except asyncio.CancelledError:
            await stop_animation()
            if turn_id:
                self.store.fail_turn(turn_id, "unknown")
            if not self.shutting_down:
                text = (
                    "⏹ Ожидание остановлено. Запрос мог быть выполнен и учтён шлюзом. "
                    "Автоматического повтора не будет. "
                    "Проверьте «Аккаунт и лимиты» перед новым запросом."
                )
                if turn_id and self.store.turn_status(turn_id) == "complete":
                    text = (
                        "⏹ Показ остановлен. Ответ уже сохранён — "
                        "он доступен в «Настройках диалога» → «Экспорт»."
                    )
                if preview and not (turn_id and self.store.turn_status(turn_id) == "complete"):
                    text = preview.interrupted(text)
                with suppress(TelegramAPIError):
                    if waiting:
                        await self.delivery.finish(
                            message, waiting, text, ui.answer_controls(), plain_text=text,
                            draft=bool(preview and preview.draft_used),
                        )
                    else:
                        await self.delivery.say(message, text, ui.answer_controls())
        except GatewayError as exc:
            await stop_animation()
            if turn_id:
                uncertain = exc.code in {
                    "outcome_unknown",
                    "invalid_response",
                    "response_too_large",
                    "provider_unavailable",
                    "provider_error",
                }
                self.store.fail_turn(turn_id, "unknown" if uncertain else "failed")
            logger.warning(
                "Сбой запроса модели: code=%s status=%s reference=%s",
                exc.code, exc.status, exc.reference or "unknown",
            )
            text = exc.user_message()
            if preview:
                text = preview.interrupted(text)
            if waiting:
                await self.delivery.finish(
                    message, waiting, text, ui.answer_controls(),
                    draft=bool(preview and preview.draft_used),
                )
            else:
                await self.delivery.say(message, text, ui.back())
        except ValueError:
            await stop_animation()
            if turn_id:
                self.store.fail_turn(turn_id, "unknown")
            await self.delivery.say(
                message,
                "📏 Сообщение не помещается в контекст. Сократите его или нажмите «Новый чат».",
                ui.back(),
            )
        except Exception as exc:
            await stop_animation()
            if turn_id:
                self.store.fail_turn(turn_id, "unknown")
            logger.warning("Сбой генерации/доставки: %s", type(exc).__name__)
            with suppress(TelegramAPIError):
                text = (
                    "📄 Ответ сохранён, но не доставлен полностью. "
                    "Получите его в «Настройках диалога» → «Экспорт»."
                    if turn_id and self.store.turn_status(turn_id) == "complete"
                    else "⚠️ Результат не подтверждён. Запрос мог быть учтён. "
                    "Автоповтора не будет; проверьте «Аккаунт и лимиты»."
                )
                await self.delivery.say(message, text, ui.answer_controls())
        finally:
            await stop_animation()
            if self.jobs.get(user_id) is task:
                self.jobs.pop(user_id, None)
                self.job_ids.pop(user_id, None)

    async def close(self) -> None:
        self.shutting_down = True
        tasks = list(self.jobs.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.gateway.close()


def create_dispatcher(controller: Controller) -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.update.outer_middleware(ClosedAccessMiddleware(controller))
    router = Router(name="shluz-telegram")
    router.message.register(controller.on_message)
    router.callback_query.register(controller.on_callback)
    dispatcher.include_router(router)
    return dispatcher
