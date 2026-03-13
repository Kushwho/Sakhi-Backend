"""
Sakhi Story Agent — Entrypoint
================================
Thin wrapper: ``python story_entrypoint.py start``  (or ``dev`` / ``console``)

All story agent logic lives in agents/story_agent.py.
"""

from livekit import agents

from agents.story_agent import server  # noqa: F401
from utils.logging_config import setup_logging

if __name__ == "__main__":
    setup_logging()
    agents.cli.run_app(server)
