"""
Sakhi Emotion Detector — Programmatic Participant
===================================================
Separate AgentServer that detects child emotions via Hume's prosody API.
Joins the LiveKit room alongside the voice agent, subscribes to child audio,
and sends detected emotions to the frontend, the voice agent, AND persists
them to the database for the parent dashboard.

Run via root entrypoint: ``python emotion_detector.py start``
"""

import json
import logging
import os

from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import AgentServer

from utils.logging_config import setup_logging

load_dotenv(".env.local")

# Configure logging at module level — LiveKit dev mode spawns a child
# worker that imports this module but doesn't run __main__.
setup_logging()

logger = logging.getLogger("sakhi.emotion")

# ---------------------------------------------------------------------------
# Database helpers (separate pool — this is its own OS process)
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
# Emotion Detector — Programmatic Participant
# ---------------------------------------------------------------------------

emotion_server = AgentServer()


@emotion_server.rtc_session(agent_name="emotion-detector")
async def emotion_detector_entrypoint(ctx: agents.JobContext):
    """Programmatic participant: detects child emotions via Hume prosody API.

    Joins the same LiveKit room, subscribes to the child's audio track,
    buffers ~3 seconds, sends to Hume for prosody analysis, then:
      1. Sets participant attributes (voice agent reads in on_user_turn_completed)
      2. Sends mapped avatar expression to frontend via RPC
      3. Persists emotion snapshot to DB for the parent dashboard
    """
    hume_key = os.getenv("HUME_API_KEY")
    if not hume_key:
        logger.warning("HUME_API_KEY not set, emotion detection disabled")
        return

    from services.hume import HumeEmotionClient, map_emotion_to_avatar

    await ctx.connect()
    logger.info(f"Emotion detector connected to room: {ctx.room.name}")
    await ctx.wait_for_participant()
    logger.info(f"Participant joined. Remote participants: {list(ctx.room.remote_participants.keys())}")

    # Extract profile_id from room metadata (set by token route)
    profile_id = None
    try:
        room_meta = json.loads(ctx.room.metadata or "{}")
        profile_id = room_meta.get("profile_id")
        if profile_id:
            logger.info(f"Profile ID for dashboard: {profile_id}")
        else:
            logger.warning("No profile_id in room metadata — emotion persistence disabled")
    except json.JSONDecodeError:
        logger.warning("Could not parse room metadata")

    client = HumeEmotionClient(hume_key)
    await client.connect()
    logger.info("Hume client connected — ready to analyze audio")

    try:
        # Find the child's audio track and subscribe
        for participant in ctx.room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    audio_stream = rtc.AudioStream(pub.track)
                    buffer = bytearray()

                    async for frame_event in audio_stream:
                        buffer.extend(frame_event.frame.data.tobytes())

                        # Buffer ~3 seconds of audio (48kHz, 16-bit mono ≈ 288KB)
                        if len(buffer) >= 288_000:
                            logger.debug(f"Audio buffer full ({len(buffer)} bytes), sending to Hume...")
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

                                # 1. Set participant attributes (voice agent reads these)
                                await ctx.room.local_participant.set_attributes(
                                    {
                                        "emotion": top_emotion_name,
                                        "avatar_expression": avatar_expr,
                                    }
                                )
                                logger.info(f"📤 Attributes set: emotion={top_emotion_name}, avatar={avatar_expr}")

                                # 2. Send to frontend via RPC (only to child, NOT the voice agent)
                                for pid, participant in ctx.room.remote_participants.items():
                                    # Only send to the child frontend — skip other agents
                                    if not pid.startswith("child-"):
                                        logger.debug(f"Skipping RPC to non-child participant: {pid}")
                                        continue

                                    rpc_payload = json.dumps(
                                        {
                                            "expression": avatar_expr,
                                            "raw_emotion": top_emotion_name,
                                            "score": top_emotion_score,
                                        }
                                    )
                                    try:
                                        await ctx.room.local_participant.perform_rpc(
                                            destination_identity=pid,
                                            method="setEmotionState",
                                            payload=rpc_payload,
                                            response_timeout=3.0,
                                        )
                                        logger.info(f"✅ RPC setEmotionState → {pid}: {rpc_payload}")
                                    except Exception as rpc_err:
                                        logger.warning(
                                            f"❌ RPC setEmotionState FAILED for {pid}: {rpc_err}"
                                        )

                                # 3. Persist to DB for the parent dashboard
                                if profile_id:
                                    await _persist_emotion(
                                        profile_id=profile_id,
                                        room_name=ctx.room.name,
                                        emotion=top_emotion_name,
                                        score=top_emotion_score,
                                        top_3=result["top_emotions"],
                                    )
                            else:
                                logger.debug("Hume returned no emotions for this audio chunk")
    finally:
        await client.close()
        # Clean up DB pool
        if _db_pool:
            await _db_pool.close()
            logger.info("Emotion detector DB pool closed")
