"""
Sakhi Backend — Database Connection Pool
==========================================
Manages an asyncpg connection pool for PostgreSQL.
"""

import logging
import os

import asyncpg

logger = logging.getLogger("sakhi.db")

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """Create the global connection pool. Called once on app startup."""
    global _pool
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    _pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=2,
        max_size=10,
    )
    logger.info("Database connection pool created")
    return _pool


def get_pool() -> asyncpg.Pool:
    """Return the active connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call init_pool() first")
    return _pool


async def close_pool() -> None:
    """Close the connection pool. Called on app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")
