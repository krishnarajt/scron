"""
Alembic environment configuration.

Reads DATABASE_URL from app.common.constants (which loads .env)
and uses the same Base metadata as the application models.
"""

import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text
from alembic import context

# Ensure the project root is on sys.path so `app` is importable
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.common import constants  # noqa: E402
from app.db.database import Base, DB_SCHEMA  # noqa: E402

# Import all models so that Base.metadata has them registered
from app.db.models import (  # noqa: E402, F401
    User,
    RefreshToken,
    Job,
    JobEnvVar,
    JobExecution,
    JobScriptVersion,
)

# Alembic Config object — provides access to alembic.ini values
config = context.config

# Override sqlalchemy.url with the real value from our app config
config.set_main_option("sqlalchemy.url", constants.DATABASE_URL)

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The MetaData object for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without a live DB."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=DB_SCHEMA,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the DB."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Ensure the target schema exists before running migrations
        if DB_SCHEMA != "public":
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"'))
            connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=DB_SCHEMA,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
