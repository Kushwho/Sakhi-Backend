"""
Sakhi Voice Agent — Agent Logic
================================
SakhiAgent class with short-term memory and emotion-aware responses.

Entrypoint: ``python sakhi.py start`` or ``python sakhi.py dev``
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    RunContext,
    function_tool,
    get_job_context,
    inference
)
from livekit.agents.voice.events import (
    UserInputTranscribedEvent,
)
from livekit.plugins import deepgram, groq, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from services.emotion_detector import EmotionState, run_emotion_detector
from services.prompts import build_system_prompt
from services.logging_config import setup_logging

load_dotenv(".env")

# Configure logging at module level — the LiveKit dev mode spawns a child
# worker process that imports this module but does NOT run __main__, so
# setup_logging() in agent.py never executes in the worker.
setup_logging()

logger = logging.getLogger("sakhi")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_EXPRESSIONS = {"happy", "thinking", "excited", "concerned", "sad", "celebrating"}


# ---------------------------------------------------------------------------
# SakhiAgent — voice agent with short-term memory & emotion awareness
# ---------------------------------------------------------------------------


class SakhiAgent(Agent):
    """OOP voice agent with child personalization, short-term memory, and emotion context."""

    def __init__(
        self,
        child_name: str = "a child",
        child_age: int = 8,
        child_language: str = "English",
        profile_id: str | None = None,
        chat_ctx: ChatContext | None = None,
        mode: str = "default",
        topic_context: dict | None = None,
        surprise_fact: str | None = None,
        emotion_state: EmotionState | None = None,
        room: rtc.Room | None = None,
    ) -> None:
        instructions = build_system_prompt(
            child_name=child_name,
            child_age=child_age,
            child_language=child_language,
            mode=mode,
            topic=topic_context,
            surprise_fact=surprise_fact,
        )
        super().__init__(instructions=instructions, chat_ctx=chat_ctx)
        self.child_name = child_name
        self.child_age = child_age
        self.child_language = child_language
        self._profile_id = profile_id
        self._room = room
        # Shared state written by the emotion detector background task.
        # Replaces the old participant-attribute roundtrip.
        self._emotion_state: EmotionState = emotion_state or EmotionState()

        if profile_id:
            from services.memory_manager import MemoryManager

            self._memory_mgr: MemoryManager | None = MemoryManager()
        else:
            self._memory_mgr = None

    # -- Short-term memory: inject emotion context before LLM responds -------

    async def on_user_turn_completed(
        self,
        turn_ctx: ChatContext,
        new_message: ChatMessage,
    ) -> None:
        """Inject the child's detected emotion into the LLM context.

        Reads emotion directly from the shared EmotionState written by the
        run_emotion_detector background task. No participant attribute scan
        needed — the detector runs in-process.
        """
        logger.debug(
            f"on_user_turn_completed: child said: {new_message.text_content[:100] if new_message.text_content else '(empty)'}"
        )

        current_emotion = self._emotion_state.emotion
        if current_emotion:
            logger.info(f"Injecting emotion context into LLM: {current_emotion}")
            turn_ctx.add_message(
                role="system",
                content=(
                    f"[Emotion context — DO NOT read this aloud or mention it to the child] "
                    f"The child's voice tone suggests they are feeling: {current_emotion}. "
                    f"Adapt your response accordingly — be extra supportive "
                    f"if they sound sad or anxious, and match their energy "
                    f"if they sound excited or happy. "
                    f"Never reveal that you are detecting their emotions."
                ),
            )
        else:
            logger.debug("No emotion detected yet — skipping emotion injection")

        # Recall relevant long-term memories based on what the child said
        if self._memory_mgr and self._profile_id and new_message.text_content:
            try:
                relevant = await self._memory_mgr.recall(
                    profile_id=self._profile_id,
                    service="sakhi",
                    query=new_message.text_content,
                    limit=3,
                )
                if relevant:
                    turn_ctx.add_message(
                        role="system",
                        content=(
                            f"[Long-term memory — DO NOT read aloud] "
                            f"You remember these things about {self.child_name}: "
                            + "; ".join(relevant)
                            + ". Use this to personalize your response naturally."
                        ),
                    )
                    logger.info(f"Injected {len(relevant)} memories into turn context")
            except Exception as e:
                logger.warning(f"Memory recall during turn failed: {e}")

    # -- Tool 1: Explain a concept (RAG stub) --------------------------------

    @function_tool()
    async def explain_concept(
        self,
        context: RunContext,
        concept: str,
        subject: str,
    ) -> str:
        """Explain a school concept to help the child learn.

        Use this tool when the child asks about a topic from their school
        curriculum (e.g. math, science, history, language).

        Args:
            concept: The concept or topic to explain (e.g. "photosynthesis").
            subject: The school subject it belongs to (e.g. "Science").
        """
        # TODO: Connect to CBSE/ICSE curriculum RAG system
        logger.info(f"explain_concept called: concept={concept}, subject={subject}")
        return (
            f"Let me help you understand {concept} in {subject}! "
            f"Think about it this way — can you tell me what you already know about {concept}? "
            f"That way we can build on what you know!"
        )

    # -- Tool 2: Generate an image -------------------------------------------

    @function_tool()
    async def generate_image(
        self,
        context: RunContext,
        description: str,
    ) -> str:
        """Draw a picture for the child and show it on their screen.

        Use this when the child asks you to draw, create, paint, or show
        a picture of something (e.g. "draw me a rocket", "show me a dinosaur",
        "can you make a picture of the sun?").

        Args:
            description: What to draw, in the child's own words.
        """
        logger.info(f"generate_image called: description='{description[:60]}'")

        if not self._profile_id:
            return "Sorry, I can't draw pictures right now — I don't know who I'm talking to!"

        from services.chat_image_service import (
            QuotaExceededError,
            generate_chat_image,
        )

        try:
            result = await generate_chat_image(
                profile_id=self._profile_id,
                prompt=description,
                aspect_ratio="1:1",
            )
            image_url = result["image_url"]
            remaining = result["remaining_today"]

            # Push the image URL to the child's frontend over LiveKit RPC
            # so it appears on-screen while Sakhi speaks about it.
            try:
                if self._room:
                    participants = list(self._room.remote_participants.values())
                    if participants:
                        child_identity = participants[0].identity
                        await self._room.local_participant.perform_rpc(
                            destination_identity=child_identity,
                            method="showImage",
                            payload=json.dumps(
                                {"image_url": image_url, "caption": description}
                            ),
                        )
                        logger.info(f"showImage RPC sent to {child_identity}")
                else:
                    logger.warning("No room attached to agent, cannot send showImage RPC")
            except Exception as rpc_err:
                # RPC failure is non-fatal — Sakhi still describes the image verbally
                logger.warning(f"showImage RPC failed (non-fatal): {rpc_err}")

            remaining_msg = (
                f" We have {remaining} drawing{'s' if remaining != 1 else ''} left today."
                if remaining <= 1
                else ""
            )
            return (
                f"I drew {description} for you! Take a look at the picture on your screen. 🎨"
                + remaining_msg
            )

        except QuotaExceededError:
            logger.info(f"Voice image quota exhausted for profile {self._profile_id}")
            return (
                "We've used all our drawing requests for today — "
                "let's try again tomorrow! In the meantime, I can describe what "
                f"{description} looks like in words — want me to do that?"
            )

        except Exception as e:
            logger.error(f"generate_image tool error: {e}", exc_info=True)
            return (
                "Hmm, I couldn't draw that right now — something went wrong on my end. "
                "Want to try describing something else?"
            )


# ---------------------------------------------------------------------------
# LiveKit Agent Server — single rtc_session registration
# ---------------------------------------------------------------------------

server = AgentServer()

# NOTE: The "emotion-detector" rtc_session registration has been removed.
# AgentServer enforces a single rtc_session per worker. The emotion detector
# now runs as an asyncio background task inside sakhi_entrypoint, sharing
# the already-connected rtc.Room object. This uses one LiveKit Cloud agent
# slot instead of two.


@server.rtc_session(agent_name="sakhi-agent")
async def sakhi_entrypoint(ctx: agents.JobContext):
    """Entrypoint for each child voice session."""
    logger.info("━━━ New Sakhi session starting ━━━")

    # Default fallback values (overwritten by room metadata below)
    child_name = "buddy"
    child_age = 8
    child_language = "English"
    profile_id = None
    mode = "default"
    topic_context = None
    surprise_fact = None

    # Connect the agent to the LiveKit room
    await ctx.connect()
    logger.info(f"Connected to room: {ctx.room.name}")

    # Extract profile and mode info from room metadata
    try:
        room_meta = json.loads(ctx.room.metadata or "{}")
        profile_id = room_meta.get("profile_id")
        mode = room_meta.get("mode", "default")
        topic_context = room_meta.get("topic_context")
        surprise_fact = room_meta.get("surprise_fact")
        if profile_id:
            logger.info(f"Profile ID for session tracking: {profile_id}")
        logger.info(f"Session mode: {mode}")
    except json.JSONDecodeError:
        logger.warning("Could not parse room metadata")

    # Wait for the child participant to connect before spawning the detector
    await ctx.wait_for_participant()
    logger.info(f"Participant joined. Remote participants: {list(ctx.room.remote_participants.keys())}")

    # Read metadata from the first remote participant (the child)
    for participant in ctx.room.remote_participants.values():
        if participant.metadata:
            try:
                meta = json.loads(participant.metadata)
                child_name = meta.get("child_name", child_name)
                child_age = meta.get("child_age", child_age)
                child_language = meta.get("child_language", child_language)
                logger.info(f"Child profile: name={child_name}, age={child_age}, lang={child_language}")
            except json.JSONDecodeError:
                logger.warning("Could not parse participant metadata as JSON, using defaults")
            break

    # Shared emotion state — written by detector task, read by SakhiAgent
    emotion_state = EmotionState()

    # Spawn emotion detector as a background task now that the room is live
    # and the child participant is present. We hold a reference to cancel it
    # cleanly on session end via the shutdown callback below.
    emotion_task = asyncio.create_task(
        run_emotion_detector(ctx.room, profile_id, emotion_state),
        name="emotion-detector",
    )
    logger.info("Emotion detector background task started")

    # Preload initial context with child profile (short-term memory)
    initial_ctx = ChatContext()
    initial_ctx.add_message(
        role="assistant",
        content=(
            f"You are talking to {child_name}, age {child_age}, "
            f"who prefers {child_language}. Remember their name and "
            f"refer to them personally throughout the conversation."
        ),
    )

    # Recall long-term memories for this child at session start
    if profile_id:
        try:
            from services.memory_manager import MemoryManager

            memory_mgr = MemoryManager()
            past_memories = await memory_mgr.recall(
                profile_id=profile_id,
                service="sakhi",
                query=child_name,
                limit=5,
            )
            if past_memories:
                initial_ctx.add_message(
                    role="system",
                    content=(
                        f"[Long-term memory — DO NOT read aloud] "
                        f"From previous conversations with {child_name}, you remember: "
                        + "; ".join(past_memories)
                        + ". Use these naturally — don't list them, just let them "
                        f"inform how you talk to {child_name}."
                    ),
                )
                logger.info(f"Injected {len(past_memories)} memories into session start context")
        except Exception as e:
            logger.warning(f"Memory recall at session start failed: {e}")

    # Build the voice pipeline
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=inference.TTS(
        model="inworld/inworld-tts-1", 
        voice="Anjali", 
        language="en"
        ),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    # ── Session timing for dashboard ──────────────────────────────────
    session_started_at = datetime.now(UTC)
    turn_count = 0

    # ── Event listeners for detailed logging ──────────────────────────

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev: UserInputTranscribedEvent) -> None:
        nonlocal turn_count
        if ev.is_final:
            logger.info(f'🎤 CHILD SAID: "{ev.transcript}"')
            turn_count += 1
        else:
            logger.debug(f'🎤 child (partial): "{ev.transcript}"')

    # ─────────────────────────────────────────────────────────────────

    # Create personalized agent with shared emotion state
    agent = SakhiAgent(
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
        profile_id=profile_id,
        chat_ctx=initial_ctx,
        mode=mode,
        topic_context=topic_context,
        surprise_fact=surprise_fact,
        emotion_state=emotion_state,
        room=ctx.room,
    )

    # Start the session
    await session.start(
        room=ctx.room,
        agent=agent,
    )
    logger.info("Session started — voice pipeline active")

    # Greet the child (mode-aware)
    if mode == "curious_topic" and topic_context:
        greeting_instructions = (
            f"Greet {child_name} warmly by name and tell them you're excited to explore "
            f"{topic_context['title']} together! Ask what they already know about it. "
            f"Keep it to 2-3 short sentences."
        )
    elif mode == "curious_surprise" and surprise_fact:
        greeting_instructions = (
            f"Greet {child_name} warmly by name and share this amazing fact: "
            f'"{surprise_fact}" — then ask what they think about it! '
            f"Keep it to 2-3 short sentences."
        )
    elif mode == "curious_open":
        greeting_instructions = (
            f"Greet {child_name} warmly by name. Tell them you're ready to explore "
            f"anything they're curious about today! Ask what they'd like to discover. "
            f"Keep it to 1-2 short sentences."
        )
    else:
        greeting_instructions = (
            f"Greet {child_name} warmly by name. You are excited to talk to them today! Keep it to 1-2 short sentences."
        )
    await session.generate_reply(instructions=greeting_instructions)
    logger.info(f"Initial greeting sent (mode={mode})")

    # ── Wait for session end and clean up ─────────────────────────────

    async def _on_session_end():
        """Cancel the emotion detector task, then summarize the session."""
        session_ended_at = datetime.now(UTC)
        logger.info("━━━ Session ending — cleaning up ━━━")

        # Cancel the emotion detector background task gracefully
        if not emotion_task.done():
            emotion_task.cancel()
            try:
                await emotion_task
            except asyncio.CancelledError:
                logger.info("Emotion detector task cancelled cleanly")
            except Exception as e:
                logger.warning(f"Emotion detector task ended with error: {e}")

        if not profile_id:
            logger.warning("No profile_id — skipping session summarization")
            return

        # Extract transcript from the agent's chat context
        try:
            transcript = []
            if agent.chat_ctx and agent.chat_ctx.items:
                for item in agent.chat_ctx.items:
                    if hasattr(item, "role") and hasattr(item, "text_content"):
                        role = item.role
                        text = item.text_content or ""
                        if role in ("user", "assistant") and text.strip():
                            transcript.append({"role": role, "text": text})

            if not transcript:
                logger.info("No transcript content — skipping summarization")
                return

            logger.info(f"Extracted transcript: {len(transcript)} messages, {turn_count} turns")

            from services.session_summarizer import summarize_session

            result = await summarize_session(
                profile_id=profile_id,
                room_name=ctx.room.name,
                started_at=session_started_at,
                ended_at=session_ended_at,
                transcript=transcript,
                turn_count=turn_count,
                mode=mode,
            )
            logger.info(f"Session summary saved: {result}")

        except Exception as e:
            logger.error(f"Session summarization failed: {e}", exc_info=True)

    ctx.add_shutdown_callback(_on_session_end)


if __name__ == "__main__":
    agents.cli.run_app(server)