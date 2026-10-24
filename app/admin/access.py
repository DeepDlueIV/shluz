"""Простое управление демо и моделями. Изменения не сбрасывают уже потраченные запросы."""

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.admin.forms import _read_form
from app.admin.routes import (
    _admin_csrf_token,
    _database_for_scope,
    _require_admin,
    _require_csrf,
    _settings,
    templates,
)
from app.db.audit import record_admin_event
from app.db.models import ModelPolicy, Plan
from app.product.service import CHANNELS, DEFAULT_MODELS, locked_session, models_in, policy

router = APIRouter(prefix="/admin/access", tags=["admin"])
Admin = Annotated[str, Depends(_require_admin)]


def parse_models(value):
    items = list(dict.fromkeys(re.split(r"[\s,]+", value.strip()))) if value.strip() else []
    if len(items) > 100 or any(not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", v) for v in items):
        raise HTTPException(
            422, detail="Укажите до 100 точных названий моделей, каждое с новой строки"
        )
    return json.dumps(items)


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, _: Admin):
    scope = request.query_params.get("scope")
    with _database_for_scope(request, scope).session() as s:
        settings = policy(s)
        rows = {
            r.id: "\n".join(sorted(models_in(r.models_json)))
            for r in s.scalars(select(ModelPolicy))
        }
        plans = (
            []
            if scope == "site"
            else list(s.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.name)))
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/access.html",
        context={
            "policy": settings,
            "demo_models": "\n".join(sorted(models_in(settings.demo_models_json))),
            "channels": [("telegram", "Telegram"), ("web", "Сайт"), ("harness", "Desktop / API")],
            "rules": rows,
            "default_models": "\n".join(sorted(DEFAULT_MODELS)),
            "plans": plans,
            "csrf_token": _admin_csrf_token(_settings(request)),
            "admin_scope": scope or "owner",
            "site_scope": scope == "site",
            "site_demo_limits": {
                "demo_max_input_chars": 1000,
                "demo_max_output_tokens": 512,
                "demo_daily_requests": 10,
                "requests_per_minute": 2,
            },
        },
    )


@router.post("/demo")
async def save_demo(request: Request, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    scope = form.get("scope")
    if form.get("mode") not in {"demo", "closed"}:
        raise HTTPException(422, detail="Выберите режим регистрации")
    values = {}
    for name, low, high in [
        ("demo_requests", 0, 100),
        ("demo_max_input_chars", 100, 200000),
        ("demo_max_output_tokens", 1, 4096),
        ("demo_daily_requests", 0, 10000),
        ("requests_per_minute", 1, 1000),
    ]:
        try:
            value = int(form.get(name, ""))
            if not low <= value <= high:
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(422, detail=f"Поле {name}: допустимо от {low} до {high}") from exc
        values[name] = value
    models = parse_models(form.get("demo_models", ""))
    if scope == "site":
        if not set(json.loads(models)).issubset({"mistral-small-3-2-24b-instruct"}):
            raise HTTPException(
                422,
                detail=(
                    "В гостевом демо сайта разрешена только согласованная модель Venice Mistral."
                ),
            )
        site_maxima = {
            "demo_requests": 1,
            "demo_max_input_chars": 1000,
            "demo_max_output_tokens": 512,
            "demo_daily_requests": 10,
            "requests_per_minute": 2,
        }
        if any(values[name] > maximum for name, maximum in site_maxima.items()):
            raise HTTPException(
                422,
                detail=(
                    "Лимит гостевого сайта нельзя поднять выше согласованных границ "
                    "(1 запрос, 10/сутки, 1000 символов, 512 токенов)."
                ),
            )
    with locked_session(_database_for_scope(request, scope)) as s:
        row = policy(s)
        for name, value in values.items():
            setattr(row, name, value)
        row.mode = form["mode"]
        row.demo_models_json = models
        s.add(row)
        record_admin_event(
            s,
            actor=actor,
            action="demo.updated",
            target_type="product_policy",
            target_id="default",
            details={"mode": row.mode, "demo_requests": row.demo_requests},
        )
    return RedirectResponse(
        "/admin/access" + ("?scope=site" if scope == "site" else ""), status_code=303
    )


@router.post("/models")
async def save_models(request: Request, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), _settings(request))
    scope = form.get("scope")
    subject = form.get("subject", "")
    if scope == "site" and subject.startswith("plan:"):
        raise HTTPException(
            422, detail="Правила отдельных тарифов не применяются к гостевому демо сайта."
        )
    with locked_session(_database_for_scope(request, scope)) as s:
        if subject.startswith("channel:"):
            if subject[8:] not in CHANNELS:
                raise HTTPException(422, detail="Неизвестный канал")
        elif not (subject.startswith("plan:") and s.get(Plan, subject[5:])):
            raise HTTPException(422, detail="Неизвестный тариф")
        existing = s.get(ModelPolicy, subject)
        if form.get("inherit") == "yes" and subject.startswith("plan:"):
            if existing:
                s.delete(existing)
        else:
            models = parse_models(form.get("models", ""))
            if (
                scope == "site"
                and subject == "channel:web"
                and not set(json.loads(models)).issubset({"mistral-small-3-2-24b-instruct"})
            ):
                raise HTTPException(
                    422,
                    detail=(
                        "Для канала сайта разрешена только согласованная модель Venice Mistral."
                    ),
                )
            row = existing or ModelPolicy(id=subject)
            row.models_json = models
            s.add(row)
        record_admin_event(
            s,
            actor=actor,
            action="models.updated",
            target_type="model_policy",
            target_id=subject,
            details={},
        )
    return RedirectResponse(
        "/admin/access" + ("?scope=site" if scope == "site" else ""), status_code=303
    )
