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
_cm = None


async def init_checkpointer():
    global _checkpointer, _cm

    if sys.platform == "win32":
        logger.warning(
            "Windows detected — using in-memory checkpointer. "
            "Conversation history will not persist across restarts."
        )
        from langgraph.checkpoint.memory import MemorySaver
        _checkpointer = MemorySaver()
        return _checkpointer
    
    # Linux / Mac — use full PostgreSQL checkpointer
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    database_url = os.getenv("DATABASE_URL", "")
    database_url = database_url.replace("&channel_binding=require", "")
    database_url = database_url.replace("channel_binding=require&", "")

    _cm = AsyncPostgresSaver.from_conn_string(database_url)
    _checkpointer = await _cm.__aenter__()
    await _checkpointer.setup()

    logger.info("LangGraph PostgreSQL checkpointer initialised")
    return _checkpointer


def get_checkpointer():
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialised — call init_checkpointer() first"
        )
    return _checkpointer


async def close_checkpointer():
    global _checkpointer, _cm
    if _cm is not None:
        await _cm.__aexit__(None, None, None)
        _cm = None
    _checkpointer = None
    logger.info("LangGraph checkpointer closed")