from fastapi import Request

from app.config import Settings
from app.providers.registry import ProviderRegistry


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_provider_registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry
