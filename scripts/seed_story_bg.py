"""
Seed script: upload story background sounds to R2.
=====================================================
Uploads .mp3 files from ``seeds/story_bg/`` to R2 under the key prefix
``seeds/story_bg/<genre>.mp3``.

Usage:
    1. Place your 6 background sound files in seeds/story_bg/:
         adventure.mp3
         fable.mp3
         fantasy.mp3
         mystery.mp3
         comedy.mp3
         moral.mp3

    2. Run:
         python scripts/seed_story_bg.py

Requires:
    - R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_PUBLIC_URL
      set in .env.local
"""

import asyncio
import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from services.r2 import get_r2_client

load_dotenv(".env.local")
load_dotenv(".env")

SEEDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seeds", "story_bg")

EXPECTED_GENRES = ["adventure", "fable", "fantasy", "mystery", "comedy", "moral"]


async def main() -> None:
    # Validate local files exist
    files: list[tuple[str, str]] = []  # (genre, filepath)
    for genre in EXPECTED_GENRES:
        path = os.path.join(SEEDS_DIR, f"{genre}.mp3")
        if os.path.isfile(path):
            files.append((genre, path))
        else:
            print(f"  [SKIP] {genre}.mp3 not found in seeds/story_bg/")

    if not files:
        print("ERROR: No .mp3 files found in seeds/story_bg/. Add your files first.")
        sys.exit(1)

    # Init R2
    try:
        r2 = get_r2_client()
        print("R2 client initialized.\n")
    except Exception as e:
        print(f"ERROR: R2 not configured — {e}")
        print("Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_PUBLIC_URL in .env.local")
        sys.exit(1)

    print(f"Uploading {len(files)} background sound(s) to R2...\n")

    for genre, filepath in files:
        r2_key = f"seeds/story_bg/{genre}.mp3"
        file_size = os.path.getsize(filepath)
        print(f"  [{genre}] Uploading {file_size / 1024:.0f} KB -> {r2_key}")

        with open(filepath, "rb") as f:
            data = f.read()

        url = await r2.upload_bytes(data, r2_key, content_type="audio/mpeg")
        print(f"  [{genre}] OK {url}\n")

    # Print the mapping for frontend reference
    print("\n--- Frontend mapping (copy-paste) ---\n")
    print("const STORY_BG_SOUNDS = {")
    for genre, _ in files:
        r2_key = f"seeds/story_bg/{genre}.mp3"
        url = r2.public_url(r2_key)
        print(f'  {genre}: "{url}",')
    print("};")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
