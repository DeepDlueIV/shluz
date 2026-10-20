"""Изолированные adapters: не отправляют писем и не проверяют настоящие токены."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.factory import create_app
from app.webaccess.settings import WebSettings


class Captcha:
    def __init__(self):
        self.calls = []
        self.accept = True

    async def verify(self, token, ip, action):
        self.calls.append((token, ip, action))
        return self.accept and token.startswith("test-verified-")


class Mailer:
    configured = True

    def __init__(self):
        self.messages = []
        self.succeed = True

    async def send_code(self, address, code):
        self.messages.append((address, code))
        return self.succeed


@pytest.fixture
def web_app(tmp_path):
    url = "sqlite+pysqlite:///" + str(tmp_path / "test.db")
    pg_url = os.environ.get("SHLUZ_ONBOARDING_TEST_POSTGRES")
    engine = schema = None
    if pg_url:
        parsed = make_url(pg_url)
        assert parsed.host in {"127.0.0.1", "localhost"}
        assert parsed.database == "shluz_onboarding_ci"
        schema = "web_test_" + uuid.uuid4().hex
        engine = create_engine(pg_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = parsed.update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(
            hide_password=False
        )
    settings = Settings(environment="test", database_url=SecretStr(url), _env_file=None)
    web = WebSettings(
        enabled=True,
        secret=SecretStr("test-" + "s" * 40),
        origins=["https://shluz.test"],
        _env_file=None,
    )
    app = create_app(settings, web_settings=web, web_captcha=Captcha(), web_mailer=Mailer())
    try:
        yield app
    finally:
        app.state.database.dispose()
        if engine:
            with engine.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()


@pytest.fixture
def browser(web_app):
    with TestClient(
        web_app, base_url="https://shluz.test", client=("198.51.100.27", 1234)
    ) as client:
        client.headers["Origin"] = "https://shluz.test"
        yield client


def captcha():
    return "test-verified-" + uuid.uuid4().hex


def start(client):
    result = client.post("/v1/web/session")
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result.json()


def ask(client, **kw):
    return client.post(
        "/v1/web/chat/completions",
        json={
            "prompt": "Тест реального потока",
            "request_id": str(uuid.uuid4()),
            "turnstile_token": captcha(),
            **kw,
        },
    )


def request_otp(client, email="person@example.org"):
    return client.post(
        "/v1/web/auth/otp/request", json={"email": email, "turnstile_token": captcha()}
    )


def confirm(client, flow, code):
    return client.post("/v1/web/auth/otp/verify", json={"challenge_id": flow, "code": code})
