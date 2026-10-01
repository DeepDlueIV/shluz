"""Русскоязычные экраны и кнопки Telegram, без выдуманных платежей или балансов."""

import html
from datetime import datetime
from hashlib import sha256

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

STYLE_LABELS = {"balanced": "Обычный", "short": "Краткий", "detailed": "Подробный"}


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    for row in rows:
        for _, value in row:
            if len(value.encode("utf-8")) > 64:
                raise ValueError("Callback payload exceeds Telegram limit")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=value) for text, value in row]
            for row in rows
        ]
    )


def menu() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [("✨ Новый чат", "new"), ("💬 Продолжить", "continue")],
            [("🤖 Модели", "models:0"), ("🗂 Диалоги", "history:0")],
            [("📊 Мой тариф", "plan"), ("💳 Лимиты", "limits")],
            [("⚙️ Настройки", "settings"), ("❓ Помощь", "help")],
        ]
    )


def answer_controls() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [("✨ Новый чат", "new"), ("🤖 Модель", "models:0")],
            [("📄 Экспорт", "export"), ("☰ Меню", "menu")],
        ]
    )


def cancel_controls() -> InlineKeyboardMarkup:
    return keyboard([[("⏹ Остановить ожидание", "cancel")]])


def back() -> InlineKeyboardMarkup:
    return keyboard([[("☰ Главное меню", "menu")]])


def settings_controls(style: str) -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(f"{'✓ ' if key == style else ''}{label}", f"style:{key}")]
            for key, label in STYLE_LABELS.items()
        ]
        + [
            [("🧹 Очистить диалог", "clear"), ("🗑 Удалить диалог", "delete")],
            [("🔐 О данных", "privacy"), ("☰ Меню", "menu")],
        ]
    )


def model_key(model: str) -> str:
    return sha256(model.encode("utf-8")).hexdigest()[:16]


def models_controls(models: list[str], selected: str | None, offset: int = 0):
    rows = [
        [(f"{'✓ ' if model == selected else ''}{model[:55]}", f"model:{model_key(model)}")]
        for model in models[offset : offset + 8]
    ]
    paging = []
    if offset:
        paging.append(("← Назад", f"models:{max(0, offset - 8)}"))
    if offset + 8 < len(models):
        paging.append(("Далее →", f"models:{offset + 8}"))
    if paging:
        rows.append(paging)
    return keyboard(rows + [[("☰ Меню", "menu")]])


def history_controls(dialogs: list[dict], active: str, offset: int):
    rows = [
        [(f"{'✓ ' if item['id'] == active else ''}{item['title'][:45]}", f"dialog:{item['id']}")]
        for item in dialogs[:8]
    ]
    paging = []
    if offset:
        paging.append(("← Назад", f"history:{max(0, offset - 8)}"))
    if len(dialogs) > 8:
        paging.append(("Далее →", f"history:{offset + 8}"))
    if paging:
        rows.append(paging)
    return keyboard(rows + [[("✨ Новый чат", "new"), ("☰ Меню", "menu")]])


def profile_text(remote: dict, local: dict) -> str:
    account, plan = remote["account"], remote["plan"]
    lines = [
        "<b>👤 Ваш аккаунт</b>",
        html.escape(account["display_name"]),
        f"Telegram ID: <code>{local['user_id']}</code>",
        f"Модель: <code>{html.escape(local['model'] or 'не выбрана')}</code>",
        "",
    ]
    if plan is None:
        return "\n".join(lines + ["📋 Тариф пока не назначен. Свяжитесь с администратором."])
    lines.append(f"<b>📊 {html.escape(plan['name'])}</b>")
    for key, label in (("requests", "Запросы"), ("credits", "Кредиты")):
        value = remote["allowance"][key]
        limit = value["limit"]
        lines.append(
            f"{label}: {value['used']} использовано"
            + (
                f" из {limit} · осталось {value['remaining']}"
                if limit is not None
                else " · лимит по этому показателю не задан"
            )
        )
        if value["pending"]:
            lines.append(f"В обработке / резерве: {value['pending']}")
        if limit:
            filled = min(10, int(10 * (value["used"] + value["pending"]) / limit))
            lines.append("▰" * filled + "▱" * (10 - filled))
    reset = datetime.fromisoformat(remote["allowance"]["resets_at"])
    lines.extend(
        [
            "",
            f"Обновление квот: {reset:%d.%m.%Y %H:%M} UTC.",
            "Кредиты — единицы доступа, не денежный баланс.",
        ]
    )
    if remote["allowance"]["scope"] == "account":
        lines.append("Сейчас эти квоты общие для каналов вашего аккаунта.")
    return "\n".join(lines)
