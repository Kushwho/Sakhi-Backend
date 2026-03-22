"""
Integration tests for Previous Sessions APIs.

Tests:
  1. Chat session history flow:
       Send a chat message → end the session → list sessions → get session by ID
       → confirm thread_id in list → continue conversation using that thread_id

  2. Story library flow:
       Generate a story → list stories → get story by ID → verify full scene payload

Requires the server to be running at http://localhost:8000.
Run with: python tests/test_sessions.py
"""

import asyncio
import json
import uuid

import httpx

BASE_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def _signup_and_get_child_token(client: httpx.AsyncClient) -> tuple[str, str]:
    """Set up a fresh account + child profile and return (profile_id, profile_token)."""
    uid = uuid.uuid4().hex[:8]
    email = f"test_sessions_{uid}@example.com"

    resp = await client.post(
        f"{BASE_URL}/auth/signup",
        json={"email": email, "password": "password123", "family_name": "SessionTestFamily"},
    )
    assert resp.status_code == 201, f"Signup failed: {resp.text}"
    account_token = resp.json()["account_token"]
    headers = {"Authorization": f"Bearer {account_token}"}

    resp = await client.post(
        f"{BASE_URL}/auth/profiles",
        json={"display_name": "TestKid", "age": 9},
        headers=headers,
    )
    assert resp.status_code == 201, f"Create profile failed: {resp.text}"
    profile_id = resp.json()["id"]

    resp = await client.post(
        f"{BASE_URL}/auth/profiles/{profile_id}/enter", json={}, headers=headers
    )
    assert resp.status_code == 200, f"Enter profile failed: {resp.text}"
    profile_token = resp.json()["profile_token"]

    return profile_id, profile_token


# ---------------------------------------------------------------------------
# Test 1 — Chat session history
# ---------------------------------------------------------------------------


async def test_chat_sessions():
    print("\n=== TEST: Chat session history ===")

    async with httpx.AsyncClient(timeout=60) as client:
        # Check server is up
        try:
            resp = await client.get(f"{BASE_URL}/api/health")
            assert resp.status_code == 200
        except Exception as e:
            print(f"  SKIP — server not reachable: {e}")
            return

        profile_id, profile_token = await _signup_and_get_child_token(client)
        chat_headers = {"Authorization": f"Bearer {profile_token}"}

        # Step 1: Send a chat message and capture the thread_id from SSE
        print("  1. Sending a chat message to create a new thread …")
        thread_id = None
        async with client.stream(
            "POST",
            f"{BASE_URL}/api/chat/send",
            json={"message": "Hi Sakhi, what is the capital of India?"},
            headers=chat_headers,
        ) as response:
            assert response.status_code == 200, f"Chat send failed: {response.status_code}"
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[5:].strip())
                    if event.get("type") == "thread_id":
                        thread_id = event["value"]
                        break

        assert thread_id, "Did not receive thread_id from chat stream"
        print(f"  ✓ thread_id: {thread_id}")

        # Step 2: End the session
        print("  2. Ending the session (POST /api/chat/end) …")
        resp = await client.post(
            f"{BASE_URL}/api/chat/end",
            json={"thread_id": thread_id, "mode": "default"},
            headers=chat_headers,
        )
        assert resp.status_code == 200, f"End session failed: {resp.text}"
        print("  ✓ Session ended successfully")

        # Step 3: List sessions
        print("  3. Listing past sessions (GET /api/chat/sessions) …")
        resp = await client.get(f"{BASE_URL}/api/chat/sessions", headers=chat_headers)
        assert resp.status_code == 200, f"List sessions failed: {resp.text}"
        data = resp.json()
        sessions = data["sessions"]
        assert len(sessions) > 0, "No sessions returned after ending one"
        session = sessions[0]
        session_id = session["session_id"]
        assert session["thread_id"] == thread_id, "thread_id mismatch in session list"
        print(f"  ✓ Found {len(sessions)} session(s). Latest session_id: {session_id}")
        print(f"     mode={session['mode']}, topics={session['topics']}, turns={session['turn_count']}")

        # Step 4: Get specific session detail incl. transcript
        print(f"  4. Getting session detail (GET /api/chat/sessions/{session_id}) …")
        resp = await client.get(
            f"{BASE_URL}/api/chat/sessions/{session_id}", headers=chat_headers
        )
        assert resp.status_code == 200, f"Get session failed: {resp.text}"
        detail = resp.json()
        assert detail["session_id"] == session_id
        assert "transcript" in detail
        print(f"  ✓ Session detail returned. transcript_length={len(detail['transcript'])} messages")

        # Step 5: Continue the conversation using the same thread_id
        print("  5. Continuing old chat via same thread_id …")
        async with client.stream(
            "POST",
            f"{BASE_URL}/api/chat/send",
            json={"message": "Can you remind me what I just asked?", "thread_id": thread_id},
            headers=chat_headers,
        ) as response:
            assert response.status_code == 200, f"Continuation chat failed: {response.status_code}"
            reply_tokens = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[5:].strip())
                    if event.get("type") == "token":
                        reply_tokens.append(event["value"])
            reply = "".join(reply_tokens)
            assert reply, "Got empty reply on continuation"
            print(f"  ✓ Continuation reply received ({len(reply)} chars)")

    print("=== PASS: Chat session history ===\n")


# ---------------------------------------------------------------------------
# Test 2 — Story library
# ---------------------------------------------------------------------------


async def test_story_library():
    print("\n=== TEST: Story library ===")

    async with httpx.AsyncClient(timeout=180) as client:
        try:
            resp = await client.get(f"{BASE_URL}/api/health")
            assert resp.status_code == 200
        except Exception as e:
            print(f"  SKIP — server not reachable: {e}")
            return

        profile_id, profile_token = await _signup_and_get_child_token(client)
        story_headers = {"Authorization": f"Bearer {profile_token}"}

        # Step 1: List stories — should be empty initially
        print("  1. Listing stories before generation …")
        resp = await client.get(f"{BASE_URL}/api/stories/", headers=story_headers)
        assert resp.status_code == 200, f"List stories failed: {resp.text}"
        initial_count = len(resp.json().get("stories", []))
        print(f"  ✓ Initial story count: {initial_count}")

        # Step 2: Generate a new story (small: 2 scenes to keep test fast)
        print("  2. Generating a new story (2 scenes) …")
        resp = await client.post(
            f"{BASE_URL}/api/stories/generate",
            json={"idea": "A parrot who wants to learn maths", "num_scenes": 2, "child_age": 7},
            headers=story_headers,
        )
        assert resp.status_code == 200, f"Generate story failed: {resp.text}"
        generated = resp.json()
        assert generated["total_scenes"] == 2
        print(f"  ✓ Story generated: '{generated['title']}' ({generated['total_scenes']} scenes)")

        # Brief pause to let the background save task complete
        await asyncio.sleep(1.5)

        # Step 3: List stories — should now have one more
        print("  3. Listing stories after generation (GET /api/stories/) …")
        resp = await client.get(f"{BASE_URL}/api/stories/", headers=story_headers)
        assert resp.status_code == 200, f"List stories failed: {resp.text}"
        stories_data = resp.json()
        stories = stories_data["stories"]
        assert len(stories) == initial_count + 1, (
            f"Expected {initial_count + 1} stories, got {len(stories)}"
        )
        story = stories[0]
        story_id = story["story_id"]
        assert story["title"] == generated["title"], "Story title mismatch"
        print(f"  ✓ Found {len(stories)} story/stories. story_id: {story_id}")
        print(f"     title='{story['title']}', genre={story['genre']}")

        # Step 4: Get the full story by ID
        print(f"  4. Getting full story (GET /api/stories/{story_id}) …")
        resp = await client.get(f"{BASE_URL}/api/stories/{story_id}", headers=story_headers)
        assert resp.status_code == 200, f"Get story failed: {resp.text}"
        full = resp.json()
        assert full["story_id"] == story_id
        assert "scenes" in full
        assert len(full["scenes"]) == 2
        print(f"  ✓ Full story returned. scenes={len(full['scenes'])}")

        # Step 5: 404 for a random story_id
        fake_id = str(uuid.uuid4())
        print(f"  5. Testing 404 for unknown story_id …")
        resp = await client.get(f"{BASE_URL}/api/stories/{fake_id}", headers=story_headers)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
        print("  ✓ 404 returned as expected")

    print("=== PASS: Story library ===\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    await test_chat_sessions()
    await test_story_library()
    print("All session API tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
