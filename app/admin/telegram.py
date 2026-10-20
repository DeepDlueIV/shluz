"""Владелец решает, кому дать доступ; приложение само создаёт аккаунт и сессию."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.admin.forms import _read_form
from app.admin.routes import (
    _admin_csrf_token,
    _database,
    _require_admin,
    _require_csrf,
    _settings,
    templates,
)
from app.db.models import Account, Plan, TelegramAdmission, TelegramIntegration, TelegramInvite
from app.telegram import service

router = APIRouter(prefix="/admin/telegram", tags=["admin"])
Admin = Annotated[str, Depends(_require_admin)]


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, _: Admin):
    database = _database(request)
    with database.session() as session:
        rows = list(
            session.scalars(
                select(TelegramAdmission)
                .order_by(
                    TelegramAdmission.created_at.desc(),
                )
                .limit(1000)
            )
        )
        active_ids = {r.id for r in rows if service.access_is_active(session, r)}
        all_accounts = list(session.scalars(select(Account)))
        plans = list(session.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.name)))
        accounts = list(
            session.scalars(
                select(Account)
                .where(
                    Account.status == "active",
                    Account.role != "service",
                )
                .order_by(Account.display_name)
            )
        )
        bot = session.get(TelegramIntegration, "default")
        invitations = list(
            session.scalars(
                select(TelegramInvite)
                .order_by(
                    TelegramInvite.created_at.desc(),
                )
                .limit(20)
            )
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/telegram.html",
        context={
            "rows": rows,
            "active_ids": active_ids,
            "account_status": {a.id: a.status for a in all_accounts},
            "plans": plans,
            "accounts": accounts,
            "account_names": {a.id: a.display_name for a in all_accounts},
            "bot_username": bot.username if bot else None,
            "invitations": invitations,
            "csrf_token": _admin_csrf_token(_settings(request)),
        },
    )


@router.post("/{admission_id}/approve")
async def approve(request: Request, admission_id: str, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    service.approve(
        _database(request),
        admission_id,
        form.get("plan_id", ""),
        actor=actor,
        account_id=form.get("account_id") or None,
    )
    return RedirectResponse("/admin/telegram", status_code=303)


@router.post("/{admission_id}/block")
async def block(request: Request, admission_id: str, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    service.block(_database(request), admission_id, actor=actor)
    return RedirectResponse("/admin/telegram", status_code=303)


@router.post("/invites", response_class=HTMLResponse)
async def invitation(request: Request, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    with _database(request).session() as session:
        bot = session.get(TelegramIntegration, "default")
        if bot is None:
            raise HTTPException(409, detail="Сначала запустите Telegram-бота")
        username = bot.username
    code = service.issue_invite(
        _database(request),
        form.get("plan_id", ""),
        actor=actor,
        account_id=form.get("account_id") or None,
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/telegram-invite.html",
        context={
            "link": f"https://t.me/{username}?start={code}",
        },
        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
    )


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(request: Request, invite_id: str, actor: Admin):
    from app.db.audit import record_admin_event

    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    with service.transaction(_database(request)) as session:
        row = session.get(TelegramInvite, invite_id)
        if row is None:
            raise HTTPException(404, detail="Приглашение не найдено")
        row.revoked = True
        record_admin_event(
            session,
            actor=actor,
            action="telegram.invite_revoked",
            target_type="telegram_invite",
            target_id=row.id,
            details={},
        )
    return RedirectResponse("/admin/telegram", status_code=303)
