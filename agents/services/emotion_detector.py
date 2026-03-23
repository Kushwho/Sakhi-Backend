"""
Sakhi Emotion Detector — Background Task
=========================================
Detects child emotions via Hume's prosody API. Runs as a background
asyncio task inside the sakhi-agent session (not a separate LiveKit agent),
subscribing to child audio and writing results to a shared EmotionState
object that SakhiAgent reads directly — no participant attribute roundtrip.

Design rationale
----------------
The AgentServer enforces a single rtc_session per worker. Previously the
emotion detector was registered as a second rtc_session ("emotion-detector"),
which caused a RuntimeError on startup. It is now launched via
``asyncio.create_task(run_emotion_detector(...))`` inside sakhi_entrypoint
after the room is connected and the child participant has joined.

What changed vs the old entrypoint
-----------------------------------
* ``async def emotion_detector_entrypoint(ctx: agents.JobContext)``
  → ``async def run_emotion_detector(room: rtc.Room, profile_id, state)``
* ``ctx.connect()`` / ``ctx.wait_for_participant()`` removed — the room is
  already live when this task starts.
* All ``ctx.room`` references replaced with ``room``.
* Detected emotion is written to ``state.emotion`` (an in-process
  ``EmotionState`` dataclass) instead of participant attributes, eliminating
  the RTC roundtrip. Participant attributes are still set for the frontend
  RPC path.
* DB pool teardown moved to a finally block inside this coroutine so it
  cleans up when the task is cancelled on session end.
"""

import json
import logging
import os
from dataclasses import dataclass, field

from livekit import rtc

from .logging_config import setup_logging

setup_logging()

logger = logging.getLogger("sakhi.emotion")


# ---------------------------------------------------------------------------
# Shared in-process emotion state (written here, read by SakhiAgent)
# ---------------------------------------------------------------------------


@dataclass
class EmotionState:
    """Mutable container shared between the emotion detector task and SakhiAgent."""

    emotion: str | None = None
    avatar_expression: str | None = None
    score: float = 0.0


# ---------------------------------------------------------------------------
# Database helpers (lazy-init pool shared within the worker process)
# ---------------------------------------------------------------------------

_db_pool = None


async def _get_db_pool():
    """Lazy-init a small asyncpg pool for persisting emotion snapshots."""
    global _db_pool
    if _db_pool is None:
        import asyncpg

        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.warning("DATABASE_URL not set, emotion persistence disabled")
            return None
        _db_pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=3)
        logger.info("Emotion detector DB pool created")
    return _db_pool


async def _persist_emotion(
    profile_id: str,
    room_name: str,
    emotion: str,
    score: float,
    top_3: list,
) -> None:
    """Write one emotion snapshot row to the database."""
    pool = await _get_db_pool()
    if not pool:
        return
    try:
        import uuid as _uuid

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO emotion_snapshots (profile_id, room_name, emotion, score, top_3)
                VALUES ($1, $2, $3, $4, $5)
                """,
                _uuid.UUID(profile_id),
                room_name,
                emotion,
                score,
                json.dumps(top_3),
            )
        logger.debug(f"Persisted emotion snapshot: {emotion} ({score:.3f})")
    except Exception as e:
        logger.warning(f"Failed to persist emotion snapshot: {e}")


# ---------------------------------------------------------------------------
# Main background task — called from sakhi_entrypoint
# ---------------------------------------------------------------------------


async def run_emotion_detector(
    room: rtc.Room,
    profile_id: str | None,
    state: EmotionState,
) -> None:
    """Background task: subscribes to child audio and writes emotion to state.

    Args:
        room:       The already-connected LiveKit room (shared with SakhiAgent).
        profile_id: Child profile UUID for DB persistence; None disables persistence.
        state:      Shared EmotionState instance — SakhiAgent reads state.emotion
                    in on_user_turn_completed instead of scanning participant attrs.
    """
    hume_key = os.getenv("HUME_API_KEY")
    if not hume_key:
        logger.warning("HUME_API_KEY not set, emotion detection disabled")
        return

    from .hume import HumeEmotionClient, map_emotion_to_avatar

    client = HumeEmotionClient(hume_key)
    await client.connect()
    logger.info("Hume client connected — ready to analyze audio")

    try:
        # Find the child's audio track among already-joined remote participants.
        # The task is spawned after ctx.wait_for_participant(), so the child
        # is guaranteed to be present, but their track may not be published yet.
        # We iterate current participants; a production improvement would also
        # hook room.on("track_subscribed") for participants who join later.
        for participant in room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    audio_stream = rtc.AudioStream(pub.track)
                    buffer = bytearray()

                    async for frame_event in audio_stream:
                        buffer.extend(frame_event.frame.data.tobytes())

                        # Buffer ~3 seconds of audio (48 kHz, 16-bit mono ≈ 288 KB)
                        if len(buffer) >= 288_000:
                            logger.debug(
                                f"Audio buffer full ({len(buffer)} bytes), sending to Hume..."
                            )
                            result = await client.analyze_audio(bytes(buffer))
                            buffer.clear()

                            if result and result["top_emotions"]:
                                top_emotion_name = result["top_emotions"][0][0]
                                top_emotion_score = result["top_emotions"][0][1]
                                avatar_expr = map_emotion_to_avatar(top_emotion_name)

                                logger.info(
                                    f"🎭 EMOTION DETECTED: {top_emotion_name} "
                                    f"(score={top_emotion_score:.3f}) → avatar:{avatar_expr}  "
                                    f"| top3={result['top_emotions']}"
                                )

                                # 1. Write to shared in-process state (SakhiAgent reads this)
                                state.emotion = top_emotion_name
                                state.avatar_expression = avatar_expr
                                state.score = top_emotion_score
                                logger.debug(
                                    f"EmotionState updated: emotion={top_emotion_name}"
                                )

                                # 2. Still set participant attributes for frontend RPC
                                await room.local_participant.set_attributes(
                                    {
                                        "emotion": top_emotion_name,
                                        "avatar_expression": avatar_expr,
                                    }
                                )

                                # 3. Send to frontend via RPC (child participant only)
                                for pid, _participant in room.remote_participants.items():
                                    if not pid.startswith("child-"):
                                        logger.debug(
                                            f"Skipping RPC to non-child participant: {pid}"
                                        )
                                        continue

                                    rpc_payload = json.dumps(
                                        {
                                            "expression": avatar_expr,
                                            "raw_emotion": top_emotion_name,
                                            "score": top_emotion_score,
                                        }
                                    )
                                    try:
                                        await room.local_participant.perform_rpc(
                                            destination_identity=pid,
                                            method="setEmotionState",
                                            payload=rpc_payload,
                                            response_timeout=3.0,
                                        )
                                        logger.info(
                                            f"✅ RPC setEmotionState → {pid}: {rpc_payload}"
                                        )
                                    except Exception as rpc_err:
                                        logger.warning(
                                            f"❌ RPC setEmotionState FAILED for {pid}: {rpc_err}"
                                        )

                                # 4. Persist to DB for the parent dashboard
                                if profile_id:
                                    await _persist_emotion(
                                        profile_id=profile_id,
                                        room_name=room.name,
                                        emotion=top_emotion_name,
                                        score=top_emotion_score,
                                        top_3=result["top_emotions"],
                                    )
                            else:
                                logger.debug(
                                    "Hume returned no emotions for this audio chunk"
                                )
    finally:
        await client.close()
        if _db_pool:
            await _db_pool.close()
            logger.info("Emotion detector DB pool closed")