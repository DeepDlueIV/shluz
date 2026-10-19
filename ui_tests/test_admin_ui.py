"""Chromium проверяет реальный HTTP-интерфейс на вымышленных данных."""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright
from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import (
    Account,
    Plan,
    Subscription,
    TelegramAdmission,
    TelegramIntegration,
    UsageEvent,
)
from app.db.repositories import create_api_token
from tests.server_support import GatewayProcess


@pytest.fixture(scope="module")
def preview(tmp_path_factory):
    server = GatewayProcess(tmp_path_factory.mktemp("admin-ui"))
    server.migrate()
    database = Database(Settings(database_url=SecretStr(server.database_url), _env_file=None))
    with database.session() as session:
        plans = [
            Plan(
                code="base",
                name="Базовый",
                monthly_price_usd=Decimal("9"),
                monthly_request_limit=200,
            ),
            Plan(code="trial", name="Тестовый", monthly_request_limit=2),
        ]
        people = [Account(display_name="Алексей"), Account(display_name="Мария")]
        session.add_all(
            plans
            + people
            + [Plan(code="tiny", name="Малый бюджет", monthly_cost_limit_usd=Decimal("6E-8"))]
        )
        session.flush()
        server.alice_id = people[0].id
        for account, plan in zip(people, plans, strict=True):
            session.add(Subscription(account_id=account.id, plan_id=plan.id))
            create_api_token(
                session,
                account_id=account.id,
                name="Личный компьютер",
                source="harness",
                raw_token="shluz_ui_only_" + account.id,
            )
            for _ in range(2):
                session.add(
                    UsageEvent(
                        account_id=account.id,
                        provider="mock",
                        model="mock-chat",
                        source="harness",
                        status="success",
                    )
                )
    with database.session() as session:
        admission = TelegramAdmission(
            telegram_id="987654321", display_name="Новый участник", username="new_person"
        )
        session.add(admission)
        session.add(TelegramIntegration(id="default", username="test_shluz_bot"))
        session.flush()
        server.admission_id = admission.id
    server.start()
    yield server, database
    server.stop()
    database.engine.dispose()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as tool:
        browser = tool.chromium.launch(
            headless=True,
            executable_path=os.environ.get("SHLUZ_BROWSER_EXECUTABLE"),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        yield browser
        browser.close()


@pytest.fixture
def page(browser, preview):
    server, _ = preview
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        http_credentials={"username": server.auth[0], "password": server.auth[1]},
    )
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(server.url) else route.abort()
        ),
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert not errors, errors


def capture(page, name):
    path = Path(os.environ.get("SHLUZ_UI_SCREENSHOTS", "/tmp/shluz-admin-ui"))
    path.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path / (name + ".png")), full_page=True)


def test_navigation_and_layout(page, preview):
    server, _ = preview
    for path, label in [
        ("", "Обзор"),
        ("/users", "Пользователи"),
        ("/plans", "Тарифы"),
        ("/usage", "Расходы"),
        ("/telegram", "Telegram"),
        ("/logs", "Журнал"),
        ("/test", "Проверка запроса"),
        ("/providers/venice", "Поставщик"),
    ]:
        page.goto(server.url + "/admin" + path)
        expect(page.locator('.app-nav [aria-current="page"]')).to_have_text(label)
        expect(page.locator("h1")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert "0E-8" not in page.locator("body").inner_text()
        if path in ["", "/users", "/plans", "/usage"]:
            capture(page, "desktop-" + (path.strip("/") or "overview"))
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        assert page.locator(".app-header").bounding_box()["y"] == 0


def test_tooltip_hover_focus_escape_and_keyboard(page, preview):
    page.goto(preview[0].url + "/admin/plans")
    form = page.locator('form[action="/admin/plans"]')
    form.locator("summary").click()
    button = form.get_by_role("button", name="Справка: Резерв кредитов на запрос", exact=True)
    button.hover()
    tooltip = page.get_by_role("tooltip")
    expect(tooltip).to_be_visible()
    expect(tooltip).to_contain_text("не дополнительная плата")
    box = tooltip.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(250)
    expect(tooltip).to_be_visible()
    capture(page, "reserve-tooltip")
    page.keyboard.press("Escape")
    expect(tooltip).to_be_hidden()
    button.focus()
    expect(tooltip).to_be_visible()
    assert button.get_attribute("aria-describedby") == "admin-help-popup"
    button.press("Enter")
    expect(tooltip).to_be_visible()
    assert page.url.endswith("/admin/plans")
    page.keyboard.press("Escape")
    expect(tooltip).to_be_hidden()


def test_mobile_help_fits_viewport(page, preview):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(preview[0].url + "/admin/plans")
    form = page.locator('form[action="/admin/plans"]')
    form.locator("summary").click()
    form.get_by_role("button", name="Справка: Бюджет на модели", exact=True).click()
    tooltip = page.get_by_role("tooltip")
    expect(tooltip).to_be_visible()
    box = tooltip.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert box["y"] >= 0 and box["y"] + box["height"] <= 844
    page.goto(preview[0].url + "/admin/users")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "mobile-users")


def test_name_link_and_search(page, preview):
    server, _ = preview
    page.goto(server.url + "/admin/usage")
    page.get_by_role("link", name="Алексей", exact=True).first.click()
    row = page.locator(f"#user-{server.alice_id}")
    expect(row).to_be_visible()
    expect(row.locator(".identifier")).to_be_hidden()
    page.locator("#user-search").fill("Мария")
    expect(row).to_be_hidden()
    expect(page.locator("[data-filter-count]")).to_contain_text("1")
    page.locator("#user-search").fill(server.alice_id)
    expect(row).to_be_visible()


def test_collapsed_advanced_values_survive_save(page, preview):
    server, database = preview
    page.goto(server.url + "/admin/plans")
    row = page.locator("tbody > tr").filter(has_text="Малый бюджет").first
    row.locator(".management-cell > details").first.locator("summary").first.click()
    form = row.locator(".plan-edit-form")
    expect(form.locator('[name="monthly_cost_limit_usd"]')).to_be_hidden()
    form.locator('[name="name"]').fill("Малый бюджет · обновлён")
    form.get_by_role("button", name="Сохранить", exact=True).click()
    expect(page.locator("tbody")).to_contain_text("Малый бюджет · обновлён")
    with database.session() as session:
        plan = session.scalar(select(Plan).where(Plan.code == "tiny"))
        assert plan.monthly_cost_limit_usd == Decimal("6E-8") and plan.active


def test_unnamed_key_and_copy(page, preview):
    server, _ = preview
    page.goto(server.url + "/admin/users")
    row = page.locator(f"#user-{server.alice_id}")
    row.locator(".token-issuer > summary").click()
    row.locator('[name="source"]').select_option("telegram")
    row.get_by_role("button", name="Создать токен", exact=True).click()
    expect(page.locator("h1")).to_have_text("Ключ доступа создан")
    expect(page.locator(".token-secret")).to_contain_text("Telegram ·")
    page.get_by_role("button", name="Скопировать ключ", exact=True).click()
    expect(page.locator(".copy-status")).not_to_be_empty()
    # Даже тестовый полный ключ не попадает в скриншоты.


def test_no_javascript_fallback(browser, preview):
    server, _ = preview
    context = browser.new_context(
        java_script_enabled=False,
        http_credentials={"username": server.auth[0], "password": server.auth[1]},
    )
    try:
        page = context.new_page()
        page.goto(server.url + "/admin/plans")
        expect(page.locator(".app-nav")).to_be_visible()
        form = page.locator('form[action="/admin/plans"]')
        form.locator("summary").click()
        button = form.get_by_role("button", name="Справка: Резерв кредитов на запрос")
        assert "Временная бронь" in button.get_attribute("title")
        expect(form.locator('[name="request_credit_reserve"]')).to_be_visible()
    finally:
        context.close()


def test_telegram_onboarding_approve_and_invite_without_per_user_files(page, preview):
    import re

    server, _ = preview
    page.goto(server.url + "/admin/telegram")
    row = page.locator("#tg-" + server.admission_id)
    expect(row).to_contain_text("Ожидает одобрения")
    row.locator('select[name="plan_id"]').select_option(label="Базовый")
    row.get_by_role("button", name="Разрешить доступ", exact=True).click()
    expect(page.locator("#tg-" + server.admission_id)).to_contain_text("Разрешён")
    capture(page, "telegram-onboarding")
    page.get_by_role("button", name="Создать приглашение", exact=True).click()
    link = page.locator("#invite-link").inner_text()
    assert re.fullmatch(r"https://t.me/test_shluz_bot\?start=join_[A-Za-z0-9_-]{32}", link)
    page.get_by_role("button", name="Скопировать приглашение").click()
    expect(page.locator(".copy-status")).to_contain_text("Приглашение")
    page.get_by_role("link", name="Вернуться к Telegram").click()
    expect(page.get_by_role("heading", name="Telegram", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_demo_policy_screen_and_safe_model_form(page, preview):
    page.goto(preview[0].url + "/admin/access")
    expect(page.get_by_role("heading", name="Демо и модели", exact=True)).to_be_visible()
    expect(page.locator("#demo_requests")).to_have_value("1")
    page.locator("#demo_requests").fill("5")
    page.get_by_role("button", name="Сохранить демо", exact=True).click()
    expect(page.locator("#demo_requests")).to_have_value("5")
    page.locator("#demo_requests").fill("1")
    page.get_by_role("button", name="Сохранить демо", exact=True).click()
    expect(page.locator("#demo_requests")).to_have_value("1")
    page.locator(".help-trigger").first.hover()
    expect(page.get_by_role("tooltip")).to_contain_text("Разовый пакет")
    page.keyboard.press("Escape")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "demo-model-permissions")


def test_diagnostics_navigation_tooltips_and_mobile(page, preview):
    server, _ = preview
    page.goto(server.url + "/admin/diagnostics")
    expect(page.get_by_role("heading", name="Диагностика моделей")).to_be_visible()
    expect(page.locator('.app-nav [aria-current="page"]')).to_have_text("Диагностика")
    assert "0E-8" not in page.locator("body").inner_text()
    page.get_by_role("button", name="Справка: p95", exact=True).hover()
    expect(page.get_by_role("tooltip")).to_be_visible()
    expect(page.get_by_role("tooltip")).to_contain_text("95%")
    page.keyboard.press("Escape")
    capture(page, "diagnostics-desktop")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(page.get_by_role("heading", name="Диагностика моделей")).to_be_visible()
    capture(page, "diagnostics-mobile")


def test_web_protection_ready_states_and_mobile(page, preview):
    page.goto(preview[0].url + "/admin/web-access")
    expect(page.get_by_role("heading", name="Защита демо", exact=True)).to_be_visible()
    expect(page.locator('.app-nav [aria-current="page"]')).to_have_text("Защита демо")
    expect(page.get_by_text("Нужен почтовый сервис", exact=True)).to_be_visible()
    expect(page.locator("#daily_usd")).to_have_value("0.00000000")
    page.locator(".help-trigger").first.hover()
    expect(page.get_by_role("tooltip")).to_contain_text("Общий резерв")
    page.keyboard.press("Escape")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "web-protection-desktop")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "web-protection-mobile")
