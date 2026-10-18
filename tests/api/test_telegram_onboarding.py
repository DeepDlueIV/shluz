"""Сценарии автоматического подключения: ни ручных ключей, ни файлов на пользователя."""

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from app.admin.routes import _admin_csrf_token
from app.config import Settings
from app.db.models import Account, ApiToken, Identity, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token, revoke_api_token
from app.factory import create_app


@pytest.fixture
def auto_client(tmp_path):
    key = tmp_path / "connector" / "key"
    pg_url = os.environ.get("SHLUZ_ONBOARDING_TEST_POSTGRES")
    admin_engine, schema = None, None
    url = "sqlite+pysqlite:///" + str(tmp_path / "gateway.sqlite")
    if pg_url:
        parsed = make_url(pg_url)
        # Этот fixture может удалять только созданную им схему в одноразовой CI-базе.
        assert parsed.host in {"127.0.0.1", "localhost"}
        assert parsed.database == "shluz_onboarding_ci"
        schema = "onboard_test_" + uuid.uuid4().hex
        admin_engine = create_engine(pg_url, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = parsed.update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(
            hide_password=False,
        )
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(url),
        admin_username="owner",
        admin_password=SecretStr("test-password"),
        telegram_connector_file=key,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            client.connector_path = key
            yield client
    finally:
        app.state.database.dispose()
        if admin_engine:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin_engine.dispose()


def connector(client):
    assert client.connector_path.exists(), "Gateway must create its own service credential"
    return {"Authorization": "Bearer " + client.connector_path.read_text().strip()}


def start(client, user_id=123456789, **kw):
    return client.post(
        "/internal/telegram/session",
        headers=connector(client),
        json={
            "telegram_id": user_id,
            "display_name": "Тест <имя>",
            "username": "test_user",
            **kw,
        },
    )


def plan(client):
    with client.app.state.database.session() as session:
        row = Plan(code="auto-beta", name="Бета 2 запроса", monthly_request_limit=2)
        session.add(row)
        session.flush()
        return row.id


def admin_post(client, path, **data):
    return client.post(
        path,
        data={
            "csrf_token": _admin_csrf_token(client.app.state.settings),
            **data,
        },
        auth=("owner", "test-password"),
        follow_redirects=False,
    )


def approve(client, request_id, plan_id, **kw):
    r = admin_post(client, f"/admin/telegram/{request_id}/approve", plan_id=plan_id, **kw)
    assert r.status_code == 303, r.text


def invite(client, plan_id):
    assert (
        client.post(
            "/internal/telegram/hello",
            headers=connector(client),
            json={
                "username": "example_beta_bot",
            },
        ).status_code
        == 200
    )
    r = admin_post(client, "/admin/telegram/invites", plan_id=plan_id)
    assert r.status_code == 200, r.text
    match = re.search(r"https://t.me/example_beta_bot\?start=(join_[A-Za-z0-9_-]+)", r.text)
    assert match, r.text
    assert len(match[1]) <= 64
    return match[1]


def test_pending_does_not_grant_models_and_approval_is_idempotent(auto_client):
    c = auto_client
    first = start(c).json()
    assert first["state"] == "pending" and "token" not in first
    assert start(c).json()["request_id"] == first["request_id"]
    pid = plan(c)
    approve(c, first["request_id"], pid)
    approved = start(c).json()
    assert approved["state"] == "active"
    headers = {"Authorization": "Bearer " + approved["token"]}
    profile = c.get("/v1/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["plan"]["id"] == pid
    assert profile.json()["channel"] == "telegram"
    for expected in [200, 200, 429]:
        r = c.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "mock-chat",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert r.status_code == expected
    approve(c, first["request_id"], pid)
    start(c)
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 1
        assert s.scalar(select(func.count()).select_from(UsageEvent)) == 2
        assert s.scalar(select(func.count()).select_from(ApiToken)) == 0


def test_connector_secret_is_scoped_and_not_a_user_or_admin_key(auto_client):
    c = auto_client
    for headers in [{}, {"Authorization": "Bearer dev-token"}, {"Authorization": "Bearer bad"}]:
        assert (
            c.post(
                "/internal/telegram/session", json={"telegram_id": 3}, headers=headers
            ).status_code
            == 401
        )
    key_headers = connector(c)
    assert c.get("/v1/models", headers=key_headers).status_code == 401
    assert c.get("/admin/telegram", headers=key_headers).status_code == 401
    assert (
        c.post(
            "/internal/telegram/session",
            headers=key_headers,
            json={
                "telegram_id": True,
                "display_name": "name",
            },
        ).status_code
        == 422
    )


def test_invitation_is_single_use_idempotent_and_does_not_reset_quota(auto_client):
    c = auto_client
    code = invite(c, plan(c))
    first = start(c, invite=code)
    assert first.status_code == 200 and first.json()["state"] == "active"
    second = start(c, invite=code)
    assert second.status_code == 200 and second.json()["account_id"] == first.json()["account_id"]
    assert start(c, 999, invite=code).status_code == 409
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 1
        assert s.scalar(select(func.count()).select_from(Identity)) == 1
    assert first.headers["cache-control"] == "no-store"


def test_blocked_access_cannot_reinvite_and_old_session_stays_revoked(auto_client):
    c = auto_client
    pid = plan(c)
    active = start(c, invite=invite(c, pid)).json()
    headers = {"Authorization": "Bearer " + active["token"]}
    assert admin_post(c, f"/admin/telegram/{active['request_id']}/block").status_code == 303
    assert c.get("/v1/me", headers=headers).status_code == 401
    assert start(c, invite=invite(c, pid)).json()["state"] == "blocked"
    approve(c, active["request_id"], pid)
    assert start(c).json()["state"] == "active"
    assert c.get("/v1/me", headers=headers).status_code == 401


def test_legacy_token_import_preserves_account_plan_and_revocation(auto_client):
    c = auto_client
    pid = plan(c)
    raw = "shluz_" + "x" * 43
    with c.app.state.database.session() as s:
        account = Account(display_name="Existing owner")
        s.add(account)
        s.flush()
        aid = account.id
        s.add(Subscription(account_id=aid, plan_id=pid))
        tok = create_api_token(s, account_id=aid, name="Old", raw_token=raw, source="telegram")
        tid = tok.id
    result = start(c, legacy_token=raw).json()
    assert result["state"] == "active" and result["account_id"] == aid
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 1
        revoke_api_token(s, tid)
    assert start(c, legacy_token=raw).json()["state"] == "blocked"
    assert (
        c.get("/v1/me", headers={"Authorization": "Bearer " + result["token"]}).status_code == 401
    )


def test_wrong_legacy_token_cannot_bind_or_receive_new_access(auto_client):
    r = start(auto_client, legacy_token="shluz_" + "wrong" * 9)
    assert r.status_code == 401
    with auto_client.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Identity)) == 0


def test_session_cannot_bind_different_id_or_use_connector_api(auto_client):
    c = auto_client
    data = start(c, invite=invite(c, plan(c))).json()
    headers = {"Authorization": "Bearer " + data["token"]}
    assert c.post("/v1/me/telegram", headers=headers, json={"telegram_id": 999}).status_code == 403
    assert (
        c.post(
            "/internal/telegram/session",
            headers=headers,
            json={
                "telegram_id": 999,
                "display_name": "attacker",
            },
        ).status_code
        == 401
    )
    tampered = data["token"][:-5] + "AAAAA"
    assert c.get("/v1/models", headers={"Authorization": "Bearer " + tampered}).status_code == 401


def test_admin_forms_require_csrf_and_escape_telegram_names(auto_client):
    c = auto_client
    pending = start(c).json()
    page = c.get("/admin/telegram", auth=("owner", "test-password"))
    assert page.status_code == 200
    assert "Тест &lt;имя&gt;" in page.text and "Тест <имя>" not in page.text
    assert (
        c.post(
            f"/admin/telegram/{pending['request_id']}/approve",
            data={"plan_id": plan(c)},
            auth=("owner", "test-password"),
        ).status_code
        == 403
    )
    assert start(c).json()["state"] == "pending"


def test_concurrent_invite_consumption_grants_only_one_user(auto_client):
    c = auto_client
    code = invite(c, plan(c))
    headers = connector(c)

    def claim(uid):
        with TestClient(c.app) as peer:
            return peer.post(
                "/internal/telegram/session",
                headers=headers,
                json={
                    "telegram_id": uid,
                    "display_name": "Race",
                    "invite": code,
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [101, 102]))
    assert sorted(results) == [200, 409]
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 1


def test_expired_invite_fails_and_restart_preserves_admission(auto_client):
    from app.db.models import TelegramInvite

    c = auto_client
    code = invite(c, plan(c))
    with c.app.state.database.session() as s:
        row = s.scalar(select(TelegramInvite))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert start(c, invite=code).status_code == 409
    pending = start(c).json()
    with TestClient(create_app(c.app.state.settings)) as restarted:
        restarted.connector_path = c.connector_path
        assert start(restarted).json()["request_id"] == pending["request_id"]


def test_invalid_input_never_reflects_legacy_key(auto_client):
    secret = "shluz_" + "s" * 180
    response = start(auto_client, legacy_token=secret)
    assert response.status_code == 422
    assert secret not in response.text


def test_archiving_then_restoring_does_not_revive_sessions(auto_client):
    from app.admin.lifecycle import change_account_archive

    c = auto_client
    data = start(c, invite=invite(c, plan(c))).json()
    headers = {"Authorization": "Bearer " + data["token"]}
    with c.app.state.database.session() as s:
        change_account_archive(s, data["account_id"], "owner", True)
    with c.app.state.database.session() as s:
        change_account_archive(s, data["account_id"], "owner", False)
    assert c.get("/v1/me", headers=headers).status_code == 401
    assert start(c).json()["state"] == "blocked"


def test_session_expires(auto_client, monkeypatch):
    from app.telegram import credentials

    c = auto_client
    data = start(c, invite=invite(c, plan(c))).json()
    headers = {"Authorization": "Bearer " + data["token"]}
    now = credentials.time.time()
    monkeypatch.setattr(credentials.time, "time", lambda: now + 901)
    assert c.get("/v1/me", headers=headers).status_code == 401


def test_import_does_not_duplicate_existing_identity_or_overwrite_owner_name(auto_client):
    c = auto_client
    pid = plan(c)
    raw = "shluz_" + "z" * 43
    with c.app.state.database.session() as s:
        account = Account(display_name="Name set by owner")
        s.add(account)
        s.flush()
        aid = account.id
        s.add(Identity(account_id=aid, provider="telegram", subject="123456789"))
        s.add(Subscription(account_id=aid, plan_id=pid))
        create_api_token(s, account_id=aid, name="Old", raw_token=raw, source="telegram")
    assert start(c, legacy_token=raw).json()["account_id"] == aid
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Identity)) == 1
        assert s.get(Account, aid).display_name == "Name set by owner"


def test_declined_pending_request_cannot_activate_invite(auto_client):
    c = auto_client
    data = start(c).json()
    assert admin_post(c, f"/admin/telegram/{data['request_id']}/block").status_code == 303
    assert start(c, invite=invite(c, plan(c))).json()["state"] == "blocked"
    with c.app.state.database.session() as s:
        assert s.scalar(select(func.count()).select_from(Subscription)) == 0


def test_a_revoked_invite_never_gives_access(auto_client):
    from app.db.models import TelegramInvite

    c = auto_client
    code = invite(c, plan(c))
    with c.app.state.database.session() as s:
        iid = s.scalar(select(TelegramInvite)).id
    assert admin_post(c, f"/admin/telegram/invites/{iid}/revoke").status_code == 303
    assert start(c, invite=code).status_code == 409


def test_concurrent_same_id_has_one_admission_not_server_error(auto_client):
    c = auto_client
    headers = connector(c)

    def connect_one(_):
        with TestClient(c.app) as peer:
            return peer.post(
                "/internal/telegram/session",
                headers=headers,
                json={
                    "telegram_id": 555,
                    "display_name": "same",
                },
            ).json()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(connect_one, range(4)))
    assert len({x["request_id"] for x in results}) == 1


def test_pending_queue_has_a_finite_cap(auto_client):
    c = auto_client
    c.app.state.settings.telegram_pending_limit = 1
    assert start(c, 100).json()["state"] == "pending"
    assert start(c, 101).status_code == 429
    assert start(c, 100).json()["state"] == "pending"


def test_wrong_channel_key_cannot_import_telegram_access(auto_client):
    c = auto_client
    raw = "shluz_" + "h" * 43
    with c.app.state.database.session() as s:
        account = Account(display_name="API only")
        s.add(account)
        s.flush()
        create_api_token(s, account_id=account.id, name="API", raw_token=raw, source="harness")
    assert start(c, legacy_token=raw).status_code == 401


def test_session_claims_are_tied_to_one_identity(auto_client):
    from app.db.models import TelegramAdmission
    from app.telegram.credentials import issue_session

    c = auto_client
    active = start(c, invite=invite(c, plan(c))).json()
    with c.app.state.database.session() as s:
        a = s.get(TelegramAdmission, active["request_id"])
        altered = issue_session(c.connector_path.read_text(), a.id, "999", a.session_version)
    assert c.get("/v1/me", headers={"Authorization": "Bearer " + altered}).status_code == 401


def test_connector_key_creation_is_atomic_and_never_overwrites(tmp_path):
    from app.telegram.credentials import ensure_connector_key, read_connector_key

    path = tmp_path / "shared" / "key"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: ensure_connector_key(path), range(16)))
    initial = read_connector_key(path)
    assert initial is not None
    ensure_connector_key(path)
    assert read_connector_key(path) == initial
    assert path.stat().st_mode & 0o077 == 0
    path.write_text("corrupt")
    with pytest.raises(RuntimeError):
        ensure_connector_key(path)
    assert path.read_text() == "corrupt"


def test_approved_account_survives_restart_without_saved_personal_key(auto_client):
    c = auto_client
    active = start(c, invite=invite(c, plan(c))).json()
    with TestClient(create_app(c.app.state.settings)) as second:
        second.connector_path = c.connector_path
        result = start(second).json()
        assert result["account_id"] == active["account_id"]
        assert result["state"] == "active"
        assert (
            second.get("/v1/me", headers={"Authorization": "Bearer " + result["token"]}).status_code
            == 200
        )
