import re

from sqlalchemy import func, select

from app.db.audit import AdminEvent, safe_details
from app.db.models import Account, Plan

AUTH = ("test-admin", "test-admin-password")


def _csrf(client):
    page = client.get("/admin/plans", auth=AUTH)
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def test_non_ascii_csrf_is_forbidden_not_server_error(client):
    response = client.post("/admin/plans", auth=AUTH, data={
        "csrf_token": "неверный", "code": "test", "name": "Тест",
    })
    assert response.status_code == 403


def test_unchecked_active_remains_unchecked_after_validation_error(client):
    response = client.post("/admin/plans", auth=AUTH, data={
        "csrf_token": _csrf(client), "code": "ошибка", "name": "Неактивный",
    })
    assert response.status_code == 422
    checkbox = re.search(r'<input[^>]*name="active"[^>]*>', response.text)
    assert checkbox is not None
    assert "checked" not in checkbox.group(0)


def test_audit_failure_rolls_back_plan_change(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("secret-db-password-must-not-leak")

    monkeypatch.setattr("app.admin.routes.record_admin_event", unavailable)
    response = client.post("/admin/plans", auth=AUTH, data={
        "csrf_token": _csrf(client), "code": "rollback", "name": "Rollback", "active": "on",
    })
    assert response.status_code == 500
    assert "secret-db-password-must-not-leak" not in response.text
    with client.app.state.database.session() as session:
        assert session.scalar(select(Plan).where(Plan.code == "rollback")) is None


def test_audit_allowlist_discards_secrets_and_nested_secrets():
    assert safe_details({
        "password": "private", "raw_token": "private", "authorization": "private",
        "before": {"active": True, "password": "private"}, "after": {"active": False},
    }) == {"before": {"active": True}, "after": {"active": False}}


def test_repeated_archive_has_only_one_audit_event(client):
    with client.app.state.database.session() as session:
        account = Account(display_name="Повтор")
        session.add(account)
        session.flush()
        account_id = account.id
    for _ in range(2):
        response = client.post(
            f"/admin/users/{account_id}/archive", auth=AUTH,
            data={"csrf_token": _csrf(client), "confirm": "yes"}, follow_redirects=False,
        )
        assert response.status_code == 303
    with client.app.state.database.session() as session:
        count = session.scalar(select(func.count()).select_from(AdminEvent).where(
            AdminEvent.action == "user.archived", AdminEvent.target_id == account_id,
        ))
    assert count == 1


def test_docs_keeps_bearer_auth_and_uses_dark_style(client):
    assert "/static/docs.css" in client.get("/docs").text
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert schemes["HTTPBearer"]["scheme"] == "bearer"


def test_log_pagination_and_filter_reject_invalid_input(client):
    assert client.get("/admin/logs?page=0", auth=AUTH).status_code == 422
    assert client.get("/admin/logs?action=unknown", auth=AUTH).status_code == 422
