"""
Sakhi — Say What You See API Routes
======================================
Endpoints for the SWYS mini-game:
  - ``GET  /api/swys/image``    — fetch a random seed image (by optional level)
  - ``POST /api/swys/attempt``  — submit kid's prompt, generate image, score + hint
  - ``GET  /api/swys/history``  — kid's recent attempts
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.dependencies import require_profile_token
from services import say_what_you_see as swys_svc

logger = logging.getLogger("sakhi.api.swys")

router = APIRouter(prefix="/api/swys", tags=["say-what-you-see"])


# ---------------------------------------------------------------------------
# GET /api/swys/image
# ---------------------------------------------------------------------------


@router.get("/image")
async def get_image(
    level: int | None = Query(None, ge=1, le=5, description="Difficulty level 1-5"),
    claims: dict = Depends(require_profile_token),
):
    """Return a random active seed image, optionally filtered by difficulty level.

    Requires a child profile token.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can play Say What You See")

    image = await swys_svc.get_random_image(level=level)
    if image is None:
        detail = f"No images found for level {level}" if level else "No images available"
        raise HTTPException(status_code=404, detail=detail)

    return image


# ---------------------------------------------------------------------------
# POST /api/swys/attempt
# ---------------------------------------------------------------------------


class AttemptRequest(BaseModel):
    image_id: str
    kid_prompt: str


@router.post("/attempt")
async def submit_attempt(
    req: AttemptRequest,
    claims: dict = Depends(require_profile_token),
):
    """Submit the kid's prompt, generate an image, judge it, and return score + hint.

    Flow:
    1. Fetch seed image URL from DB using image_id (FK → swys_images).
    2. Generate a new image from kid_prompt via Replicate flux-1.1-pro.
    3. Judge both images with a Groq vision LLM → score (0-100) + hint.
    4. Persist the attempt and return the full result.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can play Say What You See")

    if not req.kid_prompt.strip():
        raise HTTPException(status_code=400, detail="kid_prompt cannot be empty")

    # 1. Fetch seed image
    image = await swys_svc.get_image_by_id(req.image_id)
    if image is None:
        raise HTTPException(status_code=404, detail=f"Image '{req.image_id}' not found")

    original_url = image["image_url"]

    # 2. Generate image from kid's prompt
    try:
        generated_url = await swys_svc.generate_image(req.kid_prompt.strip())
    except RuntimeError as e:
        logger.error(f"Image generation error: {e}")
        raise HTTPException(status_code=502, detail="Image generation failed. Please try again.")

    # 3. Judge: compare original vs generated
    judgment = await swys_svc.judge_attempt(original_url, generated_url, req.kid_prompt)

    # 4. Persist
    attempt = await swys_svc.save_attempt(
        profile_id=claims["profile_id"],
        image_id=req.image_id,
        kid_prompt=req.kid_prompt.strip(),
        generated_image_url=generated_url,
        score=judgment["score"],
        hint=judgment["hint"],
    )

    return {
        "score": attempt["score"],
        "hint": attempt["hint"],
        "generated_image_url": attempt["generated_image_url"],
        "original_image_url": original_url,
        "attempt_id": attempt["id"],
    }


# ---------------------------------------------------------------------------
# GET /api/swys/history
# ---------------------------------------------------------------------------


@router.get("/history")
async def get_history(
    limit: int = Query(10, ge=1, le=50),
    claims: dict = Depends(require_profile_token),
):
    """Return the kid's most recent SWYS attempts with score, hint, and image title."""
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can view SWYS history")

    attempts = await swys_svc.get_attempt_history(
        profile_id=claims["profile_id"],
        limit=limit,
    )
    return {"attempts": attempts}
