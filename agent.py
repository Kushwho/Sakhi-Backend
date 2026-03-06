"""
Sakhi Voice Agent — MVP Backend
================================
Voice-first AI companion for Indian children (ages 4-12).

Runs as:
  - LiveKit Agent: `python agent.py dev` (or `console` / `start`)
  - FastAPI server: `uvicorn agent:app --reload` (token endpoint only)
"""

import json
import logging
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from livekit import agents, api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    function_tool,
    get_job_context,
    room_io,
)
from livekit.plugins import deepgram, groq, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv(".env.local")

logger = logging.getLogger("sakhi")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_EXPRESSIONS = {"happy", "thinking", "excited", "concerned", "sad", "celebrating"}

SAKHI_SYSTEM_PROMPT = """
You are Sakhi, a warm, curious, and encouraging AI companion for Indian children aged 4–12.

Personality:
- You are playful, patient, and full of wonder.
- You celebrate small wins enthusiastically.
- You speak in short, simple sentences appropriate for children.
- You are strictly child-safe — no violence, no inappropriate content, ever.

Teaching style:
- You NEVER give direct homework answers.
- You use Socratic questioning — ask guiding questions to help children discover answers themselves.
- When explaining concepts, use fun analogies, stories, and examples from everyday Indian life.

Conversation style:
- Keep responses concise (2-3 sentences max for young children, up to 4-5 for older ones).
- Use a cheerful, expressive tone.
- If the child seems confused, simplify and try a different approach.
- If the child seems sad or upset, be empathetic and supportive.

You are currently talking to {child_name}, who is {child_age} years old and prefers {child_language}.
Adjust your vocabulary and complexity to match their age.
""".strip()


# ---------------------------------------------------------------------------
# SakhiAgent — the voice agent class
# ---------------------------------------------------------------------------


class SakhiAgent(Agent):
    """OOP voice agent with child-specific personalization and three tool methods."""

    def __init__(
        self,
        child_name: str = "a child",
        child_age: int = 8,
        child_language: str = "English",
    ) -> None:
        instructions = SAKHI_SYSTEM_PROMPT.format(
            child_name=child_name,
            child_age=child_age,
            child_language=child_language,
        )
        super().__init__(instructions=instructions)
        self.child_name = child_name
        self.child_age = child_age
        self.child_language = child_language

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

    # -- Tool 2: Log emotion (DB stub) ---------------------------------------

    @function_tool()
    async def log_emotion(
        self,
        context: RunContext,
        emotion: str,
        intensity: str,
    ) -> None:
        """Log the child's detected emotional state for the parent dashboard.

        Call this when you notice a shift in the child's emotional state
        during the conversation. Do NOT tell the child you are logging this.

        Args:
            emotion: The detected emotion (e.g. "happy", "frustrated", "curious", "sad").
            intensity: How strong the emotion seems ("low", "medium", "high").
        """
        # TODO: Connect to emotion API / Postgres
        logger.info(
            f"log_emotion: child={self.child_name}, emotion={emotion}, intensity={intensity}"
        )

    # -- Tool 3: Set avatar expression (RPC to frontend) ---------------------

    @function_tool()
    async def set_avatar_expression(
        self,
        context: RunContext,
        expression: str,
    ) -> str:
        """Change Sakhi's avatar facial expression on the child's screen.

        Call this to make Sakhi's face react to the conversation. Match the
        expression to what you are saying or feeling.

        Args:
            expression: One of "happy", "thinking", "excited", "concerned", "sad", "celebrating".
        """
        if expression not in VALID_EXPRESSIONS:
            return f"Invalid expression '{expression}'. Use one of: {', '.join(sorted(VALID_EXPRESSIONS))}"

        # Send RPC to the frontend participant
        try:
            room = get_job_context().room
            for participant_id in room.remote_participants:
                await room.local_participant.perform_rpc(
                    destination_identity=participant_id,
                    method="setAvatarExpression",
                    payload=json.dumps({"expression": expression}),
                    response_timeout=5.0,
                )
            logger.info(f"set_avatar_expression: {expression}")
            return f"Expression set to {expression}"
        except Exception as e:
            logger.warning(f"set_avatar_expression failed (no frontend?): {e}")
            return f"Expression set to {expression} (frontend not connected)"


# ---------------------------------------------------------------------------
# LiveKit Agent Server
# ---------------------------------------------------------------------------

server = AgentServer()


@server.rtc_session(agent_name="sakhi-agent")
async def sakhi_entrypoint(ctx: agents.JobContext):
    """Entrypoint for each child session."""

    # Default fallback values (overwritten by participant metadata below)
    child_name = "buddy"
    child_age = 8
    child_language = "English"

    # Connect the agent to the LiveKit room
    await ctx.connect()

    # Wait for the child participant to connect
    await ctx.wait_for_participant()

    # Read metadata from the first remote participant (the child)
    for participant in ctx.room.remote_participants.values():
        if participant.metadata:
            try:
                meta = json.loads(participant.metadata)
                child_name = meta.get("child_name", child_name)
                child_age = meta.get("child_age", child_age)
                child_language = meta.get("child_language", child_language)
            except json.JSONDecodeError:
                logger.warning("Could not parse participant metadata as JSON, using defaults")
            break

    # Build the voice pipeline
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=groq.LLM(model="llama-3.1-8b-instant"),
        tts=deepgram.TTS(model="aura-2-asteria-en"),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    # Create personalized agent
    agent = SakhiAgent(
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
    )

    # Start the session
    await session.start(
        room=ctx.room,
        agent=agent,
    )

    # Greet the child
    await session.generate_reply(
        instructions=f"Greet {child_name} warmly by name. "
        f"You are excited to talk to them today! Keep it to 1-2 short sentences."
    )


# ---------------------------------------------------------------------------
# CLI entrypoint — run the LiveKit agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agents.cli.run_app(
        server,
        agents.WorkerOptions(
            agent_name="sakhi-agent",
            load_threshold=0.95,
            max_concurrent_jobs=1,
        ),
    )
