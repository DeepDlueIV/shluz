from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.routes import router as admin_router
from app.api.router import api_router
from app.config import Settings, get_settings
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
) -> FastAPI:
    """Build and configure the FastAPI application."""

    resolved_settings = settings or get_settings()
    resolved_registry = registry or ProviderRegistry([MockProvider()])

    app = FastAPI(title="Shluz", version=resolved_settings.service_version)
    app.state.settings = resolved_settings
    app.state.provider_registry = resolved_registry

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
