"""Контракт панели: без сообщений в чат, без чужих данных и обхода шлюза."""

import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest

from tests.telegram.test_bot import count_usage, environment, incoming

ORIGIN = "https://mini.noclip.test"
BOT_TOKEN = "123456:" + "A" * 35


def signed(uid=100, *, age=0, key=BOT_TOKEN, extra=None):
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": f"test-{uid}",
        "user": json.dumps({"id": uid, "first_name": "Test", "username": "tester"}),
    }
    fields.update(extra or {})
    secret = hmac.new(b"WebAppData", key.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def setup(client, tmp_path):
    from telegram_bot.miniapp.server import create_miniapp

    ctrl, dp, bot, session = environment(
        client, tmp_path, interface="miniapp", miniapp_url=ORIGIN + "/miniapp"
    )
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_miniapp(ctrl)),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
    )
    auth = await http.post("/miniapp/api/session", json={"init_data": signed()})
    assert auth.status_code == 200, auth.text
    http.headers["Authorization"] = "Bearer " + auth.json()["session"]
    return ctrl, dp, bot, session, http


def test_miniapp_requires_https_and_preserves_classic_default():
    from pydantic import ValidationError

    from telegram_bot.config import BotSettings

    assert BotSettings(_env_file=None).interface == "classic"
    for url in (
        "",
        "http://example.com/miniapp",
        "https://a:b@example.com/miniapp",
        "https://example.com/other",
        "https://example.com/miniapp?secret=x",
    ):
        with pytest.raises(ValidationError):
            BotSettings(interface="miniapp", miniapp_url=url, _env_file=None)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: "",
        lambda: signed(key="other-bot"),
        lambda: signed(age=301),
        lambda: signed(age=-61),
        lambda: signed() + "&user=other",
        lambda: signed(uid=True),
        lambda: signed(uid=-1),
        lambda: signed(extra={"user": "not-json"}),
        lambda: signed().replace("Test", "Changed"),
    ],
)
def test_init_data_invalid(factory):
    from telegram_bot.miniapp.auth import validate_init_data

    with pytest.raises(ValueError):
        validate_init_data(factory(), BOT_TOKEN)


def test_init_data_valid():
    from telegram_bot.miniapp.auth import validate_init_data

    assert validate_init_data(signed(), BOT_TOKEN)["id"] == 100


def test_static_is_public_but_api_requires_signature_and_origin(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, session, http = await setup(client, tmp_path)
        page = await http.get("/miniapp")
        assert page.status_code == 200
        assert "NoClip" in page.text and BOT_TOKEN not in page.text
        assert "no-store" in page.headers["cache-control"]
        assert "script-src" in page.headers["content-security-policy"]
        unauth = await http.get("/miniapp/api/state", headers={"Authorization": ""})
        assert unauth.status_code == 401
        for path, body in [
            ("/session", {"init_data": signed()}),
            ("/action", {"action": "new", "request_id": "a" * 24}),
        ]:
            response = await http.post(
                "/miniapp/api" + path, json=body, headers={"Origin": "https://evil.test"}
            )
            assert response.status_code == 403
        assert (
            await http.post("/miniapp/api/session", json={"init_data": "x" * 18000})
        ).status_code == 413
        assert session.calls == [] and count_usage(client) == 0
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_panel_changes_same_dialog_used_by_chat_and_keeps_chat_clean(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, transport, http = await setup(client, tmp_path)
        await dp.feed_update(bot, incoming(1, "/start"))
        assert not any(c.__api_method__ == "sendMessage" for c in transport.calls)
        result = await http.post(
            "/miniapp/api/action", json={"action": "new", "request_id": "n" * 24}
        )
        assert result.status_code == 200
        dialog_id = result.json()["active_dialog"]["id"]
        await dp.feed_update(bot, incoming(2, "Проверка Mini App"))
        state = (await http.get("/miniapp/api/state")).json()
        assert state["active_dialog"]["id"] == dialog_id
        assert "Проверка Mini App" in ctrl.store.export_dialog(100, dialog_id)
        assert count_usage(client) == 1
        for call in transport.calls:
            if call.__api_method__ in {"sendMessage", "editMessageText", "sendDocument"}:
                assert call.reply_markup is None
        assert "Демо: осталось" not in transport.texts()
        assert any(c.__api_method__ == "setChatMenuButton" for c in transport.calls)
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_actions_idempotent_and_dialog_ownership(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, transport, http = await setup(client, tmp_path)
        body = {"action": "new", "request_id": "n" * 24}
        first = await http.post("/miniapp/api/action", json=body)
        second = await http.post("/miniapp/api/action", json=body)
        assert first.status_code == second.status_code == 200
        assert len(ctrl.store.dialogs(100)) == 2
        ctrl.store.ensure_user(200, "Other")
        foreign = ctrl.store.profile(200)["active_dialog"]
        for action in ["activate", "rename", "export", "delete", "clear"]:
            response = await http.post(
                "/miniapp/api/action",
                json={
                    "action": action,
                    "target": foreign,
                    "value": "Name",
                    "request_id": action * 8,
                },
            )
            assert response.status_code == 404, response.text
        state = (await http.get("/miniapp/api/state")).json()
        assert foreign not in json.dumps(state)
        assert "shluz_" not in json.dumps(state) and "tester" not in json.dumps(state)
        assert (
            await http.post(
                "/miniapp/api/action",
                json={
                    "action": "model",
                    "value": "forbidden-model",
                    "request_id": "m" * 24,
                },
            )
        ).status_code == 403
        assert count_usage(client) == 0 and transport.calls == []
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_delete_confirmation_and_stale_state(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, transport, http = await setup(client, tmp_path)
        target = ctrl.store.profile(100)["active_dialog"]
        request = {"action": "delete", "target": target, "request_id": "d" * 24}
        assert (await http.post("/miniapp/api/action", json=request)).status_code == 409
        confirm = await http.post(
            "/miniapp/api/confirm", json={"action": "delete", "target": target}
        )
        assert confirm.status_code == 200
        nonce = confirm.json()["confirmation"]
        ctrl.store.rename_dialog(100, target, "New name")
        request["confirmation"] = nonce
        assert (await http.post("/miniapp/api/action", json=request)).status_code == 409
        assert ctrl.store.dialog_for(100, target)
        confirm = await http.post(
            "/miniapp/api/confirm", json={"action": "delete", "target": target}
        )
        request.update(confirmation=confirm.json()["confirmation"], request_id="e" * 24)
        assert (await http.post("/miniapp/api/action", json=request)).status_code == 200
        assert ctrl.store.dialog_for(100, target) is None
        assert len(ctrl.store.dialogs(100)) == 1
        assert count_usage(client) == 0 and transport.calls == []
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_active_generation_protected_and_stop_scoped(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, transport, http = await setup(client, tmp_path)
        job = asyncio.create_task(asyncio.sleep(30))
        ctrl.jobs[100] = job
        ctrl.job_ids[100] = "r" * 32
        for action in ["new", "model", "forget", "style"]:
            response = await http.post(
                "/miniapp/api/action",
                json={
                    "action": action,
                    "value": "mock-chat",
                    "request_id": action * 8,
                },
            )
            assert response.status_code == 409
        result = await http.post(
            "/miniapp/api/action",
            json={
                "action": "stop",
                "target": "stale",
                "request_id": "s" * 24,
            },
        )
        assert result.status_code == 409 and not job.cancelling()
        result = await http.post(
            "/miniapp/api/action",
            json={
                "action": "stop",
                "target": "r" * 32,
                "request_id": "t" * 24,
            },
        )
        assert result.status_code == 200
        await asyncio.gather(job, return_exceptions=True)
        assert job.cancelled()
        ctrl.jobs.clear()
        ctrl.job_ids.clear()
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_revoked_account_and_unknown_user_blocked(client, tmp_path):
    async def scenario():
        from sqlalchemy import select

        from app.db.models import Account

        ctrl, dp, bot, transport, http = await setup(client, tmp_path)
        with client.app.state.database.session() as db:
            account = db.scalar(select(Account).where(Account.display_name == "Beta 100"))
            account.status = "disabled"
        assert (await http.get("/miniapp/api/state")).status_code in {401, 403}
        auth = await http.post("/miniapp/api/session", json={"init_data": signed(uid=300)})
        assert auth.status_code == 403
        assert count_usage(client) == 0
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_browser_same_origin_get_without_origin_header(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, transport, http = await setup(client, tmp_path)
        del http.headers["Origin"]
        # Fetch same-origin GET обычно не содержит Origin; bearer остаётся обязательным.
        assert (
            await http.get("/miniapp/api/state", headers={"Sec-Fetch-Site": "same-origin"})
        ).status_code == 200
        assert (
            await http.get("/miniapp/api/state", headers={"Sec-Fetch-Site": "cross-site"})
        ).status_code == 403
        assert (
            await http.post("/miniapp/api/action", json={"action": "new", "request_id": "a" * 24})
        ).status_code == 403
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_sessions_expire_are_bounded_and_do_not_extend_on_replay():
    from pydantic import SecretStr

    from telegram_bot.miniapp.auth import Sessions

    cache = Sessions(ttl=900, capacity=1)
    user = {"id": 100}
    token, session = cache.create("raw", user, "account", SecretStr("gateway-private"))
    first_expiry = session.expires
    assert cache.create("raw", user, "account", SecretStr("gateway-private"))[0] == token
    assert session.expires == first_expiry
    with pytest.raises(ValueError):
        cache.create("another", user, "account", SecretStr("gateway-private"))
    session.expires = time.monotonic() - 1
    assert cache.get(token) is None
    assert "gateway-private" not in repr(session)


def test_classic_routes_closed_and_menu_rollback(client, tmp_path):
    async def scenario():
        from telegram_bot.miniapp.runtime import configure_menu
        from telegram_bot.miniapp.server import create_miniapp

        ctrl, dp, bot, transport = environment(client, tmp_path)
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_miniapp(ctrl)), base_url=ORIGIN
        )
        assert (await http.get("/miniapp")).status_code == 404
        assert (
            await http.post("/miniapp/api/session", json={"init_data": signed()})
        ).status_code == 404
        await configure_menu(bot, ctrl.settings)
        assert transport.calls[-1].menu_button.type == "commands"
        ctrl.settings.interface = "miniapp"
        ctrl.settings.miniapp_url = ORIGIN + "/miniapp"
        await configure_menu(bot, ctrl.settings)
        assert transport.calls[-1].menu_button.web_app.url == ORIGIN + "/miniapp"
        assert transport.calls[-1].menu_button.type == "web_app"
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())


def test_mutations_export_and_repeated_confirmation_are_isolated(client, tmp_path):
    async def scenario():
        ctrl, dp, bot, transport, http = await setup(client, tmp_path)
        uid = 100
        target = ctrl.store.profile(uid)["active_dialog"]
        for action, fields in [
            ("rename", {"target": target, "value": "Личный проект"}),
            ("style", {"value": "short"}),
            ("model", {"value": "mock-chat"}),
        ]:
            result = await http.post(
                "/miniapp/api/action", json={"action": action, "request_id": action * 8, **fields}
            )
            assert result.status_code == 200, result.text
        turn = ctrl.store.begin_turn(uid, 9, "private prompt", "mock-chat")
        ctrl.store.complete_turn(turn, "private answer")
        exported = await http.post(
            "/miniapp/api/action",
            json={"action": "export", "target": target, "request_id": "x" * 24},
        )
        assert "private answer" in exported.json()["text"]
        account = await http.get("/miniapp/api/account")
        assert account.status_code == 200 and account.json()["payments_enabled"] is False
        confirmation = await http.post(
            "/miniapp/api/confirm", json={"action": "clear", "target": target}
        )
        payload = {
            "action": "clear",
            "target": target,
            "request_id": "c" * 24,
            "confirmation": confirmation.json()["confirmation"],
        }
        assert (await http.post("/miniapp/api/action", json=payload)).status_code == 200
        assert not ctrl.store.history(uid)
        payload["request_id"] = "d" * 24
        assert (await http.post("/miniapp/api/action", json=payload)).status_code == 409
        assert count_usage(client) == 0 and transport.calls == []
        await http.aclose()
        await ctrl.close()
        await bot.session.close()

    asyncio.run(scenario())
