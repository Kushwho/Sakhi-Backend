"""
Sakhi — Story Service
======================
Data-access helpers for stories and story segments.
Used by the StoryAgent to fetch content from DB without coupling to
the FastAPI pool (same lazy-init pattern as session_summarizer.py).
"""

import logging
import os
from typing import Optional

import asyncpg

logger = logging.getLogger("sakhi.stories")

# ---------------------------------------------------------------------------
# DB pool (lazy-init — same pattern as session_summarizer)
# ---------------------------------------------------------------------------

_db_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool | None:
    global _db_pool
    if _db_pool is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.warning("DATABASE_URL not set — story service unavailable")
            return None
        _db_pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=3)
        logger.info("Story service DB pool created")
    return _db_pool


# ---------------------------------------------------------------------------
# Public read API (used by StoryAgent)
# ---------------------------------------------------------------------------


async def list_stories(
    genre: Optional[str] = None,
    age: Optional[int] = None,
    language: str = "English",
) -> list[dict]:
    """Return available stories, optionally filtered by genre and child age."""
    pool = await _get_pool()
    if not pool:
        return []

    query = "SELECT id, title, genre, age_min, age_max, total_segments FROM stories WHERE language = $1"
    params: list = [language]

    if genre:
        params.append(genre)
        query += f" AND genre = ${len(params)}"
    if age is not None:
        params.append(age)
        query += f" AND age_min <= ${len(params)} AND age_max >= ${len(params)}"

    query += " ORDER BY created_at DESC"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "genre": r["genre"],
            "age_min": r["age_min"],
            "age_max": r["age_max"],
            "total_segments": r["total_segments"],
        }
        for r in rows
    ]


async def get_story(story_id: str) -> dict | None:
    """Return metadata for a single story."""
    pool = await _get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, genre, age_min, age_max, total_segments FROM stories WHERE id = $1",
            story_id,
        )
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "genre": row["genre"],
        "total_segments": row["total_segments"],
    }


async def get_segment(story_id: str, position: int) -> str | None:
    """Return text of a story segment at the given 1-indexed position."""
    pool = await _get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content FROM story_segments WHERE story_id = $1 AND position = $2",
            story_id,
            position,
        )
    return row["content"] if row else None


async def get_all_segments(story_id: str) -> list[str]:
    """Return ALL segments of a story ordered by position.
    
    Used by the Story Agent at session start to pre-fetch the complete story
    into memory so there are no DB calls during narration.
    """
    pool = await _get_pool()
    if not pool:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content FROM story_segments WHERE story_id = $1 ORDER BY position ASC",
            story_id,
        )
    return [r["content"] for r in rows]


async def get_random_story(
    genre: str | None = None,
    age: int | None = None,
    language: str = "English",
) -> dict | None:
    """Return a random story matching the given filters.
    
    Used by the browse/preview API so the frontend can show a story title
    before the user commits to a session.
    """
    pool = await _get_pool()
    if not pool:
        return None

    query = "SELECT id, title, genre, age_min, age_max, total_segments FROM stories WHERE language = $1"
    params: list = [language]

    if genre:
        params.append(genre)
        query += f" AND genre = ${len(params)}"
    if age is not None:
        params.append(age)
        query += f" AND age_min <= ${len(params)} AND age_max >= ${len(params)}"

    # ORDER BY RANDOM() picks a random matching row
    query += " ORDER BY RANDOM() LIMIT 1"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *params)

    if not row:
        return None
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "genre": row["genre"],
        "age_min": row["age_min"],
        "age_max": row["age_max"],
        "total_segments": row["total_segments"],
    }


# ---------------------------------------------------------------------------
# Admin write API (used by story_routes.py)
# ---------------------------------------------------------------------------


async def create_story(
    title: str,
    genre: str,
    age_min: int,
    age_max: int,
    language: str,
    segments: list[str],
) -> dict:
    """Persist a new story with all its segments atomically."""
    pool = await _get_pool()
    if not pool:
        raise RuntimeError("Database not available")

    async with pool.acquire() as conn:
        async with conn.transaction():
            story_id = await conn.fetchval(
                """
                INSERT INTO stories (title, genre, age_min, age_max, language, total_segments)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                title, genre, age_min, age_max, language, len(segments),
            )
            for i, content in enumerate(segments, start=1):
                await conn.execute(
                    "INSERT INTO story_segments (story_id, position, content) VALUES ($1, $2, $3)",
                    story_id, i, content,
                )

    logger.info(f"Story created: '{title}' ({len(segments)} segments, id={story_id})")
    return {"id": str(story_id), "title": title, "total_segments": len(segments)}
