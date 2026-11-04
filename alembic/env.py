"""Alembic environment. The database URL comes from application settings (DATABASE_URL), never alembic.ini.

A caller may override it programmatically via ``config.attributes["database_url"]`` (used by tests).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.infrastructure.database.models import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    # Keep loggers created before migrations (in-process upgrades must not silence the application).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    override = config.attributes.get("database_url")
    if override:
        return str(override)
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
