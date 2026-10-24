import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from app.admin.access import router as access_admin_router
from app.admin.auxiliary import router as auxiliary_router
from app.admin.diagnostics import router as diagnostics_router
from app.admin.routes import router as admin_router
from app.admin.telegram import router as telegram_admin_router
from app.api.router import api_router
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
from app.webaccess.routes import web_http_error, web_validation_error
from app.webaccess.settings import WebSettings
from app.webaccess.telegram_login import TelegramVerifier
from app.webaccess.telegram_login import router as telegram_login_router

logger = logging.getLogger(__name__)


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
    site_demo_database: Database | None = None,
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

    owns_site_demo_database = False
    resolved_site_demo_database = site_demo_database
    if resolved_site_demo_database is None and resolved_settings.site_demo_database_url:
        site_url = make_url(resolved_settings.site_demo_database_url.get_secret_value())
        site_file_ready = (
            site_url.get_backend_name() != "sqlite"
            or site_url.database in {None, "", ":memory:"}
            or Path(site_url.database).is_file()
        )
        if site_file_ready:
            site_settings = resolved_settings.model_copy(
                update={"database_url": resolved_settings.site_demo_database_url}
            )
            candidate = Database(site_settings)
            try:
                with candidate.engine.connect() as connection:
                    tables = set(inspect(connection).get_table_names())
                    if {"product_policy", "demo_control", "demo_model_bounds"} <= tables:
                        policy_row = connection.execute(
                            text("SELECT id FROM product_policy WHERE id = 'default'")
                        ).first()
                        if policy_row:
                            resolved_site_demo_database = candidate
                            owns_site_demo_database = True
                        else:
                            candidate.dispose()
                            logger.warning(
                                "Guest-site admin database has no default product policy; "
                                "owner scope remains available."
                            )
                    else:
                        candidate.dispose()
                        logger.warning(
                            "Guest-site admin database schema is incomplete; "
                            "owner scope remains available."
                        )
            except Exception as exc:
                candidate.dispose()
                logger.warning(
                    "Guest-site admin database could not be validated (%s); "
                    "owner scope remains available.",
                    type(exc).__name__,
                )
        else:
            logger.warning(
                "Guest-site admin database file is missing; it will not be created automatically."
            )

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
            if owns_site_demo_database and resolved_site_demo_database:
                resolved_site_demo_database.dispose()
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
    app.state.site_demo_database = resolved_site_demo_database
    app.state.usage_service = UsageService(resolved_database, resolved_settings)
    app.add_exception_handler(HTTPException, web_http_error)
    app.add_exception_handler(RequestValidationError, web_validation_error)
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
