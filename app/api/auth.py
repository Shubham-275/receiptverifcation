"""Authentication API: first-run setup, login, logout, and session status."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_dep
from app.models.user import User
from app.repositories import user_repo
from app.schemas.auth import AuthStatus, AuthUser, LoginRequest, SetupRequest
from app.services.auth import (
    SESSION_COOKIE,
    create_session_token,
    hash_password,
    normalize_username,
    session_max_age,
    validate_username,
    verify_password,
    verify_session_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(user_id),
        max_age=session_max_age(),
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=get_settings().environment == "production",
        path="/",
    )


async def _user_from_request(request: Request, db: AsyncSession) -> User | None:
    user_id = verify_session_token(request.cookies.get(SESSION_COOKIE))
    if user_id is None:
        return None
    user = await user_repo.get_by_id(db, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def require_user(
    request: Request,
    db: AsyncSession = Depends(get_db_dep),
) -> User:
    """Dependency that rejects requests without a valid signed session."""
    user = await _user_from_request(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Sign in to open the ledger.",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


@router.get(
    "/status",
    response_model=AuthStatus,
    summary="Get authentication state",
    description="Whether the first-run owner account exists and whether this browser has a valid session.",
)
async def get_auth_status(
    request: Request,
    db: AsyncSession = Depends(get_db_dep),
) -> AuthStatus:
    user = await _user_from_request(request, db)
    if user is not None:
        return AuthStatus(
            setup_required=False,
            authenticated=True,
            user=AuthUser.model_validate(user),
        )
    total = await user_repo.count_users(db)
    return AuthStatus(
        setup_required=total == 0,
        authenticated=False,
        user=None,
    )


@router.post(
    "/setup",
    response_model=AuthUser,
    status_code=status.HTTP_201_CREATED,
    summary="Create the first owner account",
    description="Only available while the users table is empty.",
)
async def setup_owner(
    payload: SetupRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_dep),
) -> AuthUser:
    if await user_repo.count_users(db) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An owner account already exists. Sign in instead.",
        )

    try:
        username = validate_username(payload.username)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        user = await user_repo.create_user(
            db,
            username=username,
            display_name=(payload.display_name or username).strip(),
            password_hash=hash_password(payload.password),
        )
        await user_repo.touch_last_login(db, user)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An owner account already exists. Sign in instead.",
        ) from exc

    _set_session_cookie(response, user.id)
    return AuthUser.model_validate(user)


@router.post(
    "/login",
    response_model=AuthUser,
    summary="Sign in",
    description="Verify a username and password, then issue a signed session cookie.",
)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_dep),
) -> AuthUser:
    username = normalize_username(payload.username)
    user = await user_repo.get_by_username(db, username)

    # verify_password always hashes, even when no user matched, so response
    # time does not reveal whether an account exists.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Cookie"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled.",
        )

    await user_repo.touch_last_login(db, user)
    await db.commit()
    _set_session_cookie(response, user.id)
    return AuthUser.model_validate(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out",
    description=(
        "Clear the session cookie. API clients receive 204; browser form "
        "submissions are redirected back to the access screen."
    ),
)
async def logout(request: Request) -> Response:
    wants_html = "text/html" in request.headers.get("accept", "").lower()
    if wants_html:
        response: Response = RedirectResponse(
            url="/",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response)
    return response


@router.get(
    "/me",
    response_model=AuthUser,
    summary="Get the signed-in account",
)
async def get_me(user: User = Depends(require_user)) -> AuthUser:
    return AuthUser.model_validate(user)
