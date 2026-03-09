"""
Sakhi — Dashboard Service
===========================
Query functions for the 5 parent dashboard metrics.
All queries operate on profiles owned by the requesting account.
"""

import json
import logging
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from db.pool import get_pool

logger = logging.getLogger("sakhi.dashboard")


# ---------------------------------------------------------------------------
# 1. Time Spent with Sakhi
# ---------------------------------------------------------------------------


async def get_time_spent(profile_id: str, days: int = 7) -> dict:
    """Total minutes and daily breakdown for the last N days."""
    pool = get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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
        daily.append({
            "date": r["day"].isoformat(),
            "minutes": round(secs / 60, 1),
            "sessions": r["session_count"],
        })

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
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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
        "summaries": [
            {"date": r["day"].isoformat(), "mood": r["mood_summary"]}
            for r in summaries
        ],
        "emotion_distribution": [
            {"emotion": r["emotion"], "count": r["cnt"]}
            for r in emotion_counts
        ],
    }


# ---------------------------------------------------------------------------
# 3. Topics Explored (Top 5, deduplicated)
# ---------------------------------------------------------------------------


async def get_topics_explored(profile_id: str, days: int = 7) -> dict:
    """Top 5 topics explored across all sessions in the last N days."""
    pool = get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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
    """Fetch all 5 dashboard metrics for a child profile."""
    return {
        "time_spent": await get_time_spent(profile_id),
        "mood": await get_mood_summary(profile_id),
        "topics": await get_topics_explored(profile_id),
        "streak": await get_streak(profile_id),
        "alerts": await get_alerts(profile_id),
    }
