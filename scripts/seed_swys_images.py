"""
Seed script: populate swys_images with test images.
======================================================
Generates images via Replicate (flux-1.1-pro) and upserts them into the
swys_images table. Safe to re-run — uses ON CONFLICT DO UPDATE.

Usage:
    python scripts/seed_swys_images.py

Requires:
    - DATABASE_URL and REPLICATE_API_TOKEN set in .env.local
    - replicate package installed  (uv sync or pip install replicate)
"""

import asyncio
import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
import replicate
from dotenv import load_dotenv
from db.migrations import run_migrations

load_dotenv(".env.local")

REPLICATE_MODEL = "black-forest-labs/flux-1.1-pro"

SEED_IMAGES = [
    # --- Level 1: single object, plain background ---
    {
        "title": "Red Apple",
        "original_prompt": "A shiny red apple on a plain white table, bright studio lighting, photorealistic",
        "level": 1,
        "category": "objects",
    },
    {
        "title": "Blue Balloon",
        "original_prompt": "A single blue balloon floating against a clear blue sky, cheerful, cartoon style",
        "level": 1,
        "category": "objects",
    },
    # --- Level 2: simple scene, 2-3 elements ---
    {
        "title": "Sunny Hills",
        "original_prompt": "A cheerful yellow sun rising over rolling green hills with a blue sky, children's illustration style",
        "level": 2,
        "category": "nature",
    },
    {
        "title": "Rainy Frog",
        "original_prompt": "A small green frog sitting on a lily pad in the rain, raindrops visible, cartoon style",
        "level": 2,
        "category": "animals",
    },
    # --- Level 3: scene with action, 3-4 elements ---
    {
        "title": "Dog at Beach",
        "original_prompt": "A small brown dog running happily on a sandy beach with blue ocean waves behind it, golden hour lighting",
        "level": 3,
        "category": "animals",
    },
    # --- Level 4: complex scene with people and details ---
    {
        "title": "Indian Bazaar",
        "original_prompt": "A vibrant Indian street market with colourful fruit stalls, spice piles, and vendors wearing traditional clothes, busy and lively",
        "level": 4,
        "category": "culture",
    },
    # --- Level 5: complex with style, mood, and lighting ---
    {
        "title": "Neon City Night",
        "original_prompt": "A futuristic city at night with glowing neon lights reflecting on a wet street, a robot walking under an umbrella, cinematic lighting",
        "level": 5,
        "category": "fantasy",
    },
]


async def generate_image(prompt: str, retries: int = 3) -> str:
    """Call Replicate and return the generated image URL. Retries on 429."""
    for attempt in range(retries):
        try:
            output = await replicate.async_run(
                REPLICATE_MODEL,
                input={
                    "prompt": prompt,
                    "width": 1024,
                    "height": 1024,
                    "output_format": "webp",
                    "output_quality": 80,
                },
            )
            return str(output)
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s before retry {attempt + 2}/{retries}...")
                await asyncio.sleep(wait)
            else:
                raise


async def upsert_image(conn: asyncpg.Connection, entry: dict, image_url: str) -> None:
    """Upsert a seed image row (idempotent on original_prompt)."""
    await conn.execute(
        """
        INSERT INTO swys_images (title, original_prompt, image_url, level, category)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (original_prompt)
        DO UPDATE SET
            image_url = EXCLUDED.image_url,
            title     = EXCLUDED.title,
            level     = EXCLUDED.level,
            category  = EXCLUDED.category
        """,
        entry["title"],
        entry["original_prompt"],
        image_url,
        entry["level"],
        entry["category"],
    )


async def main() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set in .env.local")
        sys.exit(1)
    if not os.getenv("REPLICATE_API_TOKEN"):
        print("ERROR: REPLICATE_API_TOKEN not set in .env.local")
        sys.exit(1)

    # Run migrations so tables exist before we seed
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    print("Running migrations...")
    await run_migrations(pool)
    print("Migrations done.\n")

    conn = await pool.acquire()

    print(f"Seeding {len(SEED_IMAGES)} images into swys_images...\n")

    for i, entry in enumerate(SEED_IMAGES, 1):
        print(f"[{i}/{len(SEED_IMAGES)}] Generating: {entry['title']} (level {entry['level']})")
        print(f"  Prompt: {entry['original_prompt'][:70]}...")
        try:
            image_url = await generate_image(entry["original_prompt"])
            await upsert_image(conn, entry, image_url)
            print(f"  [OK] URL: {image_url[:80]}\n")
        except Exception as e:
            print(f"  [FAILED]: {e}\n")
            continue

        # Respect rate limit: wait 12s between successful requests
        if i < len(SEED_IMAGES):
            print("  Waiting 12s to respect rate limit...")
            await asyncio.sleep(12)

    await pool.release(conn)
    await pool.close()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
