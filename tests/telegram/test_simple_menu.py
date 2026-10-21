"""Регрессии нового компактного меню; сначала проверяются на прежней реализации."""

from telegram_bot import ui
from telegram_bot.runtime import COMMANDS


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_menu_has_dialog_settings_and_faq_without_continue_or_data():
    menu = buttons(ui.menu())
    assert any(b.text == "⚙️ Настройки диалога" for b in menu)
    assert any(b.text == "❓ FAQ" and b.callback_data == "faq" for b in menu)
    assert not {"continue", "privacy", "help"} & {b.callback_data for b in menu}
    assert "privacy" not in {b.callback_data for b in buttons(ui.settings_controls("short"))}
    assert not {"retry", "last", "privacy", "help"} & {name for name, _ in COMMANDS}


def test_answer_delete_button_is_bound_to_its_turn():
    turn = "a" * 32
    controls = buttons(ui.answer_controls(turn))
    assert controls[0].text == "🗑 Удалить этот диалог"
    assert controls[0].callback_data == f"delete_turn:{turn}"
    assert not any(b.callback_data.startswith(("retry:", "answer:", "up:", "down:"))
                   for b in controls)
    assert all(len(b.callback_data.encode()) <= 64 for b in controls)
    assert not any(b.callback_data.startswith("delete") for b in buttons(ui.answer_controls()))


def test_history_has_delete_all_on_each_page():
    dialogs = [{"id": str(i), "title": f"Диалог {i}"} for i in range(9)]
    for offset in (0, 8):
        controls = buttons(ui.history_controls(dialogs, "0", offset))
        assert any(b.text == "🗑 Удалить все диалоги" and b.callback_data == "forget"
                   for b in controls)


def test_profile_shows_only_noclip_id_not_telegram_identity():
    remote = {"account": {"id": "internal-id", "display_name": "Telegram User"}, "plan": None}
    text = ui.profile_text(remote, {"user_id": 12345678, "model": "mock-chat"})
    assert "Ваш ID: <code>internal-id</code>" in text
    assert "Telegram User" not in text and "12345678" not in text and "Telegram ID" not in text
