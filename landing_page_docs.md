# Sakhi Voice Bot & Emotion Detection — Technical Implementation

> **Audience:** Engineers building the Sakhi landing page, and parents who want to understand the technology powering Sakhi.
> **Scope:** End-to-end architecture of the Sakhi voice agent, Hume AI emotion detection integration, and how they work together in a live voice session.

---

## Table of Contents

1. [What is Sakhi?](#1-what-is-sakhi)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Voice Pipeline — How Sakhi Listens and Speaks](#3-voice-pipeline--how-sakhi-listens-and-speaks)
4. [Emotion Detection with Hume AI](#4-emotion-detection-with-hume-ai)
5. [How Emotion Shapes Sakhi's Responses](#5-how-emotion-shapes-sakhis-responses)
6. [Session Lifecycle — From Login to Goodbye](#6-session-lifecycle--from-login-to-goodbye)
7. [The Parent Dashboard — What Gets Recorded](#7-the-parent-dashboard--what-gets-recorded)
8. [Database Schema](#8-database-schema)
9. [API Reference](#9-api-reference)
10. [Environment Variables & Configuration](#10-environment-variables--configuration)
11. [Landing Page Integration Guide](#11-landing-page-integration-guide)

---

## 1. What is Sakhi?

Sakhi is a **voice-first AI companion for Indian children aged 4–12**. Children talk to Sakhi naturally — Sakhi listens, understands, and responds in a warm, age-appropriate way. Sakhi never gives direct homework answers; it guides children through Socratic questioning, turning learning into a conversation.

At the same time, Sakhi listens to *how* a child is speaking — not just what they say. Using Hume AI's prosody analysis, Sakhi detects the child's emotional state in real time (joy, sadness, anxiety, excitement, confusion, etc.) and:

- **Adapts its tone and words** to match the child's emotional state — more supportive when sad, more celebratory when excited.
- **Sends emotional data to the parent dashboard** so parents can understand their child's emotional wellbeing over time.
- **Drives the avatar's facial expressions** on the child's screen — Sakhi's face lights up, looks concerned, or celebrates alongside the child.

---

## 2. System Architecture Overview

Sakhi's backend consists of **three independent processes** that all join the same LiveKit room simultaneously when a child starts a session.

```
┌─────────────────────────────────────────────────────────┐
│                    LiveKit Room                          │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Child App   │  │ Sakhi Agent  │  │  Emotion     │  │
│  │  (Frontend)  │  │ (sakhi.py)   │  │  Detector    │  │
│  │              │  │              │  │ (emotion_    │  │
│  │  Publishes   │  │  STT→LLM→TTS │  │  detector.py)│  │
│  │  audio track │  │  pipeline    │  │              │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │           │
│         └─────────────────┴─────────────────┘           │
│                      Audio + RPC                         │
└─────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
  FastAPI Backend                       PostgreSQL (Neon)
  (api/routes.py)                   emotion_snapshots
  Token Generation                  session_summaries
  Auth System                       alerts
  Dashboard APIs
```

### Key Technology Choices

| Layer | Technology | Purpose |
|---|---|---|
| Real-time Transport | **LiveKit Cloud** (WebRTC) | Audio streaming between child app and agents |
| Speech-to-Text | **Deepgram Nova-3** | Multilingual STT (Hindi, Tamil, Telugu, Kannada, Marathi, Bengali, English) |
| Language Model | **Groq / Llama-3.1-8b-instant** | Low-latency conversational AI |
| Text-to-Speech | **Deepgram Aura-2 (Asteria)** | Child-friendly expressive voice output |
| Voice Activity Detection | **Silero VAD** | Detects when child starts/stops speaking |
| Turn Detection | **LiveKit MultilingualModel** | Handles children pausing mid-thought |
| Emotion Detection | **Hume AI Expression Measurement** | Real-time prosody-based emotion from audio |
| Backend API | **FastAPI** | Token generation, auth, dashboard endpoints |
| Database | **PostgreSQL (Neon)** | Persistent storage for sessions, emotions, alerts |

---

## 3. Voice Pipeline — How Sakhi Listens and Speaks

### 3.1 Session Start

When a child opens the Sakhi app and their profile is selected:

1. **The frontend calls `POST /api/token`** with a profile JWT in the `Authorization` header.
2. The backend validates the token, fetches the child's name and age from the database, and:
   - Creates a unique LiveKit room (format: `sakhi-<child-name>-<unix-timestamp>`)
   - Embeds the child's profile data in the **room metadata** as JSON
   - Dispatches the **Sakhi voice agent** (`sakhi-agent`) into the room
   - Dispatches the **emotion detector** (`emotion-detector`) into the room
   - Returns a signed **LiveKit access token** to the frontend
3. The frontend uses this token to join the LiveKit room and start streaming audio.

```python
# api/routes.py — room creation and agent dispatch
room_metadata = json.dumps({
    "child_name": child_name,
    "child_age": child_age,
    "child_language": "English",
    "profile_id": claims["profile_id"],
})

# Voice agent dispatch
await lkapi.agent_dispatch.create_dispatch(
    api.CreateAgentDispatchRequest(agent_name="sakhi-agent", room=room_name)
)

# Emotion detector dispatch (separate programmatic participant)
await lkapi.agent_dispatch.create_dispatch(
    api.CreateAgentDispatchRequest(agent_name="emotion-detector", room=room_name)
)
```

### 3.2 The Voice Pipeline (STT → LLM → TTS)

The Sakhi agent builds a `AgentSession` from the LiveKit Agents SDK that chains four components:

```python
# agents/sakhi.py — voice pipeline construction
session = AgentSession(
    stt=deepgram.STT(model="nova-3", language="multi"),  # auto-detect Indian languages
    llm=groq.LLM(model="llama-3.1-8b-instant"),          # fast, cheap LLM
    tts=deepgram.TTS(model="aura-2-asteria-en"),          # expressive child-friendly voice
    vad=silero.VAD.load(),                                # voice activity detection
    turn_detection=MultilingualModel(),                   # multilingual turn detection
)
```

**Audio flow:**
```
Child speaks → Silero VAD detects speech → Deepgram Nova-3 transcribes
    → Groq Llama-3.1 generates response → Deepgram Aura-2 synthesizes speech
    → LiveKit streams audio back to child
```

### 3.3 Child Personalization

The `SakhiAgent` reads the child's name, age, and language from room metadata and injects them into its system prompt:

```
You are Sakhi, a warm, curious, and encouraging AI companion for Indian children aged 4–12.
...
You are currently talking to {child_name}, who is {child_age} years old and prefers {child_language}.
Adjust your vocabulary and complexity to match their age.
```

The agent also maintains **short-term memory** via `ChatContext` — it remembers what was said earlier in the conversation, giving Sakhi continuity within a session.

### 3.4 The `explain_concept` Tool

When a child asks about a school topic, the agent calls its `explain_concept` function tool instead of answering directly. This is a stub for a CBSE/ICSE curriculum RAG system:

```python
@function_tool()
async def explain_concept(self, context: RunContext, concept: str, subject: str) -> str:
    """Explain a school concept to help the child learn."""
    # TODO: Connect to CBSE/ICSE curriculum RAG system
    return f"Let me help you understand {concept} in {subject}! ..."
```

---

## 4. Emotion Detection with Hume AI

### 4.1 The Emotion Detector — A Separate Programmatic Participant

The emotion detector is **not part of the Sakhi voice agent**. It is a completely separate LiveKit programmatic participant (`emotion-detector`) that joins the same room. This architectural separation means:

- Emotion detection can fail or restart without affecting the voice pipeline.
- The voice agent stays focused on conversation; emotion is a separate concern.
- Each participant runs independently and can be scaled or updated separately.

### 4.2 Real-Time Audio Analysis

Once connected, the emotion detector subscribes to the **child's audio track** (not the agent's). It buffers audio frames until it has approximately 3 seconds of audio (~288 KB of raw PCM at 48kHz, 16-bit mono), then sends that buffer to Hume for prosody analysis.

```python
# agents/emotion_detector.py — audio buffering loop
async for frame_event in audio_stream:
    buffer.extend(frame_event.frame.data.tobytes())

    # ~3 seconds of audio at 48kHz, 16-bit mono
    if len(buffer) >= 288_000:
        result = await client.analyze_audio(bytes(buffer))
        buffer.clear()
        # ... process result
```

### 4.3 Hume Streaming API Integration

The `HumeEmotionClient` (in `services/hume.py`) wraps Hume's **Expression Measurement Streaming API** over a persistent WebSocket connection. Raw PCM bytes are wrapped in a WAV header before being sent to Hume, since Hume's API requires a recognized audio format.

```python
# services/hume.py — audio format wrapping and analysis
async def analyze_audio(self, audio_bytes: bytes) -> Optional[dict]:
    # Wrap raw PCM in WAV container (48kHz, 16-bit, mono)
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)      # 16-bit
        wav_file.setframerate(48000)
        wav_file.writeframes(audio_bytes)

    wav_bytes = wav_io.getvalue()
    encoded = base64.b64encode(wav_bytes).decode("utf-8")
    response = await self._socket.send_file(file_=encoded, config=self._config)

    # Extract top 3 emotions by score
    emotions = sorted(
        [(e.name, e.score) for e in pred.emotions ...],
        key=lambda x: x[1],
        reverse=True
    )
    return {"top_emotions": emotions[:3]}
```

Hume returns up to **48 distinct emotional dimensions** per audio chunk, each with a confidence score between 0.0 and 1.0. We take the **top 3 by score** for each chunk.

### 4.4 Emotion-to-Avatar Mapping

Hume's 48 granular emotions are mapped to 6 Sakhi avatar expressions. This mapping lives in `services/hume.py`:

| Hume Emotions | Sakhi Avatar Expression |
|---|---|
| Joy, Amusement, Contentment | `happy` |
| Excitement, Surprise (positive), Ecstasy | `excited` |
| Interest, Contemplation, Concentration, Confusion, Realization | `thinking` |
| Pride, Triumph, Satisfaction, Admiration | `celebrating` |
| Sadness, Disappointment, Nostalgia | `sad` |
| Distress, Anxiety, Fear, Awkwardness, Doubt | `concerned` |
| *Any unmapped emotion* | `happy` (default) |

### 4.5 Three-Channel Emotion Dispatch

After each emotion analysis, the detector writes the result to three destinations simultaneously:

**① Participant Attributes → Voice Agent**

```python
await ctx.room.local_participant.set_attributes({
    "emotion": top_emotion_name,        # e.g. "Anxiety"
    "avatar_expression": avatar_expr,   # e.g. "concerned"
})
```

The Sakhi voice agent reads these attributes before each LLM call.

**② RPC → Child Frontend**

```python
await ctx.room.local_participant.perform_rpc(
    destination_identity=pid,           # only "child-xxx" participants
    method="setEmotionState",
    payload=json.dumps({
        "expression": avatar_expr,
        "raw_emotion": top_emotion_name,
        "score": top_emotion_score,
    }),
    response_timeout=3.0,
)
```

The frontend receives this RPC call and triggers the avatar's BlendShape animations (e.g., making Sakhi's face look concerned when the child is anxious).

**③ Database → Parent Dashboard**

```python
await conn.execute("""
    INSERT INTO emotion_snapshots (profile_id, room_name, emotion, score, top_3)
    VALUES ($1, $2, $3, $4, $5)
""", profile_id, room_name, emotion, score, json.dumps(top_3))
```

Every detected emotion is timestamped and persisted for the parent dashboard's mood trend charts.

---

## 5. How Emotion Shapes Sakhi's Responses

### 5.1 Emotion Context Injection

Before the LLM generates each response, the `SakhiAgent.on_user_turn_completed()` hook runs. This hook:

1. Reads participant attributes from the room to get the latest detected emotion.
2. Injects an **ephemeral system message** into the LLM's context window — a message that tells the LLM how the child is feeling and asks it to adapt its response accordingly.
3. This system message is deliberately phrased to never be read aloud ("DO NOT read this aloud").

```python
# agents/sakhi.py — emotion context injection
async def on_user_turn_completed(self, turn_ctx, new_message):
    for participant in room.remote_participants.values():
        attrs = participant.attributes
        if attrs and "emotion" in attrs:
            self._current_emotion = attrs["emotion"]
            break

    if self._current_emotion:
        turn_ctx.add_message(
            role="system",
            content=(
                f"[Emotion context — DO NOT read this aloud] "
                f"The child's voice tone suggests they are feeling: {self._current_emotion}. "
                f"Adapt your response accordingly — be extra supportive if they sound sad "
                f"or anxious, and match their energy if they sound excited or happy. "
                f"Never reveal that you are detecting their emotions."
            )
        )
```

### 5.2 Why This Matters for Children

This design means Sakhi is **emotionally intelligent** without being intrusive:
- If a child is frustrated, Sakhi slows down, simplifies, and becomes more encouraging.
- If a child is excited about a topic, Sakhi matches that energy and goes deeper.
- If a child sounds sad or anxious, Sakhi becomes more nurturing and checks in.
- **The child never knows** their emotion is being analyzed — Sakhi's empathy feels natural.

---

## 6. Session Lifecycle — From Login to Goodbye

```
Child logs in → Profile selected → POST /api/token
    → Room created + 2 agents dispatched
    → Child joins room and starts talking
        ├── Sakhi Agent: STT → LLM → TTS (continuous loop)
        └── Emotion Detector: Audio → Hume → 3-channel dispatch (every 3s)
    → Child disconnects / room closes
        └── Sakhi Agent: on_session_end() triggered
            ├── Extracts full transcript from ChatContext
            ├── Fetches emotion timeline from DB for this room
            ├── Single Groq LLM call: summarize_session()
            │   ├── topics: ["photosynthesis", "multiplication tables"]
            │   ├── mood_summary: "Mostly curious, brief frustration during math"
            │   └── alerts: [] or [{title, description, severity}]
            ├── Writes session_summaries row to DB
            ├── Links emotion_snapshots to session via session_id
            └── Writes any alerts to alerts table
```

### Session Summarizer

The session summarizer (`services/session_summarizer.py`) runs at the end of every session. It makes a **single LLM call** to Groq with the full transcript and emotion timeline, extracting structured JSON:

```json
{
  "topics": ["photosynthesis", "food chains"],
  "mood_summary": "Mostly enthusiastic and curious, with brief frustration during multiplication",
  "alerts": [
    {
      "title": "Child mentioned bullying",
      "description": "Child said a classmate was mean to them at recess.",
      "severity": "warning"
    }
  ]
}
```

This data powers the parent dashboard's weekly summaries, streak tracking, and alert notifications.

---

## 7. The Parent Dashboard — What Gets Recorded

Every session generates data for the parent dashboard:

| Metric | Source | DB Table |
|---|---|---|
| **Time spent with Sakhi** | Session duration (ended_at − started_at) | `session_summaries.duration_secs` |
| **Mood summary** | LLM analysis of transcript + emotion timeline | `session_summaries.mood_summary` |
| **Topics explored** | LLM extraction from transcript | `session_summaries.topics` (JSONB) |
| **Streak** | Consecutive days with sessions | Computed from `session_summaries.ended_at` |
| **Sakhi Noticed (Alerts)** | LLM flagging of concerning content | `alerts` table |
| **Emotion timeline** | Real-time Hume snapshots every ~3 seconds | `emotion_snapshots` table |

---

## 8. Database Schema

### `accounts`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `email` | TEXT | Unique login email |
| `password_hash` | TEXT | bcrypt hash |
| `family_name` | TEXT | Family display name |
| `plan` | TEXT | `free` / `premium` |
| `created_at` | TIMESTAMPTZ | |

### `profiles`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `account_id` | UUID | FK → accounts |
| `type` | TEXT | `parent` or `child` |
| `display_name` | TEXT | |
| `age` | INT | Child's age (null for parent) |
| `created_at` | TIMESTAMPTZ | |

### `emotion_snapshots`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `profile_id` | UUID | FK → profiles (child) |
| `session_id` | UUID | FK → session_summaries (linked after session) |
| `room_name` | TEXT | LiveKit room name |
| `emotion` | TEXT | Hume emotion name (e.g. "Joy") |
| `score` | REAL | Hume confidence score (0.0–1.0) |
| `top_3` | JSONB | Top 3 emotions for this snapshot |
| `recorded_at` | TIMESTAMPTZ | When the snapshot was taken |

### `session_summaries`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `profile_id` | UUID | FK → profiles (child) |
| `room_name` | TEXT | LiveKit room name |
| `started_at` | TIMESTAMPTZ | Session start |
| `ended_at` | TIMESTAMPTZ | Session end |
| `duration_secs` | INT | Total session length |
| `mood_summary` | TEXT | One-sentence LLM mood analysis |
| `topics` | JSONB | List of topics explored |
| `turn_count` | INT | Number of conversation turns |
| `transcript` | JSONB | Full conversation transcript |

### `alerts`
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `profile_id` | UUID | FK → profiles (child) |
| `session_id` | UUID | FK → session_summaries |
| `alert_type` | TEXT | `emotion`, `content`, or `pattern` |
| `severity` | TEXT | `info`, `warning`, or `critical` |
| `title` | TEXT | Short alert title |
| `description` | TEXT | Detailed description |
| `dismissed` | BOOLEAN | Whether parent dismissed the alert |

---

## 9. API Reference

### `POST /api/token`

Generates a LiveKit room token for a child session. Creates the room and dispatches both agents.

**Auth:** `Authorization: Bearer <profile_jwt>` (child profile token required)

**Response:**
```json
{
  "token": "<livekit_jwt>",
  "room_name": "sakhi-arjun-1741234567",
  "livekit_url": "wss://your-project.livekit.cloud"
}
```

**What it does:**
1. Validates the profile JWT and confirms `profile_type == "child"`
2. Fetches child's `display_name` and `age` from DB
3. Creates a LiveKit room with child metadata in `room_metadata`
4. Dispatches `sakhi-agent` and `emotion-detector` into the room
5. Returns a signed LiveKit access token for the child's frontend

---

### `GET /api/dashboard/*`

Dashboard endpoints (require a parent profile token):

| Endpoint | Returns |
|---|---|
| `GET /api/dashboard/time-spent` | Total minutes this week |
| `GET /api/dashboard/mood` | Latest mood summary + emotion trend |
| `GET /api/dashboard/topics` | Topics explored this week |
| `GET /api/dashboard/streak` | Current consecutive-day streak |
| `GET /api/dashboard/alerts` | Pending alerts for the parent |

---

### `GET /api/health`

Returns `{"status": "ok", "service": "sakhi-backend", "timestamp": <unix>}`

---

## 10. Environment Variables & Configuration

The following environment variables must be set in `.env.local`:

```bash
# LiveKit Cloud
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# Deepgram (STT + TTS)
DEEPGRAM_API_KEY=your_deepgram_key

# Groq (LLM)
GROQ_API_KEY=your_groq_key

# Hume AI (Emotion Detection)
HUME_API_KEY=your_hume_key

# Database (Neon PostgreSQL)
DATABASE_URL=postgresql://user:pass@host/sakhi

# JWT Secret (for Sakhi's own auth system)
JWT_SECRET=your_long_random_secret
```

---

## 11. Landing Page Integration Guide

The landing page aims to let **parents understand and experience Sakhi** before signing up. Here is how the voice bot and emotion detection can be integrated into the landing page experience.

### 11.1 What to Build

A demo voice experience on the landing page where:
- A parent can **click a "Try Sakhi" button** and immediately speak with Sakhi.
- Sakhi introduces itself, explains what it does, and answers parent questions.
- The landing page avatar **reacts visually** as it speaks (expressions change based on what Sakhi says).
- The emotion detection runs in the background so parents can see a live readout of the detected emotion — making the technology tangible.

### 11.2 Landing Page Token Flow

The landing page uses a **guest/demo token** — no auth required. The backend should expose a `POST /api/demo-token` endpoint that:
- Creates a short-lived LiveKit room (`sakhi-demo-<timestamp>`)
- Dispatches a **landing page variant** of the Sakhi agent (different system prompt — talks to parents, not children)
- Returns a token with a 10-minute TTL
- Does **not** dispatch the emotion detector for demo sessions (or shows a simplified version)

```
Landing page → POST /api/demo-token (no auth required)
    → Short-lived room created
    → Landing page Sakhi agent dispatched (parent-facing persona)
    → Frontend joins room, streams audio, receives responses
```

### 11.3 Recommended Landing Page Sakhi System Prompt

```
You are Sakhi, an AI companion for Indian children aged 4–12.
You are currently speaking with a parent who is exploring the Sakhi app.

Your goal is to help this parent understand:
- What Sakhi is and how it helps their child learn
- How Sakhi uses voice, emotion detection, and AI to adapt to each child
- What parents can see in the dashboard

Keep responses concise (2-3 sentences). Be warm, confident, and reassuring.
Speak in a way that a parent would trust with their child.
```

### 11.4 Frontend Integration Pattern

```javascript
// 1. Get a demo token from the backend
const { token, room_name, livekit_url } = await fetch('/api/demo-token', {
  method: 'POST'
}).then(r => r.json());

// 2. Connect to LikeKit room
const room = new Room();
await room.connect(livekit_url, token);

// 3. Enable microphone
await room.localParticipant.setMicrophoneEnabled(true);

// 4. Listen for avatar expression RPC (if emotion detector is running)
room.localParticipant.registerRpcMethod('setEmotionState', async (data) => {
  const { expression, raw_emotion, score } = JSON.parse(data.payload);
  updateAvatarExpression(expression); // trigger your Three.js / CSS animation
  return JSON.stringify({ received: true });
});
```

### 11.5 Showing Emotion Detection to Parents

To make Hume's emotion detection tangible for parents on the landing page, consider showing a **live emotion readout** panel alongside the avatar:

- Display the current detected emotion name (e.g., "Curiosity", "Joy", "Excitement")
- Show a confidence bar (Hume score 0–1)
- Animate the Sakhi avatar expression to match
- Add a caption like: *"Sakhi is sensing how your child feels and adapting in real time"*

This turns an invisible backend feature into a visible, compelling demonstration of the technology.

---

## Appendix: Key File Reference

| File | Purpose |
|---|---|
| `agent.py` | Entrypoint for the Sakhi voice agent process |
| `emotion_detector.py` | Entrypoint for the emotion detector process |
| `agents/sakhi.py` | `SakhiAgent` class + `AgentSession` voice pipeline |
| `agents/emotion_detector.py` | Hume audio loop + 3-channel dispatch |
| `services/hume.py` | `HumeEmotionClient` + emotion→avatar mapping |
| `services/session_summarizer.py` | Post-session LLM summarization |
| `api/routes.py` | FastAPI: `/api/token` + health + router mounting |
| `api/auth_routes.py` | Signup, login, profile management endpoints |
| `api/dashboard_routes.py` | Parent dashboard data endpoints |
| `db/migrations.py` | All database table definitions |
| `.env.local` | All secrets and credentials |
