import asyncio
import httpx
import uuid

BASE_URL = "http://localhost:8000"

async def test_chat_stream():
    # Verify server is running
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BASE_URL}/api/health")
            if resp.status_code != 200:
                print(f"Health check failed with status: {resp.status_code}")
                return
    except Exception as e:
        print(f"Server is not reachable at {BASE_URL}: {e}")
        return

    async with httpx.AsyncClient() as client:
        # 1. Signup and get account_token
        unique_id = uuid.uuid4().hex[:8]
        email = f"test_chat_{unique_id}@example.com"
        print(f"1. Signing up new account: {email}")
        resp = await client.post(f"{BASE_URL}/auth/signup", json={
            "email": email,
            "password": "password123",
            "family_name": "TestFamily"
        })
        assert resp.status_code == 201, f"Signup failed: {resp.text}"
        account_token = resp.json()["account_token"]

        # 2. Create a Child Profile
        print("2. Creating child profile")
        headers = {"Authorization": f"Bearer {account_token}"}
        resp = await client.post(f"{BASE_URL}/auth/profiles", json={
            "display_name": "TestBuddy",
            "age": 8
        }, headers=headers)
        assert resp.status_code == 201, f"Create profile failed: {resp.text}"
        profile_id = resp.json()["id"]

        # 3. Enter Profile to get profile_token
        print("3. Entering child profile")
        resp = await client.post(f"{BASE_URL}/auth/profiles/{profile_id}/enter", json={}, headers=headers)
        assert resp.status_code == 200, f"Enter profile failed: {resp.text}"
        profile_token = resp.json()["profile_token"]

        # 4. Test Chat Stream
        print("4. Testing Chat Stream with 'Hi Sakhi, what is 2+2?'")
        chat_headers = {"Authorization": f"Bearer {profile_token}"}
        chat_payload = {
            "messages": [
                {"role": "user", "content": "Hi Sakhi, what is 2+2?"}
            ]
        }
        
        # Test the stream endpoint
        async with client.stream("POST", f"{BASE_URL}/api/chat/stream", json=chat_payload, headers=chat_headers) as response:
            assert response.status_code == 200, f"Chat stream failed: {response.text}"
            print("\nResponse: \n--------------------")
            full_response = ""
            async for chunk in response.aiter_text():
                print(chunk, end="", flush=True)
                full_response += chunk
            print("\n--------------------\nStream finished successfully.")
            assert len(full_response) > 0, "No content returned from stream"


if __name__ == "__main__":
    asyncio.run(test_chat_stream())
