"""
Sakhi Backend — Centralized Logging Configuration
====================================================
Call ``setup_logging()`` once at startup in each process (agent, emotion
detector, API).  All sakhi.* loggers write to:

  1. Console (INFO and above, coloured)
  2. ``logs/sakhi.log`` (DEBUG and above, with timestamps + module)

The log file is rotated at 5 MB and keeps 3 backups.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "sakhi.log")


def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure the root 'sakhi' logger with console + file handlers."""

    os.makedirs(LOG_DIR, exist_ok=True)

    # Root sakhi logger — captures sakhi, sakhi.emotion, sakhi.hume, sakhi.api
    logger = logging.getLogger("sakhi")
    logger.setLevel(level)

    # Avoid adding duplicate handlers on hot-reload
    if logger.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ---- Console handler (INFO+) ----
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # ---- File handler (DEBUG+, rotated 5 MB × 3 backups) ----
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.info(f"Logging initialised → {LOG_FILE}")
