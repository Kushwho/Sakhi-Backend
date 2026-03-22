"""
Sakhi Voice Agent — Agent Logic
================================
SakhiAgent class with short-term memory and emotion-aware responses.

Entrypoint: ``python agent.py dev`` (via root agent.py thin wrapper)
"""

import json
import logging
from datetime import UTC, datetime

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    RunContext,
    function_tool,
    get_job_context,
)
from livekit.agents.voice.events import (
    UserInputTranscribedEvent,
)
from livekit.plugins import deepgram, groq, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from services.prompts import build_system_prompt
from utils.logging_config import setup_logging

load_dotenv(".env.local")

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
        self._current_emotion: str | None = None
        # One shared instance per session — DB pool and embedding model
        # are created lazily on first use and reused across all turns.
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

        The emotion detector programmatic participant sets its participant
        attributes with the latest Hume prosody result. We read those
        attributes here and add an ephemeral context message so the LLM
        can respond empathetically.
        """
        # Try to read emotion from the emotion detector's participant attributes
        logger.debug(
            f"on_user_turn_completed: child said: {new_message.text_content[:100] if new_message.text_content else '(empty)'}"
        )
        try:
            room = get_job_context().room
            for participant in room.remote_participants.values():
                attrs = participant.attributes
                if attrs and "emotion" in attrs:
                    self._current_emotion = attrs["emotion"]
                    logger.info(f"Read emotion from detector: {self._current_emotion} (attrs={attrs})")
                    break
            else:
                logger.debug("No emotion attribute found on any remote participant")
        except Exception as e:
            logger.warning(f"Failed to read emotion attributes: {e}")

        if self._current_emotion:
            logger.info(f"Injecting emotion context into LLM: {self._current_emotion}")
            turn_ctx.add_message(
                role="system",
                content=(
                    f"[Emotion context — DO NOT read this aloud or mention it to the child] "
                    f"The child's voice tone suggests they are feeling: {self._current_emotion}. "
                    f"Adapt your response accordingly — be extra supportive "
                    f"if they sound sad or anxious, and match their energy "
                    f"if they sound excited or happy. "
                    f"Never reveal that you are detecting their emotions."
                ),
            )

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

    # NOTE: log_emotion tool was removed — it exposed emotion vocabulary to the
    # LLM, which caused it to speak emotion data aloud (e.g. "emotion: sad,
    # intensity: 0.9"). Emotion detection is handled entirely by the separate
    # emotion detector participant; the voice agent only receives the emotion
    # name via participant attributes in on_user_turn_completed.


# ---------------------------------------------------------------------------
# LiveKit Agent Server
# ---------------------------------------------------------------------------

server = AgentServer()

# Register the emotion detector as a second handler on the SAME server.
# This way both "sakhi-agent" and "emotion-detector" are served by one
# deployed agent, using a single LiveKit Cloud agent slot.
from agents.emotion_detector import emotion_detector_entrypoint

server.rtc_session(agent_name="emotion-detector")(emotion_detector_entrypoint)


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

    # Wait for the child participant to connect
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
        llm=groq.LLM(model="llama-3.1-8b-instant"),
        tts=deepgram.TTS(model="aura-2-asteria-en"),
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

    # @session.on("conversation_item_added")
    # def _on_conversation_item(ev: ConversationItemAddedEvent) -> None:
    #     if not isinstance(ev.item, llm.ChatMessage):
    #         return
    #     role = ev.item.role
    #     text = ev.item.text_content or "(no text)"
    #     if role == "assistant":
    #         logger.info(f'🤖 SAKHI SAID: "{text[:200]}"')
    #     elif role == "user":
    #         logger.info(f'💬 USER TURN COMMITTED: "{text[:200]}"')
    #     else:
    #         logger.debug(f'📝 {role}: "{text[:100]}"')

    # ─────────────────────────────────────────────────────────────────

    # Create personalized agent with initial context
    agent = SakhiAgent(
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
        profile_id=profile_id,
        chat_ctx=initial_ctx,
        mode=mode,
        topic_context=topic_context,
        surprise_fact=surprise_fact,
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

    # ── Wait for session end and summarize ────────────────────────────
    # The session runs until the room closes or the child disconnects.
    # We use ctx.shutdown_event to wait for that, then summarize.

    async def _on_session_end():
        """Extract transcript from ChatContext and trigger summarization."""
        session_ended_at = datetime.now(UTC)
        logger.info("━━━ Session ending — starting summarization ━━━")

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

            # Call the session summarizer
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

    # Register shutdown callback
    ctx.add_shutdown_callback(_on_session_end)
