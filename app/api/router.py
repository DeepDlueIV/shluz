from fastapi import APIRouter, Depends

from app.api.auth import require_api_token
from app.api.routes import chat, me, models
from app.product.routes import router as product_router

api_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])
api_router.include_router(models.router, tags=["models"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(me.router, tags=["account"])
api_router.include_router(product_router, tags=["account"])
