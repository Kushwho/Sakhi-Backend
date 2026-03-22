"""
Sakhi Emotion Detector — Entrypoint (local dev convenience)
============================================================
In production, both agents run on a single AgentServer via ``agent.py``.
This entrypoint is kept for local development so you can still run
``python emotion_detector.py dev`` independently if needed — it imports
the same shared server that already has both handlers registered.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from livekit import agents

from agents.sakhi import server  # noqa: F401 — has both sakhi-agent + emotion-detector
from utils.logging_config import setup_logging

if __name__ == "__main__":
    setup_logging()
    agents.cli.run_app(server)
