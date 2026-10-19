"""Официальный Telegram ID-token: настоящие подписи; подменён только HTTP JWKS."""

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from app.db.models import Account, Identity
from tests.webaccess.conftest import ask, start

CLIENT_ID = "123456789"
TG_ID = 987654321


@pytest.fixture
def signer(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-official-key", alg="RS256", use="sig")
    real = httpx.AsyncClient
    calls = []

    def respond(req):
        calls.append(req)
        assert str(req.url) == "https://oauth.telegram.org/.well-known/jwks.json"
        assert "authorization" not in req.headers
        return httpx.Response(200, json={"keys": [jwk]})

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return real(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)

    def sign(expected_nonce, **overrides):
        now = int(time.time())
        return jwt.encode(
            {"iss": "https://oauth.telegram.org", "aud": CLIENT_ID,
             "sub": "opaque-oidc-sub-not-bot-id", "id": TG_ID, "name": "Пользователь",
             "iat": now, "exp": now + 300, "nonce": expected_nonce, **overrides},
            key, algorithm="RS256", headers={"kid": "test-official-key"},
        )

    return sign, calls


def begin(browser):
    browser.app.state.web_settings.telegram_client_id = CLIENT_ID
    start(browser)
    r = browser.post("/v1/web/auth/telegram/start", json={})
    assert r.status_code == 200, r.text
    return r.json()


def finish(browser, flow, token):
    return browser.post("/v1/web/auth/telegram/verify", json={
        "challenge_id": flow["challenge_id"], "id_token": token,
    })


def test_telegram_login_creates_one_account_and_rotates_session(browser, signer):
    sign, calls = signer
    flow = begin(browser)
    assert flow["client_id"] == CLIENT_ID and flow["scope"] == ["profile"]
    old = browser.cookies.get("__Host-shluz_web")
    result = finish(browser, flow, sign(flow["nonce"]))
    assert result.status_code == 200, result.text
    assert browser.cookies.get("__Host-shluz_web") != old
    assert result.json()["authenticated"] is True
    assert len(calls) == 1
    with browser.app.state.database.session() as s:
        identity = s.scalar(select(Identity).where(Identity.provider == "telegram"))
        assert identity.subject == str(TG_ID)  # id, а не непрозрачный sub
        assert identity.account_id == result.json()["account_id"]
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert browser.get("/v1/web/me").json()["allowance"]["requests"]["remaining"] == 1
    assert finish(browser, flow, sign(flow["nonce"])).status_code == 401


@pytest.mark.parametrize("bad", [
    {"iss": "https://attacker.invalid"}, {"aud": "other-bot"},
    {"nonce": "different"}, {"exp": 1}, {"iat": 9999999999},
    {"id": True}, {"id": "123"}, {"id": 0}, {"sub": ""},
])
def test_invalid_telegram_claims_cannot_register(browser, signer, bad):
    sign, _ = signer
    flow = begin(browser)
    result = finish(browser, flow, sign(flow["nonce"], **bad))
    assert result.status_code == 401, result.text
    with browser.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Identity)) == 0


def test_telegram_returns_same_bot_account_without_reset(browser, signer):
    from app.product.service import grant_demo

    sign, _ = signer
    with browser.app.state.database.session() as s:
        account = Account(display_name="Имя владельца")
        s.add(account)
        s.flush()
        ident = account.id
        s.add(Identity(provider="telegram", subject=str(TG_ID), account_id=ident))
        grant_demo(s, ident).requests_used = 1
    flow = begin(browser)
    result = finish(browser, flow, sign(flow["nonce"]))
    assert result.status_code == 200, result.text
    assert result.json()["account_id"] == ident
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert browser.get("/v1/web/me").json()["allowance"]["requests"]["remaining"] == 0
    with browser.app.state.database.session() as s:
        assert s.get(Account, ident).display_name == "Имя владельца"


def test_telegram_preserves_spent_guest_demo(browser, signer):
    sign, _ = signer
    flow = begin(browser)
    assert ask(browser).status_code == 200
    guest = browser.get("/v1/web/me").json()["account"]["id"]
    result = finish(browser, flow, sign(flow["nonce"]))
    assert result.status_code == 200, result.text
    assert result.json()["account_id"] == guest
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert ask(browser).status_code == 429


def test_telegram_challenge_bound_to_browser_and_csrf(browser, signer):
    sign, _ = signer
    flow = begin(browser)
    token = sign(flow["nonce"])
    csrf = browser.headers.pop("X-CSRF-Token")
    assert finish(browser, flow, token).status_code == 403
    browser.headers["X-CSRF-Token"] = csrf
    browser.cookies.clear()
    start(browser)
    assert finish(browser, flow, token).status_code == 401


def test_telegram_not_configured_is_closed(browser):
    start(browser)
    r = browser.post("/v1/web/auth/telegram/start", json={})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "telegram_login_not_configured"


def test_telegram_jwt_not_reflected_in_errors_or_logs(browser, signer, caplog):
    sign, _ = signer
    flow = begin(browser)
    token = sign(flow["nonce"], aud="wrong")
    r = finish(browser, flow, token)
    assert r.status_code == 401
    assert token not in r.text and token not in caplog.text
    r = browser.post("/v1/web/auth/telegram/verify", json={"id_token": token})
    assert r.status_code == 422 and token not in r.text


def test_forged_signature_and_algorithm_cannot_login(browser, signer):
    sign, _ = signer
    flow = begin(browser)
    claims = jwt.decode(sign(flow["nonce"]), options={"verify_signature": False})
    foreign = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, foreign, algorithm="RS256", headers={"kid": "test-official-key"})
    assert finish(browser, flow, forged).status_code == 401
    unsigned = jwt.encode(claims, key="", algorithm="none", headers={"kid": "test-official-key"})
    assert finish(browser, flow, unsigned).status_code == 401


def test_expired_challenge_and_attempt_limit(browser, signer):
    from app.webaccess.models import TelegramLoginChallenge

    sign, _ = signer
    flow = begin(browser)
    for _ in range(5):
        assert finish(browser, flow, sign(flow["nonce"], aud="wrong")).status_code == 401
    assert finish(browser, flow, sign(flow["nonce"])).status_code == 401
    flow = begin(browser)
    with browser.app.state.database.session() as s:
        for row in s.scalars(select(TelegramLoginChallenge)):
            row.expires = 0
    assert finish(browser, flow, sign(flow["nonce"])).status_code == 401


def test_parallel_redemption_has_one_session(browser, signer):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    sign, _ = signer
    flow = begin(browser)
    token = sign(flow["nonce"])
    cookies, headers = dict(browser.cookies), dict(browser.headers)

    def redeem(_):
        with TestClient(browser.app, base_url="https://shluz.test",
                        client=("198.51.100.27", 1234)) as other:
            other.cookies.update(cookies)
            other.headers.update(headers)
            return finish(other, flow, token).status_code

    # Кеш подготовлен настоящим verifier; сеть не влияет на порядок DB-гонки.
    import asyncio

    asyncio.run(browser.app.state.telegram_login.key_for("test-official-key"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(redeem, range(2)))
    assert statuses == [200, 401]
    with browser.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Identity)) == 1


def test_jwks_unavailable_does_not_allow_login(browser, signer, monkeypatch):
    from app.webaccess.telegram_login import TelegramUnavailable

    sign, _ = signer
    flow = begin(browser)

    async def fail(_):
        raise TelegramUnavailable()

    monkeypatch.setattr(browser.app.state.telegram_login, "key_for", fail)
    assert finish(browser, flow, sign(flow["nonce"])).status_code == 503


def test_email_identity_is_not_silently_linked(browser, signer):
    from tests.webaccess.conftest import confirm, request_otp

    sign, _ = signer
    start(browser)
    otp = request_otp(browser).json()
    result = confirm(browser, otp["challenge_id"], browser.app.state.web_mailer.messages[-1][1])
    email_id = result.json()["account_id"]
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    flow = begin(browser)
    result = finish(browser, flow, sign(flow["nonce"]))
    assert result.status_code == 200 and result.json()["account_id"] != email_id
    with browser.app.state.database.session() as s:
        assert s.get(Account, email_id).status == "active"


def test_telegram_new_identity_used_by_bot_start_without_second_demo(browser, signer):
    from app.telegram.routes import SessionInput
    from app.telegram.service import connect

    sign, _ = signer
    flow = begin(browser)
    result = finish(browser, flow, sign(flow["nonce"]))
    assert result.status_code == 200
    browser.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    assert ask(browser).status_code == 200
    row = connect(browser.app.state.database, SessionInput(
        telegram_id=TG_ID, display_name="Telegram",
    ))
    assert row.status == "approved" and row.account_id == result.json()["account_id"]
    assert browser.get("/v1/web/me").json()["allowance"]["requests"]["remaining"] == 0


def test_telegram_schema_migrates_without_touching_accounts(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from pydantic import SecretStr
    from sqlalchemy import inspect

    from app.config import Settings
    from app.db.database import Database

    url = "sqlite+pysqlite:///" + str(tmp_path / "tg-migration.db")
    monkeypatch.setenv("SHLUZ_DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "20261019_09")
    db = Database(Settings(database_url=SecretStr(url), _env_file=None))
    with db.session() as s:
        a = Account(display_name="Не менять")
        s.add(a)
        s.flush()
        ident = a.id
    command.upgrade(cfg, "head")
    assert "web_telegram_login_challenges" in inspect(db.engine).get_table_names()
    with db.session() as s:
        assert s.get(Account, ident).display_name == "Не менять"
    command.upgrade(cfg, "head")
    db.dispose()


@pytest.mark.parametrize("status,role", [("inactive", "user"), ("active", "service")])
def test_telegram_does_not_open_inactive_or_service_account(browser, signer, status, role):
    sign, _ = signer
    with browser.app.state.database.session() as s:
        a = Account(display_name="Недоступный", status=status, role=role)
        s.add(a)
        s.flush()
        s.add(Identity(provider="telegram", subject=str(TG_ID), account_id=a.id))
    flow = begin(browser)
    assert finish(browser, flow, sign(flow["nonce"])).status_code == 401


def test_jwks_redirect_is_rejected_without_following_or_leaking_token(browser, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    flow = begin(browser)
    now = int(time.time())
    token = jwt.encode({"iss": "https://oauth.telegram.org", "aud": CLIENT_ID,
                        "sub": "opaque", "id": TG_ID, "iat": now, "exp": now + 300,
                        "nonce": flow["nonce"]}, key, algorithm="RS256", headers={"kid": "a"})
    real = httpx.AsyncClient
    calls = []

    def respond(req):
        calls.append(req)
        assert "authorization" not in req.headers
        return httpx.Response(302, headers={"Location": "https://attacker.invalid"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(respond), **kw))
    assert finish(browser, flow, token).status_code == 503
    assert len(calls) == 1 and str(calls[0].url).startswith("https://oauth.telegram.org/")
