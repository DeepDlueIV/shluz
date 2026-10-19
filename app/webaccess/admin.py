"""Панель ограничений без изменения секретов, платежей и провайдера."""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.admin.forms import _read_form
from app.admin.routes import _admin_csrf_token, _require_admin, _require_csrf, templates
from app.db.audit import record_admin_event
from app.product.service import locked_session
from app.webaccess.models import DemoModelBound
from app.webaccess.security import control

router = APIRouter(prefix="/admin/web-access", tags=["admin"])
Admin = Annotated[str, Depends(_require_admin)]
FIELDS = [
    ("http_network_minute", "Обращений к веб-API с сети в минуту", 1, 1000),
    ("http_global_minute", "Обращений к веб-API на весь сайт в минуту", 1, 10000),
    ("captcha_global_minute", "Проверок антибота на весь сайт в минуту", 1, 1000),
    ("otp_verify_global_minute", "Проверок кода на весь сайт в минуту", 1, 1000),
    ("concurrent", "Одновременных демо на весь сервис", 1, 100),
    ("timeout_seconds", "Максимум секунд генерации демо", 5, 120),
    ("idle_seconds", "Максимум секунд без нового текста", 1, 30),
    ("network_daily", "Пробных запросов с одной сети за 24 часа", 0, 100),
    ("sessions_hourly", "Новых гостевых сессий с сети за час", 1, 100),
    ("sessions_global_daily", "Новых гостевых сессий на весь сайт за 24 часа", 1, 10000),
    ("captcha_minute", "Проверок антибота с сети в минуту", 1, 60),
    ("otp_email_daily", "Писем на один адрес за 24 часа", 1, 20),
    ("otp_network_daily", "Писем с одной сети за 24 часа", 1, 100),
    ("otp_global_daily", "Писем на весь сайт за 24 часа", 1, 10000),
    ("otp_verify_minute", "Проверок кода с сети в минуту", 1, 60),
]


def decimal_value(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or not 0 <= amount <= 10000 or amount.as_tuple().exponent < -8:
            raise ValueError()
        return amount
    except (ValueError, InvalidOperation):
        raise HTTPException(
            422, detail="Сумма должна быть от 0 до 10 000 USD, до 8 знаков после точки"
        ) from None


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, _: Admin):
    db = request.app.state.database
    with locked_session(db) as s:
        cfg = control(s)
        fields = [(name, label, getattr(cfg, name), lo, hi) for name, label, lo, hi in FIELDS]
        bounds = list(s.scalars(select(DemoModelBound).order_by(DemoModelBound.id)))
    web = request.app.state.web_settings
    return templates.TemplateResponse(
        request=request,
        name="admin/web-access.html",
        context={
            "control": cfg,
            "fields": fields,
            "bounds": bounds,
            "csrf_token": _admin_csrf_token(request.app.state.settings),
            "web_enabled": web.enabled,
            "captcha_ready": bool(
                web.captcha_secret and web.captcha_sitekey and web.captcha_hostnames
            ),
            "mail_ready": bool(web.smtp_host and web.smtp_sender),
            "secure_cookie": web.secure_cookie,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/control")
async def save_control(request: Request, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), request.app.state.settings)
    values = {}
    for name, label, low, high in FIELDS:
        try:
            value = int(form.get(name, ""))
            if not low <= value <= high:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, detail=f"{label}: допустимо от {low} до {high}") from None
        values[name] = value
    if values["idle_seconds"] > values["timeout_seconds"]:
        raise HTTPException(422, detail="Пауза без текста не может превышать общее время")
    amount = decimal_value(form.get("daily_usd", ""))
    with locked_session(request.app.state.database) as s:
        cfg = control(s)
        for name, value in values.items():
            setattr(cfg, name, value)
        cfg.daily_usd = amount
        cfg.enabled = form.get("enabled") == "on"
        record_admin_event(
            s,
            actor=actor,
            action="demo.protection.updated",
            target_type="demo_control",
            target_id="default",
            details={},
        )
    return RedirectResponse("/admin/web-access", 303)


@router.post("/model")
async def save_model(request: Request, actor: Admin):
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), request.app.state.settings)
    provider, model = form.get("provider", ""), form.get("model", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", provider) or not re.fullmatch(
        r"[A-Za-z0-9_./:-]{1,200}", model
    ):
        raise HTTPException(422, detail="Проверьте ID поставщика и модели")
    amount = decimal_value(form.get("max_cost_usd", ""))
    if amount <= 0:
        raise HTTPException(422, detail="Для внешней модели нужен положительный предел стоимости")
    try:
        chars, tokens = int(form.get("max_input_chars", "")), int(form.get("max_output_tokens", ""))
        if not 100 <= chars <= 200000 or not 1 <= tokens <= 4096:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, detail="Проверьте ограничения символов и токенов") from None
    with locked_session(request.app.state.database) as s:
        key = provider + ":" + model
        row = s.get(DemoModelBound, key) or DemoModelBound(id=key)
        row.max_cost_usd, row.max_input_chars, row.max_output_tokens = amount, chars, tokens
        row.reviewed = form.get("reviewed") == "on"
        s.add(row)
        record_admin_event(
            s,
            actor=actor,
            action="demo.model_bound.updated",
            target_type="demo_model_bound",
            target_id=hashlib.sha256(key.encode()).hexdigest(),
            details={},
        )
    return RedirectResponse("/admin/web-access", 303)
