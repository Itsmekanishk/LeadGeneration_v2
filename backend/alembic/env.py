"""Alembic environment: reads DATABASE_URL from app settings, autogenerates
migrations from app.models metadata."""
from __future__ import annotations

import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the app package importable when alembic runs from backend/
sys.path.insert(0, ".")

from app.core.config import get_settings  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models import models  # noqa: E402,F401  (register models)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    settings = get_settings()
    return settings.database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI)."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a real connection."""
    from sqlalchemy import create_engine

    url = _database_url()
    connectable = create_engine(url, pool_pre_ping=True)
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
