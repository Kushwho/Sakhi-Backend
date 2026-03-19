"""
Sakhi Backend — API Routes
===========================
FastAPI endpoints: token generation + health check + auth system.
"""

import json
import logging
import os
import time
import sys
import asyncio
from contextlib import asynccontextmanager

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from pydantic import BaseModel

load_dotenv(".env.local")

from db.pool import init_pool, close_pool
from db.migrations import run_migrations
from api.auth_routes import router as auth_router
from api.dashboard_routes import router as dashboard_router
from api.chat_routes import router as chat_router
from api.curious_routes import router as curious_router, curio_router
from api.say_what_you_see_routes import router as swys_router
from api.gentype_routes import router as gentype_router
from api.dependencies import require_profile_token
from services.profiles import get_current_profile
from services.checkpointer import init_checkpointer, close_checkpointer
from services.chat_graph import build_chat_graph
from services.prompts import load_prompts
from utils.logging_config import setup_logging

setup_logging()

logger = logging.getLogger("sakhi.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB pool + migrations + LangGraph.  Shutdown: teardown."""
    pool = await init_pool()
    await run_migrations(pool)
    logger.info("Database initialized and migrations applied")
    await load_prompts(pool)
    await init_checkpointer()
    build_chat_graph()
    logger.info("LangGraph chat pipeline ready")
    yield
    await close_checkpointer()
    await close_pool()


app = FastAPI(title="Sakhi Backend", version="0.2.0", lifespan=lifespan)

_ALLOWED_ORIGINS = os.getenv(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(chat_router)
app.include_router(curious_router)
app.include_router(curio_router)
app.include_router(swys_router)
app.include_router(gentype_router)


class TokenRequest(BaseModel):
    """Optional request body for LiveKit token generation."""
    mode: str = "default"
    topic_id: str | None = None
    surprise_fact: str | None = None


class TokenResponse(BaseModel):
    """Response with the LiveKit token and room details."""

    token: str
    room_name: str
    livekit_url: str


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "sakhi-backend", "timestamp": time.time()}


@app.post("/api/token", response_model=TokenResponse)
async def create_token(req: TokenRequest = TokenRequest(), claims: dict = Depends(require_profile_token)):
    """Generate a LiveKit room token for an authenticated child session.

    Requires a valid profile token (child type) in the Authorization header.
    The child's profile data is fetched from the database using the profile token claims.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(
            status_code=403, detail="Only child profiles can start voice sessions"
        )

    # Fetch child profile from DB
    profile = await get_current_profile(claims["profile_id"])

    child_name = profile.get("display_name", "buddy")
    child_age = profile.get("age") or 8

    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not all([livekit_url, api_key, api_secret]):
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    # Each child gets a unique room
    room_name = f"sakhi-{child_name.lower().replace(' ', '-')}-{int(time.time())}"

    # Resolve topic context if needed
    topic_context = None
    if req.mode == "curious_topic" and req.topic_id:
        from services.topics import get_topic_by_id
        topic = get_topic_by_id(req.topic_id)
        if topic:
            topic_context = {"title": topic["title"], "description": topic["description"]}

    # Room metadata carries child profile + mode context for the agent to read
    room_metadata = json.dumps(
        {
            "child_name": child_name,
            "child_age": child_age,
            "child_language": "English",
            "profile_id": claims["profile_id"],
            "mode": req.mode,
            "topic_context": topic_context,
            "surprise_fact": req.surprise_fact,
        }
    )

    # Generate access token
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"child-{child_name.lower().replace(' ', '-')}")
        .with_name(child_name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
            )
        )
        .with_metadata(room_metadata)
    )

    # Create room + dispatch the Sakhi agent and emotion detector
    try:
        lkapi = api.LiveKitAPI()

        # Create the room first
        await lkapi.room.create_room(
            api.CreateRoomRequest(name=room_name, metadata=room_metadata)
        )

        # Dispatch the voice agent
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="sakhi-agent",
                room=room_name,
            )
        )

        # Dispatch the emotion detector (programmatic participant)
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="emotion-detector",
                room=room_name,
            )
        )

        await lkapi.aclose()
        logger.info(f"Room created, agent + emotion detector dispatched: {room_name}")
    except Exception as e:
        logger.error(f"Failed to dispatch agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start voice session")

    return TokenResponse(
        token=token.to_jwt(),
        room_name=room_name,
        livekit_url=livekit_url,
    )

