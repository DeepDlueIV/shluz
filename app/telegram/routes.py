"""Внутренний API только для доверенного процесса бота, не публичный Telegram Login."""

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.dependencies import get_app_settings, get_database
from app.config import Settings
from app.db.database import Database
from app.db.models import TelegramIntegration
from app.telegram.credentials import SESSION_TTL, issue_session, read_connector_key
from app.telegram.service import connect, transaction

bearer = HTTPBearer(auto_error=False)
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
Db = Annotated[Database, Depends(get_database)]


def require_connector(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: SettingsDep,
) -> str:
    expected = read_connector_key(settings.telegram_connector_file)
    provided = credentials.credentials if credentials else ""
    if not expected or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(401, detail="Telegram connector authorization required")
    return expected


router = APIRouter(
    prefix="/internal/telegram", tags=["telegram"], dependencies=[Depends(require_connector)]
)


class SessionInput(BaseModel):
    telegram_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    display_name: str = Field(min_length=1, max_length=200)
    username: str | None = Field(default=None, max_length=200)
    invite: str | None = Field(default=None, pattern=r"^join_[A-Za-z0-9_-]{32}$")
    legacy_token: SecretStr | None = Field(default=None, max_length=128)
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class BotInput(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{5,32}$")
    model_config = ConfigDict(extra="forbid")


@router.post("/hello")
def hello(payload: BotInput, database: Db, response: Response):
    with transaction(database) as session:
        row = session.get(TelegramIntegration, "default")
        if row is None:
            row = TelegramIntegration(id="default", username=payload.username)
            session.add(row)
        row.username = payload.username
        row.updated_at = datetime.now(UTC)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}


@router.post("/session")
def session_access(
    payload: SessionInput,
    database: Db,
    settings: SettingsDep,
    response: Response,
    key: Annotated[str, Depends(require_connector)],
):
    try:
        row = connect(database, payload, pending_limit=settings.telegram_pending_limit)
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail},
                headers={"Cache-Control": "no-store"},
            )
        raise
    result = {"state": "active" if row.status == "approved" else row.status, "request_id": row.id}
    if row.status == "approved":
        result.update(
            account_id=row.account_id,
            token=issue_session(key, row.id, row.telegram_id, row.session_version),
            expires_in=SESSION_TTL,
        )
    response.headers["Cache-Control"] = "no-store"
    return result
