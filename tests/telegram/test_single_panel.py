"""Навигация редактирует одну панель, а не историю ответов модели."""

import asyncio
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import Message

from telegram_bot import ui
from telegram_bot.delivery import Delivery
from telegram_bot.storage import Store
from tests.telegram.test_bot import RecordingSession, count_usage, environment, incoming, no_wait


def methods(session, name):
    return [call for call in session.calls if call.__api_method__ == name]


def origin(bot, message_id=1, chat_id=100):
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat={"id": chat_id, "type": "private"},
        from_user={"id": chat_id, "is_bot": False, "first_name": "Owner"},
        text="/menu",
    ).as_(bot)


def test_navigation_and_text_commands_reuse_one_panel(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        for update_id, action in enumerate(
            ["settings", "history:0", "models", "profile", "tariffs", "pay", "faq", "menu"], 2
        ):
            await dp.feed_update(bot, incoming(update_id, callback=action))
        await dp.feed_update(bot, incoming(20, "/menu"))
        await dp.feed_update(bot, incoming(21, callback="rename"))
        await dp.feed_update(bot, incoming(22, "Удобное название"))
        assert len(methods(session, "sendMessage")) == 1
        assert len({c.message_id for c in methods(session, "editMessageText")}) == 1
        assert count_usage(client) == 0
        assert ctrl.store.dialogs(100)[0]["title"] == "Удобное название"

    asyncio.run(scenario())


def test_button_under_answer_moves_panel_without_editing_answer(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/menu"))
        panel_id = session.sequence
        await dp.feed_update(bot, incoming(2, "Do not erase this answer"))
        answer_id = methods(session, "editMessageText")[-1].message_id
        before = len(session.calls)
        await dp.feed_update(bot, incoming(3, callback="menu"))
        calls = session.calls[before:]
        assert len([c for c in calls if c.__api_method__ == "sendMessage"]) == 1
        assert ctrl.store.screen_id(bot.id, 100) > answer_id
        assert [c.message_id for c in calls if c.__api_method__ == "deleteMessage"] == [panel_id]
        assert not any(c.__api_method__ == "editMessageText" and c.message_id == answer_id
                       for c in calls)
        assert count_usage(client) == 1
        assert "Do not erase" in str(ctrl.store.history(100))

    asyncio.run(scenario())


def test_panel_survives_restart_and_is_scoped_to_bot_and_chat(tmp_path):
    async def scenario():
        store = Store(tmp_path / "bot.sqlite")
        session = RecordingSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=store, sleep=no_wait)
        first = await delivery.screen(origin(bot), "Menu", ui.menu())
        restarted = Delivery(store=Store(store.path), sleep=no_wait)
        await restarted.screen(origin(bot, 2), "Settings", ui.back())
        assert len(methods(session, "sendMessage")) == 1
        assert methods(session, "editMessageText")[-1].message_id == first.message_id
        await restarted.screen(origin(bot, 3, 200), "Other user", ui.menu())
        other_bot = Bot("654321:" + "B" * 35, session=session)
        await restarted.screen(origin(other_bot, 4), "Other bot", ui.menu())
        assert len(methods(session, "sendMessage")) == 3
        assert store.screen_id(bot.id, 100) == first.message_id
        assert store.screen_id(other_bot.id, 100) != first.message_id
        assert store.screen_id(bot.id, 200) != first.message_id

    asyncio.run(scenario())


def test_concurrent_navigation_cannot_duplicate_first_panel(tmp_path):
    class SlowSession(RecordingSession):
        async def make_request(self, bot, method, timeout=None):
            await asyncio.sleep(0.01)
            return await super().make_request(bot, method, timeout)

    async def scenario():
        session = SlowSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        await asyncio.gather(*(delivery.screen(origin(bot, i), f"Page {i}") for i in range(8)))
        assert len(methods(session, "sendMessage")) == 1
        assert len(methods(session, "editMessageText")) == 7
        assert len({c.message_id for c in methods(session, "editMessageText")}) == 1

    asyncio.run(scenario())


class ErrorSession(RecordingSession):
    error = None

    async def make_request(self, bot, method, timeout=None):
        if method.__api_method__ == "editMessageText" and self.error is not None:
            error, self.error = self.error, None
            self.calls.append(method)
            raise error(method=method)
        return await super().make_request(bot, method, timeout)


@pytest.mark.parametrize("description", ["message to edit not found", "message can't be edited"])
def test_missing_panel_is_replaced_once_without_touching_answer(tmp_path, description):
    async def scenario():
        session = ErrorSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        store = Store(tmp_path / "bot.sqlite")
        delivery = Delivery(store=store, sleep=no_wait)
        old = await delivery.screen(origin(bot), "Old", ui.menu())
        session.error = lambda **kw: TelegramBadRequest(message=description, **kw)
        replacement = await delivery.screen(origin(bot, 55), "Replacement", ui.back())
        await delivery.screen(origin(bot, 56), "Next", ui.menu())
        assert len(methods(session, "sendMessage")) == 2
        assert replacement.message_id != old.message_id
        assert store.screen_id(bot.id, 100) == replacement.message_id
        assert methods(session, "editMessageText")[-1].message_id == replacement.message_id
        assert [m.message_id for m in methods(session, "deleteMessage")] == [old.message_id]

    asyncio.run(scenario())


def test_not_modified_is_success_without_new_message(tmp_path):
    async def scenario():
        session = ErrorSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        old = await delivery.screen(origin(bot), "Same", ui.menu())
        session.error = lambda **kw: TelegramBadRequest(message="message is not modified", **kw)
        same = await delivery.screen(origin(bot, 8), "Same", ui.menu())
        assert same.message_id == old.message_id
        assert len(methods(session, "sendMessage")) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("network", [False, True])
def test_uncertain_or_invalid_edit_does_not_flood_new_messages(tmp_path, network):
    async def scenario():
        session = ErrorSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        await delivery.screen(origin(bot), "Menu", ui.menu())
        error_type = TelegramNetworkError if network else TelegramBadRequest
        session.error = lambda **kw: error_type(
            message="network" if network else "can't parse entities", **kw
        )
        with pytest.raises(error_type):
            await delivery.screen(origin(bot, 8), "<invalid>")
        assert len(methods(session, "sendMessage")) == 1

    asyncio.run(scenario())


def test_rate_limit_retries_same_panel_not_generation(tmp_path):
    async def scenario():
        session = ErrorSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        panel = await delivery.screen(origin(bot), "Menu", ui.menu())
        session.error = lambda **kw: TelegramRetryAfter(message="retry", retry_after=1, **kw)
        await delivery.screen(origin(bot, 9), "Settings", ui.back())
        assert len(methods(session, "sendMessage")) == 1
        assert [c.message_id for c in methods(session, "editMessageText")] == [panel.message_id] * 2

    asyncio.run(scenario())
