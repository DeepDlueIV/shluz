from collections.abc import Iterable

from app.providers.base import ModelInfo, ModelNotFoundError, Provider


class ProviderRegistry:
    """Resolves public model ids to provider adapters without startup network calls."""

    def __init__(self, providers: Iterable[Provider]) -> None:
        self._providers = list(providers)
        self._models: list[ModelInfo] | None = None
        self._model_to_provider: dict[str, Provider] | None = None

    def list_models(self) -> list[ModelInfo]:
        self._load_models()
        return list(self._models or ())

    def provider_for_model(self, model_id: str) -> Provider:
        self._load_models()
        try:
            return (self._model_to_provider or {})[model_id]
        except KeyError as exc:
            raise ModelNotFoundError(model_id) from exc

    def provider_names(self) -> list[str]:
        return sorted(provider.name for provider in self._providers)

    def provider_by_name(self, name: str) -> Provider | None:
        return next((provider for provider in self._providers if provider.name == name), None)

    def _load_models(self) -> None:
        if self._models is not None and self._model_to_provider is not None:
            return

        models: list[ModelInfo] = []
        model_to_provider: dict[str, Provider] = {}
        for provider in self._providers:
            for model in provider.list_models():
                if model.id in model_to_provider:
                    raise ValueError(f"Duplicate model id: {model.id}")
                models.append(model)
                model_to_provider[model.id] = provider

        self._models = sorted(models, key=lambda model: model.id)
        self._model_to_provider = model_to_provider
