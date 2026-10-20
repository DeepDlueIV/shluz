"""Негативные сценарии: подмена сети, повтор OTP, ложная проверка и смена сессии."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from starlette.requests import Request

from app.factory import create_app
from app.webaccess.adapters import Turnstile
from app.webaccess.models import OtpChallenge, WebBucket
from app.webaccess.security import client_network
from app.webaccess.settings import WebSettings
from tests.webaccess.conftest import ask, confirm, request_otp, start


def request(peer, forwarded=""):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/web/session",
            "headers": [(b"x-forwarded-for", forwarded.encode())],
            "client": (peer, 1),
            "server": ("shluz.test", 443),
            "scheme": "https",
            "query_string": b"",
        }
    )


def test_ip_and_ipv6_prefix_trust_are_explicit():
    settings = WebSettings(_env_file=None, secret=SecretStr("s" * 40))
    assert client_network(request("198.51.100.1", "203.0.113.9"), settings)[0] == "198.51.100.1"
    a = client_network(request("2001:db8:1:2::1"), settings)[1]
    b = client_network(request("2001:db8:1:2::ffff"), settings)[1]
    assert a == b
    assert (
        client_network(request("::ffff:198.51.100.1"), settings)[1]
        == client_network(request("198.51.100.1"), settings)[1]
    )
    settings.trusted_proxies = ["10.10.0.0/24"]
    assert (
        client_network(request("10.10.0.3", "1.1.1.1, 198.51.100.77, 10.10.0.2"), settings)[0]
        == "198.51.100.77"
    )
    with pytest.raises(HTTPException):
        client_network(request("10.10.0.3", "not-an-ip"), settings)
    with pytest.raises(ValidationError):
        WebSettings(_env_file=None, trusted_proxies=["0.0.0.0/0"])


@pytest.mark.parametrize(
    "change",
    [
        {"success": False},
        {"hostname": "evil.test"},
        {"action": "not-demo"},
        {"challenge_ts": (datetime.now(UTC) - timedelta(minutes=6)).isoformat()},
        {"challenge_ts": "bad"},
    ],
)
def test_turnstile_rejects_wrong_signed_result(monkeypatch, change):
    settings = WebSettings(
        _env_file=None,
        captcha_secret=SecretStr("synthetic-provider-secret"),
        captcha_hostnames=["shluz.test"],
    )
    actual = httpx.AsyncClient
    result = {
        "success": True,
        "hostname": "shluz.test",
        "action": "demo_chat",
        "challenge_ts": datetime.now(UTC).isoformat(),
    }
    calls = []

    def transport(req):
        calls.append(req)
        assert req.url == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        return httpx.Response(200, json={**result, **change})

    def client(**kw):
        assert kw["follow_redirects"] is False and kw["trust_env"] is False
        return actual(transport=httpx.MockTransport(transport), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert asyncio.run(Turnstile(settings).verify("x", "198.51.100.1", "demo_chat")) is False
    assert len(calls) == 1


def test_turnstile_valid_response_and_dummy_key_fail_closed(monkeypatch):
    settings = WebSettings(
        _env_file=None,
        captcha_secret=SecretStr("synthetic-secret"),
        captcha_hostnames=["shluz.test"],
    )
    real = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "success": True,
                "hostname": "shluz.test",
                "action": "demo_chat",
                "challenge_ts": datetime.now(UTC).isoformat(),
            },
        )
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    assert asyncio.run(Turnstile(settings).verify("x", "198.51.100.1", "demo_chat")) is True
    settings.captcha_secret = SecretStr("1x0000000000000000000000000000000AA")
    assert asyncio.run(Turnstile(settings).verify("x", "198.51.100.1", "demo_chat")) is False


def test_adapters_cannot_be_injected_into_production(test_settings):
    with pytest.raises(ValueError):
        create_app(test_settings, web_captcha=object())


def test_otp_expiry_binding_and_second_browser_cannot_redeem(browser):
    start(browser)
    issued = request_otp(browser)
    flow = issued.json()["challenge_id"]
    code = browser.app.state.web_mailer.messages[-1][1]
    browser.cookies.clear()
    start(browser)
    assert confirm(browser, flow, code).status_code == 401
    with browser.app.state.database.session() as s:
        row = s.scalar(select(OtpChallenge))
        assert not row.used and row.attempts == 0
        row.expires = 0
    assert confirm(browser, flow, code).status_code == 401


def test_return_to_existing_email_consumes_guest_demo_without_merging_wallets(browser):
    start(browser)
    issued = request_otp(browser)
    result = confirm(
        browser, issued.json()["challenge_id"], browser.app.state.web_mailer.messages[-1][1]
    )
    ident = result.json()["account_id"]
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert browser.post("/v1/web/logout").status_code == 200
    start(browser)
    assert ask(browser).status_code == 200
    guest_id = browser.get("/v1/web/me").json()["account"]["id"]
    assert ident != guest_id
    with browser.app.state.database.session() as s:
        for bucket in s.scalars(select(WebBucket).where(WebBucket.id.like("otp:cooldown:%"))):
            bucket.expires = 0
    issued = request_otp(browser)
    result = confirm(
        browser, issued.json()["challenge_id"], browser.app.state.web_mailer.messages[-1][1]
    )
    assert result.status_code == 200
    assert result.json()["account_id"] == ident
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert browser.get("/v1/web/me").json()["allowance"]["requests"]["remaining"] == 0
    assert ask(browser).status_code == 429


def test_startup_removes_expired_network_identifiers(web_app):
    from fastapi.testclient import TestClient

    with web_app.state.database.session() as s:
        s.add(WebBucket(id="expired-network-digest", count=2, expires=1))
    with TestClient(web_app), web_app.state.database.session() as s:
        assert s.get(WebBucket, "expired-network-digest") is None


def test_parallel_otp_redemption_grants_one_session(browser):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient
    from sqlalchemy import func

    from app.db.models import Identity
    from app.webaccess.models import WebSession

    start(browser)
    flow = request_otp(browser).json()["challenge_id"]
    code = browser.app.state.web_mailer.messages[-1][1]
    cookie = dict(browser.cookies)
    headers = dict(browser.headers)

    def redeem(_):
        with TestClient(
            browser.app, base_url="https://shluz.test", client=("198.51.100.27", 1234)
        ) as c:
            c.cookies.update(cookie)
            c.headers.update(headers)
            return confirm(c, flow, code).status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(redeem, range(5)))
    assert outcomes.count(200) == 1 and outcomes.count(401) == 4
    with browser.app.state.database.session() as s:
        assert (
            s.scalar(select(func.count()).select_from(Identity).where(Identity.provider == "email"))
            == 1
        )
        assert (
            s.scalar(
                select(func.count())
                .select_from(WebSession)
                .where(WebSession.authenticated.is_(True))
            )
            == 1
        )


def test_restart_and_changed_network_do_not_reset_cookie_quota(browser):
    from fastapi.testclient import TestClient

    start(browser)
    assert ask(browser).status_code == 200
    cookie, headers = dict(browser.cookies), dict(browser.headers)
    # Тот же сервер после повторного lifespan и другой IP: сессия хранится в БД.
    with TestClient(browser.app, base_url="https://shluz.test", client=("203.0.113.99", 4)) as c:
        c.cookies.update(cookie)
        c.headers.update(headers)
        assert c.get("/v1/web/me").json()["allowance"]["requests"]["remaining"] == 0
        assert ask(c).status_code == 429


def test_gateway_database_failure_never_grants_guest_access(browser, monkeypatch):
    from contextlib import contextmanager

    from sqlalchemy.exc import OperationalError

    @contextmanager
    def unavailable():
        raise OperationalError("SELECT secret-private-data", {}, Exception("private"))
        yield

    monkeypatch.setattr(browser.app.state.database, "session", unavailable)
    response = browser.post("/v1/web/session")
    assert response.status_code == 503
    assert "private" not in response.text and not response.cookies


def test_network_http_limit_can_be_reduced_in_control(browser):
    from app.webaccess.security import control

    with browser.app.state.database.session() as s:
        cfg = control(s)
        cfg.http_network_minute = 1
    start(browser)
    assert browser.get("/v1/web/me").status_code == 429
