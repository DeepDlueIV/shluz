import hmac
import secrets
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, ApiToken
from app.db.reports import get_usage_summary, list_accounts_overview, list_plans_overview
from app.db.repositories import create_api_token, generate_api_token, revoke_api_token
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)

_ALLOWED_ROLES = {"user", "admin"}
_ALLOWED_STATUSES = {"active", "disabled"}
_ALLOWED_SOURCES = {"harness", "site", "telegram", "api"}
_MAX_FORM_BYTES = 16_384


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry


def _database(request: Request) -> Database:
    return request.app.state.database


def _require_admin(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
    settings: Annotated[Settings, Depends(_settings)],
) -> str:
    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(
        credentials.password,
        settings.admin_password.get_secret_value(),
    )
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные данные администратора",
            headers={"WWW-Authenticate": "Basic"},
        )
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
    expected = _admin_csrf_token(settings)
    if not submitted or not secrets.compare_digest(submitted, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недействительный защитный токен формы",
        )


async def _read_form(request: Request) -> dict[str, str]:
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Форма слишком большая",
        )
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректная кодировка формы",
        ) from exc
    return {key: values[-1] for key, values in parsed.items()}


def _required_text(form: dict[str, str], key: str, *, max_length: int) -> str:
    value = form.get(key, "").strip()
    if not value or len(value) > max_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Поле {key} заполнено некорректно",
        )
    return value


def _choice(form: dict[str, str], key: str, allowed: set[str]) -> str:
    value = form.get(key, "").strip().lower()
    if value not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Недопустимое значение поля {key}",
        )
    return value


def _safe_tokens_by_account(database: Database) -> dict[str, tuple[dict[str, Any], ...]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    with database.session() as session:
        tokens = session.scalars(select(ApiToken).order_by(ApiToken.created_at, ApiToken.id))
        for token in tokens:
            grouped.setdefault(token.account_id, []).append(
                {
                    "id": token.id,
                    "name": token.name,
                    "source": token.source,
                    "prefix": token.token_prefix,
                    "created_at": token.created_at,
                    "last_used_at": token.last_used_at,
                    "revoked": token.revoked_at is not None,
                }
            )
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
        request=request,
        name="admin/index.html",
        context={
            "environment": settings.environment,
            "version": settings.service_version,
            "model_count": model_count,
            "provider_names": registry.provider_names(),
            "model_warning": model_warning,
            "account_count": len(accounts),
            "usage_request_count": usage.request_count,
            "usage_cost_usd": usage.cost_usd,
        },
    )


@router.get("/users", response_class=HTMLResponse)
def users_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    with database.session() as session:
        accounts = list_accounts_overview(session)

    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context={
            "accounts": accounts,
            "tokens_by_account": _safe_tokens_by_account(database),
            "csrf_token": _admin_csrf_token(settings),
        },
    )


@router.post("/users")
async def create_user(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    display_name = _required_text(form, "display_name", max_length=200)
    role = _choice(form, "role", _ALLOWED_ROLES)
    account_status = _choice(form, "status", _ALLOWED_STATUSES)

    with database.session() as session:
        session.add(
            Account(
                display_name=display_name,
                role=role,
                status=account_status,
            )
        )

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{account_id}/tokens", response_class=HTMLResponse)
async def issue_user_token(
    account_id: str,
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    name = _required_text(form, "name", max_length=200)
    source = _choice(form, "source", _ALLOWED_SOURCES)
    raw_token = generate_api_token()

    with database.session() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пользователь не найден",
            )
        token = create_api_token(
            session,
            account_id=account.id,
            name=name,
            source=source,
            raw_token=raw_token,
        )
        account_name = account.display_name
        token_prefix = token.token_prefix

    return templates.TemplateResponse(
        request=request,
        name="admin/token_created.html",
        context={
            "account_name": account_name,
            "token_name": name,
            "token_source": source,
            "token_prefix": token_prefix,
            "raw_token": raw_token,
        },
    )


@router.post("/tokens/{token_id}/revoke")
async def revoke_user_token(
    token_id: str,
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    database: Annotated[Database, Depends(_database)],
) -> RedirectResponse:
    form = await _read_form(request)
    _require_csrf(form.get("csrf_token", ""), settings)
    with database.session() as session:
        if not revoke_api_token(session, token_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Токен не найден",
            )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/plans", response_class=HTMLResponse)
def plans_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    with database.session() as session:
        plans = list_plans_overview(session)

    return templates.TemplateResponse(
        request=request,
        name="admin/plans.html",
        context={"plans": plans},
    )


@router.get("/usage", response_class=HTMLResponse)
def usage_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    database: Annotated[Database, Depends(_database)],
) -> HTMLResponse:
    with database.session() as session:
        usage = get_usage_summary(session)

    return templates.TemplateResponse(
        request=request,
        name="admin/usage.html",
        context={"usage": usage},
    )


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
        request=request,
        name="admin/provider.html",
        context={
            "connected": provider is not None,
            "key_configured": _secret_is_configured(settings.venice_api_key),
            "lookback": settings.venice_analytics_lookback,
            "models": models,
            "snapshot": snapshot,
            "warnings": tuple(warnings),
        },
    )
