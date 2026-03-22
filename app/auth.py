"""HTTP Basic Auth dependency."""

from __future__ import annotations

import base64
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Config, get_config

_security = HTTPBasic()


def is_authorized(authorization_header: str | None, config: Config) -> bool:
    """Return True when *authorization_header* matches configured credentials."""
    if not authorization_header:
        return False
    if not authorization_header.startswith("Basic "):
        return False

    token = authorization_header[6:].strip()
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return False

    if ":" not in decoded:
        return False

    username, password = decoded.split(":", 1)
    user_ok = secrets.compare_digest(username.encode(), config.admin_user.encode())
    pass_ok = secrets.compare_digest(password.encode(), config.admin_pass.encode())
    return user_ok and pass_ok


def require_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
    config: Annotated[Config, Depends(get_config)],
) -> str:
    """Raise 401 unless credentials match ADMIN_USER / ADMIN_PASS env vars."""
    header = (
        "Basic "
        + base64.b64encode(
            f"{credentials.username}:{credentials.password}".encode()
        ).decode()
    )
    if not is_authorized(header, config):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
