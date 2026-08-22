"""Database session management.

One async engine for the application, one sync engine for Alembic. Models
inherit from Base; every table lives in app/models/ and is imported in
app/models/__init__.py so Alembic's autogenerate can see it.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

# Explicit naming so Alembic generates stable, readable constraint names
# instead of database-assigned ones that differ between environments.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=True,  # a connection killed by the database is retried, not raised
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects stay usable after commit
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Commits on success, rolls back on any exception."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def worker_session() -> AsyncGenerator[AsyncSession, None]:
    """A session for a Celery task, on an engine that lives and dies with it.

    The module-level `engine` above is built at import time and pools its
    connections. That is right for the API, which runs one event loop for the
    life of the process — and wrong for a worker, where every task calls
    `asyncio.run()` and gets a **new** loop.

    A pooled asyncpg connection remembers the loop it was created on. The first
    task therefore succeeds and leaves a connection in the pool; the second
    picks it up on a different loop and fails with

        RuntimeError: got Future attached to a different loop

    which reads like an application bug and is really a lifetime mismatch. A
    fresh engine with `NullPool`, disposed at the end of the task, cannot
    outlive its loop. The cost is one connection setup per job, which against
    a job that runs ffmpeg for several seconds is nothing.

    Found by the end-to-end run: a single-job test never reaches the second
    task, so nothing before it could have caught this.
    """
    task_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await task_engine.dispose()


async def check_database() -> dict[str, Any]:
    """Used by /health. Never raises — reports."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
