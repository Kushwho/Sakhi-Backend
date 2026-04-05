"""
Sakhi — Chat API Routes (LangGraph-powered)
=============================================
REST endpoints for the text-based chat mode.

Endpoints:
  - ``POST /api/chat/send``               — stream a single assistant reply
  - ``POST /api/chat/history``            — retrieve message history for a thread (by thread_id)
  - ``POST /api/chat/end``               — summarise and persist a finished session
  - ``POST /api/chat/generate-image``    — rate-limited in-chat image generation
  - ``GET  /api/chat/sessions``          — list all past sessions for the child
  - ``GET  /api/chat/sessions/{id}``     — read a specific past session's transcript

All LLM calls go through the centralized ``SakhiLLM`` layer via a LangGraph
``StateGraph``.  Conversation memory is backed by a PostgreSQL checkpointer.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.dependencies import require_profile_token
from services.chat_graph import get_chat_graph
from services.chat_sessions import get_chat_session, list_chat_sessions
from services.dashboard import invalidate_dashboard_cache
from services.profiles import get_current_profile
from services.session_summarizer import summarize_session

logger = logging.getLogger("sakhi.api.chat")

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class ChatSendRequest(BaseModel):
    message: str
    thread_id: str | None = None
    mode: str = "default"  # "default" | "curious_open" | "curious_topic" | "curious_surprise"
    topic_id: str | None = None  # for curious_topic mode
    topic_title: str | None = None  # alternative to topic_id — passed directly from /start context
    topic_description: str | None = None
    surprise_fact: str | None = None  # for curious_surprise mode


class ChatHistoryRequest(BaseModel):
    thread_id: str


class EndSessionRequest(BaseModel):
    thread_id: str
    mode: str = "default"


class GenerateChatImageRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"  # "1:1" | "16:9" | "4:3"


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

            logger.debug(f"Graph event: {kind} | name: {event.get('name')}")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield f"data: {json.dumps({'type': 'token', 'value': chunk.content})}\n\n"

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

    Reads the stored transcript from the ``session_summaries`` table
    (keyed by ``room_name`` = ``thread_id``).  This is more reliable than
    reading the LangGraph checkpoint, which can be lost on serverless DB
    connection drops.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can access chat history")

    import uuid as _uuid
    from db.pool import get_pool

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT transcript
                FROM session_summaries
                WHERE room_name = $1 AND profile_id = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                req.thread_id,
                _uuid.UUID(claims["profile_id"]),
            )

        if row is None or not row["transcript"]:
            return {"thread_id": req.thread_id, "messages": []}

        import json as _json
        raw = row["transcript"]
        transcript = _json.loads(raw) if isinstance(raw, str) else raw

        # Normalise transcript format:
        #   DB stores: [{role: "human"/"ai", text: "..."}]
        #   Frontend expects: [{role: "user"/"assistant", content: "..."}]
        role_map = {"human": "user", "ai": "assistant"}
        messages = [
            {
                "role": role_map.get(m.get("role", ""), m.get("role", "unknown")),
                "content": m.get("text") or m.get("content", ""),
            }
            for m in transcript
            if isinstance(m, dict)
        ]

        return {"thread_id": req.thread_id, "messages": messages}

    except Exception as e:
        logger.error(f"Failed to load chat history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load chat history") from e


# ---------------------------------------------------------------------------
# POST /api/chat/end — session summarisation
# ---------------------------------------------------------------------------


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
            started_at=datetime.now(timezone.utc),  # approximate if not tracked
            ended_at=datetime.now(timezone.utc),
            transcript=transcript,
            turn_count=turn_count,
            mode=req.mode,
        )

        # Evict the parent's dashboard cache for this child so the next
        # dashboard load shows the freshly-written session data immediately.
        invalidate_dashboard_cache(claims["profile_id"])

        return {"status": "success", "summary": result}

    except Exception as e:
        logger.error(f"Failed to end chat session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save chat summary") from e


# ---------------------------------------------------------------------------
# POST /api/chat/generate-image — rate-limited in-chat image generation
# ---------------------------------------------------------------------------


@router.post("/generate-image")
async def generate_chat_image(
    req: GenerateChatImageRequest,
    claims: dict = Depends(require_profile_token),
):
    """Generate one image for use inside the chat flow.

    Each child profile is limited to ``CHAT_IMAGE_DAILY_LIMIT`` images per UTC
    calendar day (default: 3).  When the quota is exhausted this endpoint
    returns HTTP 429.  If image generation itself fails, HTTP 502 is returned.

    Request body:
        prompt (str): Description of the image to generate.
        aspect_ratio (str): "1:1" (default), "16:9", or "4:3".

    Returns:
        {"image_url": str, "remaining_today": int}
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(
            status_code=403, detail="Only child profiles can generate images"
        )

    from services.chat_image_service import QuotaExceededError
    from services.chat_image_service import generate_chat_image as _generate

    try:
        result = await _generate(
            profile_id=claims["profile_id"],
            prompt=req.prompt,
            aspect_ratio=req.aspect_ratio,
        )
        return result

    except QuotaExceededError:
        raise HTTPException(
            status_code=429,
            detail="Daily image limit reached. Try again tomorrow!",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Chat image generation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="Image generation failed. Please try again."
        )


# ---------------------------------------------------------------------------
# GET /api/chat/sessions — list all past sessions for this child
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100, description="Max sessions to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    claims: dict = Depends(require_profile_token),
) -> dict:
    """Return a paginated list of all past chat sessions for the child.

    Sessions are ordered newest-first.  Each item includes the ``thread_id``
    which can be passed to ``POST /api/chat/send`` to continue the
    conversation exactly where it left off.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can list chat sessions")

    sessions = await list_chat_sessions(
        profile_id=claims["profile_id"],
        limit=limit,
        offset=offset,
    )
    return {"sessions": sessions, "total": len(sessions), "offset": offset}


# ---------------------------------------------------------------------------
# GET /api/chat/sessions/{session_id} — read a specific past session
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    claims: dict = Depends(require_profile_token),
) -> dict:
    """Return the full detail of a past chat session including the stored transcript.

    The ``thread_id`` in the response can be used to resume the conversation
    via ``POST /api/chat/send``.

    Returns 404 if the session does not exist or belongs to another profile.
    """
    if claims.get("profile_type") != "child":
        raise HTTPException(status_code=403, detail="Only child profiles can read chat sessions")

    session = await get_chat_session(
        session_id=session_id,
        profile_id=claims["profile_id"],
    )

    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return session