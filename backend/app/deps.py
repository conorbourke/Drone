"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.models import User
from app.security import SESSION_COOKIE, AuthState

DbSession = Annotated[Session, Depends(get_db)]


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_auth_state(request: Request) -> AuthState:
    return request.app.state.auth


Auth = Annotated[AuthState, Depends(get_auth_state)]


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")


def current_user(request: Request, db: DbSession, auth: Auth) -> User:
    """The signed-in owner, or 401. Checks signature, age and the password fingerprint."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorized()
    data = auth.signer.load(token)
    if data is None or data.get("pw") != auth.fingerprint:
        raise _unauthorized()
    user = db.get(User, data.get("uid"))
    if user is None:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(current_user)]
