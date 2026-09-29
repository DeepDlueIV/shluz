import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_app_settings, get_database
from app.config import Settings
from app.db.database import Database
from app.db.repositories import authenticate_api_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    account_id: str
    source: str
    token_id: str | None
    bootstrap: bool = False


def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    database: Annotated[Database, Depends(get_database)],
) -> AuthenticatedPrincipal:
    provided = credentials.credentials if credentials is not None else ""
    expected = settings.bootstrap_api_token.get_secret_value()

    if provided and secrets.compare_digest(provided, expected):
        return AuthenticatedPrincipal(
            account_id="bootstrap",
            source="bootstrap",
            token_id=None,
            bootstrap=True,
        )

    with database.session() as session:
        personal = authenticate_api_token(session, provided)
    if personal is not None:
        return AuthenticatedPrincipal(
            account_id=personal.account_id,
            source=personal.source,
            token_id=personal.token_id,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API token",
        headers={"WWW-Authenticate": "Bearer"},
    )
