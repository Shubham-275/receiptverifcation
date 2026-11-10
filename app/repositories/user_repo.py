"""Database access for ledger owner accounts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def count_users(session: AsyncSession) -> int:
    """Number of accounts — used to detect first-run setup."""
    result = await session.execute(select(func.count(User.id)))
    return int(result.scalar_one() or 0)


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(
        select(User).where(User.username == username.strip().lower())
    )
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    display_name: str,
    password_hash: str,
) -> User:
    user = User(
        username=username.strip().lower(),
        display_name=display_name.strip() or username.strip().lower(),
        password_hash=password_hash,
    )
    session.add(user)
    await session.flush()
    return user


async def touch_last_login(session: AsyncSession, user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)
    await session.flush()
