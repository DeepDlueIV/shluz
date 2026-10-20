"""Проверки публичного cookie-периметра и настоящего пути генерации через шлюз."""

import uuid

from sqlalchemy import func, select

from app.db.models import Account, UsageEvent
from app.webaccess.models import DemoControl, OtpChallenge
from tests.webaccess.conftest import ask, captcha, confirm, request_otp, start


def test_empty_session_creates_no_account_then_one_stream(browser):
    start(browser)
    db = browser.app.state.database
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Account)) == 1  # bootstrap
    result = ask(browser)
    assert result.status_code == 200, result.text
    assert "text/event-stream" in result.headers["content-type"]
    assert "Shluz mock reply" in result.text
    assert "[DONE]" in result.text
    assert ask(browser).status_code == 429
    start(browser)
    assert ask(browser).status_code == 429
    me = browser.get("/v1/web/me").json()
    assert me["allowance"]["requests"]["remaining"] == 0
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(UsageEvent)) == 1


def test_captcha_csrf_cookie_and_bearer_are_independent(browser):
    assert ask(browser).status_code == 401
    start(browser)
    browser.app.state.web_captcha.accept = False
    assert ask(browser).status_code == 403
    browser.app.state.web_captcha.accept = True
    del browser.headers["X-CSRF-Token"]
    assert ask(browser).status_code == 403
    start(browser)
    assert browser.get("/v1/models").status_code == 401
    assert browser.get("/admin").status_code == 401
    assert browser.get("/internal/telegram/session").status_code in (401, 405)
    bad = ask(browser, max_tokens=99999)
    assert bad.status_code == 422
    assert "99999" not in bad.text
    assert ask(browser, model="mock-chat").status_code == 403
    assert ask(browser, prompt="a" * 3001).status_code == 400
    assert browser.get("/v1/web/me").json()["allowance"]["requests"]["remaining"] == 1


def test_raw_ip_headers_do_not_bypass_network_limit(browser):
    for n in range(3):
        browser.cookies.clear()
        browser.headers["X-Forwarded-For"] = f"203.0.113.{n + 1}"
        browser.headers["CF-Connecting-IP"] = f"203.0.113.{n + 1}"
        start(browser)
        assert ask(browser).status_code == 200
    browser.cookies.clear()
    start(browser)
    assert ask(browser).status_code == 429


def test_duplicate_id_or_captcha_is_not_replayed(browser):
    start(browser)
    ident = str(uuid.uuid4())
    token = captcha()
    assert ask(browser, request_id=ident, turnstile_token=token).status_code == 200
    assert ask(browser, request_id=ident).status_code == 409
    browser.cookies.clear()
    start(browser)
    assert ask(browser, turnstile_token=token).status_code == 403
    with browser.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(UsageEvent)) == 1


def test_session_cookie_security_and_bad_origin(browser):
    response = browser.post("/v1/web/session")
    cookie = response.headers["set-cookie"]
    assert "__Host-shluz_web=" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Domain=" not in cookie
    browser.headers["Origin"] = "https://evil.test"
    assert browser.post("/v1/web/session").status_code == 403
    r = browser.options("/v1/web/session")
    assert "access-control-allow-origin" not in r.headers


def test_size_checks_precede_schema_and_code_never_echoed(browser):
    start(browser)
    huge = browser.post(
        "/v1/web/auth/otp/verify",
        content=b"X" * 33000,
        headers={"Content-Type": "application/json"},
    )
    assert huge.status_code == 413
    r = browser.post(
        "/v1/web/auth/otp/verify",
        json={"challenge_id": "private-secret-marker", "code": "private-otp-marker"},
    )
    assert r.status_code == 422 and "private-" not in r.text
    chunks = (b"X" * 8000 for _ in range(5))
    r = browser.post(
        "/v1/web/chat/completions", content=chunks, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_otp_promotes_guest_without_new_demo_or_api_key(browser):
    start(browser)
    assert ask(browser).status_code == 200
    old_id = browser.get("/v1/web/me").json()["account"]["id"]
    old_cookie = browser.cookies.get("__Host-shluz_web")
    result = request_otp(browser)
    assert result.status_code == 200, result.text
    flow = result.json()["challenge_id"]
    code = browser.app.state.web_mailer.messages[-1][1]
    assert code not in result.text
    with browser.app.state.database.session() as s:
        row = s.scalar(select(OtpChallenge))
        assert row.code_hash != code and len(row.code_hash) == 64
    result = confirm(browser, flow, code)
    assert result.status_code == 200, result.text
    assert result.json()["account_id"] == old_id
    assert browser.cookies.get("__Host-shluz_web") != old_cookie
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert ask(browser).status_code == 429
    assert confirm(browser, flow, code).status_code == 401
    assert browser.get("/v1/models").status_code == 401  # cookie не стала Bearer-key
    assert browser.post("/v1/web/logout").status_code == 200
    assert browser.get("/v1/web/me").status_code == 401


def test_otp_attempt_limit_and_cooldown(browser):
    start(browser)
    result = request_otp(browser)
    assert result.status_code == 200
    flow = result.json()["challenge_id"]
    actual = browser.app.state.web_mailer.messages[-1][1]
    wrong = "000000" if actual != "000000" else "111111"
    for _ in range(5):
        assert confirm(browser, flow, wrong).status_code == 401
    assert confirm(browser, flow, actual).status_code == 401
    assert request_otp(browser).status_code == 429
    with browser.app.state.database.session() as s:
        row = s.scalar(select(OtpChallenge))
        assert row.used and not row.code_hash and not row.email


def test_missing_mailer_and_failed_mail_do_not_claim_delivery(browser):
    start(browser)
    browser.app.state.web_mailer.configured = False
    assert request_otp(browser).status_code == 503
    browser.app.state.web_mailer.configured = True
    browser.app.state.web_mailer.succeed = False
    assert request_otp(browser).status_code == 503
    flow = browser.app.state.web_mailer.messages
    assert len(flow) == 1


def test_kill_switch_stops_demo_before_provider(browser):
    start(browser)
    with browser.app.state.database.session() as s:
        cfg = s.get(DemoControl, "default")
        cfg.enabled = False
    assert ask(browser).status_code == 429
    with browser.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(UsageEvent)) == 0
