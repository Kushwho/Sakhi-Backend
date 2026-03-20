"""
Sakhi Backend — Story Generation Routes
==========================================
New AI-powered multi-modal story generation endpoints.

Story generation pipeline:
  1. POST /api/stories/generate  — Generate a complete story (text + images)
  2. GET  /api/stories/health    — Service health check (feature flag for frontend)

The legacy pre-authored story endpoints (browse, token, CRUD) have been
replaced by this on-demand AI generation pipeline.

Authentication:
  All endpoints require a valid profile token. Story generation is available
  to any profile type (children and parents).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import require_profile_token
from services.story_orchestrator import get_story_orchestrator

logger = logging.getLogger("sakhi.api.story")

router = APIRouter(prefix="/api/stories", tags=["story"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class StoryGenerateRequest(BaseModel):
    """Request body for multi-modal story generation."""

    idea: str = Field(
        ...,
        description="The user's story concept or idea.",
        min_length=3,
        max_length=500,
        examples=["A brave little elephant who wants to fly"],
    )
    genre: str = Field(
        default="adventure",
        description="Story genre — e.g. 'adventure', 'fable', 'fantasy', 'mystery', 'comedy'.",
        max_length=50,
        examples=["adventure"],
    )
    num_scenes: int = Field(
        default=4,
        description="Number of story scenes/paragraphs to generate (2–8).",
        ge=2,
        le=8,
    )
    child_age: int = Field(
        default=8,
        description="Target child's age for vocabulary calibration (4–12).",
        ge=4,
        le=12,
    )
    setting: str = Field(
        default="Indian or universal",
        description="Cultural / geographic context hint (e.g. 'Indian', 'jungle', 'space').",
        max_length=100,
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="Image aspect ratio for Flux generation.",
        pattern=r"^\d+:\d+$",
        examples=["16:9", "1:1", "4:3"],
    )
    output_format: str = Field(
        default="webp",
        description="Image output format — 'webp', 'jpg', or 'png'.",
        pattern=r"^(webp|jpg|png)$",
    )


class StoryScene(BaseModel):
    """A single fully-assembled story scene (text + illustration URL)."""

    scene_number: int
    story_text: str = Field(description="Narrative paragraph for this scene.")
    image_prompt: str = Field(description="The Flux prompt used to generate the illustration.")
    image_url: str | None = Field(
        default=None,
        description="URL of the generated illustration, or null if generation failed.",
    )
    audio_url: str | None = Field(
        default=None,
        description="URL of the generated TTS speech audio, or null if generation failed.",
    )


class StoryGenerateResponse(BaseModel):
    """Response payload for a fully generated multi-modal story."""

    title: str = Field(description="The generated story title.")
    scenes: list[StoryScene] = Field(description="Ordered list of story scenes.")
    total_scenes: int = Field(description="Total number of scenes in the story.")
    images_generated: int = Field(
        description="Number of scenes that have a successfully generated image URL."
    )
    audio_generated: int = Field(
        description="Number of scenes that have a successfully generated audio URL."
    )


class StoryHealthResponse(BaseModel):
    """Health check response for the story generation feature."""

    status: str
    image_generation_available: bool
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/generate", response_model=StoryGenerateResponse, status_code=200)
async def generate_story(
    req: StoryGenerateRequest,
    claims: dict = Depends(require_profile_token),
) -> StoryGenerateResponse:
    """
    Generate a complete multi-modal story from a simple idea.

    This endpoint orchestrates the full AI pipeline:
    1. Calls Groq to generate structured story scenes + image prompts (as strict JSON).
    2. Runs all image generation calls concurrently via Replicate / Flux Schnell.
    3. Returns the assembled text + image URLs payload.

    Partial results are returned gracefully — if some image generation calls
    fail, the story text is still returned with ``image_url: null`` for those
    scenes. The client should handle null image URLs gracefully (e.g. show a
    placeholder illustration).

    Note: This endpoint may take 30–60 seconds depending on the number of
    scenes and Replicate cold-start times.
    """
    logger.info(
        f"Story generation requested by profile {claims.get('profile_id', 'unknown')}: "
        f"idea='{req.idea[:60]}' genre={req.genre} scenes={req.num_scenes}"
    )

    orchestrator = get_story_orchestrator()

    try:
        result = await orchestrator.generate_story(
            idea=req.idea,
            genre=req.genre,
            num_scenes=req.num_scenes,
            child_age=req.child_age,
            setting=req.setting,
            aspect_ratio=req.aspect_ratio,
            output_format=req.output_format,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        logger.error(f"Story generation pipeline error: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Story generation failed: {e}. "
                "This may be a temporary issue — please try again."
            ),
        )
    except Exception as e:
        logger.error(f"Unexpected error in story generation: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred during story generation.",
        )

    return StoryGenerateResponse(
        title=result["title"],
        scenes=[StoryScene(**scene) for scene in result["scenes"]],
        total_scenes=result["total_scenes"],
        images_generated=result["images_generated"],
        audio_generated=result["audio_generated"],
    )


@router.get("/health", response_model=StoryHealthResponse)
async def story_health(
    claims: dict = Depends(require_profile_token),
) -> StoryHealthResponse:
    """
    Check whether the story generation feature is fully operational.

    Returns availability status for the image generation service.
    The frontend can use this to show or hide the story generation UI,
    or to warn users if images won't be available.
    """
    import os

    image_generation_available = bool(os.getenv("REPLICATE_API_TOKEN"))

    if image_generation_available:
        message = "Story generation is fully operational with text + image support."
    else:
        message = (
            "Story generation is available (text only). "
            "Set REPLICATE_API_TOKEN to enable image illustrations."
        )

    return StoryHealthResponse(
        status="ok",
        image_generation_available=image_generation_available,
        message=message,
    )
