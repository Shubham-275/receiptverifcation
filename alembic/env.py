"""
Alembic migration environment.

- Reads the sync DATABASE_URL from app settings (no secrets in alembic.ini).
- Imports all ORM models so autogenerate can detect schema changes.
- Supports both offline (SQL script) and online (live DB) modes.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

# Ensure the project root is on sys.path so `app` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models.base import Base  # noqa: F401  — registers the Base metadata

# Import all models so Alembic autogenerate sees every table.
import app.models  # noqa: F401  — registers all ORM models via models/__init__.py

# ── Alembic config ─────────────────────────────────────────────────────────────
config = context.config

# Override the DB URL from our settings so it picks up .env
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

# Standard logging setup from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Offline mode ───────────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to the database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ────────────────────────────────────────────────────────────────

def run_migrations_online() -> None:
    """Connect to the database and run migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
