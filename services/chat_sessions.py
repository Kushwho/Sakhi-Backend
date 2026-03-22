"""
Sakhi — Chat Session History Service
=======================================
Query helpers for listing and reading past chat sessions stored in
``session_summaries``.  Any session that has been ended via
``POST /api/chat/end`` (or the equivalent voice-session summariser) will
appear here.

To *continue* an old session the frontend simply passes the ``thread_id``
(= ``room_name`` in the DB) back to ``POST /api/chat/send`` — LangGraph
automatically reloads the checkpoint.

Functions:
    - ``list_chat_sessions`` — paginated session list for a child profile
    - ``get_chat_session``   — full session detail including stored transcript
"""

import json
import logging
import uuid

from db.pool import get_pool

logger = logging.getLogger("sakhi.chat_sessions")


async def list_chat_sessions(
    profile_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Return a paginated list of past chat sessions for a child profile.

    Each item includes enough metadata for the frontend to show a session
    card (title/date/duration/topics) and also the ``thread_id`` needed to
    resume the conversation via ``POST /api/chat/send``.

    Args:
        profile_id: Child's profile UUID (string form).
        limit:      Max sessions to return (1–100).
        offset:     Pagination offset.

    Returns:
        List of session dicts ordered newest-first.
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id,
                room_name   AS thread_id,
                mode,
                started_at,
                ended_at,
                duration_secs,
                mood_summary,
                topics,
                turn_count,
                created_at
            FROM session_summaries
            WHERE profile_id = $1
            ORDER BY started_at DESC
            LIMIT $2 OFFSET $3
            """,
            uuid.UUID(profile_id),
            limit,
            offset,
        )

    sessions = []
    for r in rows:
        topics_raw = r["topics"]
        if isinstance(topics_raw, str):
            topics_raw = json.loads(topics_raw)

        sessions.append(
            {
                "session_id": str(r["id"]),
                "thread_id": r["thread_id"],
                "mode": r["mode"],
                "started_at": r["started_at"].isoformat(),
                "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
                "duration_secs": r["duration_secs"],
                "mood_summary": r["mood_summary"],
                "topics": topics_raw if isinstance(topics_raw, list) else [],
                "turn_count": r["turn_count"] or 0,
                "created_at": r["created_at"].isoformat(),
            }
        )

    return sessions


async def get_chat_session(session_id: str, profile_id: str) -> dict | None:
    """Return the full detail (including transcript) for a single session.

    Args:
        session_id: The UUID of the session_summary row.
        profile_id: The child's profile UUID — used to enforce ownership.

    Returns:
        Session dict with transcript included, or ``None`` if not found /
        does not belong to this profile.
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                id,
                room_name   AS thread_id,
                mode,
                started_at,
                ended_at,
                duration_secs,
                mood_summary,
                topics,
                turn_count,
                transcript,
                created_at
            FROM session_summaries
            WHERE id = $1 AND profile_id = $2
            """,
            uuid.UUID(session_id),
            uuid.UUID(profile_id),
        )

    if row is None:
        return None

    topics_raw = row["topics"]
    if isinstance(topics_raw, str):
        topics_raw = json.loads(topics_raw)

    transcript_raw = row["transcript"]
    if isinstance(transcript_raw, str):
        transcript_raw = json.loads(transcript_raw)

    return {
        "session_id": str(row["id"]),
        "thread_id": row["thread_id"],
        "mode": row["mode"],
        "started_at": row["started_at"].isoformat(),
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "duration_secs": row["duration_secs"],
        "mood_summary": row["mood_summary"],
        "topics": topics_raw if isinstance(topics_raw, list) else [],
        "turn_count": row["turn_count"] or 0,
        "transcript": transcript_raw if isinstance(transcript_raw, list) else [],
        "created_at": row["created_at"].isoformat(),
    }
