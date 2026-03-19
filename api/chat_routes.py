"""
Sakhi — Chat API Routes (LangGraph-powered)
=============================================
REST endpoints for the text-based chat mode.

Endpoints:
  - ``POST /api/chat/send``    — stream a single assistant reply
  - ``POST /api/chat/history`` — retrieve the message history for a thread
  - ``POST /api/chat/end``     — summarise and persist a finished session

All LLM calls go through the centralized ``SakhiLLM`` layer via a LangGraph
``StateGraph``.  Conversation memory is backed by a PostgreSQL checkpointer.
"""

import json
import uuid
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.dependencies import require_profile_token
from services.profiles import get_current_profile
from services.chat_graph import get_chat_graph
from services.session_summarizer import summarize_session

logger = logging.getLogger("sakhi.api.chat")

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class ChatSendRequest(BaseModel):
    message: str
    thread_id: str | None = None
    mode: str = "default"           # "default" | "curious_open" | "curious_topic" | "curious_surprise"
    topic_id: str | None = None     # for curious_topic mode
    topic_title: str | None = None  # alternative to topic_id — passed directly from /start context
    topic_description: str | None = None
    surprise_fact: str | None = None  # for curious_surprise mode


class ChatHistoryRequest(BaseModel):
    thread_id: str


class EndSessionRequest(BaseModel):
    thread_id: str



# ---------------------------------------------------------------------------
# POST /api/chat/send — stream an assistant reply
# ---------------------------------------------------------------------------


async def _stream_graph_response(graph, user_message: str, config: dict):
    thread_id = config["configurable"]["thread_id"]
    yield f"data: {json.dumps({'type': 'thread_id', 'value': thread_id})}\n\n"

    try:
        async for event in graph.astream_events(
            {"messages": [{"role": "user", "content": user_message}]},
            config=config,
            version="v2",
        ):
            kind = event.get("event")

            # Debug — log every event type to see what's coming through
            logger.debug(f"Graph event: {kind} | name: {event.get('name')}")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield f"data: {json.dumps({'type': 'token', 'value': chunk.content})}\n\n"

            # # Fallback: full response if streaming tokens aren't firing
            # elif kind == "on_chat_model_end":
            #     output = event.get("data", {}).get("output")
            #     if output and hasattr(output, "content") and output.content:
            #         logger.warning("Streaming tokens missing — falling back to on_chat_model_end")
            #         yield f"data: {json.dumps({'type': 'token', 'value': output.content})}\n\n"

    except Exception as e:
        logger.error(f"Chat graph streaming error: {e}", exc_info=True)
        yield f"data: {json.dumps({'type': 'error', 'value': str(e)})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


@router.post("/send")
async def chat_send(req: ChatSendRequest, claims: dict = Depends(require_profile_token)):
    """Stream an assistant reply for a single user message.

    If ``thread_id`` is omitted, a new conversation thread is created.
    The thread_id is returned as the first SSE event so the frontend can
    persist it for subsequent requests.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can start chat sessions")

    profile = await get_current_profile(claims["profile_id"])

    child_name = profile.get("display_name", "buddy")
    child_age = profile.get("age") or 8
    child_language = "English"  # Defaulting for now

    thread_id = req.thread_id or str(uuid.uuid4())

    # Resolve topic context — prefer topic_id lookup, fall back to direct title/description
    topic_context = None
    if req.mode == "curious_topic":
        if req.topic_id:
            from services.topics import get_topic_by_id
            topic = get_topic_by_id(req.topic_id)
            if topic:
                topic_context = {"title": topic["title"], "description": topic["description"]}
        if not topic_context and req.topic_title:
            topic_context = {"title": req.topic_title, "description": req.topic_description or ""}

    logger.info(f"Chat send: mode={req.mode}, topic_context={topic_context}, thread={thread_id}")

    config = {
        "configurable": {
            "thread_id": thread_id,
            "child_name": child_name,
            "child_age": child_age,
            "child_language": child_language,
            "mode": req.mode,
            "topic_context": topic_context,
            "surprise_fact": req.surprise_fact,
        }
    }

    graph = get_chat_graph()

    return StreamingResponse(
        _stream_graph_response(graph, req.message, config),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# POST /api/chat/history — retrieve message history for a thread
# ---------------------------------------------------------------------------

@router.post("/history")
async def chat_history(req: ChatHistoryRequest, claims: dict = Depends(require_profile_token)):
    """Return the full message history for an existing thread.

    Loads the latest LangGraph checkpoint for the given ``thread_id``
    and returns de-serialised messages as a JSON list.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can access chat history")

    graph = get_chat_graph()
    config = {"configurable": {"thread_id": req.thread_id}}

    try:
        state = await graph.aget_state(config)
        if state is None or not state.values:
            return {"thread_id": req.thread_id, "messages": []}

        messages = []
        for msg in state.values.get("messages", []):
            messages.append({
                "role": msg.type if hasattr(msg, "type") else "unknown",
                "content": msg.content if hasattr(msg, "content") else str(msg),
            })

        return {"thread_id": req.thread_id, "messages": messages}

    except Exception as e:
        logger.error(f"Failed to load chat history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load chat history")


# ---------------------------------------------------------------------------
# POST /api/chat/end — session summarisation (unchanged)
# ---------------------------------------------------------------------------

class EndSessionRequest(BaseModel):
    thread_id: str
    mode: str = "default"


@router.post("/end")
async def end_chat_session(req: EndSessionRequest, claims: dict = Depends(require_profile_token)):
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can end chat sessions")

    try:
        # Fetch history from LangGraph checkpointer
        graph = get_chat_graph()
        config = {"configurable": {"thread_id": req.thread_id}}
        state = await graph.aget_state(config)

        messages = state.values.get("messages", []) if state and state.values else []
        transcript = [
            {"role": msg.type, "text": msg.content}
            for msg in messages
            if hasattr(msg, "type") and hasattr(msg, "content")
        ]
        turn_count = sum(1 for msg in messages if hasattr(msg, "type") and msg.type == "human")

        result = await summarize_session(
            profile_id=claims["profile_id"],
            room_name=req.thread_id,
            started_at=datetime.utcnow(),  # approximate if not tracked
            ended_at=datetime.utcnow(),
            transcript=transcript,
            turn_count=turn_count,
            mode=req.mode,
        )
        return {"status": "success", "summary": result}

    except Exception as e:
        logger.error(f"Failed to end chat session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save chat summary")