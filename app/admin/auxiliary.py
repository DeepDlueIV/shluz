from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select

from app.admin.forms import _read_form
from app.admin.lifecycle import change_account_archive, change_plan_archive
from app.admin.routes import _database, _require_admin, _require_csrf, _settings, templates
from app.config import Settings
from app.db.audit import ACTION_LABELS, AdminEvent
from app.db.database import Database

router = APIRouter(prefix="/admin", tags=["admin"])


async def _confirm(request: Request, settings: Settings) -> None:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    if form.get("confirm") != "yes":
        raise HTTPException(status_code=422, detail="Подтвердите действие галочкой в форме")


@router.post("/users/{account_id}/{action}")
async def account_archive(
    account_id: str, action: Literal["archive", "restore"], request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    await _confirm(request, settings)
    with database.session() as session:
        change_account_archive(session, account_id, actor, action == "archive")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/plans/{plan_id}/{action}")
async def plan_archive(
    plan_id: str, action: Literal["archive", "restore"], request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    await _confirm(request, settings)
    with database.session() as session:
        change_plan_archive(session, plan_id, actor, action == "archive")
    return RedirectResponse("/admin/plans", status_code=303)


@router.get("/logs", response_class=HTMLResponse)
def admin_logs(
    request: Request, _: Annotated[str, Depends(_require_admin)],
    database: Annotated[Database, Depends(_database)], page: int = 1, action: str = "",
) -> HTMLResponse:
    if not 1 <= page <= 10_000 or (action and action not in ACTION_LABELS):
        raise HTTPException(status_code=422, detail="Некорректный фильтр журнала")
    query = select(AdminEvent).order_by(desc(AdminEvent.created_at), desc(AdminEvent.id))
    if action:
        query = query.where(AdminEvent.action == action)
    with database.session() as session:
        entries = list(session.scalars(query.offset((page - 1) * 50).limit(51)))
    return templates.TemplateResponse(
        request=request, name="admin/logs.html", context={
            "entries": entries[:50], "has_next": len(entries) > 50,
            "page": page, "action": action, "labels": ACTION_LABELS,
        }, headers={"Cache-Control": "no-store"},
    )


@router.get("/test", response_class=HTMLResponse)
def test_console(
    request: Request, _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="admin/test.html",
        context={"mock_enabled": settings.active_provider == "mock"},
        headers={"Cache-Control": "no-store"},
    )
