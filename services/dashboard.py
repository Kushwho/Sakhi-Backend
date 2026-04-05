"""
Sakhi — Dashboard Service
===========================
Query functions for the 5 parent dashboard metrics.
All queries operate on profiles owned by the requesting account.

Optimisations applied
---------------------
* ``get_overview`` runs all 5 metric queries concurrently via ``asyncio.gather``,
  reducing wall-clock time to roughly the duration of the *slowest* query.
* A lightweight 30-second TTL in-memory cache prevents redundant DB round-trips
  when a parent refreshes the dashboard in quick succession or switches child
  profiles back and forth.  The cache is profile-scoped so data remains correct
  across multiple children.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from db.pool import get_pool

logger = logging.getLogger("sakhi.dashboard")

# ---------------------------------------------------------------------------
# Simple TTL in-memory cache
# ---------------------------------------------------------------------------
# Structure: { profile_id: (expires_at_monotonic, cached_payload) }
_CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(profile_id: str) -> Any | None:
    """Return cached data if it exists and has not expired, else None."""
    entry = _cache.get(profile_id)
    if entry is None:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        del _cache[profile_id]
        return None
    return payload


def _cache_set(profile_id: str, payload: Any) -> None:
    """Store payload in cache with a TTL expiry."""
    _cache[profile_id] = (time.monotonic() + _CACHE_TTL_SECONDS, payload)


def invalidate_dashboard_cache(profile_id: str) -> None:
    """Call this from any write path (session end, alert dismiss, etc.) to
    evict stale data so the next read reflects the latest state immediately."""
    _cache.pop(profile_id, None)


# ---------------------------------------------------------------------------
# 1. Time Spent with Sakhi
# ---------------------------------------------------------------------------


async def get_time_spent(profile_id: str, days: int = 7) -> dict:
    """Total minutes and daily breakdown for the last N days."""
    pool = get_pool()
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DATE(started_at AT TIME ZONE 'UTC') AS day,
                   SUM(duration_secs)                   AS total_secs,
                   COUNT(*)                              AS session_count
            FROM session_summaries
            WHERE profile_id = $1 AND started_at >= $2
            GROUP BY day
            ORDER BY day
            """,
            uuid.UUID(profile_id),
            cutoff,
        )

    daily = []
    total_secs = 0
    for r in rows:
        secs = r["total_secs"] or 0
        total_secs += secs
        daily.append(
            {
                "date": r["day"].isoformat(),
                "minutes": round(secs / 60, 1),
                "sessions": r["session_count"],
            }
        )

    return {
        "total_minutes": round(total_secs / 60, 1),
        "daily": daily,
    }


# ---------------------------------------------------------------------------
# 2. Mood Summary
# ---------------------------------------------------------------------------


async def get_mood_summary(profile_id: str, days: int = 7) -> dict:
    """Session mood summaries and aggregated emotion trend."""
    pool = get_pool()
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with pool.acquire() as conn:
        # Daily mood summaries from sessions
        summaries = await conn.fetch(
            """
            SELECT DATE(started_at AT TIME ZONE 'UTC') AS day,
                   mood_summary
            FROM session_summaries
            WHERE profile_id = $1 AND started_at >= $2
            ORDER BY started_at DESC
            """,
            uuid.UUID(profile_id),
            cutoff,
        )

        # Emotion distribution from snapshots
        emotion_counts = await conn.fetch(
            """
            SELECT emotion, COUNT(*) AS cnt
            FROM emotion_snapshots
            WHERE profile_id = $1 AND recorded_at >= $2
            GROUP BY emotion
            ORDER BY cnt DESC
            LIMIT 10
            """,
            uuid.UUID(profile_id),
            cutoff,
        )

    return {
        "summaries": [{"date": r["day"].isoformat(), "mood": r["mood_summary"]} for r in summaries],
        "emotion_distribution": [{"emotion": r["emotion"], "count": r["cnt"]} for r in emotion_counts],
    }


# ---------------------------------------------------------------------------
# 3. Topics Explored (Top 5, deduplicated)
# ---------------------------------------------------------------------------


async def get_topics_explored(profile_id: str, days: int = 7) -> dict:
    """Top 5 topics explored across all sessions in the last N days."""
    pool = get_pool()
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT topics
            FROM session_summaries
            WHERE profile_id = $1 AND started_at >= $2
            """,
            uuid.UUID(profile_id),
            cutoff,
        )

    # Aggregate and count topic frequency
    counter: Counter = Counter()
    for r in rows:
        topics_raw = r["topics"]
        if isinstance(topics_raw, str):
            topics_raw = json.loads(topics_raw)
        if isinstance(topics_raw, list):
            for t in topics_raw:
                counter[t.strip().lower()] += 1

    # Return top 5
    top_5 = counter.most_common(5)
    return {
        "topics": [{"name": name, "count": count} for name, count in top_5],
        "total_unique": len(counter),
    }


# ---------------------------------------------------------------------------
# 4. Streak
# ---------------------------------------------------------------------------


async def get_streak(profile_id: str) -> dict:
    """Current and longest streak of consecutive days with sessions."""
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT DATE(started_at AT TIME ZONE 'UTC') AS day
            FROM session_summaries
            WHERE profile_id = $1
            ORDER BY day DESC
            """,
            uuid.UUID(profile_id),
        )

    if not rows:
        return {"current_streak": 0, "longest_streak": 0}

    days_set = {r["day"] for r in rows}
    today = date.today()

    # Current streak: count consecutive days backwards from today
    current = 0
    check = today
    while check in days_set:
        current += 1
        check -= timedelta(days=1)

    # If no session today, check if yesterday counts
    if current == 0:
        check = today - timedelta(days=1)
        while check in days_set:
            current += 1
            check -= timedelta(days=1)

    # Longest streak: iterate all dates
    sorted_days = sorted(days_set)
    longest = 1
    run = 1
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1

    return {
        "current_streak": current,
        "longest_streak": longest,
    }


# ---------------------------------------------------------------------------
# 5. Sakhi Noticed (Alerts)
# ---------------------------------------------------------------------------


async def get_alerts(profile_id: str, limit: int = 20) -> dict:
    """Recent alerts/flags for the parent feed."""
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, alert_type, severity, title, description,
                   recorded_at, dismissed
            FROM alerts
            WHERE profile_id = $1
            ORDER BY recorded_at DESC
            LIMIT $2
            """,
            uuid.UUID(profile_id),
            limit,
        )

    return {
        "alerts": [
            {
                "id": str(r["id"]),
                "type": r["alert_type"],
                "severity": r["severity"],
                "title": r["title"],
                "description": r["description"],
                "recorded_at": r["recorded_at"].isoformat(),
                "dismissed": r["dismissed"],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Overview: All 5 metrics in one call
# ---------------------------------------------------------------------------


async def get_overview(profile_id: str) -> dict:
    """Fetch all 5 dashboard metrics for a child profile.

    Optimisations:
    - Cache: returns the cached payload for up to 30 seconds to avoid
      hammering the DB on quick refreshes or child-profile switches.
    - Concurrency: ``asyncio.gather`` fires all 5 metric coroutines in
      parallel so wall-clock latency is bounded by the *slowest* query
      rather than the *sum* of all queries.
    """
    cached = _cache_get(profile_id)
    if cached is not None:
        logger.debug("dashboard cache hit for profile %s", profile_id)
        return cached

    time_spent, mood, topics, streak, alerts = await asyncio.gather(
        get_time_spent(profile_id),
        get_mood_summary(profile_id),
        get_topics_explored(profile_id),
        get_streak(profile_id),
        get_alerts(profile_id),
    )

    payload = {
        "time_spent": time_spent,
        "mood": mood,
        "topics": topics,
        "streak": streak,
        "alerts": alerts,
    }
    _cache_set(profile_id, payload)
    return payload
