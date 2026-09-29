from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.routes import router as admin_router
from app.api.router import api_router
from app.config import Settings, get_settings
from app.db.session import (
    SessionFactory,
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.providers.venice import VeniceProvider


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    """Create the configured provider registry without making network requests."""

    if settings.active_provider == "venice":
        return ProviderRegistry([VeniceProvider(settings)])
    return ProviderRegistry([MockProvider()])


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    session_factory: SessionFactory | None = None,
) -> FastAPI:
    """Build and configure the FastAPI application."""

    resolved_settings = settings or get_settings()
    resolved_registry = registry or build_provider_registry(resolved_settings)

    database_engine = None
    resolved_session_factory = session_factory
    if resolved_session_factory is None:
        database_engine = create_database_engine(
            resolved_settings.database_url,
            echo=resolved_settings.database_echo,
        )
        initialize_database(database_engine)
        resolved_session_factory = create_session_factory(database_engine)

    app = FastAPI(title="Shluz", version=resolved_settings.service_version)
    app.state.settings = resolved_settings
    app.state.provider_registry = resolved_registry
    app.state.database_engine = database_engine
    app.state.session_factory = resolved_session_factory

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": resolved_settings.service_name,
            "version": resolved_settings.service_version,
        }

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(api_router)
    app.include_router(admin_router)
    return app
