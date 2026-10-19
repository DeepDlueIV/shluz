# ruff: noqa: F811
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.admin.lifecycle import change_account_archive
from app.db.models import Account, ApiToken, Subscription, TelegramAdmission
from app.db.repositories import create_api_token
from tests.api.test_telegram_onboarding import (  # noqa: F401
    admin_post,
    approve,
    auto_client,
    invite,
    plan,
    start,
)


def connected(c):
    row = start(c).json()
    approve(c, row["request_id"], plan(c))
    return start(c).json()


def test_restore_resumes_new_session_and_same_plan_not_old_token(auto_client):
    c = auto_client
    data = connected(c)
    db = c.app.state.database
    end = datetime.now(UTC) + timedelta(days=7)
    with db.session() as s:
        sub = s.scalar(select(Subscription).where(Subscription.account_id == data["account_id"]))
        sub_id = sub.id
        sub.ends_at = end
        create_api_token(
            s,
            account_id=data["account_id"],
            name="old",
            source="telegram",
            raw_token="shluz_old_never_revive",
        )
    with db.session() as s:
        change_account_archive(s, data["account_id"], "owner", True)
    assert start(c).json()["state"] != "active"
    with db.session() as s:
        change_account_archive(s, data["account_id"], "owner", False)
    new = start(c).json()
    assert new["state"] == "active"
    assert new["account_id"] == data["account_id"]
    assert c.get("/v1/me", headers={"Authorization": "Bearer " + data["token"]}).status_code == 401
    assert c.get("/v1/me", headers={"Authorization": "Bearer " + new["token"]}).status_code == 200
    with db.session() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.status == "active"
        assert sub.ends_at.replace(tzinfo=UTC) == end
        assert s.scalar(select(ApiToken)).revoked_at is not None


def test_explicit_block_not_undone_by_archive_or_invite(auto_client):
    c = auto_client
    data = connected(c)
    admin_post(c, f"/admin/telegram/{data['request_id']}/block")
    with c.app.state.database.session() as s:
        change_account_archive(s, data["account_id"], "owner", True)
    with c.app.state.database.session() as s:
        change_account_archive(s, data["account_id"], "owner", False)
    assert start(c, invite=invite(c, plan_existing(c))).json()["state"] == "blocked"


def test_old_archive_restored_through_admin_without_secret(auto_client):
    c = auto_client
    data = connected(c)
    with c.app.state.database.session() as s:
        s.get(Account, data["account_id"]).status = "active"
        s.get(TelegramAdmission, data["request_id"]).status = "blocked"
        s.scalar(select(Subscription)).status = "cancelled"
    pid = plan_existing(c)
    approve(c, data["request_id"], pid)
    assert start(c).json()["state"] == "active"
    assert start(c).json()["account_id"] == data["account_id"]


def plan_existing(c):
    with c.app.state.database.session() as s:
        return s.scalar(select(Subscription)).plan_id


def test_visible_telegram_id_and_recovery_link(auto_client):
    c = auto_client
    data = connected(c)
    page = c.get("/admin/users", auth=("owner", "test-password")).text
    assert "Telegram ID: 123456789" in page
    assert f"/admin/telegram#tg-{data['request_id']}" in page
