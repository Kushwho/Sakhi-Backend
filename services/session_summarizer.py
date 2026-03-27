"""
Sakhi — Session Summarizer
============================
Single LLM call at session end to extract topics, mood summary, and alerts
from the full conversation transcript + emotion timeline.

Uses Groq/Llama (same as the voice agent) to keep costs near zero.

NOTE: This runs inside the agent process (not FastAPI), so it creates
its own DB pool instead of using the shared FastAPI pool.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
import asyncpg

logger = logging.getLogger("sakhi.summarizer")

# ---------------------------------------------------------------------------
# Database pool (lazy-init — separate from FastAPI's pool)
# ---------------------------------------------------------------------------

_db_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool | None:
    """Lazy-init a small asyncpg pool for the agent process."""
    global _db_pool
    if _db_pool is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.warning("DATABASE_URL not set — session persistence disabled")
            return None
        _db_pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=3)
        logger.info("Session summarizer DB pool created")
    return _db_pool


# ---------------------------------------------------------------------------
# Summarization Prompt
# ---------------------------------------------------------------------------

SUMMARIZE_PROMPT = """\
You are analyzing a conversation between Sakhi (an AI companion for children aged 4-12) \
and a child. You are given the conversation transcript and the child's detected emotions \
during the session.

Analyze the conversation and return a JSON object with exactly these fields:

1. "topics": A list of 1-5 short topic labels the child explored (e.g. "photosynthesis", \
"multiplication tables", "dinosaurs", "feelings about school"). Be specific, not generic.

2. "mood_summary": One sentence describing the child's overall emotional state during \
the session (e.g. "Mostly happy and curious, with brief frustration during math problems").

3. "alerts": A list of objects with {{title, description, severity}} for any concerning \
content. Severity must be "info", "warning", or "critical". Look for:
   - References to bullying, self-harm, violence, or abuse
   - Sustained sadness, anxiety, or fear
   - Concerning statements about family, school, or relationships
   - Any content that a parent should be aware of
   If nothing concerning, return an empty list.

IMPORTANT: Return ONLY the JSON object, no markdown, no explanation.

TRANSCRIPT:
{transcript}

EMOTION TIMELINE:
{emotions}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def summarize_session(
    profile_id: str,
    room_name: str,
    started_at: datetime,
    ended_at: datetime,
    transcript: list[dict],
    turn_count: int,
    mode: str = "default",
) -> dict:
    """Summarize a completed session and persist results to the database.

    Args:
        profile_id: Child's profile UUID.
        room_name: LiveKit room name for linking emotion snapshots.
        started_at: Session start timestamp.
        ended_at: Session end timestamp.
        transcript: List of {role, text} dicts from ChatContext.
        turn_count: Number of conversation turns.
        mode: Session mode (e.g. "default", "curious_open", "curious_topic").

    Returns:
        The session_summary dict that was written to the database.
    """
    duration_secs = int((ended_at - started_at).total_seconds())

    # Fetch emotion snapshots for this room from the DB
    emotions_text = await _fetch_emotion_timeline(room_name)

    # Format transcript for the LLM
    transcript_text = _format_transcript(transcript)

    # Single LLM call
    summary = await _call_llm(transcript_text, emotions_text)

    # Write session summary to DB
    pool = await _get_pool()
    if not pool:
        logger.warning("No DB pool — skipping session persistence")
        return {
            "session_id": None,
            "duration_secs": duration_secs,
            "mood_summary": summary.get("mood_summary", ""),
            "topics": summary.get("topics", []),
            "alerts": summary.get("alerts", []),
        }

    async with pool.acquire() as conn:
        session_row = await conn.fetchrow(
            """
            INSERT INTO session_summaries
                (profile_id, room_name, started_at, ended_at, duration_secs,
                 mood_summary, topics, turn_count, transcript, mode)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (profile_id, room_name) DO UPDATE SET
                ended_at      = EXCLUDED.ended_at,
                duration_secs = EXCLUDED.duration_secs,
                mood_summary  = EXCLUDED.mood_summary,
                topics        = EXCLUDED.topics,
                turn_count    = EXCLUDED.turn_count,
                transcript    = EXCLUDED.transcript,
                mode          = EXCLUDED.mode
            RETURNING id
            """,
            uuid.UUID(profile_id),
            room_name,
            started_at,
            ended_at,
            duration_secs,
            summary.get("mood_summary", ""),
            json.dumps(summary.get("topics", [])),
            turn_count,
            json.dumps(transcript),
            mode,
        )
        session_id = session_row["id"]

        # Link emotion snapshots to this session
        await conn.execute(
            """
            UPDATE emotion_snapshots SET session_id = $1
            WHERE room_name = $2 AND session_id IS NULL
            """,
            session_id,
            room_name,
        )

        # Write any alerts
        for alert in summary.get("alerts", []):
            await conn.execute(
                """
                INSERT INTO alerts
                    (profile_id, session_id, alert_type, severity, title, description)
                VALUES ($1, $2, 'content', $3, $4, $5)
                """,
                uuid.UUID(profile_id),
                session_id,
                alert.get("severity", "info"),
                alert.get("title", "Sakhi noticed something"),
                alert.get("description", ""),
            )

    logger.info(
        f"Session summarized: {room_name} | "
        f"duration={duration_secs}s, topics={summary.get('topics')}, "
        f"alerts={len(summary.get('alerts', []))}"
    )

    # Extract and store long-term memories in a background task
    # so the HTTP response returns immediately
    asyncio.create_task(
        _extract_memories_background(profile_id, transcript),
        name=f"memory-extraction-{room_name}",
    )

    return {
        "session_id": str(session_id),
        "duration_secs": duration_secs,
        "mood_summary": summary.get("mood_summary", ""),
        "topics": summary.get("topics", []),
        "alerts": summary.get("alerts", []),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


# Extract and store long-term memories in a background task
    # so the HTTP response returns immediately
    asyncio.create_task(
        _extract_memories_background(profile_id, transcript),
        name=f"memory-extraction-{room_name}",
    )

    return {
        "session_id": str(session_id),
        "duration_secs": duration_secs,
        "mood_summary": summary.get("mood_summary", ""),
        "topics": summary.get("topics", []),
        "alerts": summary.get("alerts", []),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _extract_memories_background(profile_id: str, transcript: list[dict]):
    """Fire-and-forget memory extraction so it never blocks the response."""
    try:
        from services.memory_manager import MemoryManager

        memory_mgr = MemoryManager()
        memories_stored = await memory_mgr.extract_and_store(
            profile_id=profile_id,
            service="sakhi",
            transcript=transcript,
        )
        logger.info(f"Long-term memories stored: {len(memories_stored)} items")
    except Exception:
        logger.exception("Background memory extraction failed")



async def _fetch_emotion_timeline(room_name: str) -> str:
    """Fetch emotion snapshots from DB and format as a readable timeline."""
    try:
        pool = await _get_pool()
        if not pool:
            return "Emotion data unavailable (no DB)."
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT emotion, score, recorded_at
                FROM emotion_snapshots
                WHERE room_name = $1
                ORDER BY recorded_at
                """,
                room_name,
            )
        if not rows:
            return "No emotion data recorded for this session."

        lines = []
        for r in rows:
            ts = r["recorded_at"].strftime("%H:%M:%S")
            lines.append(f"[{ts}] {r['emotion']} (score={r['score']:.2f})")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Failed to fetch emotion timeline: {e}")
        return "Emotion data unavailable."


def _format_transcript(transcript: list[dict]) -> str:
    """Format chat transcript for the LLM prompt."""
    if not transcript:
        return "No transcript available."

    lines = []
    for msg in transcript:
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        if role == "system":
            continue  # Skip system/emotion injection messages
        speaker = "CHILD" if role == "user" else "SAKHI"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines) if lines else "No conversation content."


async def _call_llm(transcript_text: str, emotions_text: str) -> dict:
    """Make a single Groq LLM call to extract topics, mood, and alerts."""
    fallback = {
        "topics": [],
        "mood_summary": "Summarization unavailable",
        "alerts": [],
    }

    # ── LLM call
    try:
        from services.llm import get_llm_client

        llm = get_llm_client()
        prompt = SUMMARIZE_PROMPT.format(
            transcript=transcript_text,
            emotions=emotions_text,
        )
        result = await llm.generate_json(
            prompt=prompt,
            temperature=0.3,
            max_tokens=500,
        )

        if not isinstance(result, dict):
            logger.warning(f"LLM returned non-dict JSON: {type(result)}")
            return fallback

    except Exception as e:
        logger.error(f"LLM call / JSON parse failed: {e}", exc_info=True)
        return fallback

    # ── Validate structure (separate try so LLM errors don't mask parse errors)
    try:
        topics = result.get("topics", [])
        if not isinstance(topics, list):
            topics = []

        mood = result.get("mood_summary", "No mood data available")
        if not isinstance(mood, str):
            mood = str(mood)

        raw_alerts = result.get("alerts", [])
        validated_alerts = []
        if isinstance(raw_alerts, list):
            for alert in raw_alerts:
                if not isinstance(alert, dict):
                    continue
                validated_alerts.append({
                    "title": str(alert.get("title", alert.get("type", "Sakhi noticed something"))),
                    "description": str(alert.get("description", alert.get("message", ""))),
                    "severity": str(alert.get("severity", "info")),
                })

        validated = {
            "topics": topics,
            "mood_summary": mood,
            "alerts": validated_alerts,
        }
        logger.info(
            f"LLM summarization complete: {len(topics)} topics, "
            f"{len(validated_alerts)} alerts"
        )
        return validated

    except Exception as e:
        logger.error(f"LLM response validation failed: {e}", exc_info=True)
        logger.error(f"Raw result was: {result}")
        return fallback