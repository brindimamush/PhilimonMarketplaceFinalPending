# migrations/env.py
# LAYER: Infrastructure / Database Migrations
# PURPOSE: Configures Alembic to run asynchronous database migrations.
# WHY HERE: This file bridges the gap between Alembic's migration engine and 
# our refactored Clean Architecture codebase. It ensures Alembic knows where 
# to find the SQLAlchemy Base metadata and the dynamic database connection string.

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# ==============================================================================
# IMPORTS ADJUSTED FOR THE NEW PRODUCTION ARCHITECTURE
# ==============================================================================

# 1. Import the Base class from the new db.base module.
# WHY: Alembic needs the metadata from this specific Base class to auto-generate 
# or run migrations. In the old structure, this was in app.db. Now it's in app.db.base.
from app.db.base import Base

# 2. Import the settings from the new config.settings module.
# WHY: This provides the dynamic database URL based on environment variables (.env).
from app.config.settings import get_settings

# 3. Import the models package.
# WHY: We don't necessarily need to import specific models here, but importing the 
# app.models package triggers the app/models/__init__.py file. That __init__.py 
# imports all the domain models (user, marketplace, system), which registers their 
# tables with the Base.metadata. 
# CRITICAL: If you forget this import, Alembic will think your database is empty 
# and will try to drop all tables when generating a migration!
import app.models  # noqa: F401

# ==============================================================================
# ALEMBIC CONFIGURATION
# ==============================================================================

# Alembic Config object, provides access to the values in alembic.ini
config = context.config

# Interpret the config file for Python logging if present.
# This sets up the loggers defined in alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Dynamically set the database URL from our Pydantic settings.
# WHY: This overrides the placeholder URL in alembic.ini, ensuring we use 
# the correct async PostgreSQL connection string (postgresql+asyncpg://...) 
# constructed by our Settings class from the .env file.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# Set the metadata object that Alembic will use to compare against the DB schema.
# Because we imported `app.models` above, this metadata now contains all our tables.
target_metadata = Base.metadata


# ==============================================================================
# MIGRATION EXECUTION FUNCTIONS
# ==============================================================================

def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.
    
    WHY: Generates SQL scripts without actually connecting to the database.
    This is useful for DBA reviews, generating changelogs, or CI/CD pipelines 
    where direct DB access isn't available or permitted.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """
    Helper function to configure the migration context and run migrations 
    within an active database connection.
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode asynchronously.
    
    WHY: Our application uses asyncpg (async SQLAlchemy). We must create an 
    async engine and use `run_sync` to execute the synchronous Alembic context 
    operations within the async event loop.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool, # Use NullPool for migrations to avoid connection leaks
    )

    async with connectable.connect() as connection:
        # run_sync bridges the async connection with Alembic's sync context
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Entry point for running migrations in 'online' mode.
    Uses asyncio to run the async migration function against the live database.
    """
    asyncio.run(run_async_migrations())


# ==============================================================================
# ENTRY POINT ROUTING
# ==============================================================================

# Check if Alembic is running in offline or online mode and execute accordingly.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()