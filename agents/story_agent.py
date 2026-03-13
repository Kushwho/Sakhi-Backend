"""
Sakhi Story Agent — Agent Logic & Server
=========================================
StoryAgent class + AgentServer + session entrypoint.

Entrypoint: ``python story_entrypoint.py dev`` (via root story_entrypoint.py thin wrapper)

Flow:
  1. Frontend browses: GET /api/stories/random?genre=adventure
  2. User confirms story title → POST /api/story-token {story_id}
  3. LiveKit room is created with story_id in metadata; story-agent is dispatched
  4. This session connects, pre-fetches ALL segments from DB into memory (one DB call)
  5. StoryAgent narrates purely from memory — zero DB calls during the voice session
"""

import json
import logging
from datetime import datetime, timezone

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
    llm,
)
from livekit.agents.voice.events import (
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
)
from livekit.plugins import deepgram, groq, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from utils.logging_config import setup_logging

load_dotenv(".env.local")

# Configure logging at module level — LiveKit dev mode spawns a child worker
# process that imports this module but does NOT run __main__, so
# setup_logging() in story_entrypoint.py never executes in the worker.
setup_logging()

logger = logging.getLogger("sakhi.story")

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

STORY_SYSTEM_PROMPT = """\
You are Sakhi the Storyteller — a warm, expressive, and magical narrator for Indian children aged 4–12.

Your job today is to narrate the story "{story_title}" to {child_name} (age {child_age}).

How to narrate:
- Bring each segment alive with dramatic pauses, varied pace, and expressive tone.
- After narrating a segment, briefly react and ask the child ONE simple, fun question \
  (e.g. "What do you think happens next?" or "Wasn't that exciting?").
- If the child comments or asks a question, respond warmly in 1-2 sentences, then continue.
- Match your language complexity and vocabulary to the child's age ({child_age} years).

Rules:
- NEVER make up story content. ALWAYS use get_next_segment to fetch the real text.
- Go through segments ONE AT A TIME — never skip ahead.
- When the story is finished (you receive [THE END]), congratulate {child_name} warmly \
  and ask what their favourite part was.
- You are strictly child-safe — no violence, no inappropriate content, ever.

Session start: Greet {child_name} warmly, tell them you are about to read "{story_title}" \
together, then immediately call get_next_segment to get Part 1 and begin narrating.
"""


# ---------------------------------------------------------------------------
# StoryAgent — voice agent with in-memory story segments
# ---------------------------------------------------------------------------


class StoryAgent(Agent):
    """Voice agent that narrates a pre-loaded story from in-memory segments.

    All segments are supplied at construction time (pre-fetched from DB in the
    session entrypoint). No database calls happen during narration.
    """

    def __init__(
        self,
        story_title: str,
        segments: list[str],
        child_name: str = "buddy",
        child_age: int = 8,
        child_language: str = "English",
        chat_ctx: ChatContext | None = None,
    ) -> None:
        if not segments:
            raise ValueError("StoryAgent requires at least one story segment")

        instructions = STORY_SYSTEM_PROMPT.format(
            story_title=story_title,
            child_name=child_name,
            child_age=child_age,
            child_language=child_language,
        )
        super().__init__(instructions=instructions, chat_ctx=chat_ctx)

        self.story_title = story_title
        self.child_name = child_name
        self.child_age = child_age
        self.child_language = child_language

        # In-memory story cache
        self._segments: list[str] = segments
        self._total: int = len(segments)
        self._position: int = 0   # 0 = not started; 1..N = current segment

    # ── Tool 1: get the next segment ────────────────────────────────────────

    @function_tool()
    async def get_next_segment(self, context: RunContext, confirm: str = "yes") -> str:
        """Get the next part of the story from memory.

        Call this at the very start of the session (to get Part 1), and again
        after finishing each segment when the child is ready for more.
        Never call this twice in a row without narrating the returned text first.

        Args:
            confirm: Always pass "yes" to confirm fetching the next segment.
        """
        next_pos = self._position + 1

        if next_pos > self._total:
            logger.info(f"Story '{self.story_title}' complete. All {self._total} segments narrated.")
            return (
                f"[THE END — '{self.story_title}' is complete! "
                f"All {self._total} parts have been narrated.]"
            )

        self._position = next_pos
        segment_text = self._segments[next_pos - 1]  # list is 0-indexed
        logger.info(f"Serving segment {next_pos}/{self._total} of '{self.story_title}' from memory")
        return f"[Part {next_pos} of {self._total}]\n\n{segment_text}"

    # ── Tool 2: repeat the current segment ──────────────────────────────────

    @function_tool()
    async def repeat_current_segment(self, context: RunContext, confirm: str = "yes") -> str:
        """Repeat the current story segment without advancing.

        Call this if the child asks you to repeat something, or if the narration
        was interrupted. This does NOT move to the next part.

        Args:
            confirm: Always pass "yes" to confirm repeating.
        """
        if self._position == 0:
            return "We haven't started yet! Let me get the first part of the story for you."

        segment_text = self._segments[self._position - 1]
        logger.info(f"Repeating segment {self._position}/{self._total} of '{self.story_title}'")
        return f"[Repeating Part {self._position} of {self._total}]\n\n{segment_text}"


# ---------------------------------------------------------------------------
# LiveKit Agent Server
# ---------------------------------------------------------------------------

server = AgentServer()


@server.rtc_session(agent_name="story-agent")
async def story_session_entrypoint(ctx: agents.JobContext):
    """Entrypoint for each child story narration session."""
    logger.info("━━━ New Story session starting ━━━")

    # Defaults (overwritten by room metadata below)
    child_name = "buddy"
    child_age = 8
    child_language = "English"
    profile_id = None
    story_id = None
    story_title = "Unknown Story"

    await ctx.connect()
    logger.info(f"Story agent connected to room: {ctx.room.name}")

    # ── Read child profile + story from room metadata ──────────────────────
    try:
        room_meta = json.loads(ctx.room.metadata or "{}")
        profile_id = room_meta.get("profile_id")
        story_id = room_meta.get("story_id")
        story_title = room_meta.get("story_title", story_title)
        child_name = room_meta.get("child_name", child_name)
        child_age = room_meta.get("child_age", child_age)
        child_language = room_meta.get("child_language", child_language)
        logger.info(
            f"Room metadata: profile_id={profile_id}, story_id={story_id}, "
            f"child={child_name} (age {child_age}), story='{story_title}'"
        )
    except json.JSONDecodeError:
        logger.warning("Could not parse room metadata, using defaults")

    if not story_id:
        logger.error("No story_id in room metadata — cannot start story session")
        return

    # Wait for the child to join before doing DB work
    await ctx.wait_for_participant()
    logger.info(f"Child participant joined. Room: {list(ctx.room.remote_participants.keys())}")

    # ── Pre-fetch ALL story segments from DB into memory ───────────────────
    # One DB call here, before the voice pipeline starts.
    # The agent will use these from memory — zero DB calls during narration.
    logger.info(f"Pre-fetching segments for story '{story_title}' ({story_id})...")
    try:
        from services.stories import get_all_segments, get_story

        story_meta = await get_story(story_id)
        segments = await get_all_segments(story_id)

        if not story_meta or not segments:
            logger.error(f"Story {story_id} not found or has no segments — aborting")
            return

        # Use DB title as source of truth
        story_title = story_meta["title"]
        logger.info(f"Story loaded: '{story_title}' — {len(segments)} segments in memory ✓")
    except Exception as e:
        logger.error(f"Failed to load story from DB: {e}", exc_info=True)
        return
    # ──────────────────────────────────────────────────────────────────────

    # Preload context with child + story info (short-term memory)
    initial_ctx = ChatContext()
    initial_ctx.add_message(
        role="assistant",
        content=(
            f"You are narrating '{story_title}' to {child_name}, age {child_age}, "
            f"who prefers {child_language}. The story has {len(segments)} parts. "
            f"Always refer to them by name."
        ),
    )

    # ── Build the voice pipeline ───────────────────────────────────────────
    session = AgentSession(
        stt=deepgram.STT(
            model="nova-3",
            language="multi",
            smart_format=True,
            endpointing_ms=300,
            profanity_filter=True,
        ),
        llm=groq.LLM(model="llama-3.1-8b-instant"),
        tts=deepgram.TTS(model="aura-2-asteria-en"),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    # ── Session-level tracking ─────────────────────────────────────────────
    session_started_at = datetime.now(timezone.utc)
    turn_count = 0

    # ── Event listeners ────────────────────────────────────────────────────

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev: UserInputTranscribedEvent) -> None:
        nonlocal turn_count
        if ev.is_final:
            logger.info(f'🎤 CHILD SAID: "{ev.transcript}"')
            turn_count += 1
        else:
            logger.debug(f'🎤 child (partial): "{ev.transcript}"')

    @session.on("conversation_item_added")
    def _on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        if not isinstance(ev.item, llm.ChatMessage):
            return
        role = ev.item.role
        text = ev.item.text_content or "(no text)"
        if role == "assistant":
            logger.info(f'📖 SAKHI NARRATES: "{text[:200]}"')
        elif role == "user":
            logger.debug(f'💬 USER TURN COMMITTED: "{text[:200]}"')

    # ──────────────────────────────────────────────────────────────────────

    # Create the story agent with in-memory segments
    agent = StoryAgent(
        story_title=story_title,
        segments=segments,
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
        chat_ctx=initial_ctx,
    )

    # Start the voice session
    await session.start(room=ctx.room, agent=agent)
    logger.info("Story session started — voice pipeline active")

    # Opening: greet the child and immediately begin narrating Part 1
    await session.generate_reply(
        instructions=(
            f"Greet {child_name} warmly and tell them you're going to read "
            f"'{story_title}' together. Build a little excitement, then immediately "
            f"call get_next_segment to get Part 1 and begin narrating it with expression."
        )
    )
    logger.info("Opening greeting sent — narration beginning")

    # ── Wait for session end and save summary ──────────────────────────────

    async def _on_session_end():
        """Extract transcript from ChatContext and trigger summarization."""
        session_ended_at = datetime.now(timezone.utc)
        logger.info("━━━ Story session ending — starting summarization ━━━")

        if not profile_id:
            logger.warning("No profile_id — skipping story session summarization")
            return

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
            )
            logger.info(f"Story session summary saved: {result}")

        except Exception as e:
            logger.error(f"Story session summarization failed: {e}", exc_info=True)

    # Register shutdown callback
    ctx.add_shutdown_callback(_on_session_end)
