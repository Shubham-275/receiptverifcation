"""
Seed the demo owner account when the users table is empty.

This keeps the local demo usable out of the box. Credentials can be changed
with AUTH_SEED_USERNAME / AUTH_SEED_PASSWORD, or seeding can be disabled with
AUTH_SEED_ENABLED=false.
"""
from __future__ import annotations

from app.config import get_settings
from app.logging_config import get_logger
from app.repositories import user_repo
from app.services.auth import hash_password, normalize_username

log = get_logger(__name__)


async def seed_default_user() -> None:
    settings = get_settings()
    if not settings.auth_seed_enabled:
        return

    # Imported lazily so app startup does not open a database connection
    # unless seeding is actually needed.
    from app.database import get_db

    try:
        async with get_db() as session:
            if await user_repo.count_users(session) > 0:
                return
            username = normalize_username(settings.auth_seed_username)
            user = await user_repo.create_user(
                session,
                username=username,
                display_name=settings.auth_seed_display_name or username,
                password_hash=hash_password(settings.auth_seed_password),
            )
            log.info("auth_seed_user_created", username=user.username)
    except Exception as exc:  # noqa: BLE001 — startup must survive an offline DB
        log.warning("auth_seed_skipped", error=str(exc))
