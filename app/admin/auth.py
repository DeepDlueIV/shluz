import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.api.dependencies import get_app_settings
from app.config import Settings

_basic = HTTPBasic(auto_error=False)


def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    provided_username = credentials.username if credentials is not None else ""
    provided_password = credentials.password if credentials is not None else ""

    username_ok = secrets.compare_digest(
        provided_username.encode("utf-8"),
        settings.admin_username.encode("utf-8"),
    )
    password_ok = secrets.compare_digest(
        provided_password.encode("utf-8"),
        settings.admin_password.get_secret_value().encode("utf-8"),
    )

    if not (username_ok & password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
