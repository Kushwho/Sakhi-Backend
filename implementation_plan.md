# Multi-Modal Story Generation Pipeline

Replace the legacy pre-authored story narration feature (LiveKit voice agent + DB segments) with a new AI-powered pipeline that **generates** stories on-the-fly using Groq (text) and Replicate Flux (images).

## User Review Required

> [!IMPORTANT]
> **DB tables left in place**: The existing [stories](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/api/story_routes.py#186-195) and `story_segments` tables in [db/migrations.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/db/migrations.py) will **not** be dropped — `CREATE TABLE IF NOT EXISTS` is idempotent and dropping tables in a migration file risks data loss on existing environments. These tables simply become unused.

> [!IMPORTANT]
> **`REPLICATE_API_TOKEN` required**: You will need to add this environment variable for image generation to work. The service will fail gracefully if it's missing.

> [!WARNING]
> **[story_emitter.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/story_emitter.py) contains a hardcoded database URL** (line 9). This file is being deleted entirely.

---

## Proposed Changes

### Phase 1 — Legacy Teardown

Files to **delete** entirely:

#### [DELETE] [story_agent.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/agents/story_agent.py)
LiveKit voice agent + [StoryAgent](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/agents/story_agent.py#84-166) class (359 lines). No longer needed — stories will be text+image, not voice-narrated.

#### [DELETE] [story_routes.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/api/story_routes.py)
Old REST endpoints: GET/POST stories, story-token LiveKit flow (219 lines). Will be replaced with new routes.

#### [DELETE] [stories.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/services/stories.py)
DB-access layer for pre-authored stories + segments (211 lines).

#### [DELETE] [story_emitter.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/story_emitter.py)
Seed script with hardcoded DB credentials (102 lines).

#### [DELETE] [story_entrypoint.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/story_entrypoint.py)
LiveKit agent CLI wrapper (17 lines).

#### [DELETE] [test_story.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/tests/test_story.py)
Integration tests for old story routes (275 lines).

#### [DELETE] [story_mode.md](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/docs/story_mode.md)
Documentation for old story feature.

---

Files to **modify**:

#### [MODIFY] [routes.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/api/routes.py)
- Remove line 32: `from api.story_routes import router as story_router`
- Remove line 76: `app.include_router(story_router)`
- Add import + include for new story router (Phase 3)

#### [MODIFY] [start.sh](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/start.sh)
- Remove lines 29-32 (Story Agent startup block)
- Remove `$STORY_PID` from lines 45 and 49

---

### Phase 2 — ImageGenerationService (Replicate + Flux)

#### [NEW] [image_generation.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/services/image_generation.py)

A **standalone, reusable** service decoupled from stories:

```python
class ImageGenerationService:
    """Replicate-powered image generation using Flux Schnell."""
    
    async def generate_image(
        prompt: str,
        aspect_ratio: str = "16:9",
        output_format: str = "webp",
        num_outputs: int = 1,
    ) -> str | None:
        """Returns the generated image URL or None on failure."""
```

Key design decisions:
- Uses `httpx` (already a dependency) for direct Replicate HTTP API calls — avoids adding the `replicate` Python SDK as a new dep
- Targets `black-forest-labs/flux-schnell` (fast, high-quality)
- Robust error handling: API timeouts, content safety flags, missing API key
- Returns `None` on failure instead of raising — callers can decide how to handle missing images
- Environment variable: `REPLICATE_API_TOKEN`

#### [MODIFY] [pyproject.toml](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/pyproject.toml)
No changes needed — we use `httpx` (already installed) for Replicate API calls.

---

### Phase 3 — StoryOrchestrationService (Groq + Pipeline)

#### [NEW] [story_orchestrator.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/services/story_orchestrator.py)

Orchestrates end-to-end story generation:

1. Accepts user story idea + optional parameters (genre, number of scenes, child age)
2. Calls Groq via existing `SakhiLLM.generate_json()` with a system prompt enforcing strict JSON output:
   ```json
   {
     "title": "The Magic Kite",
     "scenes": [
       {
         "story_text": "Once upon a time...",
         "image_prompt": "A vibrant watercolor illustration of a young Indian girl flying a colorful kite..."
       }
     ]
   }
   ```
3. Parses the JSON response
4. Fires all `image_prompt` values concurrently through `ImageGenerationService` using `asyncio.gather()`
5. Stitches image URLs back to their scenes
6. Returns the assembled multi-modal payload

#### [NEW] [story_routes.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/api/story_routes.py)

New API route:

```
POST /api/stories/generate
Body: { "idea": "A story about a brave monkey in space", "num_scenes": 4, "genre": "adventure" }
Auth: Requires profile_token (child profile)
Response: {
  "title": "...",
  "scenes": [
    { "story_text": "...", "image_url": "https://..." },
    ...
  ]
}
```

#### [MODIFY] [routes.py](file:///c:/Users/Bhavesh/Desktop/Sakhi/Sakhi-Backend/api/routes.py)
- Add: `from api.story_routes import router as story_router`
- Add: `app.include_router(story_router)`

---

## Verification Plan

### Automated Tests

#### 1. Server startup check
```bash
cd c:\Users\Bhavesh\Desktop\Sakhi\Sakhi-Backend
python -c "from api.routes import app; print('OK: app loads without errors')"
```
Validates no import errors from removed modules.

#### 2. Unit test: `tests/test_story_pipeline.py`
- Tests `ImageGenerationService` with mocked Replicate API
- Tests `StoryOrchestrationService` with mocked Groq + mocked image service
- Tests the JSON parsing and scene stitching logic
- Run via: `python -m pytest tests/test_story_pipeline.py -v`

### Manual Verification
> [!NOTE]
> **For the user**: After implementation, you can test the full pipeline by:
> 1. Start the server: `python run.py`
> 2. Authenticate and get a profile token (existing auth flow)
> 3. Call: `POST /api/stories/generate` with body `{"idea": "A story about a brave little elephant"}` and `Authorization: Bearer <profile_token>`
> 4. Verify the response contains a `title`, and `scenes` array where each scene has `story_text` and `image_url`
