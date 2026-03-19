"""
Sakhi — Say What You See Service
==================================
Core logic for the SWYS mini-game:
  - Fetch a random seed image (optionally by difficulty level)
  - Generate an image from the kid's prompt (via ``SakhiLLM.generate_image``)
  - Judge both images with a vision LLM (via ``SakhiLLM.vision_json``)
  - Persist the attempt and return history
"""

import logging
import uuid as uuid_lib

from db.pool import get_pool
from services.llm import get_llm_client

logger = logging.getLogger("sakhi.swys")

JUDGE_SYSTEM_PROMPT = (
    "You are a helpful, encouraging judge for a children's image description game. "
    "You compare two images and give a similarity score and a kind, constructive hint."
)

JUDGE_PROMPT_TEMPLATE = (
    "A child is playing a game where they describe an image using words, "
    "and we generate a new image from their description.\n\n"
    'The child wrote this prompt: "{kid_prompt}"\n\n'
    "Below you will see two images:\n"
    "- FIRST image: The ORIGINAL image the child was trying to describe\n"
    "- SECOND image: The image GENERATED from the child's prompt\n\n"
    "Compare them carefully.\n"
    "1. Score how similar they are from 0 to 100 (100 = almost identical scene/subject/mood).\n"
    "2. Write a short, encouraging hint (1-2 sentences, child-friendly) telling the child what "
    "they could add or change in their description to get a higher score next time.\n\n"
    'Return ONLY valid JSON: {{"score": <int 0-100>, "hint": "<string>"}}'
)


# ---------------------------------------------------------------------------
# Fetch a random seed image
# ---------------------------------------------------------------------------


async def get_random_image(level: int | None = None) -> dict | None:
    """Return a random active seed image, optionally filtered by difficulty level."""
    pool = get_pool()
    async with pool.acquire() as conn:
        if level is not None:
            row = await conn.fetchrow(
                """
                SELECT id, title, image_url, level, category
                FROM swys_images
                WHERE is_active = true AND level = $1
                ORDER BY RANDOM()
                LIMIT 1
                """,
                level,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT id, title, image_url, level, category
                FROM swys_images
                WHERE is_active = true
                ORDER BY RANDOM()
                LIMIT 1
                """
            )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "image_url": row["image_url"],
        "level": row["level"],
        "category": row["category"],
    }


async def get_image_by_id(image_id: str) -> dict | None:
    """Fetch a specific seed image by its UUID."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, image_url, level, category FROM swys_images WHERE id = $1",
            uuid_lib.UUID(image_id),
        )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "image_url": row["image_url"],
        "level": row["level"],
        "category": row["category"],
    }


# ---------------------------------------------------------------------------
# Generate image via SakhiLLM
# ---------------------------------------------------------------------------


async def generate_image(prompt: str) -> str:
    """Generate an image from the kid's prompt via Replicate (delegates to SakhiLLM)."""
    llm = get_llm_client()
    return await llm.generate_image(prompt)


# ---------------------------------------------------------------------------
# Judge: compare original vs generated image
# ---------------------------------------------------------------------------


async def judge_attempt(
    original_url: str,
    generated_url: str,
    kid_prompt: str,
) -> dict:
    """
    Use the vision LLM to compare the original seed image with the
    kid's generated image. Returns {score: int, hint: str}.

    Falls back to {score: 50, hint: "..."} on any error.
    """
    prompt = JUDGE_PROMPT_TEMPLATE.format(kid_prompt=kid_prompt)

    logger.info(f"Judging: original={original_url[:80]}, generated={generated_url[:80]}")
    try:
        import json as _json
        import os

        from groq import AsyncGroq

        client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY", ""))

        user_content = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": "ORIGINAL image (the one the child was shown):"},
            {"type": "image_url", "image_url": {"url": original_url}},
            {"type": "text", "text": "GENERATED image (created from the child's prompt):"},
            {"type": "image_url", "image_url": {"url": generated_url}},
        ]

        messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        response = await client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=messages,
            temperature=0.2,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        result = _json.loads(response.choices[0].message.content)
        logger.info(f"Vision LLM raw result: {result}")
        score = max(0, min(100, int(result.get("score", 50))))
        hint = result.get("hint", "Keep trying -- you're doing great!")
        logger.info(f"Judge result: score={score}")
        return {"score": score, "hint": hint}
    except Exception as e:
        logger.error(f"Vision judge failed: {e}", exc_info=True)
        return {
            "score": 50,
            "hint": "Great try! Add more details about colours, shapes, and objects you see.",
        }


# ---------------------------------------------------------------------------
# Persist attempt
# ---------------------------------------------------------------------------


async def save_attempt(
    profile_id: str,
    image_id: str,
    kid_prompt: str,
    generated_image_url: str,
    score: int,
    hint: str,
) -> dict:
    """Insert an attempt record into swys_attempts and return it."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO swys_attempts
                (profile_id, image_id, kid_prompt, generated_image_url, score, hint)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, created_at
            """,
            uuid_lib.UUID(profile_id),
            uuid_lib.UUID(image_id),
            kid_prompt,
            generated_image_url,
            score,
            hint,
        )
    return {
        "id": str(row["id"]),
        "profile_id": profile_id,
        "image_id": image_id,
        "kid_prompt": kid_prompt,
        "generated_image_url": generated_image_url,
        "score": score,
        "hint": hint,
        "created_at": row["created_at"].isoformat(),
    }


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def get_attempt_history(profile_id: str, limit: int = 10) -> list[dict]:
    """Return the kid's most recent SWYS attempts with image title."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                a.id, a.kid_prompt, a.generated_image_url, a.score, a.hint, a.created_at,
                i.title AS image_title, i.level AS image_level
            FROM swys_attempts a
            JOIN swys_images i ON i.id = a.image_id
            WHERE a.profile_id = $1
            ORDER BY a.created_at DESC
            LIMIT $2
            """,
            uuid_lib.UUID(profile_id),
            limit,
        )
    return [
        {
            "id": str(r["id"]),
            "kid_prompt": r["kid_prompt"],
            "generated_image_url": r["generated_image_url"],
            "score": r["score"],
            "hint": r["hint"],
            "created_at": r["created_at"].isoformat(),
            "image_title": r["image_title"],
            "image_level": r["image_level"],
        }
        for r in rows
    ]
