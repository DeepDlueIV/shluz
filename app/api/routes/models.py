from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.auth import AuthenticatedPrincipal, require_api_token
from app.api.dependencies import get_database, get_provider_registry
from app.api.schemas import (
    ErrorDetailSchema,
    ErrorResponseSchema,
    ModelListResponseSchema,
    ModelObjectSchema,
)
from app.db.database import Database
from app.product.service import NoModelsProvisioned, allowed_models
from app.providers.registry import ProviderRegistry

router = APIRouter()


@router.get("/models", response_model=ModelListResponseSchema, response_model_exclude_none=True)
def list_models(
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    principal: Annotated[AuthenticatedPrincipal, Depends(require_api_token)],
    database: Annotated[Database, Depends(get_database)],
) -> ModelListResponseSchema:
    with database.session() as session:
        allowed = allowed_models(session, principal.account_id, principal.source)
    if allowed is not None and not allowed:
        # Пустое пересечение — внятная ошибка вместо молчаливого пустого списка.
        error = ErrorResponseSchema(
            error=ErrorDetailSchema(
                message=NoModelsProvisioned.message,
                type="access_policy_error",
                param=None,
                code=NoModelsProvisioned.code,
            )
        )
        return JSONResponse(status_code=NoModelsProvisioned.status, content=error.model_dump())
    return ModelListResponseSchema(
        data=[
            ModelObjectSchema(
                id=model.id,
                owned_by=model.provider,
                # exclude_none выше: без capabilities от провайдера поле не попадает
                # в ответ — существующие клиенты видят прежнюю форму элемента.
                capabilities=model.capabilities,
            )
            for model in registry.list_models()
            if allowed is None or model.id in allowed
        ]
    )
