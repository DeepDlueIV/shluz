"""Сценарии закрытого бота: меню, диалоги, ограничения, отмена и защита от повторов."""

import asyncio
import html
import logging
import time
from contextlib import suppress

from aiogram import BaseMiddleware, Dispatcher, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, Update
from pydantic import SecretStr

from telegram_bot import ui
from telegram_bot.config import AccessRegistry, BotSettings
from telegram_bot.delivery import Delivery
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
        token = self.controller.access.token_for(sender.id)
        if token is None:
            if callback:
                await callback.answer("Доступ к закрытой бете не выдан.", show_alert=True)
            else:
                await self.controller.delivery.say(
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
            await self.controller.delivery.say(message, ERROR_TEXT[exc.code], ui.back())
        except DialogLimit:
            await self.controller.delivery.say(
                message,
                "🗂 Достигнут лимит диалогов. Удалите ненужный через «Настройки».",
                ui.back(),
            )
        except Exception as exc:
            # Не логируем тело сообщения, токены, URL запроса или traceback с секретами.
            logger.warning("Ошибка обработки Telegram: %s", type(exc).__name__)
            with suppress(TelegramAPIError):
                await self.controller.delivery.say(
                    message,
                    "⚠️ Не удалось выполнить действие. Попробуйте открыть /menu.",
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
        self.delivery = Delivery(max_parts=settings.max_text_parts, sleep=sleep)
        self.jobs: dict[int, asyncio.Task] = {}
        self.last_request: dict[int, float] = {}
        self.shutting_down = False

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

    async def show_menu(self, message: Message, user_id: int) -> None:
        profile = self.store.profile(user_id)
        model = html.escape(profile["model"] or "будет выбрана при первом сообщении")
        text = (
            "<b>🧪 Shluz · закрытая бета</b>\n\n"
            "Напишите сообщение — отвечу в контексте текущего диалога.\n"
            f"Модель: <code>{model}</code>\n\n"
            "«Новый чат» начинает отдельный диалог. Предыдущие остаются в «Диалогах»."
        )
        if profile["model"] == "mock-chat":
            text += "\n\n🧩 mock-chat — тестовый ответ, не настоящая нейросеть."
        await self.delivery.say(message, text, ui.menu())

    async def on_message(
        self, message: Message, gateway_token: SecretStr, telegram_update_id: int
    ) -> None:
        user_id = message.from_user.id
        text = message.text
        if text is None or not text.strip():
            await self.delivery.say(
                message,
                "📝 Пока поддерживаются только текстовые сообщения. "
                "Файлы, фото и голосовые модели не отправляются.",
                ui.back(),
            )
            return
        if text.startswith("/"):
            command = text.split()[0].split("@")[0][1:].lower()
            await self.action(message, user_id, gateway_token.get_secret_value(), command)
            return
        if len(text) > self.settings.max_input_chars:
            await self.delivery.say(
                message,
                "📏 Сообщение слишком большое. Разделите его на несколько более коротких запросов.",
                ui.back(),
            )
            return
        await self.generate(message, gateway_token, telegram_update_id)

    async def on_callback(
        self, callback: CallbackQuery, gateway_token: SecretStr, telegram_update_id: int
    ) -> None:
        await callback.answer()
        action, _, value = (callback.data or "").partition(":")
        await self.action(
            callback.message,
            callback.from_user.id,
            gateway_token.get_secret_value(),
            action,
            value,
            user=callback.from_user,
        )

    async def action(
        self, message: Message, user_id: int, token: str, action: str, value: str = "", *, user=None
    ) -> None:
        local = self.store.profile(user_id)
        busy_actions = {
            "new",
            "clear",
            "delete",
            "clear_yes",
            "delete_yes",
            "dialog",
            "model",
            "style",
        }
        if action in busy_actions and user_id in self.jobs:
            await self.delivery.say(
                message,
                "⏳ Ответ уже готовится. Дождитесь его или нажмите /cancel.",
                ui.cancel_controls(),
            )
            return
        if action in {"start", "menu", "continue"}:
            if action == "start":
                await self.linked_profile(message, token, user=user)
                if local["model"] is None:
                    models = await self.gateway.models(token)
                    selected = (
                        self.settings.default_model
                        if self.settings.default_model in models
                        else models[0]
                    )
                    self.store.set_model(user_id, selected)
            await self.show_menu(message, user_id)
        elif action == "new":
            self.store.new_dialog(user_id)
            await self.delivery.say(
                message, "✨ Новый диалог открыт. О чём поговорим?", ui.answer_controls()
            )
        elif action in {"plan", "balance", "limits", "profile"}:
            remote = await self.linked_profile(message, token, user=user)
            await self.delivery.say(message, ui.profile_text(remote, local), ui.back())
        elif action == "models":
            models = await self.gateway.models(token)
            offset = int(value) if value.isdigit() else 0
            offset = min(max(0, offset), max(0, len(models) - 1))
            await self.delivery.say(
                message,
                "<b>🤖 Выберите модель</b>\n"
                "Список приходит из Shluz. Смена модели сохраняет контекст.",
                ui.models_controls(models, local["model"], offset),
            )
        elif action == "model":
            models = await self.gateway.models(token)
            matches = [model for model in models if ui.model_key(model) == value]
            if len(matches) != 1:
                await self.delivery.say(
                    message, "🤖 Список изменился. Откройте /models ещё раз.", ui.back()
                )
                return
            self.store.set_model(user_id, matches[0])
            await self.delivery.say(
                message,
                f"✅ Выбрана модель <code>{html.escape(matches[0])}</code>.",
                ui.answer_controls(),
            )
        elif action == "settings":
            await self.delivery.say(
                message,
                "<b>⚙️ Настройки</b>\nВыберите стиль ответа. Содержимое диалога не изменяется.",
                ui.settings_controls(local["style"]),
            )
        elif action == "style":
            if value not in ui.STYLE_LABELS:
                await self.delivery.say(message, "Кнопка устарела. Откройте /settings.", ui.back())
                return
            self.store.set_style(user_id, value)
            await self.delivery.say(
                message, f"✅ Стиль ответа: {ui.STYLE_LABELS[value]}.", ui.settings_controls(value)
            )
        elif action == "history":
            offset = min(int(value), self.settings.max_dialogs) if value.isdigit() else 0
            dialogs = self.store.dialogs(user_id, offset=offset, limit=9)
            await self.delivery.say(
                message,
                "<b>🗂 Ваши диалоги</b>\nВыберите диалог для продолжения.",
                ui.history_controls(dialogs, local["active_dialog"], offset),
            )
        elif action == "dialog":
            if not self.store.activate_dialog(user_id, value):
                await self.delivery.say(
                    message, "Этот диалог недоступен. Откройте /history.", ui.back()
                )
                return
            history = self.store.history(user_id)
            text = "💬 Диалог выбран. Следующее сообщение продолжит его."
            if history:
                excerpt = history[-1]["content"][:700]
                text += "\n\n<b>Начало последнего ответа:</b>\n" + html.escape(excerpt)
                text += "\n\nПолная история доступна через /export."
            await self.delivery.say(message, text, ui.answer_controls())
        elif action in {"clear", "delete"}:
            verb = (
                "Очистить историю текущего диалога"
                if action == "clear"
                else "Удалить текущий диалог"
            )
            await self.delivery.say(
                message,
                f"<b>🗑 {verb}?</b>\n"
                "Это удалит историю на сервере бота. Сообщения в Telegram "
                "и журнал расходов шлюза останутся.",
                ui.keyboard(
                    [
                        [("Да, удалить", f"{action}_yes:{local['active_dialog']}")],
                        [("Отмена", "menu")],
                    ]
                ),
            )
        elif action in {"clear_yes", "delete_yes"}:
            change = self.store.clear_dialog if action == "clear_yes" else self.store.delete_dialog
            if change(user_id, value):
                await self.delivery.say(
                    message, "✅ История выбранного диалога удалена.", ui.answer_controls()
                )
            else:
                await self.delivery.say(message, "Диалог уже удалён или недоступен.", ui.back())
        elif action == "export":
            if not self.store.history(user_id):
                await self.delivery.say(
                    message, "📄 В текущем диалоге пока нет завершённых ответов.", ui.back()
                )
                return
            await self.delivery.export(message, self.store.export_dialog(user_id))
        elif action == "cancel":
            job = self.jobs.get(user_id)
            if job:
                job.cancel()
            else:
                await self.delivery.say(
                    message, "Сейчас нет запроса, которого нужно ждать.", ui.back()
                )
        elif action == "privacy":
            await self.delivery.say(
                message,
                "<b>🔐 О ваших данных</b>\n\n"
                "Бот хранит текст диалогов на сервере для продолжения разговора. "
                f"Срок хранения — {self.settings.history_retention_days} дней; "
                f"до {self.settings.max_saved_turns} обменов в каждом диалоге. "
                "История не имеет сквозного шифрования. Она передаётся через Shluz выбранному "
                "поставщику модели. Не отправляйте пароли и другие секреты.\n\n"
                "/clear очищает текущий диалог, /delete удаляет его. Сообщения в Telegram "
                "удаляйте средствами Telegram; резервные копии сервера управляются отдельно. "
                "Удаление истории не отменяет уже учтённый расход.",
                ui.back(),
            )
        elif action == "help":
            await self.delivery.say(
                message,
                "<b>❓ Как пользоваться Shluz</b>\n\n"
                "Пишите обычным текстом. Контекст сохраняется в текущем диалоге.\n"
                "/new — новый диалог\n/models — выбор модели\n/history — ваши диалоги\n"
                "/plan — тариф и лимиты\n/settings — стиль ответа и очистка\n"
                "/export — полный текст диалога\n/cancel — остановить ожидание\n"
                "/privacy — хранение данных\n/menu — главное меню\n\n"
                "Если запрос уже ушёл модели, отмена ожидания не гарантирует отмену расхода. "
                "Платежи в этой версии не принимаются.\n\n"
                + html.escape(self.settings.support_contact),
                ui.back(),
            )
        else:
            await self.delivery.say(
                message, "Неизвестная команда или устаревшая кнопка. Откройте /menu.", ui.back()
            )

    async def generate(self, message: Message, secret: SecretStr, update_id: int) -> None:
        user_id = message.from_user.id
        if user_id in self.jobs:
            await self.delivery.say(
                message,
                "⏳ Ответ уже готовится. Дождитесь его или нажмите /cancel.",
                ui.cancel_controls(),
            )
            return
        if len(self.jobs) >= self.settings.max_parallel_requests:
            await self.delivery.say(
                message, "⏳ Сейчас все места обработки заняты. Попробуйте чуть позже.", ui.back()
            )
            return
        if time.monotonic() - self.last_request.get(user_id, 0) < self.settings.cooldown_seconds:
            await self.delivery.say(
                message, "⏳ Слишком быстро. Подождите пару секунд перед новым запросом.", ui.back()
            )
            return
        task = asyncio.current_task()
        self.jobs[user_id] = task
        waiting, spinner, turn_id = None, None, None
        stop = asyncio.Event()

        async def stop_animation() -> None:
            stop.set()
            if spinner:
                spinner.cancel()
                with suppress(asyncio.CancelledError):
                    await spinner

        try:
            token = secret.get_secret_value()
            remote = await self.linked_profile(message, token)
            if remote["plan"] is None:
                raise GatewayError("subscription_required")
            models = await self.gateway.models(token)
            local = self.store.profile(user_id)
            model = local["model"]
            if model is None:
                model = (
                    self.settings.default_model
                    if self.settings.default_model in models
                    else models[0]
                )
                self.store.set_model(user_id, model)
            if model not in models:
                raise GatewayError("model_not_found")
            messages, trimmed = prepare_messages(
                self.store.history(user_id),
                message.text,
                style=local["style"],
                budget=self.settings.context_char_limit,
            )
            latest = self.access.token_for(user_id)
            if latest is None or latest.get_secret_value() != token:
                raise GatewayError("unauthorized")
            waiting = await self.delivery.waiting(message)
            turn_id = self.store.begin_turn(user_id, update_id, message.text, model)
            if turn_id is None:
                await self.delivery.edit(
                    message, waiting, "Этот запрос уже обработан. Проверьте /history."
                )
                return
            self.last_request[user_id] = time.monotonic()
            spinner = asyncio.create_task(self.delivery.animate(message, waiting, stop))
            answer = await self.gateway.chat(token, model, messages, turn_id)
            self.store.complete_turn(turn_id, answer)
            await stop_animation()
            await self.delivery.answer(message, waiting, answer)
            if trimmed:
                await self.delivery.say(
                    message,
                    "ℹ️ В запрос вошла только последняя часть длинной истории. "
                    "Полная сохранённая история доступна через /export.",
                )
        except asyncio.CancelledError:
            await stop_animation()
            if turn_id:
                self.store.fail_turn(turn_id, "unknown")
            if not self.shutting_down:
                text = (
                    "⏹ Ожидание остановлено. Запрос мог быть выполнен и учтён шлюзом. "
                    "Автоматического повтора не будет. Проверьте /plan перед новым запросом."
                )
                if turn_id and self.store.turn_status(turn_id) == "complete":
                    text = "⏹ Показ остановлен. Ответ уже сохранён — он доступен через /export."
                with suppress(TelegramAPIError):
                    if waiting:
                        await self.delivery.edit(
                            message, waiting, text, ui.answer_controls(), plain_text=text
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
                }
                self.store.fail_turn(turn_id, "unknown" if uncertain else "failed")
            text = ERROR_TEXT.get(exc.code, ERROR_TEXT["unavailable"])
            if waiting:
                await self.delivery.edit(message, waiting, text, ui.answer_controls())
            else:
                await self.delivery.say(message, text, ui.back())
        except ValueError:
            await stop_animation()
            if turn_id:
                self.store.fail_turn(turn_id, "unknown")
            await self.delivery.say(
                message,
                "📏 Сообщение не помещается в контекст. Сократите его или начните /new.",
                ui.back(),
            )
        except Exception as exc:
            await stop_animation()
            if turn_id:
                self.store.fail_turn(turn_id, "unknown")
            logger.warning("Сбой генерации/доставки: %s", type(exc).__name__)
            with suppress(TelegramAPIError):
                text = (
                    "📄 Ответ сохранён, но не доставлен полностью. Получите его через /export."
                    if turn_id and self.store.turn_status(turn_id) == "complete"
                    else "⚠️ Результат не подтверждён. Запрос мог быть учтён. "
                    "Автоповтора не будет; проверьте /plan."
                )
                await self.delivery.say(message, text, ui.answer_controls())
        finally:
            await stop_animation()
            if self.jobs.get(user_id) is task:
                self.jobs.pop(user_id, None)

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
