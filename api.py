import json
import logging
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from pydantic import BaseModel

load_dotenv(".env.local")

logger = logging.getLogger("sakhi-api")

app = FastAPI(title="Sakhi Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TokenRequest(BaseModel):
    """Request body for generating a LiveKit room token."""

    child_name: str = "buddy"
    child_age: int = 8
    child_language: str = "English"


class TokenResponse(BaseModel):
    """Response with the LiveKit token and room details."""

    token: str
    room_name: str
    livekit_url: str


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "sakhi-backend", "timestamp": time.time()}


@app.post("/api/token", response_model=TokenResponse)
async def create_token(req: TokenRequest):
    """Generate a LiveKit room token for a child session.

    The frontend calls this to get credentials before connecting to LiveKit.
    Room metadata carries the child's profile so the agent can personalize.
    """
    livekit_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not all([livekit_url, api_key, api_secret]):
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    # Each child gets a unique room
    room_name = f"sakhi-{req.child_name.lower().replace(' ', '-')}-{int(time.time())}"

    # Room metadata carries child profile for the agent to read
    room_metadata = json.dumps(
        {
            "child_name": req.child_name,
            "child_age": req.child_age,
            "child_language": req.child_language,
        }
    )

    # Generate access token
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"child-{req.child_name.lower().replace(' ', '-')}")
        .with_name(req.child_name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
            )
        )
        .with_metadata(room_metadata)
    )

    # Create room + dispatch the Sakhi agent to it
    try:
        lkapi = api.LiveKitAPI()

        # Create the room first
        await lkapi.room.create_room(
            api.CreateRoomRequest(name=room_name, metadata=room_metadata)
        )

        # Explicitly dispatch the agent to the room
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="sakhi-agent",
                room=room_name,
            )
        )

        await lkapi.aclose()
        logger.info(f"Room created and agent dispatched: {room_name}")
    except Exception as e:
        logger.error(f"Failed to dispatch agent: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to dispatch agent: {e}")

    return TokenResponse(
        token=token.to_jwt(),
        room_name=room_name,
        livekit_url=livekit_url,
    )
