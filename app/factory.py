import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from app.admin.access import router as access_admin_router
from app.admin.auxiliary import router as auxiliary_router
from app.admin.diagnostics import router as diagnostics_router
from app.admin.routes import router as admin_router
from app.admin.telegram import router as telegram_admin_router
from app.api.router import api_router
from app.api.validation import api_validation_error
from app.billing.admin import router as wallet_admin_router
from app.config import Settings, get_settings
from app.db.database import Database
from app.db.repositories import ensure_bootstrap_account
from app.observability import install_observability
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.providers.venice import VeniceProvider
from app.streaming.telemetry import Telemetry
from app.telegram.credentials import ensure_connector_key
from app.telegram.routes import router as telegram_router
from app.usage.service import UsageService
from app.webaccess.adapters import SmtpMailer, Turnstile
from app.webaccess.admin import router as web_admin_router
from app.webaccess.maintenance import cleanup, maintain
from app.webaccess.perimeter import WebPerimeter
from app.webaccess.routes import router as web_router
from app.webaccess.routes import web_http_error
from app.webaccess.settings import WebSettings
from app.webaccess.telegram_login import TelegramVerifier
from app.webaccess.telegram_login import router as telegram_login_router


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
    web_settings: WebSettings | None = None,
    web_captcha=None,
    web_mailer=None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_web = web_settings or WebSettings()
    if (
        web_captcha is not None or web_mailer is not None
    ) and resolved_settings.environment != "test":
        raise ValueError("Подмена почты и антибот-проверки допустима только в изолированных тестах")
    resolved_registry = registry or build_provider_registry(resolved_settings)
    resolved_database = database or build_database(resolved_settings)
    resolved_database.create_schema()
    with resolved_database.session() as session:
        ensure_bootstrap_account(session)

    if resolved_settings.telegram_connector_file:
        ensure_connector_key(resolved_settings.telegram_connector_file)

    telemetry = Telemetry(resolved_settings)

    @asynccontextmanager
    async def lifespan(app):
        worker = None
        if resolved_web.enabled:
            await run_in_threadpool(cleanup, resolved_database)
            worker = asyncio.create_task(maintain(resolved_database))
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            telemetry.close()

    app = FastAPI(
        lifespan=lifespan,
        title="Shluz",
        version=resolved_settings.service_version,
        docs_url=None,
        redoc_url=None,
    )
    app.state.telegram_login = TelegramVerifier()
    app.state.web_settings = resolved_web
    app.state.web_captcha = web_captcha if web_captcha is not None else Turnstile(resolved_web)
    app.state.web_mailer = web_mailer if web_mailer is not None else SmtpMailer(resolved_web)
    app.state.telemetry = telemetry
    app.state.settings = resolved_settings
    app.state.provider_registry = resolved_registry
    app.state.database = resolved_database
    app.state.usage_service = UsageService(resolved_database, resolved_settings)
    app.add_exception_handler(HTTPException, web_http_error)
    app.add_exception_handler(RequestValidationError, api_validation_error)
    install_observability(app)
    app.add_middleware(WebPerimeter, settings=resolved_web, database=resolved_database)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": resolved_settings.service_name,
            "version": resolved_settings.service_version,
            **({"build": resolved_settings.build_sha} if resolved_settings.build_sha else {}),
        }

    @app.get("/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        page = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title="Shluz — документация API",
            swagger_ui_parameters={"persistAuthorization": False},
        )
        html = page.body.decode("utf-8").replace(
            "</head>",
            '<link rel="stylesheet" href="/static/docs.css"></head>',
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/redoc", include_in_schema=False)
    def redoc() -> RedirectResponse:
        return RedirectResponse("/docs")

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(diagnostics_router)
    app.include_router(access_admin_router)
    app.include_router(api_router)
    app.include_router(web_router)
    app.include_router(telegram_login_router)
    app.include_router(web_admin_router)
    app.include_router(telegram_router)
    app.include_router(telegram_admin_router)
    app.include_router(admin_router)
    app.include_router(wallet_admin_router)
    app.include_router(auxiliary_router)
    return app
