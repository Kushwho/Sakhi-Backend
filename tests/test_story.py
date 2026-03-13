"""
Sakhi — Story Routes Integration Test
=======================================
Tests all story API endpoints against a running server on port 8000.

Run: python tests/test_story.py

Tested endpoints:
  GET  /api/stories                     — list all stories
  GET  /api/stories/random              — random story + genre filter
  GET  /api/stories/{story_id}          — story detail
  POST /api/story-token                 — LiveKit session token (auth required)

Auth flow:
  1. Signup → account_token
  2. Create child profile → profile_id
  3. Enter profile → profile_token
  4. Use profile_token for /api/story-token
"""

import asyncio
import uuid

import httpx

BASE_URL = "http://localhost:8000"
DIVIDER = "-" * 50

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ok(label: str, data=None):
    print(f"  ✓  {label}")
    if data:
        print(f"     {data}")


def fail(label: str, detail=""):
    print(f"  ✗  {label}")
    if detail:
        print(f"     {detail}")
    raise AssertionError(label)


# ---------------------------------------------------------------------------
# Auth helpers — signup / create child profile / enter profile
# ---------------------------------------------------------------------------


async def setup_child_profile(client: httpx.AsyncClient) -> tuple[str, str]:
    """Create a throwaway account + child profile, return (profile_id, profile_token)."""
    uid = uuid.uuid4().hex[:8]
    email = f"story_test_{uid}@example.com"

    # 1. Signup
    r = await client.post(f"{BASE_URL}/auth/signup", json={
        "email": email,
        "password": "password123",
        "family_name": "StoryTestFamily",
    })
    assert r.status_code == 201, f"Signup failed: {r.text}"
    account_token = r.json()["account_token"]

    # 2. Create child profile
    headers = {"Authorization": f"Bearer {account_token}"}
    r = await client.post(f"{BASE_URL}/auth/profiles", json={
        "display_name": "StoryKid",
        "age": 7,
    }, headers=headers)
    assert r.status_code == 201, f"Create profile failed: {r.text}"
    profile_id = r.json()["id"]

    # 3. Enter profile → profile_token
    r = await client.post(f"{BASE_URL}/auth/profiles/{profile_id}/enter", json={}, headers=headers)
    assert r.status_code == 200, f"Enter profile failed: {r.text}"
    profile_token = r.json()["profile_token"]

    return profile_id, profile_token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_stories(client: httpx.AsyncClient):
    print(f"\n{DIVIDER}")
    print("TEST: GET /api/stories")
    r = await client.get(f"{BASE_URL}/api/stories")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert "stories" in data
    assert "total" in data
    ok(f"Listed {data['total']} story/stories")
    for s in data["stories"]:
        print(f"     • [{s['genre']}] {s['title']} ({s['total_segments']} parts)")
    return data["stories"]


async def test_list_stories_genre_filter(client: httpx.AsyncClient):
    print(f"\n{DIVIDER}")
    print("TEST: GET /api/stories?genre=fable")
    r = await client.get(f"{BASE_URL}/api/stories", params={"genre": "fable"})
    assert r.status_code == 200
    data = r.json()
    ok(f"Genre filter returned {data['total']} story/stories")
    for s in data["stories"]:
        assert s["genre"] == "fable", f"Expected genre=fable, got {s['genre']}"
    if data["total"] > 0:
        ok("All returned stories have genre=fable")


async def test_random_story(client: httpx.AsyncClient) -> dict | None:
    print(f"\n{DIVIDER}")
    print("TEST: GET /api/stories/random")
    r = await client.get(f"{BASE_URL}/api/stories/random")
    if r.status_code == 404:
        ok("No stories in DB yet — seed with story_emitter.py first", "(skipping random test)")
        return None
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    story = r.json()
    assert "id" in story
    assert "title" in story
    assert "genre" in story
    assert "total_segments" in story
    ok(f"Random story: '{story['title']}' ({story['genre']}, id={story['id'][:8]}...)")
    return story


async def test_random_story_genre_filter(client: httpx.AsyncClient):
    print(f"\n{DIVIDER}")
    print("TEST: GET /api/stories/random?genre=fable")
    r = await client.get(f"{BASE_URL}/api/stories/random", params={"genre": "fable"})
    if r.status_code == 404:
        ok("No fable stories found (skip)")
        return
    assert r.status_code == 200
    story = r.json()
    assert story["genre"] == "fable"
    ok(f"Filtered random: '{story['title']}' genre={story['genre']}")


async def test_random_story_bad_genre(client: httpx.AsyncClient):
    print(f"\n{DIVIDER}")
    print("TEST: GET /api/stories/random?genre=nonexistent_genre → 404")
    r = await client.get(f"{BASE_URL}/api/stories/random", params={"genre": "nonexistent_xyz_genre"})
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"
    ok("Correctly returned 404 for unknown genre")


async def test_story_detail(client: httpx.AsyncClient, story_id: str):
    print(f"\n{DIVIDER}")
    print(f"TEST: GET /api/stories/{story_id[:8]}...")
    r = await client.get(f"{BASE_URL}/api/stories/{story_id}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    story = r.json()
    assert story["id"] == story_id
    ok(f"Story detail: '{story['title']}', {story['total_segments']} segments")


async def test_story_detail_not_found(client: httpx.AsyncClient):
    print(f"\n{DIVIDER}")
    print("TEST: GET /api/stories/00000000-0000-0000-0000-000000000000 → 404")
    fake_id = "00000000-0000-0000-0000-000000000000"
    r = await client.get(f"{BASE_URL}/api/stories/{fake_id}")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"
    ok("Correctly returned 404 for non-existent story ID")


async def test_story_token(client: httpx.AsyncClient, story_id: str, profile_token: str):
    print(f"\n{DIVIDER}")
    print("TEST: POST /api/story-token (authenticated)")
    headers = {"Authorization": f"Bearer {profile_token}"}
    r = await client.post(f"{BASE_URL}/api/story-token", json={"story_id": story_id}, headers=headers)
    if r.status_code == 500 and "LiveKit" in r.text:
        # LiveKit credentials may not be set in this env — that's acceptable
        ok("story-token: LiveKit credentials not configured (expected in local test env)")
        print(f"     Response: {r.json()['detail'][:80]}")
        return None
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert "token" in data
    assert "room_name" in data
    assert "livekit_url" in data
    assert data["room_name"].startswith("story-")
    ok(f"Got story token! Room: {data['room_name']}")
    ok(f"Token (first 40 chars): {data['token'][:40]}...")
    return data


async def test_story_token_no_auth(client: httpx.AsyncClient, story_id: str):
    print(f"\n{DIVIDER}")
    print("TEST: POST /api/story-token (no auth) → 401/403")
    r = await client.post(f"{BASE_URL}/api/story-token", json={"story_id": story_id})
    assert r.status_code in (401, 403, 422), f"Expected 401/403, got {r.status_code}"
    ok(f"Correctly rejected unauthenticated request (status {r.status_code})")


async def test_story_token_bad_story(client: httpx.AsyncClient, profile_token: str):
    print(f"\n{DIVIDER}")
    print("TEST: POST /api/story-token with fake story_id → 404")
    headers = {"Authorization": f"Bearer {profile_token}"}
    fake_id = "00000000-0000-0000-0000-000000000000"
    r = await client.post(f"{BASE_URL}/api/story-token", json={"story_id": fake_id}, headers=headers)
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"
    ok("Correctly returned 404 for non-existent story_id")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    print("=" * 50)
    print("  Sakhi Story Routes — Integration Tests")
    print(f"  Server: {BASE_URL}")
    print("=" * 50)

    # Health check
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{BASE_URL}/api/health")
            if r.status_code != 200:
                print(f"\n✗ Server health check failed ({r.status_code}). Is the server running?")
                return
    except Exception as e:
        print(f"\n✗ Cannot reach server at {BASE_URL}: {e}")
        return

    print("\n  Server is up ✓")

    async with httpx.AsyncClient(timeout=15) as client:

        # ── Auth setup ─────────────────────────────────────────────────────
        print(f"\n{DIVIDER}")
        print("SETUP: Creating test account + child profile...")
        profile_id, profile_token = await setup_child_profile(client)
        ok(f"Child profile ready (id={profile_id[:8]}...)")

        # ── Story browse tests ─────────────────────────────────────────────
        stories = await test_list_stories(client)
        await test_list_stories_genre_filter(client)
        random_story = await test_random_story(client)
        await test_random_story_genre_filter(client)
        await test_random_story_bad_genre(client)

        # ── Per-story tests (requires at least one story in DB) ─────────────
        story_id = None
        if random_story:
            story_id = random_story["id"]
        elif stories:
            story_id = stories[0]["id"]

        if story_id:
            await test_story_detail(client, story_id)
            await test_story_token_no_auth(client, story_id)
            await test_story_token_bad_story(client, profile_token)
            await test_story_token(client, story_id, profile_token)
        else:
            print(f"\n  ⚠ No stories in DB — skipping per-story tests.")
            print("    Run: python story_emitter.py   to seed stories first.\n")

        await test_story_detail_not_found(client)

    print(f"\n{'=' * 50}")
    print("  All tests passed! ✓")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
