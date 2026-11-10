"""
Async SQLAlchemy database engine, session factory, and dependency helper.

Usage:
    async with get_db() as session:
        ...

    # FastAPI dependency injection:
    async def endpoint(db: AsyncSession = Depends(get_db_dep)):
        ...
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.exceptions import DatabaseError
from app.logging_config import get_logger

log = get_logger(__name__)

# Module-level engine and session factory — created lazily on first use.
_engine = None
_async_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_pre_ping=True,   # detect stale connections
            echo=settings.debug,  # log SQL only in debug mode
        )
        log.info("database_engine_created", url=settings.database_url.split("@")[-1])
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=_get_engine(),
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_session_factory


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Context-manager that yields a session and commits/rolls back automatically."""
    session_factory = _get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                await session.close()
            except Exception:
                pass


async def get_db_dep() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a DB session per request."""
    try:
        async with get_db() as session:
            yield session
    except (SQLAlchemyError, OSError) as exc:
        log.error("database_connection_failed", error=str(exc))
        raise DatabaseError(
            "Database connection failed.",
            detail=(
                "PostgreSQL database is currently offline or unreachable on localhost:5432. "
                "Switch to 'Preview Only' mode above to verify and extract receipts without database persistence, "
                "or start PostgreSQL service."
            ),
        )


async def check_db_connection() -> bool:
    """Health-check helper — returns True if the database is reachable."""
    from sqlalchemy import text

    try:
        async with get_db() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.error("database_health_check_failed", error=str(exc))
        return False


async def close_db() -> None:
    """Dispose the connection pool — call on application shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        log.info("database_engine_disposed")
        _engine = None
