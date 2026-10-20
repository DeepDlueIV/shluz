"""Редактирование владельцем не меняет личность, деньги или права входа."""

import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.billing.models import Wallet
from app.db.audit import AdminEvent
from app.db.models import Account, ApiToken, Identity, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token

AUTH = ("test-admin", "test-admin-password")


def csrf(client):
    html = client.get("/admin/users", auth=AUTH).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def person(client, role="user"):
    with client.app.state.database.session() as s:
        a = Account(display_name="username test", role=role)
        p = Plan(code="editing", name="Тест", monthly_request_limit=20)
        s.add_all([a, p])
        s.flush()
        s.add_all(
            [
                Identity(account_id=a.id, provider="telegram", subject="233375703"),
                Subscription(account_id=a.id, plan_id=p.id),
                Wallet(account_id=a.id, balance_usd=Decimal("10")),
                UsageEvent(
                    account_id=a.id,
                    model="mock-chat",
                    provider="mock",
                    source="telegram",
                    status="success",
                    cost_usd=Decimal("0.001"),
                ),
            ]
        )
        t = create_api_token(
            s, account_id=a.id, name="test", source="harness", raw_token="shluz_rename_test_only"
        )
        return a.id, t.id


def test_rename_preserves_account_identity_balance_and_keys(client):
    aid, tid = person(client)
    response = client.post(
        f"/admin/users/{aid}/name",
        auth=AUTH,
        data={"csrf_token": csrf(client), "display_name": "  Мой аккаунт  "},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/users#user-{aid}"
    with client.app.state.database.session() as s:
        assert s.get(Account, aid).display_name == "Мой аккаунт"
        assert s.get(Account, aid).role == "user"
        assert s.scalar(select(Identity).where(Identity.account_id == aid)).subject == "233375703"
        assert s.get(Wallet, aid).balance_usd == Decimal("10")
        assert s.get(ApiToken, tid).revoked_at is None
        assert (
            s.scalar(select(Subscription).where(Subscription.account_id == aid)).status == "active"
        )
        assert s.scalar(select(UsageEvent).where(UsageEvent.account_id == aid)).cost_usd == Decimal(
            "0.001"
        )
        event = s.scalar(select(AdminEvent).where(AdminEvent.action == "user.renamed"))
        assert event.details == {
            "before": {"display_name": "username test"},
            "after": {"display_name": "Мой аккаунт"},
        }
    html = client.get("/admin/users", auth=AUTH).text
    assert "Мой аккаунт" in html
    assert "Мой аккаунт" in client.get("/admin/usage", auth=AUTH).text


@pytest.mark.parametrize("value", ["", "   ", "x" * 201, "имя\nстрока", "имя\u202e"])
def test_rename_validation_and_no_mutation(client, value):
    aid, _ = person(client)
    result = client.post(
        f"/admin/users/{aid}/name",
        auth=AUTH,
        data={"csrf_token": csrf(client), "display_name": value},
    )
    assert result.status_code == 422
    with client.app.state.database.session() as s:
        assert s.get(Account, aid).display_name == "username test"


def test_rename_requires_owner_and_csrf_and_rejects_role_mass_assignment(client):
    aid, _ = person(client)
    url = f"/admin/users/{aid}/name"
    assert client.post(url, data={"display_name": "new"}).status_code == 401
    assert client.post(url, auth=AUTH, data={"display_name": "new"}).status_code == 403
    result = client.post(
        url,
        auth=AUTH,
        data={
            "csrf_token": csrf(client),
            "display_name": "<script>test</script>",
            "role": "admin",
            "status": "archived",
        },
    )
    assert result.status_code == 200
    assert "&lt;script&gt;test&lt;/script&gt;" in result.text
    assert "<script>test</script>" not in result.text
    with client.app.state.database.session() as s:
        assert s.get(Account, aid).role == "user"
        assert s.get(Account, aid).status == "active"


def test_admin_label_can_be_archived_without_removing_owner_login(client):
    aid, tid = person(client, role="admin")
    for action, expected in [("archive", "archived"), ("restore", "active")]:
        result = client.post(
            f"/admin/users/{aid}/{action}",
            auth=AUTH,
            data={"csrf_token": csrf(client), "confirm": "yes"},
            follow_redirects=False,
        )
        assert result.status_code == 303
        with client.app.state.database.session() as s:
            assert s.get(Account, aid).status == expected
            assert s.get(ApiToken, tid).revoked_at is not None
            assert s.get(Wallet, aid).balance_usd == Decimal("10")
            assert s.scalar(select(UsageEvent).where(UsageEvent.account_id == aid))
        assert client.get("/admin", auth=AUTH).status_code == 200


@pytest.mark.parametrize("role", ["service", "unknown"])
def test_protected_accounts_cannot_be_renamed_or_archived(client, role):
    aid, _ = person(client, role=role)
    for path, values in [("name", {"display_name": "new"}), ("archive", {"confirm": "yes"})]:
        assert (
            client.post(
                f"/admin/users/{aid}/{path}", auth=AUTH, data={"csrf_token": csrf(client), **values}
            ).status_code
            == 409
        )
    assert (
        client.post(
            "/admin/users/bootstrap/name",
            auth=AUTH,
            data={"csrf_token": csrf(client), "display_name": "new"},
        ).status_code
        == 409
    )


def test_rename_archived_user_stays_in_archive_and_audit_failure_rolls_back(client, monkeypatch):
    aid, _ = person(client)
    with client.app.state.database.session() as s:
        s.get(Account, aid).status = "archived"
    result = client.post(
        f"/admin/users/{aid}/name",
        auth=AUTH,
        data={"csrf_token": csrf(client), "display_name": "Архивный"},
        follow_redirects=False,
    )
    assert result.status_code == 303
    assert result.headers["location"] == f"/admin/users?archive=1#user-{aid}"

    def fail(*a, **kw):
        raise RuntimeError("secret-never-print")

    monkeypatch.setattr("app.admin.routes.record_admin_event", fail)
    result = client.post(
        f"/admin/users/{aid}/name",
        auth=AUTH,
        data={"csrf_token": csrf(client), "display_name": "Потеря"},
    )
    assert result.status_code == 500
    assert "secret-never-print" not in result.text
    with client.app.state.database.session() as s:
        assert s.get(Account, aid).display_name == "Архивный"
