from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import time
import uuid
import logging
from datetime import datetime, timezone

from api.dependencies import require_profile_token
from services.profiles import get_current_profile
from groq import AsyncGroq
from services.session_summarizer import summarize_session

logger = logging.getLogger("sakhi.api.chat")

router = APIRouter(prefix="/api/chat", tags=["chat"])

# System prompt identical to the one in agent.py
SAKHI_SYSTEM_PROMPT = """\
You are Sakhi, a warm, curious, and encouraging AI companion for Indian children aged 4–12.

Personality:
- You are playful, patient, and full of wonder.
- You celebrate small wins enthusiastically.
- You speak in short, simple sentences appropriate for children.
- You are strictly child-safe — no violence, no inappropriate content, ever.

Teaching style:
- You NEVER give direct homework answers.
- You use Socratic questioning — ask guiding questions to help children discover answers themselves.
- When explaining concepts, use fun analogies, stories, and examples from everyday Indian life.

Conversation style:
- Keep responses concise (2-3 sentences max for young children, up to 4-5 for older ones).
- Use a cheerful, expressive tone.
- If the child seems confused, simplify and try a different approach.
- If the child seems sad or upset, be empathetic and supportive.

You are currently talking to {child_name}, who is {child_age} years old and prefers {child_language}.
Adjust your vocabulary and complexity to match their age.\
"""

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]

class EndSessionRequest(BaseModel):
    chat_session_id: str
    started_at: datetime
    ended_at: datetime
    transcript: list[dict] # Expected format: [{"role": "user"|"assistant", "text": "..."}]
    turn_count: int

async def stream_groq_response(messages: list[dict]):
    client = AsyncGroq()
    try:
        response = await client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            stream=True
        )
        async for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
    except Exception as e:
        logger.error(f"Groq streaming error: {e}")
        yield f"Oops, I had a little hiccup thinking about that! {str(e)}"

@router.post("/stream")
async def chat_stream(req: ChatRequest, claims: dict = Depends(require_profile_token)):
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can start chat sessions")

    profile = await get_current_profile(claims["profile_id"])

    child_name = profile.get("display_name", "buddy")
    child_age = profile.get("age") or 8
    child_language = "English" # Defaulting for now

    system_prompt = SAKHI_SYSTEM_PROMPT.format(
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
    )

    groq_messages = [{"role": "system", "content": system_prompt}]
    for msg in req.messages:
        groq_messages.append({"role": msg.role, "content": msg.content})

    return StreamingResponse(
        stream_groq_response(groq_messages), 
        media_type="text/event-stream"
    )

@router.post("/end")
async def end_chat_session(req: EndSessionRequest, claims: dict = Depends(require_profile_token)):
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can end chat sessions")

    try:
        result = await summarize_session(
            profile_id=claims["profile_id"],
            room_name=req.chat_session_id, # Re-using room_name field for chat_session_id
            started_at=req.started_at,
            ended_at=req.ended_at,
            transcript=req.transcript,
            turn_count=req.turn_count,
        )
        return {"status": "success", "summary": result}
    except Exception as e:
        logger.error(f"Failed to end chat session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save chat summary")
