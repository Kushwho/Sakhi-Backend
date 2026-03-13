"""
Sakhi Backend — Story Routes
==============================
Endpoints for the Story Narration feature:

  Browse flow (frontend):
    1. GET  /api/stories/random?genre=adventure&age=8  → preview (id + title)
    2. GET  /api/stories/{id}                          → confirm story details
    3. POST /api/story-token  {story_id: "..."}        → start session

  Admin (seeding):
    GET  /api/stories          → list all
    POST /api/stories          → create with segments
"""

import json
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from livekit import api
from pydantic import BaseModel

from api.dependencies import require_profile_token
from services.profiles import get_current_profile
from services.stories import create_story, get_random_story, get_story, list_stories

logger = logging.getLogger("sakhi.api.stories")

router = APIRouter(tags=["stories"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    token: str
    room_name: str
    livekit_url: str


class StoryTokenRequest(BaseModel):
    story_id: str   # Required — user must confirm the story before starting


class CreateStoryRequest(BaseModel):
    title: str
    genre: str = "general"
    age_min: int = 4
    age_max: int = 12
    language: str = "English"
    segments: list[str]


# ---------------------------------------------------------------------------
# Browse: random story preview
# ---------------------------------------------------------------------------


@router.get("/api/stories/random")
async def random_story(
    genre: str = "",
    age: int = 0,
    language: str = "English",
):
    """Return a random story matching the genre/age filters.

    The frontend shows the title to the user. If they like it, they call
    POST /api/story-token with the returned story_id. If not, they call
    this endpoint again to get another random story.
    """
    story = await get_random_story(
        genre=genre or None,
        age=age or None,
        language=language,
    )
    if not story:
        raise HTTPException(
            status_code=404,
            detail="No stories found matching the given filters."
        )
    return story   # {id, title, genre, age_min, age_max, total_segments}


# ---------------------------------------------------------------------------
# Story detail
# ---------------------------------------------------------------------------


@router.get("/api/stories/{story_id}")
async def get_story_detail(story_id: str):
    """Get metadata for a confirmed story (title, genre, segments count)."""
    story = await get_story(story_id)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


# ---------------------------------------------------------------------------
# Story session token — starts the LiveKit narration session
# ---------------------------------------------------------------------------


@router.post("/api/story-token", response_model=TokenResponse)
async def create_story_token(
    req: StoryTokenRequest,
    claims: dict = Depends(require_profile_token),
):
    """Generate a LiveKit room token for a confirmed Story Narration session.

    The story_id is required — the user must have already previewed and
    confirmed the story before calling this endpoint.
    
    Room metadata includes story_id so the story-agent can pre-fetch all 
    segments at session start. No DB calls happen during narration.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(
            status_code=403, detail="Only child profiles can start story sessions"
        )

    # Validate story exists
    story = await get_story(req.story_id)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")

    profile = await get_current_profile(claims["profile_id"])
    child_name = profile.get("display_name", "buddy")
    child_age = profile.get("age") or 8

    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not all([livekit_url, api_key, api_secret]):
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    room_name = f"story-{child_name.lower().replace(' ', '-')}-{int(time.time())}"

    room_metadata = json.dumps({
        "child_name": child_name,
        "child_age": child_age,
        "child_language": "English",
        "profile_id": claims["profile_id"],
        "story_id": req.story_id,
        "story_title": story["title"],
    })

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"child-{child_name.lower().replace(' ', '-')}")
        .with_name(child_name)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_metadata(room_metadata)
    )

    try:
        lkapi = api.LiveKitAPI()
        await lkapi.room.create_room(
            api.CreateRoomRequest(name=room_name, metadata=room_metadata)
        )
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name="story-agent", room=room_name)
        )
        await lkapi.aclose()
        logger.info(f"Story room '{room_name}' created for story '{story['title']}'")
    except Exception as e:
        logger.error(f"Failed to dispatch story agent: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to dispatch story agent: {e}")

    return TokenResponse(
        token=token.to_jwt(),
        room_name=room_name,
        livekit_url=livekit_url,
    )


# ---------------------------------------------------------------------------
# Admin CRUD (no auth for MVP)
# ---------------------------------------------------------------------------


@router.get("/api/stories")
async def get_stories(genre: str = "", age: int = 0, language: str = "English"):
    """List all stories, optionally filtered by genre/age/language."""
    results = await list_stories(
        genre=genre or None,
        age=age or None,
        language=language,
    )
    return {"stories": results, "total": len(results)}


@router.post("/api/stories", status_code=201)
async def create_new_story(req: CreateStoryRequest):
    """Admin: Create a new story with ordered text segments.
    
    NOTE: No auth for MVP — add API-key protection before production.
    """
    if not req.segments:
        raise HTTPException(status_code=422, detail="At least one segment required")
    try:
        result = await create_story(
            title=req.title,
            genre=req.genre,
            age_min=req.age_min,
            age_max=req.age_max,
            language=req.language,
            segments=req.segments,
        )
        logger.info(f"Admin created story '{req.title}' ({len(req.segments)} segments)")
        return result
    except Exception as e:
        logger.error(f"Failed to create story: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
