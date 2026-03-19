"""
Sakhi — LangGraph PostgreSQL Checkpointer
===========================================
Manages the lifecycle of the ``AsyncPostgresSaver`` that persists
LangGraph conversation state (checkpoints) to the same PostgreSQL
database used by the rest of the application.

On Windows: uses MemorySaver (psycopg incompatible with ProactorEventLoop).
On Linux/Mac: uses AsyncPostgresSaver with PostgreSQL.

Initialised at FastAPI startup, closed at shutdown.
"""

import logging
import os
import sys

logger = logging.getLogger("sakhi.checkpointer")

_checkpointer = None
_pool = None


async def init_checkpointer():
    global _checkpointer, _pool

    if sys.platform == "win32":
        logger.warning(
            "Windows detected — using in-memory checkpointer. Conversation history will not persist across restarts."
        )
        from langgraph.checkpoint.memory import MemorySaver

        _checkpointer = MemorySaver()
        return _checkpointer

    # Linux / Mac — use full PostgreSQL checkpointer
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    database_url = os.getenv("DATABASE_URL", "")
    database_url = database_url.replace("&channel_binding=require", "")
    database_url = database_url.replace("channel_binding=require&", "")

    # Use a connection pool instead of a single connection so that
    # stale/dropped connections (common with NeonDB serverless) are
    # automatically replaced.
    _pool = AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"autocommit": True},
        # NeonDB serverless closes idle connections after ~5 min.
        # check runs a lightweight query before handing a conn to a caller,
        # so stale SSL connections are detected and replaced automatically.
        check=AsyncConnectionPool.check_connection,
        max_idle=300,  # close conns idle > 5 min before NeonDB does
    )
    await _pool.open()

    _checkpointer = AsyncPostgresSaver(conn=_pool)
    await _checkpointer.setup()

    logger.info("LangGraph PostgreSQL checkpointer initialised (pooled)")
    return _checkpointer


def get_checkpointer():
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialised — call init_checkpointer() first")
    return _checkpointer


async def close_checkpointer():
    global _checkpointer, _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    _checkpointer = None
    logger.info("LangGraph checkpointer closed")
