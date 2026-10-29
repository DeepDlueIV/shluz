"""Owner-only trial must not onboard strangers or change the global bot menu."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from tests.telegram.test_bot import count_usage, environment, incoming
from tests.telegram.test_miniapp import ORIGIN, signed


def test_trial_rejects_other_telegram_user_before_gateway(client, tmp_path):
    async def run():
        ctrl, dp, bot, recording = environment(
            client,
            tmp_path,
            interface="miniapp",
            miniapp_url=ORIGIN + "/miniapp",
            trial_owner_id=100,
        )
        await dp.feed_update(bot, incoming(78001, "A paid request", user_id=200))
        assert count_usage(client) == 0
        assert recording.calls == []
        await ctrl.close()

    asyncio.run(run())


def test_trial_signed_init_data_is_not_enough_for_another_user(client, tmp_path):
    async def run():
        from telegram_bot.miniapp.server import create_miniapp

        ctrl, _, bot, _ = environment(
            client,
            tmp_path,
            interface="miniapp",
            miniapp_url=ORIGIN + "/miniapp",
            trial_owner_id=100,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_miniapp(ctrl)),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN},
        ) as http:
            assert (
                await http.post("/miniapp/api/session", json={"init_data": signed(200)})
            ).status_code == 403
            r = await http.post("/miniapp/api/session", json={"init_data": signed(100)})
            assert r.status_code == 200
            headers = {"Authorization": "Bearer " + r.json()["session"]}
            ctrl.settings.trial_owner_id = 300
            assert (await http.get("/miniapp/api/state", headers=headers)).status_code == 403
            assert (await http.get("/admin")).status_code == 404
            assert (await http.get("/v1/models")).status_code == 404
            assert (await http.get("/miniapp/health")).status_code == 200
        await ctrl.close()

    asyncio.run(run())


def test_trial_menu_is_per_owner_not_global():
    from telegram_bot.config import BotSettings
    from telegram_bot.miniapp.runtime import configure_menu

    settings = BotSettings(interface="miniapp", miniapp_url=ORIGIN + "/miniapp", trial_owner_id=100)
    bot = SimpleNamespace(set_chat_menu_button=AsyncMock())
    asyncio.run(configure_menu(bot, settings))
    assert bot.set_chat_menu_button.call_args.kwargs["chat_id"] == 100
    assert bot.set_chat_menu_button.call_args.kwargs["menu_button"].type == "web_app"


def test_monochrome_is_not_overridden_by_telegram_colors():
    css = Path("telegram_bot/miniapp/static/app.css").read_text()
    js = Path("telegram_bot/miniapp/static/app.js").read_text()
    assert "--bg:#0c0d0f" in css
    assert "--accent:#f4f4f4" in css
    assert "--tg-theme-bg-color" not in css
    assert "data-theme=light" not in css
    assert 'setHeaderColor?.("#0c0d0f")' in js
