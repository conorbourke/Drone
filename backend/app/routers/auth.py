"""Login, logout and the current user.

``public_router`` holds the one route that must work without a session (login); ``router``
carries the session dependency like every other router.
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.deps import AppSettings, Auth, CurrentUser, DbSession, current_user
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    client_ip,
    password_too_long,
    verify_password,
)

public_router = APIRouter(prefix="/api/auth", tags=["auth"])
router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(current_user)])


def _cookie_kwargs(secure: bool) -> dict[str, object]:
    return {"httponly": True, "samesite": "lax", "secure": secure, "path": "/"}


@public_router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    auth: Auth,
) -> LoginResponse:
    ip = client_ip(request, settings)
    wait = auth.rate_limiter.retry_after_seconds(ip)
    if wait > 0:
        minutes = max(1, math.ceil(wait / 60))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {minutes} minute"
            f"{'s' if minutes != 1 else ''}.",
            headers={"Retry-After": str(math.ceil(wait))},
        )
    if password_too_long(body.password):
        auth.rate_limiter.record_failure(ip)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Wrong password. (Passwords are at most 72 bytes long.)",
        )
    if not verify_password(body.password, auth.password_hash):
        auth.rate_limiter.record_failure(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Wrong password.")

    user = db.scalar(select(User).where(User.is_owner.is_(True)).order_by(User.id))
    if user is None:  # the lifespan creates it; this guards a half-initialised database
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="The owner account is not set up yet."
        )
    auth.rate_limiter.record_success(ip)
    token = auth.signer.create(user.id, auth.fingerprint)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        **_cookie_kwargs(settings.is_production),  # type: ignore[arg-type]
    )
    return LoginResponse(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, settings: AppSettings) -> None:
    response.delete_cookie(SESSION_COOKIE, **_cookie_kwargs(settings.is_production))  # type: ignore[arg-type]


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
