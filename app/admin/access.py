"""Простое управление демо и моделями. Изменения не сбрасывают уже потраченные запросы."""

import json
import re
from decimal import Decimal
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
from app.webaccess.models import DemoControl, DemoModelBound

router = APIRouter(prefix="/admin/access", tags=["admin"])
Admin = Annotated[str, Depends(_require_admin)]
SITE_DEMO_LIMITS = {
    "demo_requests": 3,
    "demo_max_input_chars": 1000,
    "demo_max_output_tokens": 512,
    "demo_daily_requests": 10,
    "requests_per_minute": 2,
}
SITE_DEMO_MAX_MODEL_COST = Decimal("0.02")


def parse_models(value):
    items = list(dict.fromkeys(re.split(r"[\s,]+", value.strip()))) if value.strip() else []
    if len(items) > 100 or any(not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", v) for v in items):
        raise HTTPException(
            422, detail="Укажите до 100 точных названий моделей, каждое с новой строки"
        )
    return json.dumps(items)


def _site_model_options(session):
    controls = session.get(DemoControl, "default")
    daily_budget = controls.daily_usd if controls else Decimal("0")
    options = []
    for bound in session.scalars(select(DemoModelBound).order_by(DemoModelBound.id)):
        provider, separator, model_id = bound.id.partition(":")
        if (
            not separator
            or provider != "venice"
            or not bound.reviewed
            or bound.max_cost_usd <= 0
            or bound.max_cost_usd > min(SITE_DEMO_MAX_MODEL_COST, daily_budget)
            or bound.max_input_chars < SITE_DEMO_LIMITS["demo_max_input_chars"]
            or bound.max_output_tokens < SITE_DEMO_LIMITS["demo_max_output_tokens"]
        ):
            continue
        options.append(
            {
                "id": model_id,
                "label": (f"Venice · {model_id} · до ${bound.max_cost_usd.normalize():f}/запрос"),
            }
        )
    return options


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
        site_model_options = _site_model_options(s) if scope == "site" else []
        site_models = models_in(settings.demo_models_json)
        site_model_current = next(iter(sorted(site_models)), "")
        site_channel_models = models_in(rows.get("channel:web", ""))
    return templates.TemplateResponse(
        request=request,
        name="admin/access.html",
        context={
            "policy": settings,
            "demo_models": "\n".join(sorted(models_in(settings.demo_models_json))),
            "channels": (
                [("web", "Сайт")]
                if scope == "site"
                else [
                    ("telegram", "Telegram"),
                    ("web", "Сайт · основная база"),
                    ("harness", "Desktop / API"),
                ]
            ),
            "rules": rows,
            "default_models": "\n".join(sorted(DEFAULT_MODELS)),
            "plans": plans,
            "csrf_token": _admin_csrf_token(_settings(request)),
            "admin_scope": scope or "owner",
            "site_scope": scope == "site",
            "site_model_options": site_model_options,
            "site_model_current": site_model_current,
            "site_model_in_sync": (
                scope != "site" or (len(site_models) == 1 and site_models == site_channel_models)
            ),
            "site_demo_limits": SITE_DEMO_LIMITS,
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
    selected_site_model = None
    if scope == "site":
        selected_site_model = form.get("demo_model", "").strip()
        models = parse_models(selected_site_model)
        if len(json.loads(models)) != 1:
            raise HTTPException(422, detail="Выберите проверенную модель гостевого сайта.")
        if any(values[name] > maximum for name, maximum in SITE_DEMO_LIMITS.items()):
            raise HTTPException(
                422,
                detail=(
                    "Для гостевого сайта действуют пределы: 3 запроса в пакете, "
                    "10 в сутки, ввод 1000 символов и ответ 512 токенов."
                ),
            )
    else:
        models = parse_models(form.get("demo_models", ""))
    with locked_session(_database_for_scope(request, scope)) as s:
        if scope == "site":
            bound = s.get(DemoModelBound, f"venice:{selected_site_model}")
            budget = s.get(DemoControl, "default")
            if (
                not bound
                or not bound.reviewed
                or bound.max_cost_usd <= 0
                or not budget
                or bound.max_cost_usd > min(SITE_DEMO_MAX_MODEL_COST, budget.daily_usd)
                or bound.max_input_chars < values["demo_max_input_chars"]
                or bound.max_output_tokens < values["demo_max_output_tokens"]
            ):
                raise HTTPException(
                    422,
                    detail=(
                        "Модель недоступна для гостевого демо: нужен проверенный предел "
                        "стоимости, подходящий под бюджет и ограничения сайта."
                    ),
                )
        row = policy(s)
        for name, value in values.items():
            setattr(row, name, value)
        row.mode = form["mode"]
        row.demo_models_json = models
        s.add(row)
        if scope == "site":
            channel = s.get(ModelPolicy, "channel:web") or ModelPolicy(id="channel:web")
            channel.models_json = models
            s.add(channel)
        details = {"mode": row.mode, "demo_requests": row.demo_requests}
        if selected_site_model:
            details["model_id"] = selected_site_model
        record_admin_event(
            s,
            actor=actor,
            action="demo.updated",
            target_type="product_policy",
            target_id="default",
            details=details,
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
    if scope == "site" and subject == "channel:web":
        raise HTTPException(
            422,
            detail="Модель гостей сайта выбирается в поле «Модель ответов гостям сайта».",
        )
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
