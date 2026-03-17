# Curio Features -- Technical Documentation

This document covers the three Curio activity features: **Thinking** (Curious Mode), **Say What You See** (SWYS), and **GenType**. All three are launched from a unified activity grid served by `GET /api/curio/activities`.

---

## Table of Contents

1. [Unified Curio Activity Grid](#1-unified-curio-activity-grid)
2. [Thinking (Curious Mode)](#2-thinking-curious-mode)
3. [Say What You See (SWYS)](#3-say-what-you-see-swys)
4. [GenType](#4-gentype)
5. [Centralized AI Provider (SakhiLLM)](#5-centralized-ai-provider-sakhillm)
6. [Database Schema](#6-database-schema)
7. [System Prompts Architecture](#7-system-prompts-architecture)
8. [Environment Variables](#8-environment-variables)

---

## 1. Unified Curio Activity Grid

All three features are tiles in a 2x2 Curio grid. The grid is served as a static catalog from `api/curious_routes.py`.

**Endpoint:** `GET /api/curio/activities`
**Auth:** `require_profile_token` (child only)
**Response:**
```json
{
  "activities": [
    {"id": "thinking",          "title": "Thinking",          "emoji": "...", "is_available": true},
    {"id": "say_what_you_see",  "title": "Say What You See",  "emoji": "...", "is_available": true},
    {"id": "gentype",           "title": "GenType",           "emoji": "...", "is_available": true},
    {"id": "coming_soon",       "title": "Coming Soon",       "emoji": "...", "is_available": false}
  ]
}
```

**Start endpoint:** `POST /api/curio/activities/{activity_id}/start`
Returns the `mode` key (system prompt mode) and an activity-specific `context` dict that gets passed to the chat/voice session.

### Files
| File | Role |
|------|------|
| `api/curious_routes.py` | Curio grid endpoints, Thinking sub-mode routing, Surprise generation |
| `api/routes.py` | Registers `curious_router` and `curio_router` on the FastAPI app |

---

## 2. Thinking (Curious Mode)

Thinking is the conversational exploration feature with three sub-modes that change the system prompt overlay on top of the base Sakhi prompt.

### Sub-modes

| Sub-mode | System prompt key | Description |
|----------|------------------|-------------|
| `curious_open` | `curious_open` | Free-form exploration. No additional context needed. Child asks about anything. |
| `curious_topic` | `curious_topic` | Structured topic exploration. A topic from the curated catalog provides `{topic_title}` and `{topic_description}` placeholders for the prompt. |
| `curious_surprise` | `curious_surprise` | Starts with a random LLM-generated fact. The `surprise_generator` prompt mode generates a fact, topic label, and follow-up question as JSON. |

### Topic Catalog

68 curated topics across 11 categories, defined statically in `services/topics.py`.

**Categories:** Science, Space, Nature, Body, Math, History, Culture, Technology, Art, Environment.

Each topic has:
```python
{
    "id": "science-photosynthesis",
    "title": "How Plants Make Food",
    "emoji": "...",
    "description": "...",
    "category": "Science",
    "age_range": [6, 12],
    "tags": ["plants", "photosynthesis", "nature", "biology"]
}
```

Topics are age-filtered using `age_range`: `get_topics_for_age(age)` returns only topics where `min <= child_age <= max`. The response endpoint shuffles and limits to 12.

### Surprise Generation Flow

1. Random category selected from `SURPRISE_CATEGORIES` list (10 categories).
2. `surprise_generator` prompt template is fetched from the prompt cache.
3. `SakhiLLM.generate_json()` is called with `temperature=0.9`, `max_tokens=300`.
4. Expected JSON response: `{"fact": "...", "topic": "...", "follow_up_question": "..."}`.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/curious/topics` | Age-filtered topic cards. Returns up to 12 shuffled topics. |
| `GET` | `/api/curious/surprise` | Generate a random fact via LLM. |
| `POST` | `/api/curio/activities/thinking/start` | Start Thinking activity. Body: `{sub_mode, topic_id?}`. Returns mode + context. |

### How Thinking Connects to Chat/Voice

The `start` endpoint returns a `mode` and `context` dict. The frontend passes these to:
- **Text chat:** `POST /api/chat/send` with `mode`, `topic_id`, `surprise_fact` in the request body.
- **Voice:** `POST /api/token` with `mode`, `topic_id`, `surprise_fact` in the request body. These are embedded in LiveKit room metadata and read by the voice agent on connect.

In both paths, `build_system_prompt()` in `services/prompts.py` assembles: `base_prompt + "\n\n" + mode_addon` with placeholders filled.

### Files
| File | Role |
|------|------|
| `api/curious_routes.py` | Route handlers for topics, surprise, activity start |
| `services/topics.py` | Static topic catalog (68 topics), age filtering, lookup |
| `services/prompts.py` | Prompt cache, `build_system_prompt()`, prompt versioning |
| `services/llm.py` | `generate_json()` for surprise fact generation |

---

## 3. Say What You See (SWYS)

SWYS is an image-prompt game. The child is shown a pre-generated seed image and writes a natural language prompt to describe/recreate it. The system generates a new image from that prompt, then a vision LLM judges similarity and returns a score + hint.

### Game Flow

```
Kid sees seed image (from DB)
        |
        v
Kid writes a text prompt
        |
        v
Replicate flux-1.1-pro generates image from prompt
        |
        v
Groq vision LLM compares original + generated
        |
        v
Returns: score (0-100) + child-friendly hint
        |
        v
Attempt persisted to swys_attempts
```

### Seed Images

Stored in the `swys_images` table. Each image has:
- `title` -- human-readable label
- `original_prompt` -- the Replicate prompt used to generate it (unique constraint for idempotent upserts)
- `image_url` -- Replicate delivery URL
- `level` -- difficulty 1-5 based on prompt complexity
- `category` -- grouping (objects, nature, animals, culture, fantasy)
- `is_active` -- soft-delete flag

**Seeded via:** `python scripts/seed_swys_images.py` (standalone async script).

Current seed set (7 images):

| Level | Title | Category |
|-------|-------|----------|
| 1 | Red Apple | objects |
| 1 | Blue Balloon | objects |
| 2 | Sunny Hills | nature |
| 2 | Rainy Frog | animals |
| 3 | Dog at Beach | animals |
| 4 | Indian Bazaar | culture |
| 5 | Neon City Night | fantasy |

Levels 1-2 have 2 images each so `ORDER BY RANDOM()` gives different kids different images. The catalog is designed to grow -- randomization benefits all levels automatically as more images are added.

### Image Generation

Delegates to `SakhiLLM.generate_image()` in `services/llm.py`.
- **Model:** `black-forest-labs/flux-1.1-pro` (configurable via `SAKHI_IMAGE_MODEL` env var)
- **Provider:** Replicate (async via `replicate.async_run()`)
- **Output:** 1024x1024, webp format, quality 80
- **Returns:** Public URL (Replicate delivery URL, expires ~1 hour)

### Vision Judge

Delegates to `SakhiLLM.vision_json()` in `services/llm.py`.
- **Model:** `meta-llama/llama-4-scout-17b-16e-instruct` (configurable via `SAKHI_VISION_MODEL` env var)
- **Provider:** Groq (vision-capable model)
- **Input:** Two image URLs (original + generated) + kid's prompt text
- **Output:** JSON `{"score": int, "hint": str}`
- **Temperature:** 0.2 (deterministic judging)
- **Fallback on error:** `{"score": 50, "hint": "Great try! Add more details about colours, shapes, and objects you see."}`

**Judge prompt structure:**
- System prompt: encouraging, child-friendly judge persona
- User prompt: describes the game context, asks for 0-100 similarity score and a constructive hint
- Images passed as `image_url` content blocks in the Groq multimodal message format

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/swys/image?level=N` | Fetch a random active seed image. Optional `level` (1-5) filter. |
| `POST` | `/api/swys/attempt` | Submit `{image_id, kid_prompt}`. Generates image, judges, persists, returns result. |
| `GET` | `/api/swys/history?limit=N` | Kid's recent attempts (default 10, max 50). Joins with `swys_images` for title + level. |

**Auth:** All endpoints require `require_profile_token` with `profile_type == "child"`.

### POST /api/swys/attempt -- Detailed Flow

1. Validate `kid_prompt` is not empty.
2. `get_image_by_id(image_id)` -- fetch seed image from DB. 404 if not found.
3. `generate_image(kid_prompt)` -- Replicate call via `SakhiLLM.generate_image()`. Returns URL. 502 on failure.
4. `judge_attempt(original_url, generated_url, kid_prompt)` -- Groq vision call via `SakhiLLM.vision_json()`. Returns `{score, hint}`. Falls back gracefully on error.
5. `save_attempt(...)` -- INSERT into `swys_attempts` with FK to `swys_images`.
6. Response: `{score, hint, generated_image_url, original_image_url, attempt_id}`.

### Seed Script

`scripts/seed_swys_images.py` -- standalone async script that:
1. Runs all DB migrations (so tables exist).
2. For each seed entry, calls `replicate.async_run()` to generate the image.
3. Upserts into `swys_images` using `ON CONFLICT (original_prompt) DO UPDATE`.
4. Waits 12s between requests to respect Replicate rate limits (free tier: 1 req/burst, 6 req/min).
5. Retries on 429 with exponential backoff (15s, 30s, 45s).

**Run:** `python scripts/seed_swys_images.py`

### Files
| File | Role |
|------|------|
| `api/say_what_you_see_routes.py` | Route handlers (image, attempt, history) |
| `services/say_what_you_see.py` | Game logic (fetch, generate, judge, save, history) |
| `services/llm.py` | `generate_image()` and `vision_json()` on `SakhiLLM` |
| `db/migrations.py` | `swys_images` and `swys_attempts` table schemas |
| `scripts/seed_swys_images.py` | Standalone seed script |

---

## 4. GenType

GenType is a creative alphabet game. The child picks a visual theme (dinosaurs, space, candy, etc.) and the system generates each letter of the alphabet as an image in that theme using Replicate.

### Game Flow

```
Kid picks a theme (e.g. "Dinosaur World")
        |
        v
Kid picks a letter OR uses "spell my name"
        |
        v
For each letter:
  - Check cache (theme_id:letter key)
  - If cached: return cached URL
  - If not: build themed prompt -> Replicate flux-1.1-pro -> cache result
        |
        v
Return letter image(s)
```

### Theme Catalog

8 themes defined statically in `services/image_gen.py`:

| Theme ID | Name | Description |
|----------|------|-------------|
| `dinosaurs` | Dinosaur World | Letters from dinosaurs and ancient bones |
| `space` | Outer Space | Letters from planets, stars, rocket ships |
| `candy` | Candy Land | Letters from sweets, lollipops, sprinkles |
| `ocean` | Under the Ocean | Letters from fish, coral, waves |
| `jungle` | Jungle Adventure | Letters from animals and tropical leaves |
| `robots` | Robot Factory | Letters from gears, bolts, friendly robots |
| `flowers` | Magical Garden | Letters from flowers, butterflies, vines |
| `animals` | Animal Kingdom | Letters from friendly animals |

Each theme has a `flux_style_suffix` -- a detailed style description appended to the letter generation prompt. This is internal and not exposed to the API.

### Prompt Construction

`build_letter_prompt(letter, theme_id)` in `services/image_gen.py` builds:

```
A single large capital letter "{LETTER}" filling the entire frame, {flux_style_suffix}.
The letter shape must be clearly readable.
Pure white background. Isolated single letter, no other text, no alphabet series.
Children's book illustration quality, vivid colours, clean composition.
Square format, 1:1 aspect ratio.
```

### Caching

GenType uses aggressive DB caching since the same letter + theme always produces a valid result.

- **Cache key:** `{theme_id}:{letter}` (e.g. `dinosaurs:A`)
- **Table:** `gentype_cache` with unique constraint on `cache_key`
- **Upsert:** `ON CONFLICT (cache_key) DO UPDATE SET image_url = EXCLUDED.image_url`
- **Force regenerate:** `force_regenerate: true` in the request body skips cache lookup

The cache is shared across all kids -- once letter `A` in the `dinosaurs` theme is generated for any child, all subsequent children get the cached URL instantly.

### Spell Name

`POST /api/curio/gentype/spell-name` generates all unique letters of the child's name:

1. Fetches `display_name` from the child's profile.
2. Deduplicates letters preserving first-occurrence order (e.g. "AANYA" -> ["A", "N", "Y"]).
3. Batch cache check for all letters.
4. Uncached letters are generated **in parallel** via `asyncio.gather()`.
5. Successful generations are cached.
6. Response preserves name-letter order and includes `has_errors` flag.

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/api/curio/gentype/themes` | **None** (public) | List available themes (without internal flux_style_suffix) |
| `POST` | `/api/curio/gentype/generate` | Child profile | Generate single letter. Body: `{theme_id, letter, force_regenerate?}` |
| `POST` | `/api/curio/gentype/spell-name` | Child profile | Generate all unique letters of child's name. Body: `{theme_id}` |

### Response Format (generate)
```json
{
  "letter": "A",
  "theme_id": "dinosaurs",
  "image_url": "https://replicate.delivery/...",
  "from_cache": false
}
```

### Response Format (spell-name)
```json
{
  "name": "Arjun",
  "theme_id": "space",
  "letters": [
    {"letter": "A", "image_url": "...", "from_cache": true, "error": null},
    {"letter": "R", "image_url": "...", "from_cache": false, "error": null},
    {"letter": "J", "image_url": null, "from_cache": false, "error": "Rate limited"},
    {"letter": "U", "image_url": "...", "from_cache": false, "error": null},
    {"letter": "N", "image_url": "...", "from_cache": true, "error": null}
  ],
  "has_errors": true
}
```

### Files
| File | Role |
|------|------|
| `api/gentype_routes.py` | Route handlers (themes, generate, spell-name) |
| `services/image_gen.py` | Theme catalog, prompt builder (`build_letter_prompt`) |
| `services/llm.py` | `generate_image()` on `SakhiLLM` (Replicate call) |
| `db/migrations.py` | `gentype_cache` table schema |

---

## 5. Centralized AI Provider (SakhiLLM)

All three features use `services/llm.py` as the single point of contact for external AI services. The `SakhiLLM` class wraps:

| Method | Provider | Model | Used By |
|--------|----------|-------|---------|
| `generate_json(prompt, system_prompt?, temperature?, max_tokens?)` | Groq | `llama-3.1-8b-instant` | Thinking surprise, session summarizer, memory extraction |
| `vision_json(image_urls, prompt, system_prompt?, model?)` | Groq | `meta-llama/llama-4-scout-17b-16e-instruct` | SWYS judge |
| `generate_image(prompt, model?, width?, height?, output_format?)` | Replicate | `black-forest-labs/flux-1.1-pro` | SWYS seed images, GenType letters |
| `get_langchain_chat_model()` | Groq (LangChain) | `llama-3.1-8b-instant` | LangGraph text chat pipeline |

**Singleton pattern:** `get_llm_client()` returns a lazy singleton. The client is not instantiated at import time to avoid breaking test collection when API keys are not set (uses `"dummy_key_for_tests"` fallback).

**Model overrides via env vars:**
- `SAKHI_DEFAULT_LLM_MODEL` -- text LLM (default: `llama-3.1-8b-instant`)
- `SAKHI_VISION_MODEL` -- vision LLM (default: `meta-llama/llama-4-scout-17b-16e-instruct`)
- `SAKHI_IMAGE_MODEL` -- image gen model (default: `black-forest-labs/flux-1.1-pro`)

---

## 6. Database Schema

### swys_images
```sql
CREATE TABLE swys_images (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    original_prompt TEXT NOT NULL,
    image_url       TEXT NOT NULL,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 5),
    category        TEXT NOT NULL DEFAULT 'general',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT swys_images_prompt_unique UNIQUE (original_prompt)
);
-- Index: idx_swys_images_level ON swys_images(level, is_active)
```

### swys_attempts
```sql
CREATE TABLE swys_attempts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id          UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    image_id            UUID NOT NULL REFERENCES swys_images(id),
    kid_prompt          TEXT NOT NULL,
    generated_image_url TEXT NOT NULL,
    score               SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    hint                TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Index: idx_swys_attempts_profile ON swys_attempts(profile_id, created_at DESC)
```

### gentype_cache
```sql
CREATE TABLE gentype_cache (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cache_key   TEXT UNIQUE NOT NULL,    -- format: "{theme_id}:{letter}"
    letter      CHAR(1) NOT NULL,
    theme_id    TEXT NOT NULL,
    image_url   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Index: idx_gentype_cache_key ON gentype_cache(cache_key)
```

### system_prompts
```sql
CREATE TABLE system_prompts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mode            TEXT UNIQUE NOT NULL,
    prompt_template TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    version         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### prompt_versions
```sql
CREATE TABLE prompt_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id       UUID NOT NULL REFERENCES system_prompts(id) ON DELETE CASCADE,
    mode            TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    version         INT NOT NULL,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Index: idx_prompt_versions_prompt_id ON prompt_versions(prompt_id, version DESC)
```

All migrations are idempotent (`CREATE TABLE IF NOT EXISTS`, `ON CONFLICT DO NOTHING`) and run automatically on FastAPI startup via `db/migrations.py`.

---

## 7. System Prompts Architecture

System prompts are stored in the `system_prompts` DB table and seeded idempotently at startup.

### Prompt Modes

| Mode | Used By | Placeholders |
|------|---------|-------------|
| `base` | All modes | `{child_name}`, `{child_age}`, `{child_language}` |
| `curious_open` | Thinking (open) | None |
| `curious_topic` | Thinking (topic) | `{topic_title}`, `{topic_description}` |
| `curious_surprise` | Thinking (surprise) | `{surprise_fact}` |
| `surprise_generator` | Surprise fact gen | `{child_age}`, `{category}` |
| `curio_say_what_you_see` | SWYS voice/chat | `{child_name}`, `{scene_description}` |
| `curio_say_what_you_see_generator` | SWYS scene gen | `{child_age}` |
| `curio_gentype` | GenType voice/chat | `{child_name}` |

### Prompt Assembly (`build_system_prompt()`)

```
final_prompt = base.format(child_name, child_age, child_language)
             + "\n\n"
             + mode_addon.format(**mode_specific_kwargs)
```

The base prompt is always included. Mode `"default"` uses base only. All other modes append their addon.

### Caching & Loading

1. `_DEFAULT_PROMPTS` dict is hardcoded as fallback (agent processes that never call `load_prompts()` still work).
2. On FastAPI startup, `load_prompts(pool)` fetches all active prompts from DB and overlays them onto the cache.
3. `get_prompt_template(mode)` reads from the in-memory cache.
4. `update_prompt(pool, mode, new_template)` archives the old version to `prompt_versions`, bumps the version number, and refreshes the cache.

---

## 8. Environment Variables

| Variable | Used By | Default |
|----------|---------|---------|
| `GROQ_API_KEY` | Text LLM, Vision LLM | Required |
| `REPLICATE_API_TOKEN` | Image generation (SWYS, GenType) | Required |
| `SAKHI_DEFAULT_LLM_MODEL` | Text LLM model | `llama-3.1-8b-instant` |
| `SAKHI_VISION_MODEL` | Vision LLM model | `meta-llama/llama-4-scout-17b-16e-instruct` |
| `SAKHI_IMAGE_MODEL` | Replicate image model | `black-forest-labs/flux-1.1-pro` |
| `DATABASE_URL` | All DB operations | Required |
