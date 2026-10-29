"""Проверка настоящего UI/HTTP в Chromium. Только синтетические данные, без Telegram/Venice."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app.config import Settings  # noqa: E402
from app.factory import create_app  # noqa: E402
from telegram_bot.miniapp.server import create_miniapp  # noqa: E402
from tests.telegram.test_bot import count_usage, environment  # noqa: E402
from tests.telegram.test_miniapp import signed  # noqa: E402


def main():
    output = Path(os.environ.get("MINIAPP_SCREENSHOTS", "artifacts/miniapp"))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="noclip-miniapp-test-") as directory:
        root = Path(directory)
        settings = Settings(
            database_url=SecretStr(f"sqlite+pysqlite:///{root / 'gateway.sqlite'}"),
            active_provider="mock",
            bootstrap_api_token=SecretStr("isolated-test"),
            admin_username="test",
            admin_password=SecretStr("isolated-not-production"),
        )
        gateway = TestClient(create_app(settings))
        ctrl, _, bot, recording = environment(
            gateway,
            root,
            interface="miniapp",
            miniapp_url="https://localhost/miniapp",
        )
        # Только тестовая программная настройка. Production валидатор требует HTTPS.
        port = int(os.environ.get("MINIAPP_TEST_PORT", "18281"))
        base = f"http://127.0.0.1:{port}"
        ctrl.settings.miniapp_url = base + "/miniapp"
        ctrl.store.ensure_user(100, "Test")
        for number, title in enumerate(
            ["Как работает память ИИ?", "Идеи для проекта", "План на неделю"]
        ):
            if number:
                ctrl.store.new_dialog(100)
            d = ctrl.store.profile(100)["active_dialog"]
            ctrl.store.rename_dialog(100, d, title)
            turn = ctrl.store.begin_turn(100, 500 + number, title, "mock-chat")
            ctrl.store.complete_turn(turn, "Контролируемый ответ для проверки интерфейса.")
        ctrl.store.set_model(100, "mock-chat")
        server = uvicorn.Server(
            uvicorn.Config(
                create_miniapp(ctrl),
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            with sync_playwright() as pw:
                executable = os.environ.get("CHROMIUM_EXECUTABLE")
                browser = pw.chromium.launch(
                    **({"executable_path": executable} if executable else {})
                )
                page = browser.new_page(
                    viewport={"width": 390, "height": 844}, device_scale_factor=1
                )
                page_errors = []
                page.on("pageerror", lambda e: page_errors.append(str(e)))
                page.route(
                    "https://telegram.org/js/telegram-web-app.js", lambda r: r.fulfill(body="")
                )
                stub = {"initData": signed(), "colorScheme": "dark"}
                page.add_init_script(
                    "window.Telegram={WebApp:Object.assign("
                    + json.dumps(stub)
                    + ", {ready(){},expand(){},close(){window.closedPanel=true},onEvent(){},"
                    "BackButton:{hide(){},show(){},onClick(){}}})};"
                )
                page.goto(base + "/miniapp")
                page.get_by_role("heading", name="Ваше пространство").wait_for()
                assert page.locator("body").evaluate("e => e.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(output / "home-mobile.png"), full_page=True)
                page.get_by_role("button", name="Диалоги", exact=True).click()
                page.get_by_role("heading", name="Диалоги", exact=True).wait_for()
                page.screenshot(path=str(output / "dialogs-mobile.png"), full_page=True)
                page.get_by_role("button", name="Как работает память ИИ?", exact=False).click()
                page.get_by_role("status").filter(has_text="Диалог выбран").wait_for()
                page.get_by_role("button", name="Настройки", exact=True).click()
                page.get_by_role(
                    "button", name="Название Как работает память ИИ?", exact=False
                ).click()
                page.get_by_label("Название диалога", exact=True).fill("Исследование NoClip")
                page.get_by_role("button", name="Сохранить", exact=True).click()
                page.get_by_role("dialog").wait_for(state="hidden")
                assert (
                    ctrl.store.dialog_for(100, ctrl.store.profile(100)["active_dialog"])["title"]
                    == "Исследование NoClip"
                )
                page.screenshot(path=str(output / "settings-mobile.png"), full_page=True)
                page.get_by_role("button", name="Краткий", exact=False).click()
                page.get_by_role("status").filter(has_text="Стиль сохранён").wait_for()
                assert ctrl.store.profile(100)["style"] == "short"
                page.get_by_role("button", name="Модели", exact=True).click()
                page.get_by_role("button", name="mock-chat", exact=False).wait_for()
                page.get_by_role("button", name="Аккаунт", exact=True).click()
                page.get_by_role("heading", name="Тарифы", exact=True).wait_for()
                assert page.get_by_role("button", name="Оплата скоро").first.is_disabled()
                page.screenshot(path=str(output / "account-mobile.png"), full_page=True)
                page.get_by_role("button", name="Диалоги", exact=True).click()
                page.get_by_role("button", name="Удалить все диалоги", exact=True).click()
                page.get_by_role("dialog").wait_for()
                page.get_by_role("button", name="Отмена", exact=True).click()
                assert len(ctrl.store.dialogs(100)) == 3
                page.get_by_role("button", name="Удалить все диалоги", exact=True).click()
                page.get_by_role("button", name="Подтвердить", exact=True).click()
                page.get_by_role("dialog").wait_for(state="hidden")
                assert len(ctrl.store.dialogs(100)) == 1
                page.get_by_role("button", name="Главная", exact=True).click()
                page.set_viewport_size({"width": 1100, "height": 850})
                page.screenshot(path=str(output / "home-desktop.png"), full_page=True)
                page.get_by_role("button", name="Вернуться в чат  ↗", exact=True).click()
                assert page.evaluate("window.closedPanel") is True
                assert not page_errors, page_errors
                assert recording.calls == [], "Панель не должна отправлять сообщения в Telegram"
                assert count_usage(gateway) == 0, "Управление не вызывает модель"
                browser.close()
                print("PASS: real Chromium + HTTP; navigation, same dialog, rename, style, models,")
                print(
                    "account, inactive payments, delete-all confirm/cancel, "
                    "mobile/desktop and close."
                )
                print("Telegram messages: 0; model calls/charges: 0; JS errors: 0.")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            asyncio.run(ctrl.close())
            asyncio.run(bot.session.close())
            gateway.close()


if __name__ == "__main__":
    main()
