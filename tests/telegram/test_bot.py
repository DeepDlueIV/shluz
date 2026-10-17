import asyncio
import json
from datetime import UTC, datetime

import httpx
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update, User
from sqlalchemy import func, select

from app.db.models import Account, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token

TOKEN = "shluz_" + "a" * 43
OTHER_TOKEN = "shluz_" + "b" * 43


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.sequence = 1000
        self.fail_final_edit = False

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        from aiogram.exceptions import TelegramNetworkError

        self.calls.append(method)
        name = method.__api_method__
        if name == "getMe":
            return User(id=999, is_bot=True, first_name="Shluz", username="test_shluz_bot")
        if (
            self.fail_final_edit
            and name == "editMessageText"
            and "Shluz mock reply" in (method.text or "")
        ):
            raise TelegramNetworkError(method=method, message="test connection lost")
        if name in {"sendMessage", "editMessageText", "sendDocument"}:
            self.sequence += 1
            return Message(
                message_id=getattr(method, "message_id", None) or self.sequence,
                date=datetime.now(UTC),
                chat={"id": int(method.chat_id), "type": "private"},
                text=getattr(method, "text", None),
                from_user=User(id=999, is_bot=True, first_name="Shluz"),
            )
        return True

    async def stream_content(
        self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True
    ):
        yield b""

    def texts(self):
        return "\n".join(getattr(call, "text", "") or "" for call in self.calls)


def incoming(update_id, text=None, user_id=100, *, callback=None, group=False):
    user = {
        "id": user_id,
        "is_bot": False,
        "first_name": f"User {user_id}",
        "username": f"u{user_id}",
    }
    message = {
        "message_id": update_id,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": -1000 if group else user_id, "type": "group" if group else "private"},
        "from": user,
    }
    if text is not None:
        message["text"] = text
    if callback is not None:
        return Update.model_validate(
            {
                "update_id": update_id,
                "callback_query": {
                    "id": f"cb{update_id}",
                    "from": user,
                    "chat_instance": "test",
                    "message": message,
                    "data": callback,
                },
            }
        )
    return Update.model_validate({"update_id": update_id, "message": message})


async def no_wait(seconds):
    pass


def environment(client, tmp_path, *, source="telegram", request_limit=100, **settings_kwargs):
    from telegram_bot.config import AccessRegistry, BotSettings
    from telegram_bot.controller import Controller, create_dispatcher
    from telegram_bot.gateway import GatewayClient
    from telegram_bot.storage import Store

    path = tmp_path / "access.json"
    path.write_text(json.dumps({"100": TOKEN, "200": OTHER_TOKEN}))
    with client.app.state.database.session() as db:
        plan = Plan(code="beta", name="Закрытая бета", monthly_request_limit=request_limit)
        db.add(plan)
        db.flush()
        for user_id, token in [(100, TOKEN), (200, OTHER_TOKEN)]:
            account = Account(display_name=f"Beta {user_id}")
            db.add(account)
            db.flush()
            db.add(Subscription(account_id=account.id, plan_id=plan.id))
            create_api_token(db, account_id=account.id, name="Beta", source=source, raw_token=token)
    settings = BotSettings(
        bot_token="123456:" + "A" * 35,
        access_file=path,
        database_path=tmp_path / "bot.sqlite",
        cooldown_seconds=0,
        _env_file=None,
        **settings_kwargs,
    )
    store = Store(settings.database_path)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app))
    gateway = GatewayClient("http://gateway", http=http)
    controller = Controller(settings, AccessRegistry(path), store, gateway, sleep=no_wait)
    session = RecordingSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=session)
    return controller, create_dispatcher(controller), bot, session


def count_usage(client):
    with client.app.state.database.session() as db:
        return db.scalar(select(func.count()).select_from(UsageEvent))


def test_private_beta_blocks_unknown_users_groups_and_unsupported_media(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start", user_id=300))
        await dp.feed_update(bot, incoming(2, "group secret", group=True))
        await dp.feed_update(bot, incoming(3, user_id=100))
        assert count_usage(client) == 0
        assert "300" in session.texts()
        assert "текст" in session.texts().lower()
        assert ctrl.jobs == {}

    asyncio.run(scenario())


def test_real_gateway_round_trip_deduplication_and_separate_histories(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        await dp.feed_update(bot, incoming(2, "Привет"))
        await dp.feed_update(bot, incoming(2, "Привет"))
        await dp.feed_update(bot, incoming(3, "Другая история", user_id=200))
        assert count_usage(client) == 2
        assert "Shluz mock reply: Привет" in session.texts()
        assert "Другая история" not in str(ctrl.store.history(100))
        assert "Привет" not in str(ctrl.store.history(200))
        assert len(ctrl.store.history(100)) == 2
        assert ctrl.store.profile(100)["account_id"] is not None
        assert any(call.__api_method__ == "sendChatAction" for call in session.calls)

    asyncio.run(scenario())


def test_consecutive_messages_send_context_and_new_chat_resets_only_current(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        original = ctrl.gateway.chat
        contexts = []

        async def spy(token, model, messages, request_id):
            contexts.append(messages)
            return await original(token, model, messages, request_id)

        ctrl.gateway.chat = spy
        await dp.feed_update(bot, incoming(1, "Первое"))
        old_dialog = ctrl.store.profile(100)["active_dialog"]
        await dp.feed_update(bot, incoming(2, "Второе"))
        assert contexts[1][-3]["content"] == "Первое"
        await dp.feed_update(bot, incoming(3, "/new"))
        await dp.feed_update(bot, incoming(4, "Третье"))
        assert len(contexts[2]) == 2
        assert len(ctrl.store.history(100, old_dialog)) == 4
        assert len(ctrl.store.dialogs(100)) == 2

    asyncio.run(scenario())


def test_plan_models_settings_help_do_not_spend_tokens(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        for update_id, text in enumerate(
            [
                "/start",
                "/models",
                "/plan",
                "/limits",
                "/profile",
                "/settings",
                "/help",
                "/privacy",
            ],
            1,
        ):
            await dp.feed_update(bot, incoming(update_id, text))
        assert count_usage(client) == 0
        assert "Закрытая бета" in session.texts()
        assert "mock-chat" in session.texts()
        assert "100" in session.texts()
        await dp.feed_update(bot, incoming(20, callback="style:short"))
        assert ctrl.store.profile(100)["style"] == "short"

    asyncio.run(scenario())


def test_quota_error_and_wrong_channel_are_explained(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path, request_limit=1)
        await dp.feed_update(bot, incoming(1, "first"))
        await dp.feed_update(bot, incoming(2, "second"))
        assert count_usage(client) == 1
        assert "лимит" in session.texts().lower()
        assert len(ctrl.store.history(100)) == 2

    asyncio.run(scenario())


def test_harness_token_cannot_be_used_by_bot(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path, source="harness")
        await dp.feed_update(bot, incoming(1, "secret prompt"))
        assert count_usage(client) == 0
        assert "канала Telegram" in session.texts()

    asyncio.run(scenario())


def test_long_answer_is_exported_in_full_not_truncated(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        answer = "Большой ответ <script> 🚀\n" * 3000

        async def long_answer(*args):
            return answer

        ctrl.gateway.chat = long_answer
        await dp.feed_update(bot, incoming(1, "Напиши длинно"))
        files = [call for call in session.calls if call.__api_method__ == "sendDocument"]
        assert len(files) == 1
        assert files[0].document.data.decode("utf-8") == answer
        assert ctrl.store.history(100)[-1]["content"] == answer
        assert "файл" in session.texts().lower()

    asyncio.run(scenario())


def test_multipart_answer_and_html_injection(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        answer = "<script>" + "x" * 8000

        async def long_answer(*args):
            return answer

        ctrl.gateway.chat = long_answer
        await dp.feed_update(bot, incoming(1, "long"))
        assert "<script>" not in session.texts()
        assert "&lt;script&gt;" in session.texts()
        assert not any(call.__api_method__ == "sendDocument" for call in session.calls)
        assert sum((getattr(call, "text", "") or "").count("x") for call in session.calls) == 8000

    asyncio.run(scenario())


def test_cancel_is_not_a_refund_and_unknown_request_is_not_replayed(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        started = asyncio.Event()

        async def slow(*args):
            started.set()
            await asyncio.Event().wait()

        ctrl.gateway.chat = slow
        task = asyncio.create_task(dp.feed_update(bot, incoming(1, "slow")))
        await asyncio.wait_for(started.wait(), 3)
        await dp.feed_update(bot, incoming(2, "/cancel"))
        await task
        assert ctrl.jobs == {}
        assert "учтён" in session.texts()
        edits = [call for call in session.calls if call.__api_method__ == "editMessageText"]
        assert edits and "остановлено" in edits[-1].text
        assert ctrl.store.history(100) == []
        await dp.feed_update(bot, incoming(1, "slow"))
        assert ctrl.jobs == {}

    asyncio.run(scenario())


def test_busy_user_cannot_start_second_generation(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []

        async def slow(*args):
            calls.append(args)
            started.set()
            await finish.wait()
            return "done"

        ctrl.gateway.chat = slow
        first = asyncio.create_task(dp.feed_update(bot, incoming(1, "first")))
        await asyncio.wait_for(started.wait(), 3)
        await dp.feed_update(bot, incoming(2, "second"))
        assert len(calls) == 1
        assert "уже" in session.texts().lower()
        finish.set()
        await first

    asyncio.run(scenario())


def test_clear_confirmation_and_foreign_dialog_protection(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "keep"))
        await dp.feed_update(bot, incoming(2, "/clear"))
        confirmation = callback_value(session, "clear_yes:")
        assert ctrl.store.history(100)
        await dp.feed_update(bot, incoming(3, callback=confirmation, user_id=200))
        assert ctrl.store.history(100)
        await dp.feed_update(bot, incoming(4, callback=confirmation))
        assert ctrl.store.history(100) == []
        assert count_usage(client) == 1

    asyncio.run(scenario())


def test_delivery_failure_keeps_answer_and_export_does_not_regenerate(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        session.fail_final_edit = True
        await dp.feed_update(bot, incoming(1, "save me"))
        assert ctrl.store.history(100)[-1]["content"] == "Shluz mock reply: save me"
        session.fail_final_edit = False
        await dp.feed_update(bot, incoming(2, "/export"))
        assert count_usage(client) == 1
        files = [call for call in session.calls if call.__api_method__ == "sendDocument"]
        assert "save me" in files[-1].document.data.decode("utf-8")

    asyncio.run(scenario())


def test_access_revocation_applies_without_restart(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "allowed"))
        ctrl.access.path.write_text("{}")
        await dp.feed_update(bot, incoming(2, "denied"))
        assert count_usage(client) == 1
        assert "denied" not in str(ctrl.store.history(100))

    asyncio.run(scenario())


def test_spinner_stops_before_error_is_rendered(client, tmp_path):
    from telegram_bot.gateway import GatewayError

    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        started, stopped = asyncio.Event(), asyncio.Event()

        async def animate(message, waiting, stop):
            started.set()
            try:
                await stop.wait()
            finally:
                stopped.set()

        async def failed(*args):
            await started.wait()
            raise GatewayError("provider_unavailable")

        original_edit = ctrl.delivery.edit

        async def edit(*args, **kwargs):
            assert stopped.is_set()
            return await original_edit(*args, **kwargs)

        ctrl.delivery.animate, ctrl.gateway.chat, ctrl.delivery.edit = animate, failed, edit
        await dp.feed_update(bot, incoming(1, "fail"))
        assert "недоступна" in session.texts()

    asyncio.run(scenario())


def test_owner_old_stop_button_does_not_cancel_new_request(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "finished"))
        waiting = next(c for c in session.calls if getattr(c, "text", "") == "⏳ Готовлю ответ…")
        old_button = waiting.reply_markup.inline_keyboard[0][0].callback_data
        started = asyncio.Event()

        async def slow(*args):
            started.set()
            await asyncio.Event().wait()

        ctrl.gateway.chat = slow
        task = asyncio.create_task(dp.feed_update(bot, incoming(2, "new request")))
        try:
            await asyncio.wait_for(started.wait(), 3)
            await dp.feed_update(bot, incoming(3, callback=old_button))
            await asyncio.sleep(0)
            assert not task.done() and not task.cancelling(), "Old button must not stop new work"
        finally:
            await dp.feed_update(bot, incoming(4, "/cancel"))
            await task

    asyncio.run(scenario())


def callback_value(session, prefix):
    for call in reversed(session.calls):
        markup = getattr(call, "reply_markup", None)
        if markup:
            for row in markup.inline_keyboard:
                for button in row:
                    if (button.callback_data or "").startswith(prefix):
                        return button.callback_data
    return None


def test_owner_answer_file_targets_original_after_switching_dialog(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "original question"))
        file_action = callback_value(session, "answer:")
        assert file_action, "Each answer needs its own file button"
        await dp.feed_update(bot, incoming(2, "/new"))
        await dp.feed_update(bot, incoming(3, "new question"))
        await dp.feed_update(bot, incoming(4, callback=file_action))
        files = [c for c in session.calls if c.__api_method__ == "sendDocument"]
        assert files[-1].document.data.decode() == "Shluz mock reply: original question"
        assert count_usage(client) == 2
        await dp.feed_update(bot, incoming(5, callback=file_action, user_id=200))
        assert len([c for c in session.calls if c.__api_method__ == "sendDocument"]) == 1

    asyncio.run(scenario())


def test_owner_retry_requires_one_time_confirmation_and_clean_context(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        original_chat = ctrl.gateway.chat
        contexts = []

        async def spy(token, model, messages, request_id):
            contexts.append(messages)
            return await original_chat(token, model, messages, request_id)

        ctrl.gateway.chat = spy
        await dp.feed_update(bot, incoming(1, "first"))
        await dp.feed_update(bot, incoming(2, "second"))
        retry = callback_value(session, "retry:")
        assert retry
        await dp.feed_update(bot, incoming(3, callback=retry))
        assert count_usage(client) == 2
        assert "новый запрос" in session.texts().lower()
        confirmation = callback_value(session, "retry_yes:")
        assert confirmation
        await dp.feed_update(bot, incoming(4, callback=confirmation))
        await dp.feed_update(bot, incoming(5, callback=confirmation))
        assert count_usage(client) == 3
        assert len(contexts[-1]) == 4
        assert contexts[-1][-1]["content"] == "second"
        assert sum(m["content"] == "second" for m in contexts[-1]) == 1
        assert len(ctrl.store.history(100)) == 4
        assert ctrl.jobs == {}

    asyncio.run(scenario())


def test_owner_retry_confirmation_is_invalidated_by_new_message(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "first"))
        await dp.feed_update(bot, incoming(2, "/retry"))
        confirmation = callback_value(session, "retry_yes:")
        assert confirmation
        await dp.feed_update(bot, incoming(3, "second"))
        await dp.feed_update(bot, incoming(4, callback=confirmation))
        assert count_usage(client) == 2

    asyncio.run(scenario())


def test_owner_continue_recovers_saved_answer_without_model_call(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "saved"))
        session.calls.clear()
        await dp.feed_update(bot, incoming(2, callback="continue"))
        assert "Shluz mock reply: saved" in session.texts()
        assert count_usage(client) == 1

    asyncio.run(scenario())


def test_owner_rename_feedback_support_and_stats_do_not_spend(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "original"))
        vote = callback_value(session, "up:")
        assert vote
        await dp.feed_update(bot, incoming(2, callback=vote))
        await dp.feed_update(bot, incoming(3, "/rename Мой проект"))
        assert ctrl.store.dialogs(100)[0]["title"] == "Мой проект"
        for i, command in enumerate(["/stats", "/support", "/privacy", "/last"], 4):
            await dp.feed_update(bot, incoming(i, command))
        assert count_usage(client) == 1
        assert "Мой проект" in session.texts()
        assert "администратору" in session.texts()
        assert sum(row["positive"] for row in ctrl.store.metrics()) == 1

    asyncio.run(scenario())


def test_owner_forget_is_confirmed_scoped_and_old_button_is_harmless(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "Alice secret"))
        await dp.feed_update(bot, incoming(2, "Bob secret", user_id=200))
        await dp.feed_update(bot, incoming(3, "/forget"))
        confirm = callback_value(session, "forget_yes:")
        assert confirm
        assert ctrl.store.history(100)
        await dp.feed_update(bot, incoming(4, callback=confirm, user_id=200))
        assert ctrl.store.history(100)
        await dp.feed_update(bot, incoming(5, callback=confirm))
        assert ctrl.store.history(100) == []
        assert ctrl.store.history(200)
        await dp.feed_update(bot, incoming(6, "new secret"))
        await dp.feed_update(bot, incoming(7, callback=confirm))
        assert ctrl.store.history(100)
        assert count_usage(client) == 3
        await dp.feed_update(bot, incoming(1, "Alice secret"))
        assert count_usage(client) == 3

    asyncio.run(scenario())


def test_owner_menu_navigation_edits_only_menu_never_an_answer(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/menu"))
        screen_id = ctrl.delivery.screen_ids[100]
        event = incoming(2, callback="settings")
        event = event.model_copy(
            update={
                "callback_query": event.callback_query.model_copy(
                    update={
                        "message": event.callback_query.message.model_copy(
                            update={
                                "message_id": screen_id,
                                "from_user": User(id=999, is_bot=True, first_name="Shluz"),
                            }
                        ),
                    }
                )
            }
        )
        before = len(session.calls)
        await dp.feed_update(bot, event)
        calls = session.calls[before:]
        assert any(c.__api_method__ == "editMessageText" for c in calls)
        assert not any(c.__api_method__ == "sendMessage" for c in calls)
        await dp.feed_update(bot, incoming(3, "model reply"))
        final = next(c for c in reversed(session.calls) if c.__api_method__ == "editMessageText")
        event = incoming(4, callback="settings")
        event = event.model_copy(
            update={
                "callback_query": event.callback_query.model_copy(
                    update={
                        "message": event.callback_query.message.model_copy(
                            update={
                                "message_id": final.message_id,
                                "from_user": User(id=999, is_bot=True, first_name="Shluz"),
                            }
                        ),
                    }
                )
            }
        )
        before = len(session.calls)
        await dp.feed_update(bot, event)
        assert any(c.__api_method__ == "sendMessage" for c in session.calls[before:])

    asyncio.run(scenario())


def test_owner_old_clear_and_delete_confirmations_cannot_delete_new_messages(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        for start, action in [(10, "clear"), (20, "delete")]:
            await dp.feed_update(bot, incoming(start, "before confirmation"))
            dialog = ctrl.store.profile(100)["active_dialog"]
            await dp.feed_update(bot, incoming(start + 1, f"/{action}"))
            button = callback_value(session, action + "_yes:")
            await dp.feed_update(bot, incoming(start + 2, "new protected message"))
            await dp.feed_update(bot, incoming(start + 3, callback=button))
            assert ctrl.store.profile(100)["active_dialog"] == dialog
            assert "new protected message" in str(ctrl.store.history(100))
        assert count_usage(client) == 4

    asyncio.run(scenario())
