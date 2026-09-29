from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.admin.auth import require_admin
from app.api.dependencies import get_app_settings, get_provider_registry
from app.config import Settings
from app.providers.registry import ProviderRegistry

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="admin/index.html",
        context={
            "service_name": "Shluz",
            "environment": settings.environment,
            "version": settings.service_version,
            "provider_names": registry.provider_names(),
            "model_count": len(registry.list_models()),
        },
    )
