"""
Sakhi — Story Orchestration Service
======================================
Generates multi-modal children's stories (text + images) on demand using:
  • Groq (via existing SakhiLLM layer) for structured scene generation
  • ImageGenerationService (Replicate / Flux Schnell) for per-scene illustrations
  • TTSService for per-scene audio narration
  • R2Client for caching all media assets

Pipeline:
  1. Accept user's story idea + parameters (genre, num_scenes, child_age)
  2. Call Groq with a strict JSON schema prompt → get back story title + scenes
  3. Parse and validate the JSON response
  4. For each scene sequentially:
     a. Generate image → immediately upload to R2 (before URL expires ~60s)
     b. Generate TTS audio → upload to R2
  5. Stitch all URLs back to their respective scenes
  6. Return the fully assembled multi-modal payload

Each scene in the output payload:
  {
    "scene_number": 1,
    "story_text":   "Once upon a time...",
    "image_prompt": "The original Flux prompt used for reference",
    "image_url":    "https://storage.googleapis.com/..."   # or raw Replicate URL as fallback
    "audio_url":    "https://storage.googleapis.com/..."   # or None if generation failed
  }

KEY FIX: Replicate delivery URLs expire in ~60 seconds. The image must be
downloaded and uploaded to R2 IMMEDIATELY after generation — before any TTS
call adds delay. If R2 upload fails, we fall back to the raw Replicate URL
so the caller always gets a usable image URL.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from services.image_generation import get_image_service
from services.llm import get_llm_client
from services.prompts import get_prompt_template
from services.tts_generation import get_tts_service
from services.r2 import get_r2_client
from db.pool import get_pool

logger = logging.getLogger("sakhi.story_orchestrator")

# ---------------------------------------------------------------------------
# Groq system prompt — fetched from DB, hardcoded fallback
# ---------------------------------------------------------------------------

_FALLBACK_STORY_SYSTEM_PROMPT = """\
You are a world-class children's story writer for a global audience of children aged 4–12.

Your task is to write a vivid, imaginative, age-appropriate short story based on the user's idea.

STRICT RULES:
1. Output ONLY valid JSON. No prose, no explanation, no markdown fences.
2. The JSON must conform EXACTLY to this schema:
   {
     "title": "string — a short, catchy story title",
     "design_system": {
       "art_style": "string — master art style for ALL illustrations, e.g. 'soft watercolour illustration, children's book style, rich saturated colours, gentle brushstroke textures'",
       "color_palette": ["list of 3-5 dominant colours as descriptive names or hex codes, e.g. 'warm saffron', 'deep forest green', '#2A9D8F'"],
       "characters": [
         {
           "name": "character name",
           "description": "complete physical appearance: age, skin tone, hair style and colour, clothing, any distinctive features — specific enough that an artist draws them identically in every scene"
         }
       ],
       "setting_style": "string — visual environment language that persists across all scenes, e.g. 'lush Indian village, ancient banyan trees, terracotta earth paths, monsoon greenery'",
       "lighting": "string — lighting direction and quality, e.g. 'warm golden hour light, soft diffused shadows, slight atmospheric haze in backgrounds'",
       "mood_atmosphere": "string — emotional tone of the illustrations, e.g. 'cheerful, wonder-filled, inviting, magical realism'"
     },
     "scenes": [
       {
         "story_text": "string — one full narrative paragraph (60-120 words). Expressive, child-friendly language.",
         "image_prompt": "string — describe ONLY the scene-specific action, foreground detail, and any environment change. Do NOT repeat art style, colour palette, lighting, or character descriptions — those are already in design_system. Example: 'Priya discovers a tiny glowing door at the base of an ancient banyan tree, crouching down with wide curious eyes, autumn leaves swirling around her feet.'"
       }
     ]
   }

3. The story must be child-safe — no violence, no inappropriate content, ever.
4. Calibrate vocabulary and complexity to the child's age (provided by the user).
5. Stories should reflect the cultural setting requested by the user. Default to a universal, globally relatable setting with diverse, inclusive characters.
6. Do NOT include any text outside the JSON object.
"""


def _get_story_system_prompt() -> str:
    return get_prompt_template("story_writer") or _FALLBACK_STORY_SYSTEM_PROMPT


def _format_design_system_prompt(design_system: dict) -> str:
    """Serialize the structured design_system dict into a deterministic Flux prompt prefix.

    Fields are emitted in a fixed order so the same JSON always produces
    the same string. This prefix is prepended to every scene's image_prompt,
    ensuring visual consistency across all scenes in a story.

    Returns an empty string if design_system is empty or None.
    """
    if not design_system or not isinstance(design_system, dict):
        return ""

    parts: list[str] = []

    # Art style first — highest influence on Flux's visual embedding
    art_style = design_system.get("art_style", "").strip()
    if art_style:
        parts.append(art_style)

    color_palette = design_system.get("color_palette", [])
    if color_palette:
        palette_str = (
            ", ".join(str(c) for c in color_palette)
            if isinstance(color_palette, list)
            else str(color_palette)
        )
        parts.append(f"colour palette: {palette_str}")

    lighting = design_system.get("lighting", "").strip()
    if lighting:
        parts.append(f"lighting: {lighting}")

    mood = design_system.get("mood_atmosphere", "").strip()
    if mood:
        parts.append(f"mood: {mood}")

    setting = design_system.get("setting_style", "").strip()
    if setting:
        parts.append(f"setting style: {setting}")

    # Characters last so they bridge naturally into scene-specific action
    for char in design_system.get("characters", []):
        name = char.get("name", "").strip()
        desc = char.get("description", "").strip()
        if name and desc:
            parts.append(f"character {name}: {desc}")
        elif desc:
            parts.append(desc)

    return ", ".join(parts)


_STORY_USER_PROMPT_TEMPLATE = """\
Write a {genre} story for a {child_age}-year-old child about: "{idea}"

Requirements:
- Exactly {num_scenes} scenes
- Each scene should advance the story naturally
- Age-appropriate vocabulary for a {child_age}-year-old
- Cultural/geographic setting: {setting}
- End with a positive, uplifting conclusion
- Characters should be diverse and relatable to a global audience unless the setting specifies otherwise

Return the story as a JSON object following the schema in your instructions.
"""

# ---------------------------------------------------------------------------
# SSML / Emotion markup prompt — fetched from DB, hardcoded fallback
# ---------------------------------------------------------------------------

_STORY_MODEL = "openai/gpt-oss-20b"
_SSML_MODEL = "llama-3.3-70b-versatile"

_FALLBACK_SSML_SYSTEM_PROMPT = """\
You are a voice-acting director for a children's story TTS engine.

Your task: take a plain story paragraph and insert expressive markup tags so the \
narrator sounds natural, emotional, and engaging for children aged 4–12.

════════════════════════════════════════
AVAILABLE TAGS
════════════════════════════════════════
Emotion (set the narrator's tone for the sentence that follows):
  [happy]  [sad]  [angry]  [surprised]  [fearful]  [disgusted]

Delivery (change HOW the sentence is spoken):
  [laughing]   [whispering]

Non-verbal sounds (inserted as a beat, standalone between sentences):
  [breathe]  [laugh]  [sigh]  [clear_throat]  [cough]  [yawn]

Pauses (inserted between sentences at natural beats):
  <break time="500ms" />   <break time="1s" />

════════════════════════════════════════
STRICT PLACEMENT RULES
════════════════════════════════════════
1. Emotion tags  → always at the START of a sentence, never mid-sentence.
   ✓  [happy] She jumped up and clapped her hands.
   ✗  She jumped up [happy] and clapped her hands.

2. Delivery tags → always at the START of the sentence they modify.
   ✓  [whispering] "Don't wake the dragon," she said.
   ✗  "Don't wake [whispering] the dragon," she said.

3. Non-verbal sounds → standalone BETWEEN sentences, never inside one.
   ✓  He finally reached the top. [sigh] The view was beautiful.
   ✗  He finally [sigh] reached the top.

4. Pauses → standalone BETWEEN sentences, after punctuation.
   ✓  The door creaked open. <break time="500ms" /> No one was there.
   ✗  The door <break time="500ms" /> creaked open.

5. Only ONE emotion or delivery tag per sentence. Never stack two on the same sentence.
   ✗  [happy] [laughing] She danced around the room.
   ✓  [laughing] She danced around the room.

════════════════════════════════════════
GENERAL RULES
════════════════════════════════════════
- Output ONLY the marked-up text. No preamble, no explanation, no surrounding quotes.
- Do NOT alter, remove, or reorder any words in the original text.
- Use 3–6 tags total per paragraph. Do not over-tag.
- Prefer variety — do not repeat the same tag more than twice in one paragraph.
- Use [whispering] only for actual whispered/secret dialogue.
- Use <break time="1s" /> only at major scene pauses; use 500ms for lighter beats.

════════════════════════════════════════
EXAMPLES
════════════════════════════════════════

Input:
Rani looked up at the tall, tall mountain. "I can do this," she whispered to herself. She took a deep breath and began to climb.

Output:
Rani looked up at the tall, tall mountain. <break time="500ms" />[whispering] "I can do this," she whispered to herself. [breathe] She took a deep breath and began to climb.

---

Input:
The monkey swung from tree to tree, laughing as the birds chased him. "You can't catch me!" he shouted. But then he slipped and tumbled into the river with a big splash!

Output:
[happy] The monkey swung from tree to tree, laughing as the birds chased him. [laughing] "You can't catch me!" he shouted. <break time="500ms" />[surprised] But then he slipped and tumbled into the river with a big splash!

---

Input:
The forest was dark and quiet. Arjun could hear his own heartbeat. Somewhere far away, an owl hooted. He wanted to go home.

Output:
[fearful] The forest was dark and quiet. <break time="500ms" /> Arjun could hear his own heartbeat. <break time="1s" />Somewhere far away, an owl hooted. [sad] He wanted to go home.

---

Input:
"We did it!" cheered Maya, jumping up and down. The whole village came out to celebrate. There was music, dancing, and the biggest feast anyone had ever seen.

Output:
[happy] "We did it!" cheered Maya, jumping up and down. <break time="500ms" />The whole village came out to celebrate. [laughing] There was music, dancing, and the biggest feast anyone had ever seen.

---

Input:
The old man sat alone under the banyan tree. He had not eaten all day. A little girl walked up and offered him half her roti.

Output:
[sad] The old man sat alone under the banyan tree. [sigh] He had not eaten all day. <break time="500ms" />[surprised] A little girl walked up and offered him half her roti.
"""


def _get_ssml_system_prompt() -> str:
    return get_prompt_template("story_ssml") or _FALLBACK_SSML_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Defaults and limits
# ---------------------------------------------------------------------------

DEFAULT_NUM_SCENES = 4
MIN_NUM_SCENES = 2
MAX_NUM_SCENES = 8
DEFAULT_CHILD_AGE = 8
DEFAULT_GENRE = "adventure"
DEFAULT_SETTING = "universal"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_OUTPUT_FORMAT = "webp"


# ---------------------------------------------------------------------------
# StoryOrchestrationService
# ---------------------------------------------------------------------------


class StoryOrchestrationService:
    """
    Orchestrates the full multi-modal story generation pipeline.

    Designed to be instantiated once (or use the module-level singleton via
    ``get_story_orchestrator()``) and called for each story generation request.
    """

    def __init__(self) -> None:
        self._llm = get_llm_client()
        self._image_service = get_image_service()
        self._tts_service = get_tts_service()
        self._storage = get_r2_client()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def generate_story(
        self,
        idea: str,
        genre: str = DEFAULT_GENRE,
        num_scenes: int = DEFAULT_NUM_SCENES,
        child_age: int = DEFAULT_CHILD_AGE,
        setting: str = DEFAULT_SETTING,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        output_format: str = DEFAULT_OUTPUT_FORMAT,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a complete multi-modal story from a simple idea.

        Args:
            idea: The user's story concept (e.g. "a brave monkey on the moon").
            genre: Story genre — e.g. "adventure", "fable", "fantasy", "mystery".
            num_scenes: Number of story scenes/paragraphs (2–8, default 4).
            child_age: Target child's age for vocabulary calibration (default 8).
            setting: Cultural or geographic context hint for the Groq prompt.
            aspect_ratio: Target aspect ratio for Flux image generation.
            output_format: Image format — "webp", "jpg", or "png".

        Returns:
            A dict with the following structure::

                {
                  "title": "The Brave Monkey's Moon Adventure",
                  "scenes": [
                    {
                      "scene_number": 1,
                      "story_text": "Once upon a time...",
                      "image_prompt": "Vivid illustration of...",
                      "image_url": "https://pub-xxxxxx.r2.dev/...",  # or raw Replicate URL
                      "audio_url": "https://pub-xxxxxx.r2.dev/..."   # or None
                    },
                    ...
                  ],
                  "total_scenes": 4,
                  "images_generated": 3,
                  "audio_generated": 3,
                }

        Raises:
            ValueError: If the idea is empty or parameters are out of range.
            RuntimeError: If the Groq LLM call fails or returns invalid JSON.
        """
        # Validate inputs
        idea = idea.strip()
        if not idea:
            raise ValueError("Story idea cannot be empty")

        num_scenes = max(MIN_NUM_SCENES, min(num_scenes, MAX_NUM_SCENES))

        logger.info(
            f"Generating story: idea='{idea[:60]}' genre={genre} "
            f"scenes={num_scenes} child_age={child_age}"
        )

        # ── Step 1: Generate structured story content via Groq ────────────────
        raw_scenes = await self._generate_story_structure(
            idea=idea,
            genre=genre,
            num_scenes=num_scenes,
            child_age=child_age,
            setting=setting,
        )

        title = raw_scenes.get("title", "A New Adventure")
        scenes_data: list[dict] = raw_scenes.get("scenes", [])
        design_system_raw = raw_scenes.get("design_system")
        design_system: dict = design_system_raw if isinstance(design_system_raw, dict) else {}

        if not scenes_data:
            raise RuntimeError("Groq returned zero scenes — cannot build story")

        # Build the deterministic prompt prefix from the structured design system.
        # Falls back to legacy visual_style if design_system is absent (handles
        # the transitional window where the DB still serves the old prompt).
        if design_system:
            visual_prefix = _format_design_system_prompt(design_system)
        else:
            if design_system_raw is not None and not isinstance(design_system_raw, dict):
                logger.warning(
                    f"Groq returned unexpected type for 'design_system': {type(design_system_raw).__name__} — "
                    "falling back to visual_style"
                )
            visual_prefix = raw_scenes.get("visual_style", "")
            if not visual_prefix:
                logger.warning(
                    "Groq response missing both 'design_system' and 'visual_style' — "
                    "scene images may be visually inconsistent"
                )

        logger.info(f"Story structure ready: '{title}' — {len(scenes_data)} scenes")

        # ── Step 2 & 3: Generate media sequentially ───────────────────────────
        # IMPORTANT: For each scene, generate the image and upload it to R2
        # IMMEDIATELY before doing anything else (especially TTS). Replicate
        # delivery URLs expire in ~60 seconds — if TTS runs first, the image
        # URL may be dead by the time we try to download it for R2 upload.
        assembled_scenes = []
        for i, scene in enumerate(scenes_data, start=1):
            logger.info(f"Processing media for Scene {i}/{len(scenes_data)}...")
            story_text = scene.get("story_text", "")
            scene_prompt = scene.get("image_prompt", "")
            # Prepend the shared design system prefix so every scene image uses
            # identical art style, colours, lighting, and character descriptions.
            image_prompt = f"{visual_prefix} {scene_prompt}".strip() if visual_prefix else scene_prompt

            # ── Image: generate → upload immediately ──────────────────────────
            image_url = await self._generate_and_cache_image(
                scene_number=i,
                image_prompt=image_prompt,
                aspect_ratio=aspect_ratio,
                output_format=output_format,
            )

            # ── SSML: enrich plain text with emotion/pause markup for TTS ──
            tts_text = await self._add_ssml_markup(scene_number=i, story_text=story_text)

            # ── TTS: generate → upload (uses SSML-enriched text, not plain) ──
            audio_url = await self._generate_and_cache_audio(
                scene_number=i,
                story_text=tts_text,
            )
            # await asyncio.sleep(12)
            assembled_scenes.append({
                "scene_number": i,
                "story_text": story_text,
                "image_prompt": scene_prompt,  # original scene-specific prompt; design_system stored separately
                "image_url": image_url,
                "audio_url": audio_url,
            })

        images_generated = sum(1 for s in assembled_scenes if s["image_url"] is not None)
        audio_generated = sum(1 for s in assembled_scenes if s["audio_url"] is not None)

        logger.info(
            f"Story assembled: '{title}' — {len(assembled_scenes)} scenes, "
            f"{images_generated}/{len(assembled_scenes)} images, "
            f"{audio_generated}/{len(assembled_scenes)} audio files."
        )

        result = {
            "title": title,
            "scenes": assembled_scenes,
            "total_scenes": len(assembled_scenes),
            "images_generated": images_generated,
            "audio_generated": audio_generated,
            "design_system": design_system,
        }

        # Persist the story to the DB so a child can view it later.
        # Run as a background task so it never delays the response.
        if profile_id:
            asyncio.create_task(
                self._save_story(
                    profile_id=profile_id,
                    idea=idea,
                    genre=genre,
                    result=result,
                    design_system=design_system,
                ),
                name=f"save-story-{profile_id[:8]}",
            )

        return result

    # -----------------------------------------------------------------------
    # Private persistence helper
    # -----------------------------------------------------------------------

    async def _save_story(
        self,
        profile_id: str,
        idea: str,
        genre: str,
        result: dict,
        design_system: dict | None = None,
    ) -> str | None:
        """Persist a generated story to the ``stories`` table.

        Returns the new story UUID string, or ``None`` if the insert fails.
        """
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO stories
                        (profile_id, title, genre, idea,
                         total_segments, scenes_payload, design_system)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    uuid.UUID(profile_id),
                    result["title"],
                    genre,
                    idea,
                    result["total_scenes"],
                    json.dumps(result["scenes"]),
                    json.dumps(design_system or {}),
                )
            story_id = str(row["id"])
            logger.info(f"Story persisted: {story_id} — '{result['title']}'")
            return story_id
        except Exception:
            logger.exception("Failed to persist generated story")
            return None

    # -----------------------------------------------------------------------
    # Private media helpers
    # -----------------------------------------------------------------------

    async def _generate_and_cache_image(
        self,
        scene_number: int,
        image_prompt: str,
        aspect_ratio: str,
        output_format: str,
    ) -> str | None:
        """
        Generate an image via Replicate and immediately upload it to R2.

        The upload happens RIGHT AFTER generation because Replicate delivery
        URLs expire in ~60 seconds. Any delay (e.g. a TTS call) risks the
        URL becoming unreachable before we can download it.

        Falls back to the raw Replicate URL if R2 upload fails, so the
        caller always gets a usable URL when generation succeeds.

        Returns:
            R2 public URL (preferred), raw Replicate URL (fallback), or
            None if image generation itself failed.
        """
        if not image_prompt:
            logger.warning(f"Scene {scene_number}: no image_prompt, skipping image generation")
            return None

        raw_image_url = await self._image_service.generate_image(
            prompt=image_prompt,
            aspect_ratio=aspect_ratio,
            output_format=output_format,
        )

        if not raw_image_url:
            logger.error(f"Scene {scene_number}: image generation returned None")
            return None

        logger.debug(f"Scene {scene_number}: raw image URL received, uploading to GCP immediately...")

        # Upload to R2 right away — before any other async work for this scene
        image_key = f"story_images/{uuid.uuid4()}.{output_format}"
        content_type = f"image/{'jpeg' if output_format.lower() in ('jpg', 'jpeg') else output_format.lower()}"
        
        try:
            r2_url = await self._storage.upload_from_url(
                source_url=raw_image_url,
                r2_key=image_key,
                content_type=content_type,
            )
            logger.info(f"Scene {scene_number}: image cached at R2 → {r2_url[:80]}...")
            return r2_url
        except Exception:
            # R2 upload failed — fall back to the raw Replicate URL.
            # It may still be alive for a short time; better than returning None.
            logger.exception(
                f"Scene {scene_number}: R2 upload failed, falling back to raw Replicate URL. "
                f"This URL will expire shortly: {raw_image_url[:80]}..."
            )
            return raw_image_url

    async def _generate_and_cache_audio(
        self,
        scene_number: int,
        story_text: str,
    ) -> str | None:
        """
        Generate TTS audio and upload it to R2.

        TTS URLs also tend to be ephemeral, so we upload immediately.
        Falls back to the raw TTS URL if R2 upload fails.

        Returns:
            R2 public URL (preferred), raw TTS URL (fallback), or
            None if TTS generation itself failed.
        """
        if not story_text:
            logger.warning(f"Scene {scene_number}: no story_text, skipping TTS generation")
            return None

        raw_audio_url = await self._tts_service.generate_speech(
            text=story_text,
            voice="Ashley",
            speed=1.0,
        )

        if not raw_audio_url:
            logger.error(f"Scene {scene_number}: TTS generation returned None")
            return None

        audio_key = f"story_audio/{uuid.uuid4()}.wav"
        
        try:
            r2_url = await self._storage.upload_from_url(
                source_url=raw_audio_url,
                r2_key=audio_key,
                content_type="audio/wav",
            )
            logger.info(f"Scene {scene_number}: audio cached at R2 → {r2_url[:80]}...")
            return r2_url
        except Exception:
            logger.exception(
                f"Scene {scene_number}: R2 audio upload failed, falling back to raw TTS URL. "
                f"URL: {raw_audio_url[:80]}..."
            )
            return raw_audio_url

    # -----------------------------------------------------------------------
    # SSML markup helper
    # -----------------------------------------------------------------------

    async def _add_ssml_markup(self, scene_number: int, story_text: str) -> str:
        """
        Run the plain story text through an LLM pass to add expressive
        SSML / emotion markup tags for the TTS engine.

        If the LLM call fails, falls back to the original plain text so
        that TTS still works (just without expressiveness).
        """
        if not story_text:
            return story_text

        try:
            marked_up = await self._llm.generate_text(
                prompt=story_text,
                system_prompt=_get_ssml_system_prompt(),
                temperature=0.3,
                max_tokens=1000,
                model=_SSML_MODEL,
            )
            marked_up = marked_up.strip()
            if marked_up:
                logger.info(f"Scene {scene_number}: SSML markup applied → {marked_up}")
                return marked_up
        except Exception as e:
            logger.warning(
                f"Scene {scene_number}: SSML markup failed, using plain text — {e}"
            )

        return story_text

    # -----------------------------------------------------------------------
    # Private story structure helper
    # -----------------------------------------------------------------------

    async def _generate_story_structure(
        self,
        idea: str,
        genre: str,
        num_scenes: int,
        child_age: int,
        setting: str,
    ) -> dict[str, Any]:
        """
        Call Groq to generate the structured story JSON.

        Returns a dict with 'title' and 'scenes' keys.

        Raises:
            RuntimeError: If the LLM call fails, returns non-JSON, or the
                          schema is malformed.
        """
        user_prompt = _STORY_USER_PROMPT_TEMPLATE.format(
            genre=genre,
            child_age=child_age,
            idea=idea,
            num_scenes=num_scenes,
            setting=setting,
        )

        try:
            # SakhiLLM.generate_json() forces response_format=json_object,
            # returns a parsed dict, and handles low-level errors.
            result = await self._llm.generate_json(
                prompt=user_prompt,
                system_prompt=_get_story_system_prompt(),
                temperature=0.75,    # slightly higher for creative variation
                max_tokens=4096,     # enough for 8 scenes with detailed prompts
                model=_STORY_MODEL,
            )
        except Exception as e:
            logger.error(f"Groq LLM call failed during story generation: {e}", exc_info=True)
            raise RuntimeError(f"Story text generation failed: {e}") from e

        # Validate basic schema
        if not isinstance(result, dict):
            raise RuntimeError(f"Groq returned unexpected type: {type(result)}")

        if "scenes" not in result or not isinstance(result.get("scenes"), list):
            logger.error(f"Groq response missing 'scenes' array. Response: {result}")
            raise RuntimeError(
                "Groq response did not contain the expected 'scenes' array. "
                "This may be a transient issue — please try again."
            )

        if not result.get("design_system") and not result.get("visual_style"):
            logger.warning(
                "Groq response missing 'design_system' (and no legacy 'visual_style') — "
                "scene images may be visually inconsistent"
            )

        for i, scene in enumerate(result["scenes"]):
            if "story_text" not in scene:
                raise RuntimeError(f"Scene {i+1} is missing 'story_text'")
            if "image_prompt" not in scene:
                logger.warning(
                    f"Scene {i+1} is missing 'image_prompt' — falling back to truncated story_text"
                )
                scene["image_prompt"] = scene.get("story_text", "")[:200]

        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_orchestrator: StoryOrchestrationService | None = None


def get_story_orchestrator() -> StoryOrchestrationService:
    """Get the module-level singleton StoryOrchestrationService."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = StoryOrchestrationService()
    return _orchestrator