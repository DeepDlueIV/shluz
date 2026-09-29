import json
import logging
import re
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select, text

from app.db.models import Account, ApiToken, Plan, Subscription, UsageEvent
from app.db.repositories import create_api_token

AUTH = ("test-admin", "test-admin-password")


def _csrf(client):
    page = client.get("/admin/plans", auth=AUTH)
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def _form(client, **changes):
    return {
        "csrf_token": _csrf(client), "code": "test", "name": "Тестовый тариф",
        "monthly_price_usd": "0", "monthly_request_limit": "2",
        "markup_percent": "0", "request_credit_reserve": "0", "active": "on",
        **changes,
    }


@pytest.mark.parametrize("changes,status_code", [
    ({"code": "тест"}, 422),
    ({"monthly_request_limit": "-2"}, 422),
])
def test_invalid_plan_returns_form_with_preserved_values(client, changes, status_code):
    response = client.post("/admin/plans", auth=AUTH, data=_form(client, **changes))
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("text/html")
    assert 'role="alert"' in response.text
    assert 'value="Тестовый тариф"' in response.text
    assert 'action="/admin/plans"' in response.text
    assert "test-admin-password" not in response.text


def test_duplicate_plan_returns_html_not_json(client):
    client.post("/admin/plans", auth=AUTH, data=_form(client))
    response = client.post("/admin/plans", auth=AUTH, data=_form(client))
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert "уже существует" in response.text
    assert 'value="Тестовый тариф"' in response.text


def test_edit_error_preserves_form_and_opens_editor(client):
    database = client.app.state.database
    with database.session() as session:
        plan = Plan(code="original", name="Исходный")
        session.add(plan)
        session.flush()
        plan_id = plan.id
    response = client.post(
        f"/admin/plans/{plan_id}", auth=AUTH,
        data=_form(client, code="ошибка", name="Новое название"),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert 'value="Новое название"' in response.text
    assert "<details open" in response.text
    with database.session() as session:
        assert session.get(Plan, plan_id).name == "Исходный"


def test_error_form_escapes_markup(client):
    response = client.post(
        "/admin/plans", auth=AUTH,
        data=_form(client, code="ошибка", name='<script>alert("x")</script>'),
    )
    assert response.headers["content-type"].startswith("text/html")
    assert '<script>alert("x")</script>' not in response.text
    assert "&lt;script&gt;" in response.text


def test_dark_theme_and_plan_code_hint(client):
    css = client.get("/static/admin.css").text
    assert "color-scheme: dark" in css
    page = client.get("/admin/plans", auth=AUTH).text
    assert "латин" in page.lower()
    assert 'pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}"' in page


def _account(client):
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Архивный тест")
        session.add(account)
        session.flush()
        token = create_api_token(
            session, account_id=account.id, name="Личный", raw_token="shluz_test_archive_key",
        )
        session.add(UsageEvent(
            account_id=account.id, source="harness", provider="mock", model="mock-chat",
            cost_usd=Decimal("0.01"), status="success",
        ))
        return account.id, token.id


def test_archive_user_preserves_history_and_revokes_access(client):
    account_id, token_id = _account(client)
    response = client.post(
        f"/admin/users/{account_id}/archive", auth=AUTH,
        data={"csrf_token": _csrf(client), "confirm": "yes"}, follow_redirects=False,
    )
    assert response.status_code == 303
    database = client.app.state.database
    with database.session() as session:
        assert session.get(Account, account_id).status == "archived"
        assert session.get(ApiToken, token_id).revoked_at is not None
        assert session.scalar(select(UsageEvent).where(UsageEvent.account_id == account_id))
    assert client.get("/v1/models", headers={
        "Authorization": "Bearer shluz_test_archive_key",
    }).status_code == 401
    response = client.post(
        f"/admin/users/{account_id}/restore", auth=AUTH,
        data={"csrf_token": _csrf(client), "confirm": "yes"}, follow_redirects=False,
    )
    assert response.status_code == 303
    with database.session() as session:
        assert session.get(Account, account_id).status == "active"
        assert session.get(ApiToken, token_id).revoked_at is not None


def test_archive_requires_csrf_confirmation_and_protects_bootstrap(client):
    account_id, _ = _account(client)
    path = f"/admin/users/{account_id}/archive"
    assert client.post(path, data={"confirm": "yes"}).status_code == 401
    assert client.post(path, auth=AUTH, data={"confirm": "yes"}).status_code == 403
    assert client.post(path, auth=AUTH, data={"csrf_token": _csrf(client)}).status_code == 422
    assert client.post(
        "/admin/users/bootstrap/archive", auth=AUTH,
        data={"csrf_token": _csrf(client), "confirm": "yes"},
    ).status_code == 409


def test_archive_plan_with_subscribers_is_blocked(client):
    database = client.app.state.database
    with database.session() as session:
        account = Account(display_name="Подписчик")
        plan = Plan(code="busy", name="Занятый тариф")
        session.add_all([account, plan])
        session.flush()
        session.add(Subscription(account_id=account.id, plan_id=plan.id))
        plan_id = plan.id
    response = client.post(
        f"/admin/plans/{plan_id}/archive", auth=AUTH,
        data={"csrf_token": _csrf(client), "confirm": "yes"},
    )
    assert response.status_code == 409
    assert "подпис" in response.text.lower()
    with database.session() as session:
        assert session.get(Plan, plan_id).active is True


def test_archive_and_restore_unused_plan(client):
    database = client.app.state.database
    with database.session() as session:
        plan = Plan(code="unused", name="Неиспользуемый")
        session.add(plan)
        session.flush()
        plan_id = plan.id
    for action, expected in [("archive", False), ("restore", True)]:
        response = client.post(
            f"/admin/plans/{plan_id}/{action}", auth=AUTH,
            data={"csrf_token": _csrf(client), "confirm": "yes"}, follow_redirects=False,
        )
        assert response.status_code == 303
        with database.session() as session:
            assert session.get(Plan, plan_id).active is expected


def test_plan_mutations_are_logged_without_credentials(client):
    client.post("/admin/plans", auth=AUTH, data=_form(client))
    database = client.app.state.database
    assert "admin_events" in inspect(database.engine).get_table_names()
    with database.session() as session:
        rows = session.execute(text("SELECT actor, action, details FROM admin_events")).all()
    assert rows
    assert any(row.actor == "test-admin" and row.action == "plan.created" for row in rows)
    page = client.get("/admin/logs", auth=AUTH)
    assert page.status_code == 200
    assert "Тариф создан" in page.text
    assert "test-admin-password" not in page.text
    assert "test-api-token" not in page.text


@pytest.mark.parametrize("path", ["/admin/logs", "/admin/test"])
def test_new_pages_are_private_and_accessible_to_owner(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, auth=AUTH).status_code == 200


def test_test_page_explains_token_and_does_not_store_it(client):
    page = client.get("/admin/test", auth=AUTH).text
    assert "Личный токен" in page
    assert "mock-chat" in page
    script = client.get("/static/test-client.js").text
    assert "/v1/chat/completions" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "textContent" in script


def test_http_log_has_correlation_id_and_no_query_or_message(client, caplog):
    with caplog.at_level(logging.INFO, logger="shluz.access"):
        response = client.post(
            "/v1/chat/completions?key=secret-query-123", headers={
                "Authorization": "Bearer test-api-token", "X-Request-ID": "untrusted-id",
            }, json={"model": "mock-chat", "messages": [{
                "role": "user", "content": "secret-prompt-456",
            }]},
        )
    assert response.status_code == 200
    request_id = response.headers.get("x-request-id")
    assert request_id and request_id != "untrusted-id"
    entries = [
        json.loads(record.message) for record in caplog.records
        if record.name == "shluz.access"
    ]
    assert any(
        entry["request_id"] == request_id and entry["status"] == 200 for entry in entries
    )
    assert "secret-query-123" not in caplog.text
    assert "secret-prompt-456" not in caplog.text
    assert "test-api-token" not in caplog.text


def test_public_validation_stays_json(client, authorized_headers):
    response = client.post("/v1/chat/completions", headers=authorized_headers, json={})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
