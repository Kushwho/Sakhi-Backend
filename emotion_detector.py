"""
Sakhi Emotion Detector — Entrypoint
======================================
Thin wrapper: ``python emotion_detector.py start``

Emotion detection logic lives in agents/emotion_detector.py.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from livekit import agents

from agents.emotion_detector import emotion_server  # noqa: F401
from utils.logging_config import setup_logging

if __name__ == "__main__":
    setup_logging()
    agents.cli.run_app(emotion_server)
