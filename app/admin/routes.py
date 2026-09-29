import hmac
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.admin.forms import _choice, _plan_values, _read_form, _required_text
from app.admin.lifecycle import ensure_plan_unused
from app.config import Settings
from app.db.audit import plan_snapshot, record_admin_event
from app.db.database import Database
from app.db.models import Account, ApiToken, Plan, Subscription
from app.db.reports import get_usage_summary, list_accounts_overview, list_plans_overview
from app.db.repositories import create_api_token, generate_api_token, revoke_api_token
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
_ALLOWED_ROLES = {"user", "admin"}
_ALLOWED_STATUSES = {"active", "disabled"}
_ALLOWED_SOURCES = {"harness", "site", "telegram", "api"}


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry


def _database(request: Request) -> Database:
    return request.app.state.database


def _require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
    settings: Annotated[Settings, Depends(_settings)],
) -> str:
    username_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.admin_username.encode("utf-8"),
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.admin_password.get_secret_value().encode("utf-8"),
    )
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=401, detail="Неверные данные администратора",
            headers={"WWW-Authenticate": "Basic"},
        )
    request.state.admin_actor = credentials.username
    return credentials.username


def _secret_is_configured(value: Any) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    resolved = getter() if callable(getter) else str(value)
    return bool(resolved.strip())


def _admin_csrf_token(settings: Settings) -> str:
    secret = settings.admin_password.get_secret_value().encode("utf-8")
    return hmac.new(secret, b"shluz-admin-actions-v1", sha256).hexdigest()


def _require_csrf(submitted: str, settings: Settings) -> None:
    if not submitted or not secrets.compare_digest(submitted, _admin_csrf_token(settings)):
        raise HTTPException(status_code=403, detail="Недействительный защитный токен формы")


def _safe_tokens_by_account(database: Database) -> dict[str, tuple[dict[str, Any], ...]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    with database.session() as session:
        tokens = session.scalars(select(ApiToken).order_by(ApiToken.created_at, ApiToken.id))
        for token in tokens:
            grouped.setdefault(token.account_id, []).append({
                "id": token.id, "name": token.name, "source": token.source,
                "prefix": token.token_prefix, "created_at": token.created_at,
                "last_used_at": token.last_used_at, "revoked": token.revoked_at is not None,
            })
    return {account_id: tuple(items) for account_id, items in grouped.items()}


@router.get("", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    registry: Annotated[ProviderRegistry, Depends(_registry)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    model_warning = None
    try:
        model_count: int | str = len(registry.list_models())
    except ProviderError:
        model_count = "—"
        model_warning = "Список моделей провайдера временно недоступен"
    with database.session() as session:
        accounts = list_accounts_overview(session)
        usage = get_usage_summary(session, recent_limit=0)
    return templates.TemplateResponse(
        request=request, name="admin/index.html", context={
            "environment": settings.environment, "version": settings.service_version,
            "model_count": model_count, "provider_names": registry.provider_names(),
            "model_warning": model_warning, "account_count": len(accounts),
            "usage_request_count": usage.request_count, "usage_cost_usd": usage.cost_usd,
        },
    )


@router.get("/users", response_class=HTMLResponse)
def users_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    show_archived = request.query_params.get("archive") == "1"
    with database.session() as session:
        accounts = [
            account for account in list_accounts_overview(session)
            if (account.status == "archived") == show_archived
        ]
        plans = list(session.scalars(
            select(Plan).where(Plan.active.is_(True)).order_by(Plan.name, Plan.code)
        ))
    return templates.TemplateResponse(
        request=request, name="admin/users.html", context={
            "accounts": accounts, "plans": plans, "show_archived": show_archived,
            "tokens_by_account": _safe_tokens_by_account(database),
            "csrf_token": _admin_csrf_token(settings),
        },
    )


@router.post("/users")
async def create_user(
    request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    display_name = _required_text(form, "display_name", max_length=200)
    role = _choice(form, "role", _ALLOWED_ROLES)
    account_status = _choice(form, "status", _ALLOWED_STATUSES)
    with database.session() as session:
        account = Account(display_name=display_name, role=role, status=account_status)
        session.add(account)
        session.flush()
        record_admin_event(
            session, actor=actor, action="user.created", target_type="account",
            target_id=account.id, details={"role": role, "status": account_status},
        )
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{account_id}/plan")
async def assign_user_plan(
    account_id: str,
    request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    plan_id = form.get("plan_id", "").strip()
    now = datetime.now(UTC)
    with database.session() as session:
        account = session.scalar(
            select(Account).where(Account.id == account_id).with_for_update()
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if account.status == "archived":
            raise HTTPException(status_code=409, detail="Сначала восстановите пользователя из архива")
        if plan_id:
            plan = session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
            if plan is None or not plan.active:
                raise HTTPException(status_code=422, detail="Выбранный тариф недоступен")
        cancelled = 0
        for subscription in session.scalars(select(Subscription).where(
            Subscription.account_id == account_id, Subscription.status == "active",
        )):
            subscription.status = "cancelled"
            subscription.ends_at = now
            cancelled += 1
        if plan_id:
            session.add(Subscription(
                account_id=account_id, plan_id=plan_id, status="active", starts_at=now,
            ))
        record_admin_event(
            session, actor=actor, action="subscription.assigned", target_type="account",
            target_id=account_id,
            details={"plan_id": plan_id or None, "cancelled_subscriptions": cancelled},
        )
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{account_id}/tokens", response_class=HTMLResponse)
async def issue_user_token(
    account_id: str,
    request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    name = _required_text(form, "name", max_length=200)
    source = _choice(form, "source", _ALLOWED_SOURCES)
    raw_token = generate_api_token()
    with database.session() as session:
        account = session.scalar(
            select(Account).where(Account.id == account_id).with_for_update()
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if account.status != "active":
            raise HTTPException(status_code=409, detail="Выдача доступа требует активного аккаунта")
        token = create_api_token(
            session, account_id=account.id, name=name, source=source, raw_token=raw_token,
        )
        account_name = account.display_name
        token_prefix = token.token_prefix
        record_admin_event(
            session, actor=actor, action="token.created", target_type="token",
            target_id=token.id, details={"account_id": account_id, "source": source},
        )
    return templates.TemplateResponse(
        request=request, name="admin/token_created.html", context={
            "account_name": account_name, "token_name": name, "token_source": source,
            "token_prefix": token_prefix, "raw_token": raw_token,
        }, headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/tokens/{token_id}/revoke")
async def revoke_user_token(
    token_id: str,
    request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    with database.session() as session:
        token = session.get(ApiToken, token_id)
        if token is None:
            raise HTTPException(status_code=404, detail="Токен не найден")
        was_active = token.revoked_at is None
        revoke_api_token(session, token_id)
        if was_active:
            record_admin_event(
                session, actor=actor, action="token.revoked", target_type="token",
                target_id=token_id, details={"account_id": token.account_id},
            )
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/plans", response_class=HTMLResponse)
def plans_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    show_archived = request.query_params.get("archive") == "1"
    with database.session() as session:
        plans = [plan for plan in list_plans_overview(session) if plan.active != show_archived]
    return templates.TemplateResponse(
        request=request, name="admin/plans.html", context={
            "plans": plans, "csrf_token": _admin_csrf_token(settings),
            "show_archived": show_archived,
        },
    )


@router.post("/plans")
async def create_plan(
    request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    values = _plan_values(form)
    try:
        with database.session() as session:
            if session.scalar(select(Plan).where(Plan.code == values["code"])) is not None:
                raise HTTPException(status_code=409, detail="Тариф с таким кодом уже существует")
            plan = Plan(**values)
            session.add(plan)
            session.flush()
            record_admin_event(
                session, actor=actor, action="plan.created", target_type="plan",
                target_id=plan.id, details={"after": plan_snapshot(plan)},
            )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Тариф с таким кодом уже существует") from exc
    return RedirectResponse(url="/admin/plans", status_code=303)


@router.post("/plans/{plan_id}")
async def update_plan(
    plan_id: str,
    request: Request,
    actor: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    values = _plan_values(form)
    try:
        with database.session() as session:
            plan = session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
            if plan is None:
                raise HTTPException(status_code=404, detail="Тариф не найден")
            if session.scalar(select(Plan).where(
                Plan.code == values["code"], Plan.id != plan_id,
            )) is not None:
                raise HTTPException(status_code=409, detail="Тариф с таким кодом уже существует")
            if plan.active and not values["active"]:
                ensure_plan_unused(session, plan_id)
            before = plan_snapshot(plan)
            for key, value in values.items():
                setattr(plan, key, value)
            session.flush()
            record_admin_event(
                session, actor=actor, action="plan.updated", target_type="plan",
                target_id=plan.id, details={"before": before, "after": plan_snapshot(plan)},
            )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Тариф с таким кодом уже существует") from exc
    return RedirectResponse(url="/admin/plans", status_code=303)


@router.get("/usage", response_class=HTMLResponse)
def usage_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    with database.session() as session:
        usage = get_usage_summary(session)
    return templates.TemplateResponse(request=request, name="admin/usage.html", context={"usage": usage})


@router.get("/providers/venice", response_class=HTMLResponse)
def venice_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    registry: Annotated[ProviderRegistry, Depends(_registry)],
) -> HTMLResponse:
    provider = registry.provider_by_name("venice")
    models = []
    snapshot = None
    warnings: list[str] = []
    if provider is not None:
        try:
            catalog = getattr(provider, "list_model_catalog", provider.list_models)
            models = catalog()
        except ProviderError:
            warnings.append("Каталог моделей Venice временно недоступен")
        try:
            get_snapshot = provider.get_account_snapshot
            snapshot = get_snapshot(lookback=settings.venice_analytics_lookback)
        except (AttributeError, ProviderError):
            warnings.append("Состояние аккаунта Venice временно недоступно")
    return templates.TemplateResponse(
        request=request, name="admin/provider.html", context={
            "connected": provider is not None,
            "key_configured": _secret_is_configured(settings.venice_api_key),
            "lookback": settings.venice_analytics_lookback, "models": models,
            "snapshot": snapshot, "warnings": tuple(warnings),
        },
    )
