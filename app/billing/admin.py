"""Панель кошелька: явные начисления, отдельные тарифы каналов, предохранитель цены."""

import secrets
import time
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.admin.forms import _choice, _read_form, _required_text
from app.admin.routes import (
    _admin_csrf_token,
    _database,
    _require_admin,
    _require_csrf,
    _settings,
    templates,
)
from app.billing.models import WalletEntry, WalletHold, WalletModelBound
from app.billing.service import QUANTUM, credit, settle, snapshot, wallet
from app.db.audit import record_admin_event
from app.db.models import Account, Plan, Subscription, UsageEvent
from app.product.service import CHANNELS, locked_session

router = APIRouter(prefix="/admin/wallets", tags=["admin"])
Admin = Annotated[str, Depends(_require_admin)]


def number(form, name, maximum="9999999999"):
    try:
        value = Decimal(form.get(name, "").replace(",", "."))
        if (
            not value.is_finite()
            or not 0 <= value <= Decimal(maximum)
            or value != value.quantize(QUANTUM)
        ):
            raise ValueError()
        return value
    except (ValueError, InvalidOperation) as exc:
        raise HTTPException(422, detail="Проверьте число: " + name) from exc


@router.get("")
def dashboard(request: Request, _: Admin):
    db = _database(request)
    with db.session() as s:
        accounts = list(
            s.scalars(select(Account).where(Account.role == "user").order_by(Account.display_name))
        )
        plans = list(s.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.name)))
        rows = [{"account": a, "wallet": snapshot(s, a.id)} for a in accounts]
        assignments = list(s.scalars(select(Subscription).where(Subscription.status == "active")))
        rules = list(s.scalars(select(WalletModelBound)))
        holds = list(
            s.scalars(select(WalletHold).where(WalletHold.state == "uncertain").limit(100))
        )
        entries = list(
            s.scalars(select(WalletEntry).order_by(WalletEntry.created_at.desc()).limit(50))
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/wallets.html",
        context={
            "rows": rows,
            "plans": plans,
            "assignments": assignments,
            "rules": rules,
            "holds": holds,
            "entries": entries,
            "names": {a.id: a.display_name for a in accounts},
            "plan_names": {p.id: p.name for p in plans},
            "csrf_token": _admin_csrf_token(_settings(request)),
            "operation_id": secrets.token_hex(16),
        },
    )


@router.post("/{account_id}/credit")
async def add_credit(request: Request, account_id: str, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    try:
        with locked_session(_database(request)) as s:
            entry = credit(
                s,
                account_id,
                number(form, "amount"),
                form.get("operation_id", ""),
                actor,
                form.get("note", ""),
            )
            record_admin_event(
                s,
                actor=actor,
                action="wallet.credited",
                target_type="account",
                target_id=account_id,
                details={"source": entry.id},
            )
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return RedirectResponse("/admin/wallets#account-" + account_id, 303)


@router.post("/{account_id}/unit")
async def change_unit(request: Request, account_id: str, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    unit = _choice(form, "unit", {"usd", "credits"})
    with locked_session(_database(request)) as s:
        if not s.get(Account, account_id):
            raise HTTPException(404, detail="Аккаунт не найден")
        wallet(s, account_id).display_unit = unit
        record_admin_event(
            s,
            actor=actor,
            action="wallet.unit",
            target_type="account",
            target_id=account_id,
            details={"source": unit},
        )
    return RedirectResponse("/admin/wallets#account-" + account_id, 303)


@router.post("/{account_id}/plan")
async def assign(request: Request, account_id: str, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    source = _choice(form, "source", {"*", *CHANNELS})
    plan_id = form.get("plan_id", "")
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    with locked_session(_database(request)) as s:
        account = s.get(Account, account_id)
        if not account or account.status != "active":
            raise HTTPException(409, detail="Аккаунт неактивен")
        plan = s.get(Plan, plan_id) if plan_id else None
        if plan_id and (not plan or not plan.active):
            raise HTTPException(422, detail="Тариф недоступен")
        for row in s.scalars(
            select(Subscription).where(
                Subscription.account_id == account_id,
                Subscription.source == source,
                Subscription.status == "active",
            )
        ):
            row.status = "cancelled"
            row.ends_at = now
        if plan:
            s.add(
                Subscription(account_id=account_id, plan_id=plan.id, source=source, starts_at=now)
            )
        record_admin_event(
            s,
            actor=actor,
            action="subscription.assigned",
            target_type="account",
            target_id=account_id,
            details={"plan_id": plan_id, "source": source},
        )
    return RedirectResponse("/admin/wallets#account-" + account_id, 303)


@router.post("/model-bound")
async def model_bound(request: Request, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    key = _required_text(form, "model_key", max_length=300)
    if ":" not in key:
        raise HTTPException(422, detail="Укажите поставщик:model-id")
    cost = number(form, "max_cost_usd")
    try:
        chars = int(form.get("max_input_chars", ""))
        tokens = int(form.get("max_output_tokens", ""))
        if not 1 <= chars <= 200000 or not 1 <= tokens <= 4096:
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(422, detail="Пределы: 1–200000 символов; 1–4096 токенов") from exc
    reviewed = form.get("reviewed") == "yes"
    if reviewed and cost <= 0:
        raise HTTPException(422, detail="Для внешней модели нужен положительный резерв")
    with locked_session(_database(request)) as s:
        row = s.get(WalletModelBound, key)
        if not row:
            row = WalletModelBound(id=key)
            s.add(row)
        row.max_cost_usd = cost
        row.max_input_chars = chars
        row.max_output_tokens = tokens
        row.reviewed = reviewed
        row.expires = int(time.time()) + 86400
        record_admin_event(
            s,
            actor=actor,
            action="wallet.price_reviewed",
            target_type="model",
            target_id=key[:64],
            details={"source": "reviewed" if reviewed else "disabled"},
        )
    return RedirectResponse("/admin/wallets", 303)


@router.post("/holds/{event_id}/resolve")
async def reconcile(request: Request, event_id: str, actor: Admin):
    """Только явная сверка законченного неизвестного расхода; не освобождение по таймеру."""
    from app.providers.base import ChatResult

    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    if form.get("confirm") != "yes":
        raise HTTPException(422, detail="Подтвердите сверку с поставщиком")
    cost = number(form, "cost_usd")
    note = _required_text(form, "note", max_length=200)
    with locked_session(_database(request)) as s:
        hold, event = s.get(WalletHold, event_id), s.get(UsageEvent, event_id)
        if not hold or not event:
            raise HTTPException(404, detail="Резерв не найден")
        if hold.state in {"settled", "released", "review"}:
            return RedirectResponse("/admin/wallets", 303)
        if hold.state != "uncertain" or event.status == "pending":
            raise HTTPException(409, detail="Запрос ещё выполняется; сверка недоступна")
        settle(s, event, result=ChatResult(content="", cost_usd=cost))
        event.cost_usd, event.cost_known = cost, True
        record_admin_event(
            s,
            actor=actor,
            action="wallet.reconciled",
            target_type="usage",
            target_id=event_id,
            details={"source": note, "cost_usd": str(cost)},
        )
    return RedirectResponse("/admin/wallets", 303)
