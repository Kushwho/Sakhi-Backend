"""
Sakhi — GenType API Routes
============================
Endpoints for the GenType activity (Curio grid):
  - ``GET  /api/curio/gentype/themes``      — available visual themes
  - ``POST /api/curio/gentype/generate``     — generate (or cache-hit) a single letter
  - ``POST /api/curio/gentype/spell-name``   — generate all unique letters of the child's name
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import require_profile_token
from db.pool import get_pool
from services.image_gen import build_letter_prompt, get_theme_by_id, get_themes
from services.llm import get_llm_client
from services.profiles import get_current_profile

logger = logging.getLogger("sakhi.api.gentype")

router = APIRouter(prefix="/api/curio/gentype", tags=["gentype"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GenerateLetterRequest(BaseModel):
    theme_id: str
    letter: str
    force_regenerate: bool = False


class SpellNameRequest(BaseModel):
    theme_id: str


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


async def _get_cached(conn, cache_key: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT image_url FROM gentype_cache WHERE cache_key = $1",
        cache_key,
    )
    return row["image_url"] if row else None


async def _upsert_cache(conn, cache_key: str, letter: str, theme_id: str, image_url: str) -> None:
    await conn.execute(
        """
        INSERT INTO gentype_cache (cache_key, letter, theme_id, image_url)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (cache_key) DO UPDATE SET image_url = EXCLUDED.image_url
        """,
        cache_key,
        letter.upper(),
        theme_id,
        image_url,
    )


# ---------------------------------------------------------------------------
# GET /themes — public (no auth)
# ---------------------------------------------------------------------------


@router.get("/themes")
async def list_themes():
    """Return the available GenType themes."""
    return {"themes": get_themes()}


# ---------------------------------------------------------------------------
# POST /generate — single letter
# ---------------------------------------------------------------------------


@router.post("/generate")
async def generate_letter(
    req: GenerateLetterRequest,
    claims: dict = Depends(require_profile_token),
):
    """Generate a single styled letter image (with cache)."""
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can use GenType")

    # Validate inputs
    if len(req.letter) != 1 or not req.letter.isalpha():
        raise HTTPException(status_code=400, detail="letter must be a single A-Z character")
    if not get_theme_by_id(req.theme_id):
        raise HTTPException(status_code=400, detail=f"Unknown theme_id: {req.theme_id!r}")

    letter = req.letter.upper()
    cache_key = f"{req.theme_id}:{letter}"
    pool = get_pool()

    # Cache check (skip if force_regenerate)
    if not req.force_regenerate:
        async with pool.acquire() as conn:
            cached_url = await _get_cached(conn, cache_key)
            if cached_url:
                return {
                    "letter": letter,
                    "theme_id": req.theme_id,
                    "image_url": cached_url,
                    "from_cache": True,
                }

    # Generate via Replicate (through SakhiLLM)
    prompt = build_letter_prompt(letter, req.theme_id)
    try:
        llm = get_llm_client()
        image_url = await llm.generate_image(prompt)
    except Exception as e:
        logger.error(f"GenType generation failed for {letter}/{req.theme_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Image generation failed") from e

    # Cache the result
    async with pool.acquire() as conn:
        await _upsert_cache(conn, cache_key, letter, req.theme_id, image_url)

    return {
        "letter": letter,
        "theme_id": req.theme_id,
        "image_url": image_url,
        "from_cache": False,
    }


# ---------------------------------------------------------------------------
# POST /spell-name — generate letters of the child's name
# ---------------------------------------------------------------------------


@router.post("/spell-name")
async def spell_name(
    req: SpellNameRequest,
    claims: dict = Depends(require_profile_token),
):
    """Generate styled letter images for the unique letters of the child's name."""
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can use GenType")
    if not get_theme_by_id(req.theme_id):
        raise HTTPException(status_code=400, detail=f"Unknown theme_id: {req.theme_id!r}")

    profile = await get_current_profile(claims["profile_id"])
    child_name = profile.get("display_name", "buddy")

    # Deduplicate letters, preserving first-occurrence order
    seen: set[str] = set()
    unique_letters: list[str] = []
    for ch in child_name.upper():
        if ch.isalpha() and ch not in seen:
            seen.add(ch)
            unique_letters.append(ch)

    if not unique_letters:
        return {"name": child_name, "theme_id": req.theme_id, "letters": [], "has_errors": False}

    pool = get_pool()

    # Batch cache check
    cache_results: dict[str, str | None] = {}
    async with pool.acquire() as conn:
        for letter in unique_letters:
            cache_results[letter] = await _get_cached(conn, f"{req.theme_id}:{letter}")

    # Generate uncached letters sequentially (Replicate rate limit: burst of 1)
    uncached = [ch for ch in unique_letters if cache_results[ch] is None]
    generated: list[dict] = []
    llm = get_llm_client()
    for i, letter in enumerate(uncached):
        prompt = build_letter_prompt(letter, req.theme_id)
        try:
            url = await llm.generate_image(prompt)
            generated.append({"letter": letter, "image_url": url, "from_cache": False, "error": None})
        except Exception as e:
            logger.error(f"GenType spell-name failed for {letter}: {e}", exc_info=True)
            generated.append({"letter": letter, "image_url": None, "from_cache": False, "error": str(e)})
        # Respect Replicate rate limit (6 req/min, burst 1, resets ~10s)
        if i < len(uncached) - 1:
            await asyncio.sleep(12)
    gen_map = {r["letter"]: r for r in generated}

    # Cache successful generations
    async with pool.acquire() as conn:
        for result in generated:
            if result["image_url"]:
                await _upsert_cache(
                    conn,
                    f"{req.theme_id}:{result['letter']}",
                    result["letter"],
                    req.theme_id,
                    result["image_url"],
                )

    # Assemble response in name-letter order
    letters_out = []
    for letter in unique_letters:
        if cache_results[letter]:
            letters_out.append(
                {
                    "letter": letter,
                    "image_url": cache_results[letter],
                    "from_cache": True,
                    "error": None,
                }
            )
        else:
            letters_out.append(gen_map[letter])

    return {
        "name": child_name,
        "theme_id": req.theme_id,
        "letters": letters_out,
        "has_errors": any(entry["error"] for entry in letters_out),
    }
