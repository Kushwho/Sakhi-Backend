"""
Sakhi Voice Agent — Entrypoint
===============================
Thin wrapper: ``python agent.py dev`` (or ``console`` / ``start``)

All agent logic lives in agents/sakhi.py.
"""

from livekit import agents

from agents.sakhi import server  # noqa: F401
from utils.logging_config import setup_logging

if __name__ == "__main__":
    setup_logging()
    agents.cli.run_app(server)
