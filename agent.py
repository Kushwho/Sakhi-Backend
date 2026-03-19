"""
Sakhi Voice Agent — Entrypoint
===============================
Thin wrapper: ``python agent.py dev`` (or ``console`` / ``start``)

All agent logic lives in agents/sakhi.py.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from livekit import agents

from agents.sakhi import server  # noqa: F401
from utils.logging_config import setup_logging

if __name__ == "__main__":
    setup_logging()
    agents.cli.run_app(server)
