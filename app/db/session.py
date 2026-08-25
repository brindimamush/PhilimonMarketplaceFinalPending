# app/db/session.py
# LAYER: Infrastructure / Database
# PURPOSE: Manages the async database engine, session factory, and transaction boundaries.
# WHY HERE: Centralizes database connections. The context manager ensures transactions 
# are safely committed on success and rolled back on exceptions, preventing data corruption.

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings

settings = get_settings()

# pool_pre_ping=True ensures we don't use stale connections if the DB restarted
engine = create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)
# expire_on_commit=False prevents attributes from expiring after a commit, which is crucial for async
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@asynccontextmanager
async def session_scope():
    """
    Provides a transactional scope around a series of operations.
    Acquires a session, yields it, and handles commit/rollback automatically.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit() # Commit if no exceptions occurred
        except Exception:
            await session.rollback() # Rollback on any error to maintain DB integrity
            raise