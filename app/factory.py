from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.admin.auxiliary import router as auxiliary_router
from app.admin.errors import admin_http_error, admin_validation_error
from app.admin.routes import router as admin_router
from app.api.router import api_router
from app.config import Settings, get_settings
from app.db.database import Database
from app.db.repositories import ensure_bootstrap_account
from app.observability import install_observability
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.providers.venice import VeniceProvider
from app.usage.service import UsageService


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    """Создать провайдер без внешних запросов во время запуска."""
    if settings.active_provider == "venice":
        return ProviderRegistry([VeniceProvider(settings)])
    return ProviderRegistry([MockProvider()])


def build_database(settings: Settings) -> Database:
    database = Database(settings)
    database.create_schema()
    with database.session() as session:
        ensure_bootstrap_account(session)
    return database


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    database: Database | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_registry = registry or build_provider_registry(resolved_settings)
    resolved_database = database or build_database(resolved_settings)
    resolved_database.create_schema()
    with resolved_database.session() as session:
        ensure_bootstrap_account(session)

    app = FastAPI(
        title="Shluz", version=resolved_settings.service_version, docs_url=None, redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.state.provider_registry = resolved_registry
    app.state.database = resolved_database
    app.state.usage_service = UsageService(resolved_database, resolved_settings)
    app.add_exception_handler(HTTPException, admin_http_error)
    app.add_exception_handler(RequestValidationError, admin_validation_error)
    install_observability(app)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok", "service": resolved_settings.service_name,
            "version": resolved_settings.service_version,
        }

    @app.get("/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        page = get_swagger_ui_html(
            openapi_url=app.openapi_url, title="Shluz — документация API",
            swagger_ui_parameters={"persistAuthorization": False},
        )
        html = page.body.decode("utf-8").replace(
            "</head>", '<link rel="stylesheet" href="/static/docs.css"></head>',
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/redoc", include_in_schema=False)
    def redoc() -> RedirectResponse:
        return RedirectResponse("/docs")

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(api_router)
    app.include_router(admin_router)
    app.include_router(auxiliary_router)
    return app
