from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_provider_registry
from app.api.schemas import ModelListResponseSchema, ModelObjectSchema
from app.providers.registry import ProviderRegistry

router = APIRouter()


@router.get("/models", response_model=ModelListResponseSchema)
def list_models(
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
) -> ModelListResponseSchema:
    return ModelListResponseSchema(
        data=[
            ModelObjectSchema(id=model.id, owned_by=model.provider)
            for model in registry.list_models()
        ]
    )
