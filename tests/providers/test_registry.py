import pytest

from app.providers.base import ModelNotFoundError
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry


def test_registry_lists_models_and_provider_names():
    registry = ProviderRegistry([MockProvider()])

    assert [model.id for model in registry.list_models()] == ["mock-chat"]
    assert registry.provider_names() == ["mock"]


def test_registry_resolves_provider_by_model():
    registry = ProviderRegistry([MockProvider()])

    provider = registry.provider_for_model("mock-chat")

    assert provider.name == "mock"


def test_registry_rejects_unknown_model():
    registry = ProviderRegistry([MockProvider()])

    with pytest.raises(ModelNotFoundError, match="unknown-model"):
        registry.provider_for_model("unknown-model")
