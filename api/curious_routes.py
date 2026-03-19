"""
Sakhi — Curious Mode API Routes
=================================
Endpoints for the Curious Mode feature:
  - ``GET /api/curious/topics``   — age-filtered topic cards
  - ``GET /api/curious/surprise`` — random interesting fact for the child

Unified Curio grid endpoints:
  - ``GET /api/curio/activities``                     — all Curio activity tiles
  - ``POST /api/curio/activities/{activity_id}/start`` — start a specific activity
"""

import logging
import random
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import require_profile_token
from services.profiles import get_current_profile
from services.prompts import get_prompt_template
from services.topics import get_topics_response

logger = logging.getLogger("sakhi.api.curious")

router = APIRouter(prefix="/api/curious", tags=["curious"])
curio_router = APIRouter(prefix="/api/curio", tags=["curio"])

# ---------------------------------------------------------------------------
# Curio activity catalog — drives the 2×2 grid on the frontend
# ---------------------------------------------------------------------------

CURIO_ACTIVITIES: list[dict[str, Any]] = [
    {
        "id": "thinking",
        "title": "Thinking",
        "emoji": "🤔",
        "description": "Explore topics, get surprised by facts, or ask anything you're curious about!",
        "is_available": True,
    },
    {
        "id": "say_what_you_see",
        "title": "Say What You See",
        "emoji": "👁️",
        "description": "Look at a scene and describe what you see — Sakhi will help you explore it!",
        "is_available": True,
    },
    {
        "id": "gentype",
        "title": "GenType",
        "emoji": "🔤",
        "description": "Design your own alphabet made of anything you can imagine!",
        "is_available": True,
    },
    {
        "id": "coming_soon",
        "title": "Coming Soon",
        "emoji": "✨",
        "description": "Something amazing is on its way...",
        "is_available": False,
    },
]

# Topic categories used for random selection in surprise mode
SURPRISE_CATEGORIES = [
    "Science", "Space", "Nature", "Animals", "Human Body",
    "Math", "History", "Technology", "Art", "Environment",
]


# ---------------------------------------------------------------------------
# GET /api/curious/topics
# ---------------------------------------------------------------------------


@router.get("/topics")
async def get_topics(claims: dict = Depends(require_profile_token)):
    """Return age-filtered topic cards for the Curious Mode home page."""
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can access topics")

    profile = await get_current_profile(claims["profile_id"])
    child_age = profile.get("age") or 8

    topics = get_topics_response(child_age)
    return {"topics": topics}


# ---------------------------------------------------------------------------
# GET /api/curious/surprise
# ---------------------------------------------------------------------------


@router.get("/surprise")
async def get_surprise(claims: dict = Depends(require_profile_token)):
    """Generate a random interesting fact appropriate for the child's age."""
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can access surprise mode")

    profile = await get_current_profile(claims["profile_id"])
    child_age = profile.get("age") or 8
    category = random.choice(SURPRISE_CATEGORIES)

    from services.llm import get_llm_client

    llm = get_llm_client()

    template = get_prompt_template("surprise_generator")
    if not template:
        raise HTTPException(status_code=500, detail="Surprise generator prompt not configured")

    prompt = template.format(child_age=child_age, category=category)

    try:
        result = await llm.generate_json(
            prompt=prompt,
            temperature=0.9,
            max_tokens=300,
        )
        return {
            "fact": result.get("fact", ""),
            "topic": result.get("topic", category),
            "follow_up_question": result.get("follow_up_question", ""),
        }
    except Exception as e:
        logger.error(f"Surprise fact generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate surprise fact")


# ---------------------------------------------------------------------------
# GET /api/curio/activities — unified Curio grid
# ---------------------------------------------------------------------------


@curio_router.get("/activities")
async def get_curio_activities(claims: dict = Depends(require_profile_token)):
    """Return the full list of Curio activity tiles for the frontend grid."""
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can access Curio")

    return {"activities": CURIO_ACTIVITIES}


# ---------------------------------------------------------------------------
# POST /api/curio/activities/{activity_id}/start — start a Curio activity
# ---------------------------------------------------------------------------


class StartActivityRequest(BaseModel):
    # For "thinking" activity only
    sub_mode: str = "curious_open"   # "curious_open" | "curious_topic" | "curious_surprise"
    topic_id: str | None = None      # required when sub_mode == "curious_topic"


@curio_router.post("/activities/{activity_id}/start")
async def start_curio_activity(
    activity_id: str,
    req: StartActivityRequest = StartActivityRequest(),
    claims: dict = Depends(require_profile_token),
):
    """Start a Curio activity and return the mode + context needed to launch a session.

    Returns ``activity_id``, ``mode`` (the system prompt key), and an
    activity-specific ``context`` dict that callers pass to the chat / voice
    session so Sakhi knows what to talk about.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can start Curio activities")

    # Validate activity exists and is available
    activity = next((a for a in CURIO_ACTIVITIES if a["id"] == activity_id), None)
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity '{activity_id}' not found")
    if not activity["is_available"]:
        raise HTTPException(status_code=400, detail=f"Activity '{activity_id}' is not yet available")

    profile = await get_current_profile(claims["profile_id"])
    child_age = profile.get("age") or 8

    # ── Thinking ────────────────────────────────────────────────────────────
    if activity_id == "thinking":
        valid_sub_modes = {"curious_open", "curious_topic", "curious_surprise"}
        if req.sub_mode not in valid_sub_modes:
            raise HTTPException(status_code=400, detail=f"sub_mode must be one of {valid_sub_modes}")

        if req.sub_mode == "curious_topic":
            if req.topic_id:
                from services.topics import get_topic_by_id
                topic = get_topic_by_id(req.topic_id)
                if not topic:
                    raise HTTPException(status_code=404, detail=f"Topic '{req.topic_id}' not found")
                return {
                    "activity_id": activity_id,
                    "mode": "curious_topic",
                    "context": {
                        "topic_id": req.topic_id,
                        "topic": {"title": topic["title"], "description": topic["description"]},
                    },
                }
            else:
                # No topic_id given — return topic list so frontend can let child pick
                topics = get_topics_response(child_age)
                return {
                    "activity_id": activity_id,
                    "mode": "curious_topic",
                    "context": {"topics": topics},
                }

        if req.sub_mode == "curious_surprise":
            category = random.choice(SURPRISE_CATEGORIES)
            from services.llm import get_llm_client
            llm = get_llm_client()
            template = get_prompt_template("surprise_generator")
            if not template:
                raise HTTPException(status_code=500, detail="Surprise generator prompt not configured")
            try:
                result = await llm.generate_json(
                    prompt=template.format(child_age=child_age, category=category),
                    temperature=0.9,
                    max_tokens=300,
                )
                return {
                    "activity_id": activity_id,
                    "mode": "curious_surprise",
                    "context": {
                        "fact": result.get("fact", ""),
                        "topic": result.get("topic", category),
                        "follow_up_question": result.get("follow_up_question", ""),
                    },
                }
            except Exception as e:
                logger.error(f"Surprise generation failed: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Failed to generate surprise fact")

        # curious_open — no extra context needed
        return {"activity_id": activity_id, "mode": "curious_open", "context": {}}

    # ── Say What You See ─────────────────────────────────────────────────────
    if activity_id == "say_what_you_see":
        template = get_prompt_template("curio_say_what_you_see_generator")
        if not template:
            raise HTTPException(status_code=500, detail="Scene generator prompt not configured")
        from services.llm import get_llm_client
        llm = get_llm_client()
        try:
            result = await llm.generate_json(
                prompt=template.format(child_age=child_age),
                temperature=0.9,
                max_tokens=400,
            )
            return {
                "activity_id": activity_id,
                "mode": "curio_say_what_you_see",
                "context": {
                    "scene_description": result.get("scene_description", ""),
                    "scene_prompt": result.get("scene_prompt", ""),
                    "discussion_starters": result.get("discussion_starters", []),
                },
            }
        except Exception as e:
            logger.error(f"Scene generation failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to generate scene")

    # ── GenType ──────────────────────────────────────────────────────────────
    if activity_id == "gentype":
        from services.image_gen import get_themes
        return {
            "activity_id": activity_id,
            "mode": "curio_gentype",
            "context": {"themes": get_themes()},
        }

    # Fallback (shouldn't reach here given catalog check above)
    raise HTTPException(status_code=400, detail=f"Activity '{activity_id}' has no start handler")
