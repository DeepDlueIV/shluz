from collections.abc import Iterable

from app.providers.base import ModelInfo, ModelNotFoundError, Provider


class ProviderRegistry:
    """Resolves public model ids to provider adapters."""

    def __init__(self, providers: Iterable[Provider]) -> None:
        self._providers = list(providers)
        self._model_to_provider: dict[str, Provider] = {}
        for provider in self._providers:
            for model in provider.list_models():
                if model.id in self._model_to_provider:
                    raise ValueError(f"Duplicate model id: {model.id}")
                self._model_to_provider[model.id] = provider

    def list_models(self) -> list[ModelInfo]:
        models = [model for provider in self._providers for model in provider.list_models()]
        return sorted(models, key=lambda model: model.id)

    def provider_for_model(self, model_id: str) -> Provider:
        try:
            return self._model_to_provider[model_id]
        except KeyError as exc:
            raise ModelNotFoundError(model_id) from exc

    def provider_names(self) -> list[str]:
        return sorted(provider.name for provider in self._providers)
