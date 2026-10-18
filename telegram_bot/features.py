"""Сервисные действия беты. Повторная генерация требует отдельного подтверждения."""

import html
import secrets
import time
from typing import TYPE_CHECKING

from pydantic import SecretStr

from telegram_bot import ui

if TYPE_CHECKING:
    from telegram_bot.controller import Controller


class Confirmations:
    """Короткоживущие одноразовые намерения, привязанные к пользователю и действию."""

    def __init__(self) -> None:
        self.pending: dict[int, tuple[str, str, str, float]] = {}

    def clear(self, user_id: int) -> None:
        self.pending.pop(user_id, None)

    def issue(self, user_id: int, action: str, target: str = "") -> str:
        now = time.monotonic()
        self.pending = {key: val for key, val in self.pending.items() if val[3] > now}
        nonce = secrets.token_urlsafe(12)
        self.pending[user_id] = (action, target, nonce, now + 120)
        return nonce

    def take(self, user_id: int, action: str, nonce: str) -> str | None:
        record = self.pending.get(user_id)
        if not record or record[0] != action or not secrets.compare_digest(record[2], nonce):
            return None
        self.clear(user_id)
        return record[1] if record[3] > time.monotonic() else None


async def handle_action(
    controller: "Controller",
    message,
    user_id: int,
    token: str,
    action: str,
    value: str,
    *,
    user=None,
    update_id: int | None = None,
) -> bool:
    store, delivery = controller.store, controller.delivery
    local = store.profile(user_id)
    if action == "tariffs":
        catalog = await controller.gateway.plans(token)
        rows = catalog.get("data", [])[:20]
        lines = ["<b>💳 Тарифы и оплата</b>", ""]
        buttons = []
        for plan in rows:
            count = plan.get("monthly_request_limit")
            model_list = plan.get("models", [])
            models = ", ".join(m[:100] for m in model_list[:3]) or "модели пока не настроены"
            if len(model_list) > 3:
                models += f" и ещё {len(model_list) - 3}"
            entry = (
                f"<b>{html.escape(plan['name'][:100])}</b> · "
                f"{html.escape(plan['monthly_price_usd'])} USD/месяц\n"
                f"Запросов: {count if count is not None else 'лимит не задан'}\n"
                f"Модели Telegram: {html.escape(models)}"
            )
            if len("\n\n".join(lines)) + len(entry) > 3200:
                lines.append("Показаны первые тарифы. Остальные уточните у поддержки.")
                break
            lines.append(entry)
            buttons.append([("Оплатить · " + plan["name"][:25], "pay")])
        if not rows:
            lines.append("Тарифы готовятся. Стоимость и модели появятся здесь после настройки.")
            buttons.append([("Оплатить", "pay")])
        lines.append("\nОплата пока не подключена. Деньги не списываются.")
        buttons.append([("☰ Меню", "menu")])
        # Не позволяем очень длинному каталогу превысить лимит сообщения Telegram.
        await delivery.screen(message, "\n\n".join(lines), ui.keyboard(buttons))
    elif action == "pay":
        await delivery.screen(
            message,
            "💳 Оплата ещё не подключена. Деньги не списаны, тариф не изменён.",
            ui.keyboard([[("← Тарифы", "tariffs")], [("☰ Меню", "menu")]]),
        )
    elif action in {"last", "continue"}:
        turn = store.latest_turn(user_id)
        if not turn:
            await delivery.screen(
                message, "💬 Здесь пока нет ответов. Напишите сообщение.", ui.back()
            )
        elif turn["status"] != "complete":
            await delivery.screen(
                message,
                "⚠️ Последний запрос не завершён или его результат не подтверждён. "
                "Автоповтора нет. Проверьте /plan; сохранённые ответы доступны через /export.",
                ui.back(),
            )
        else:
            placeholder = await delivery.say(
                message, "💬 Показываю сохранённый ответ, без генерации…"
            )
            await delivery.answer(
                message, placeholder, turn["assistant_content"], turn_id=turn["id"]
            )
    elif action == "answer":
        turn = store.turn_for(user_id, value)
        if not turn or turn["status"] != "complete":
            await delivery.screen(
                message, "Ответ удалён или недоступен. Откройте /history.", ui.back()
            )
        else:
            await delivery.answer_file(message, turn["assistant_content"], turn["id"])
    elif action == "retry":
        turn = store.retry_source(user_id, value)
        if not turn:
            await delivery.screen(
                message,
                "Повтор доступен только для последнего завершённого ответа в текущем диалоге. "
                "Неподтверждённый запрос повторять автоматически нельзя.",
                ui.back(),
            )
        else:
            nonce = controller.confirmations.issue(user_id, "retry", turn["id"])
            await delivery.screen(
                message,
                "<b>🔄 Получить другой вариант ответа?</b>\n\n"
                "Это <b>новый запрос</b>: он снова расходует квоту и может иметь стоимость. "
                "Старый вариант сохранится, но в дальнейшем контексте будет использован новый.\n"
                f"Модель: <code>{html.escape(turn['model'])}</code>. "
                "Подтверждение действует 2 минуты, до следующего действия.",
                ui.keyboard(
                    [
                        [("🔄 Да, новый запрос", f"retry_yes:{nonce}")],
                        [("Отмена", "menu")],
                    ]
                ),
            )
    elif action == "retry_yes":
        target = controller.confirmations.take(user_id, "retry", value)
        turn = store.retry_source(user_id, target) if target else None
        if not turn or update_id is None:
            await delivery.screen(
                message, "Подтверждение устарело. Откройте /retry заново.", ui.back()
            )
        else:
            await controller.generate(
                message,
                SecretStr(token),
                update_id,
                user=user,
                text=turn["user_content"],
                retry_of=turn["id"],
                model_override=turn["model"],
            )
    elif action == "rename":
        if not value.strip():
            await delivery.screen(
                message,
                "✏️ Напишите <code>/rename Название диалога</code> (до 60 символов).\n"
                "Название не отправляется модели и не расходует квоту.",
                ui.back(),
            )
        else:
            try:
                store.rename_dialog(user_id, local["active_dialog"], value)
            except ValueError:
                await delivery.screen(
                    message, "Название должно содержать от 1 до 60 символов.", ui.back()
                )
            else:
                await delivery.screen(
                    message, f"✅ Название: {html.escape(value.strip())}", ui.back()
                )
    elif action == "forget":
        nonce = controller.confirmations.issue(user_id, "forget")
        await delivery.screen(
            message,
            "<b>🗑 Удалить ВСЕ ваши диалоги на сервере бота?</b>\n\n"
            "Будут удалены тексты, названия и оценки ответов. Восстановить их из бота нельзя. "
            "Аккаунт, настройки, привязка доступа, защита от повторов и учёт расходов сохранятся. "
            "Сообщения в Telegram, данные поставщика и резервные копии этим не удаляются.\n\n"
            "Подтверждение действует 2 минуты, до следующего действия.",
            ui.keyboard(
                [[("🗑 Да, удалить все диалоги", f"forget_yes:{nonce}")], [("Отмена", "menu")]]
            ),
        )
    elif action == "forget_yes":
        valid = controller.confirmations.take(user_id, "forget", value)
        if valid is None:
            await delivery.screen(
                message, "Подтверждение устарело. Откройте /forget заново.", ui.back()
            )
        else:
            store.forget_dialogs(user_id)
            await delivery.screen(
                message, "✅ Все ваши диалоги и оценки на сервере бота удалены.", ui.back()
            )
    elif action == "support":
        turn = store.latest_turn(user_id)
        reference = (
            f"\nПоследний запрос: <code>{turn['id']}</code>\n"
            f"Локальный статус: <code>{turn['status']}</code>"
            if turn
            else ""
        )
        await delivery.screen(
            message,
            "<b>🛟 Поддержка</b>\n\n"
            + html.escape(controller.settings.support_contact)
            + f"\nВаш Telegram ID: <code>{user_id}</code>"
            + reference
            + "\n\nСообщите время и описание проблемы. Не присылайте API-ключи и пароли. "
            "Тексты диалогов автоматически в поддержку не передаются.",
            ui.back(),
        )
    else:
        return False
    return True
