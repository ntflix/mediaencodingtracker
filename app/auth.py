"""HTTP Basic Auth dependency."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Config, get_config

_security = HTTPBasic()


def require_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
    config: Annotated[Config, Depends(get_config)],
) -> str:
    """Raise 401 unless credentials match ADMIN_USER / ADMIN_PASS env vars."""
    user_ok = secrets.compare_digest(
        credentials.username.encode(), config.admin_user.encode()
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode(), config.admin_pass.encode()
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
