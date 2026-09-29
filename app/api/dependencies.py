from fastapi import Request

from app.config import Settings
from app.db.database import Database
from app.providers.registry import ProviderRegistry
from app.usage.service import UsageService


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_provider_registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_usage_service(request: Request) -> UsageService:
    return request.app.state.usage_service
