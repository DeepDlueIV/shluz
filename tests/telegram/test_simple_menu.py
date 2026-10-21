"""Проверки компактного меню и удаления строго выбранного диалога."""

import asyncio
import time

from telegram_bot import ui
from telegram_bot.runtime import COMMANDS
from tests.telegram.test_bot import callback_value, count_usage, environment, incoming


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
    assert not any(
        b.callback_data.startswith(("retry:", "answer:", "up:", "down:")) for b in controls
    )
    assert all(len(b.callback_data.encode()) <= 64 for b in controls)
    assert not any(b.callback_data.startswith("delete") for b in buttons(ui.answer_controls()))


def test_history_has_delete_all_on_each_page():
    dialogs = [{"id": str(i), "title": f"Диалог {i}"} for i in range(9)]
    for offset in (0, 8):
        controls = buttons(ui.history_controls(dialogs, "0", offset))
        assert any(
            b.text == "🗑 Удалить все диалоги" and b.callback_data == "forget" for b in controls
        )


def test_profile_shows_only_noclip_id_not_telegram_identity():
    remote = {"account": {"id": "internal-id", "display_name": "Telegram User"}, "plan": None}
    text = ui.profile_text(remote, {"user_id": 12345678, "model": "mock-chat"})
    assert "Ваш ID: <code>internal-id</code>" in text
    assert "Telegram User" not in text and "12345678" not in text and "Telegram ID" not in text


def test_menu_names_active_dialog_and_keeps_plain_message_continuation(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "first"))
        dialog = ctrl.store.profile(100)["active_dialog"]
        ctrl.store.rename_dialog(100, dialog, "Проект <test>")
        session.calls.clear()
        await dp.feed_update(bot, incoming(2, callback="menu"))
        assert "Сейчас открыт:" in session.texts() and "Проект &lt;test&gt;" in session.texts()
        assert "Просто напишите" in session.texts()
        await dp.feed_update(bot, incoming(3, "second"))
        assert len(ctrl.store.history(100, dialog)) == 4 and count_usage(client) == 2

    asyncio.run(scenario())


def test_delete_from_old_answer_targets_original_not_current_and_is_scoped(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "original"))
        original = ctrl.store.profile(100)["active_dialog"]
        action = callback_value(session, "delete_turn:")
        assert action
        await dp.feed_update(bot, incoming(2, callback="new"))
        await dp.feed_update(bot, incoming(3, "keep this"))
        current = ctrl.store.profile(100)["active_dialog"]
        session.calls.clear()
        await dp.feed_update(bot, incoming(4, callback=action, user_id=200))
        assert not callback_value(session, "delete_yes:") and ctrl.store.history(100, original)
        await dp.feed_update(bot, incoming(5, callback=action))
        confirm = callback_value(session, "delete_yes:")
        assert confirm and ctrl.store.history(100, original)
        assert "Сообщения в Telegram останутся" in session.texts()
        await dp.feed_update(bot, incoming(6, callback=confirm))
        assert not ctrl.store.history(100, original)
        assert ctrl.store.profile(100)["active_dialog"] == current
        assert ctrl.store.history(100, current)
        await dp.feed_update(bot, incoming(7, callback=confirm))
        assert ctrl.store.history(100, current) and count_usage(client) == 2

    asyncio.run(scenario())


def test_old_delete_confirmation_does_not_remove_new_turn(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "old"))
        action = callback_value(session, "delete_turn:")
        assert action
        await dp.feed_update(bot, incoming(2, callback=action))
        confirm = callback_value(session, "delete_yes:")
        await dp.feed_update(bot, incoming(3, "new protected"))
        await dp.feed_update(bot, incoming(4, callback=confirm))
        assert "new protected" in str(ctrl.store.history(100)) and count_usage(client) == 2

    asyncio.run(scenario())


def test_delete_all_from_history_preserves_account_and_other_user(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "mine"))
        await dp.feed_update(bot, incoming(2, "other", user_id=200))
        before = ctrl.store.profile(100)
        await dp.feed_update(bot, incoming(3, callback="history:0"))
        assert callback_value(session, "forget") == "forget"
        await dp.feed_update(bot, incoming(4, callback="forget"))
        confirm = callback_value(session, "forget_yes:")
        assert confirm and ctrl.store.history(100)
        await dp.feed_update(bot, incoming(5, callback=confirm))
        after = ctrl.store.profile(100)
        assert before["account_id"] == after["account_id"]
        assert before["model"] == after["model"] and before["style"] == after["style"]
        assert not ctrl.store.history(100) and ctrl.store.history(200)
        assert count_usage(client) == 2
        assert len(ctrl.store.dialogs(100)) == 1

    asyncio.run(scenario())


def test_rename_uses_next_text_without_slash_or_generation(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        session.calls.clear()
        await dp.feed_update(bot, incoming(2, callback="rename"))
        assert "/rename" not in session.texts()
        await dp.feed_update(bot, incoming(3, "Мой <проект>"))
        assert ctrl.store.dialogs(100)[0]["title"] == "Мой <проект>"
        assert "Мой &lt;проект&gt;" in session.texts()
        assert count_usage(client) == 0 and not ctrl.store.history(100)
        await dp.feed_update(bot, incoming(4, "hello"))
        assert count_usage(client) == 1

    asyncio.run(scenario())


def test_invalid_rename_keeps_prompt_and_never_generates(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        await dp.feed_update(bot, incoming(2, callback="rename"))
        await dp.feed_update(bot, incoming(3, "x" * 61))
        assert count_usage(client) == 0
        await dp.feed_update(bot, incoming(4, "Valid name"))
        assert ctrl.store.dialogs(100)[0]["title"] == "Valid name"
        assert count_usage(client) == 0

    asyncio.run(scenario())


def test_rename_cancel_navigation_and_expiry_are_not_hidden_generations(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        await dp.feed_update(bot, incoming(2, callback="rename"))
        assert callback_value(session, "rename_cancel")
        await dp.feed_update(bot, incoming(3, callback="rename_cancel"))
        await dp.feed_update(bot, incoming(4, "normal message"))
        assert count_usage(client) == 1
        await dp.feed_update(bot, incoming(5, callback="rename"))
        dialog, _ = ctrl.rename_targets[100]
        ctrl.rename_targets[100] = (dialog, time.monotonic() - 1)
        await dp.feed_update(bot, incoming(6, "expired name"))
        assert count_usage(client) == 1 and "expired name" not in str(ctrl.store.history(100))
        await dp.feed_update(bot, incoming(7, callback="rename"))
        await dp.feed_update(bot, incoming(8, callback="new"))
        await dp.feed_update(bot, incoming(9, "new conversation"))
        assert count_usage(client) == 2

    asyncio.run(scenario())


def test_settings_show_actual_retention_and_faq_not_command_manual(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path, history_retention_days=17)
        for number, action in enumerate(["settings", "faq", "help", "privacy"], 1):
            await dp.feed_update(bot, incoming(number, callback=action))
        text = session.texts()
        assert "17" in text and "История" in text and "FAQ" in text
        assert "telegram-privacy.md" in text  # Честная политика доступна без отдельного меню.
        assert "/clear" not in text and "/delete" not in text and "/forget" not in text
        assert "сквозное шифрование есть" not in text
        assert "на сервере бота" not in text
        assert count_usage(client) == 0

    asyncio.run(scenario())


def test_confirmation_copy_has_scope_without_storage_implementation(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "example"))
        session.calls.clear()
        for number, action in enumerate(["clear", "delete", "forget"], 2):
            await dp.feed_update(bot, incoming(number, callback=action))
        assert "на сервере" not in session.texts()
        assert "Сообщения в Telegram останутся" in session.texts()
        assert count_usage(client) == 1 and ctrl.store.history(100)

    asyncio.run(scenario())


def test_delete_answer_is_blocked_while_response_is_in_progress(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "old"))
        action = callback_value(session, "delete_turn:")
        assert action
        started = asyncio.Event()

        async def slow(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        ctrl.gateway.chat = slow
        task = asyncio.create_task(dp.feed_update(bot, incoming(2, "in progress")))
        try:
            await asyncio.wait_for(started.wait(), 3)
            session.calls.clear()
            await dp.feed_update(bot, incoming(3, callback=action))
            assert not callback_value(session, "delete_yes:")
            assert ctrl.store.history(100) and not task.done()
        finally:
            await dp.feed_update(bot, incoming(4, "/cancel"))
            await task

    asyncio.run(scenario())


def test_slow_stream_shows_text_before_final_answer(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)

        async def slow(token, model, messages, request_id, *, on_delta):
            await on_delta("Первые слова")
            for _ in range(30):
                await asyncio.sleep(0.01)
                if "Первые слова" in session.texts():
                    break
            assert "Первые слова" in session.texts()
            assert ctrl.store.latest_turn(100)["status"] == "processing"
            await on_delta("Первые слова и окончание")
            return "Первые слова и окончание"

        ctrl.gateway.chat = slow
        await dp.feed_update(bot, incoming(1, "test stream"))
        assert "Первые слова и окончание" in session.texts()
        assert ctrl.store.latest_turn(100)["status"] == "complete"
        assert callback_value(session, "delete_turn:")

    asyncio.run(scenario())
