"""Регрессии реальной хронологии чата, анимированного потока и безопасных ошибок."""

import asyncio

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

from telegram_bot import ui
from telegram_bot.delivery import Delivery, StreamPreview
from telegram_bot.gateway import GatewayClient, GatewayError
from telegram_bot.storage import Store
from tests.telegram.test_bot import RecordingSession, count_usage, environment, incoming, no_wait
from tests.telegram.test_single_panel import methods, origin


def test_menu_moves_below_answer_then_edits_in_place(tmp_path):
    async def scenario():
        session = RecordingSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        panel = await delivery.screen(origin(bot), "Menu", ui.menu())
        answer = await delivery.say(origin(bot), "Keep this model answer", ui.back())
        latest = await delivery.screen(answer.as_(bot), "Dialogs", ui.back())
        assert latest.message_id > answer.message_id
        assert [m.message_id for m in methods(session, "deleteMessage")] == [panel.message_id]
        await delivery.screen(latest.as_(bot), "Settings", ui.back())
        assert len(methods(session, "sendMessage")) == 3
        assert methods(session, "editMessageText")[-1].message_id == latest.message_id
        assert answer.message_id not in [m.message_id for m in methods(session, "deleteMessage")]

    asyncio.run(scenario())


def test_old_button_after_restart_opens_latest_panel(tmp_path):
    async def scenario():
        session = RecordingSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        store = Store(tmp_path / "bot.sqlite")
        delivery = Delivery(store=store, sleep=no_wait)
        panel = await delivery.screen(origin(bot), "Menu", ui.menu())
        answer = await delivery.say(origin(bot), "New answer")
        restarted = Delivery(store=Store(store.path), sleep=no_wait)
        latest = await restarted.screen(panel.as_(bot), "History", ui.back())
        assert latest.message_id > answer.message_id
        assert store.screen_id(bot.id, 100) == latest.message_id

    asyncio.run(scenario())


def test_text_command_after_menu_relocates_without_new_model_call(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        previous = ctrl.store.screen_id(bot.id, 100)
        # Настоящие message_id растут для сообщений обеих сторон, не только для бота.
        session.sequence = 2000
        await dp.feed_update(bot, incoming(2000, "/menu"))
        assert ctrl.store.screen_id(bot.id, 100) > 2000
        assert previous in [m.message_id for m in methods(session, "deleteMessage")]
        assert count_usage(client) == 0

    asyncio.run(scenario())


def test_incoming_text_is_remembered_even_before_bot_answer(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        panel_id = ctrl.store.screen_id(bot.id, 100)
        called = asyncio.Event()
        release = asyncio.Event()

        async def stalled(*args, **kwargs):
            called.set()
            await release.wait()

        ctrl.generate = stalled
        session.sequence = 3000
        task = asyncio.create_task(dp.feed_update(bot, incoming(3000, "text")))
        await called.wait()
        old_callback = incoming(3001, callback="history:0")
        old_callback = old_callback.model_copy(
            update={
                "callback_query": old_callback.callback_query.model_copy(
                    update={
                        "message": old_callback.callback_query.message.model_copy(
                            update={"message_id": panel_id}
                        )
                    }
                )
            }
        )
        await dp.feed_update(bot, old_callback)
        release.set()
        await task
        assert ctrl.store.screen_id(bot.id, 100) > 3000

    asyncio.run(scenario())


def test_old_panel_cleanup_failure_does_not_remove_answers(tmp_path):
    class Session(RecordingSession):
        async def make_request(self, bot, method, timeout=None):
            if method.__api_method__ == "deleteMessage":
                self.calls.append(method)
                raise TelegramBadRequest(method=method, message="message can't be deleted")
            return await super().make_request(bot, method, timeout)

    async def scenario():
        session = Session()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        old = await delivery.screen(origin(bot), "Menu")
        answer = await delivery.say(origin(bot), "Answer")
        new = await delivery.screen(answer.as_(bot), "New menu")
        assert new.message_id > answer.message_id
        assert methods(session, "editMessageReplyMarkup")[-1].message_id == old.message_id
        assert methods(session, "editMessageReplyMarkup")[-1].reply_markup is None

    asyncio.run(scenario())


def test_drafts_use_stable_id_then_persist_final_message(tmp_path):
    async def scenario():
        session = RecordingSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        message = origin(bot)
        waiting = await delivery.waiting(message, request_id="test")
        preview = StreamPreview(delivery, message, waiting, interval=0.01)
        try:
            await preview.push("При")
            await asyncio.sleep(0.03)
            await preview.push("Привет <>& 🙂")
            await asyncio.sleep(0.03)
        finally:
            await preview.close()
        drafts = methods(session, "sendMessageDraft")
        assert [d.text for d in drafts] == ["При", "Привет <>& 🙂"]
        assert len({d.draft_id for d in drafts}) == 1 and drafts[0].draft_id != 0
        assert all(d.parse_mode is None for d in drafts)
        assert not methods(session, "editMessageText")
        await delivery.answer(message, waiting, preview.latest, draft=preview.draft_used)
        assert methods(session, "sendMessage")[-1].text == "Привет &lt;&gt;&amp; 🙂"
        assert methods(session, "deleteMessage")[-1].message_id == waiting.message_id
        count = len(session.calls)
        await asyncio.sleep(0.03)
        assert len(session.calls) == count

    asyncio.run(scenario())


def test_unsupported_drafts_fall_back_without_another_generation(tmp_path):
    class Session(RecordingSession):
        async def make_request(self, bot, method, timeout=None):
            if method.__api_method__ == "sendMessageDraft":
                self.calls.append(method)
                raise TelegramBadRequest(method=method, message="method not found")
            return await super().make_request(bot, method, timeout)

    async def scenario():
        session = Session()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        message = origin(bot)
        waiting = await delivery.waiting(message)
        preview = StreamPreview(delivery, message, waiting, interval=0.01)
        await preview.push("partial")
        await asyncio.sleep(0.05)
        await preview.close()
        assert len(methods(session, "sendMessageDraft")) == 1
        assert len(methods(session, "editMessageText")) == 1
        assert not preview.draft_used

    asyncio.run(scenario())


@pytest.mark.parametrize("network", [False, True])
def test_draft_transport_failures_do_not_flood_or_break_stream(tmp_path, network):
    class Session(RecordingSession):
        async def make_request(self, bot, method, timeout=None):
            if method.__api_method__ == "sendMessageDraft":
                self.calls.append(method)
                if network:
                    raise TelegramNetworkError(method=method, message="network")
                raise TelegramRetryAfter(method=method, message="slow down", retry_after=20)
            return await super().make_request(bot, method, timeout)

    async def scenario():
        session = Session()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        message = origin(bot)
        waiting = await delivery.waiting(message)
        preview = StreamPreview(delivery, message, waiting, interval=0.01)
        for i in range(15):
            await preview.push("word " * (i + 1))
            await asyncio.sleep(0.005)
        await preview.close()
        assert len(methods(session, "sendMessageDraft")) == 1
        assert not methods(session, "editMessageText")
        await delivery.answer(message, waiting, preview.latest, draft=preview.draft_used)
        assert "word" in session.texts()

    asyncio.run(scenario())


def test_long_unicode_draft_stays_within_telegram_limit(tmp_path):
    async def scenario():
        session = RecordingSession()
        bot = Bot("123456:" + "A" * 35, session=session)
        delivery = Delivery(store=Store(tmp_path / "bot.sqlite"), sleep=no_wait)
        message = origin(bot)
        waiting = await delivery.waiting(message)
        preview = StreamPreview(delivery, message, waiting, interval=0.01)
        await preview.push("🙂<&" * 2500)
        await asyncio.sleep(0.03)
        await preview.close()
        drafts = methods(session, "sendMessageDraft")
        assert drafts
        assert all(len(d.text.encode("utf-16-le")) // 2 <= 4096 for d in drafts)

    asyncio.run(scenario())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("code", ["provider_authentication_error", "provider_balance_exhausted"])
def test_provider_errors_keep_safe_reason_and_gateway_reference(stream, code):
    async def scenario():
        calls = []
        reference = "f" * 32

        def handler(request):
            calls.append(request)
            return httpx.Response(
                503,
                headers={"x-request-id": reference},
                json={"error": {"code": code, "message": "SECRET body/token"}},
            )

        async def delta(text):
            pass

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = GatewayClient("http://gateway", http=http)
            with pytest.raises(GatewayError) as exc:
                await client.chat(
                    "secret", "model", [], "local", on_delta=delta if stream else None
                )
        assert exc.value.code == code
        assert exc.value.reference == reference
        assert reference in exc.value.user_message()
        assert "SECRET" not in exc.value.user_message()
        assert len(calls) == 1

    asyncio.run(scenario())


def test_failed_menu_replacement_keeps_old_panel(tmp_path):
    class Session(RecordingSession):
        fail_send = False

        async def make_request(self, bot, method, timeout=None):
            if method.__api_method__ == "sendMessage" and self.fail_send:
                self.calls.append(method)
                raise TelegramNetworkError(method=method, message="uncertain send")
            return await super().make_request(bot, method, timeout)

    async def scenario():
        session = Session()
        bot = Bot("123456:" + "A" * 35, session=session)
        store = Store(tmp_path / "bot.sqlite")
        delivery = Delivery(store=store, sleep=no_wait)
        old = await delivery.screen(origin(bot), "Menu")
        answer = await delivery.say(origin(bot), "Answer")
        session.fail_send = True
        with pytest.raises(TelegramNetworkError):
            await delivery.screen(answer.as_(bot), "New menu")
        assert store.screen_id(bot.id, 100) == old.message_id
        assert not methods(session, "deleteMessage")
        assert len(methods(session, "sendMessage")) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_draft_interruption_persists_partial_with_warning_and_stops_worker(
    client, tmp_path, cancel
):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        ready = asyncio.Event()
        calls = []

        async def slow(token, model, messages, request_id, *, on_delta):
            calls.append(request_id)
            await on_delta("Сохранённая часть")
            for _ in range(100):
                if methods(session, "sendMessageDraft"):
                    break
                await asyncio.sleep(0.005)
            assert methods(session, "sendMessageDraft")
            ready.set()
            if cancel:
                await asyncio.Event().wait()
            raise GatewayError("outcome_unknown", reference="a" * 32)

        ctrl.gateway.chat = slow
        task = asyncio.create_task(dp.feed_update(bot, incoming(1, "Question")))
        await asyncio.wait_for(ready.wait(), timeout=2)
        if cancel:
            await dp.feed_update(bot, incoming(2, callback=f"cancel:{ctrl.job_ids[100]}"))
        await task
        persisted = methods(session, "sendMessage")[-1].text
        assert "Сохранённая часть" in persisted
        assert "остановлено" in persisted if cancel else "подтвердить" in persisted
        assert ctrl.store.latest_turn(100)["status"] == "unknown"
        assert ctrl.store.history(100) == []
        assert len(calls) == 1 and ctrl.jobs == {}
        total = len(session.calls)
        await asyncio.sleep(0.03)
        assert len(session.calls) == total

    asyncio.run(scenario())


def test_reference_never_echoes_arbitrary_header():
    error = GatewayError("provider_unavailable", 503, "secret\n<script>token</script>")
    assert error.reference is None
    assert "secret" not in error.user_message()


def test_uncertain_menu_send_is_not_repeated_by_middleware(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session = environment(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/menu"))
        await ctrl.delivery.say(origin(bot), "Answer after menu")
        original = session.make_request
        attempts = []

        async def fail_send(bot, method, timeout=None):
            if method.__api_method__ == "sendMessage":
                attempts.append(method)
                raise TelegramNetworkError(method=method, message="uncertain send")
            return await original(bot, method, timeout)

        session.make_request = fail_send
        await dp.feed_update(bot, incoming(2, callback="menu"))
        assert len(attempts) == 1
        assert count_usage(client) == 0

    asyncio.run(scenario())
