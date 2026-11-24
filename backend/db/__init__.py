"""SQLAlchemy async engine, session factory, and declarative Base."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

def normalize_database_url(url: str) -> str:
    """Make a hosted Postgres URL usable by SQLAlchemy's asyncpg driver.

    Render/Neon/Supabase hand out ``postgres://`` or ``postgresql://`` URLs,
    often with ``?sslmode=require``; asyncpg needs ``postgresql+asyncpg://``
    and spells the SSL option ``ssl=``.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
            break
    return url.replace("sslmode=", "ssl=")


_DATABASE_URL = normalize_database_url(
    os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://claimback:claimback@localhost:5432/claimback",
    )
)

engine = create_async_engine(_DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionFactory() as session:
        yield session
