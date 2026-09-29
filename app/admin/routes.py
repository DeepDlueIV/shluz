import secrets
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry


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


@router.get("", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    _: Annotated[str, Depends(_require_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    registry: Annotated[ProviderRegistry, Depends(_registry)],
) -> HTMLResponse:
    model_warning = None
    try:
        model_count: int | str = len(registry.list_models())
    except ProviderError:
        model_count = "—"
        model_warning = "Список моделей провайдера временно недоступен"

    return templates.TemplateResponse(
        request=request,
        name="admin/index.html",
        context={
            "environment": settings.environment,
            "version": settings.service_version,
            "model_count": model_count,
            "provider_names": registry.provider_names(),
            "model_warning": model_warning,
        },
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
            get_snapshot = getattr(provider, "get_account_snapshot")
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
