"""
Sakhi Voice Agent — Chat Image Service (Rate-Limited)
======================================================
Self-contained image generation service for the voice agent process.

Uses the same ``chat_image_usage`` table as the FastAPI chat endpoint,
so the daily quota (default 3, env-override via CHAT_IMAGE_DAILY_LIMIT)
is shared across both chat and voice sessions.

DB pool pattern mirrors MemoryManager._get_pool() — lazy-init asyncpg pool
from DATABASE_URL, separate from the FastAPI pool.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

import asyncpg
import httpx

logger = logging.getLogger("sakhi.agent.image")

# ---------------------------------------------------------------------------
# Replicate image generation (inlined — no FastAPI dependency)
# ---------------------------------------------------------------------------

REPLICATE_API_BASE = "https://api.replicate.com/v1"
FLUX_MODEL = "black-forest-labs/flux-schnell"
_POLL_INTERVAL_S = 1.5
_MAX_POLL_ATTEMPTS = 40
_REQUEST_TIMEOUT_S = 30.0

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_DAILY_LIMIT = 100


def _get_daily_limit() -> int:
    try:
        print(os.getenv("CHAT_IMAGE_DAILY_LIMIT",str(_DEFAULT_DAILY_LIMIT)))
        return int(os.getenv("CHAT_IMAGE_DAILY_LIMIT", str(_DEFAULT_DAILY_LIMIT)))
    except (ValueError, TypeError):
        return _DEFAULT_DAILY_LIMIT


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class QuotaExceededError(Exception):
    """Raised when the profile has exhausted its daily image quota."""


# ---------------------------------------------------------------------------
# DB pool — lazy-init, separate from FastAPI
# ---------------------------------------------------------------------------

_db_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool | None:
    """Return (or create) the agent's DB connection pool."""
    global _db_pool
    if _db_pool is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.warning("DATABASE_URL not set — image quota tracking disabled")
            return None
        _db_pool = await asyncpg.create_pool(
            dsn=database_url, min_size=1, max_size=2
        )
        logger.info("Agent image-service DB pool created")
    return _db_pool


# ---------------------------------------------------------------------------
# Quota helpers
# ---------------------------------------------------------------------------


async def get_daily_usage(profile_id: str) -> int:
    """Count how many images this profile has generated today (UTC)."""
    pool = await _get_pool()
    if not pool:
        return 0  # fail-open if DB unavailable

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS cnt
            FROM chat_image_usage
            WHERE profile_id = $1
              AND created_at >= $2
            """,
            uuid.UUID(profile_id),
            today_start,
        )
    return int(row["cnt"]) if row else 0


async def _record_usage(profile_id: str) -> None:
    """Insert one usage row after a successful generation."""
    pool = await _get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO chat_image_usage (profile_id) VALUES ($1)",
            uuid.UUID(profile_id),
        )


# ---------------------------------------------------------------------------
# Replicate image generation (inlined, stateless)
# ---------------------------------------------------------------------------


async def _create_prediction(prompt: str, aspect_ratio: str) -> str | None:
    """Submit a Flux Schnell prediction and return the prediction ID."""
    api_token = os.getenv("REPLICATE_API_TOKEN")
    if not api_token:
        logger.error("REPLICATE_API_TOKEN not set")
        return None

    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    payload = {
        "input": {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": "webp",
            "num_outputs": 1,
            "go_fast": True,
        }
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = await client.post(
                f"{REPLICATE_API_BASE}/models/{FLUX_MODEL}/predictions",
                headers=headers,
                json=payload,
            )
        if resp.status_code not in (200, 201):
            logger.error(f"Replicate prediction create failed: HTTP {resp.status_code}")
            return None
        data = resp.json()
        return data.get("id")
    except Exception as e:
        logger.error(f"Replicate prediction create error: {e}")
        return None


async def _poll_prediction(prediction_id: str) -> str | None:
    """Poll until the prediction succeeds or fails. Returns the image URL."""
    api_token = os.getenv("REPLICATE_API_TOKEN")
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    poll_url = f"{REPLICATE_API_BASE}/predictions/{prediction_id}"

    import asyncio

    for attempt in range(1, _MAX_POLL_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.get(poll_url, headers=headers)
            if resp.status_code != 200:
                logger.error(f"Poll {attempt}: HTTP {resp.status_code}")
                return None
            data = resp.json()
            status = data.get("status", "unknown")
            if status == "succeeded":
                output = data.get("output")
                if isinstance(output, list) and output:
                    return output[0]
                if isinstance(output, str):
                    return output
                return None
            elif status in ("failed", "canceled"):
                logger.error(f"Prediction {status}: {data.get('error')}")
                return None
            await asyncio.sleep(_POLL_INTERVAL_S)
        except Exception as e:
            logger.warning(f"Poll {attempt} error: {e}")
            await asyncio.sleep(_POLL_INTERVAL_S)

    logger.error(f"Image generation timed out after {_MAX_POLL_ATTEMPTS} attempts")
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_chat_image(
    profile_id: str,
    prompt: str,
    aspect_ratio: str = "1:1",
) -> dict:
    """
    Generate one image for the voice agent, subject to the daily quota.

    Shares the ``chat_image_usage`` table with the chat API endpoint so
    the quota is consumed across both chat and voice.

    Returns:
        {"image_url": str, "remaining_today": int}

    Raises:
        QuotaExceededError: quota exhausted for today.
        RuntimeError: Replicate failed to produce an image.
    """
    if not prompt or not prompt.strip():
        raise ValueError("Image prompt cannot be empty")

    daily_limit = _get_daily_limit()
    used = await get_daily_usage(profile_id)

    if used >= daily_limit:
        logger.info(
            f"Voice image quota exhausted for {profile_id}: {used}/{daily_limit}"
        )
        raise QuotaExceededError(
            f"Daily image limit of {daily_limit} reached"
        )

    logger.info(
        f"Voice image generation: '{prompt[:60]}' [{aspect_ratio}] "
        f"— usage {used + 1}/{daily_limit}"
    )

    prediction_id = await _create_prediction(prompt.strip(), aspect_ratio)
    if not prediction_id:
        raise RuntimeError("Failed to create Replicate prediction")

    image_url = await _poll_prediction(prediction_id)
    if not image_url:
        raise RuntimeError("Image generation produced no URL")

    await _record_usage(profile_id)

    remaining = daily_limit - (used + 1)
    logger.info(f"Voice image ready for {profile_id}. Remaining today: {remaining}")
    return {"image_url": image_url, "remaining_today": remaining}
