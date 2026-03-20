"""
Sakhi — Story Orchestration Service
======================================
Generates multi-modal children's stories (text + images) on demand using:
  • Groq (via existing SakhiLLM layer) for structured scene generation
  • ImageGenerationService (Replicate / Flux Schnell) for per-scene illustrations
  • TTSService for per-scene audio narration
  • GCPStorageService for caching all media assets

Pipeline:
  1. Accept user's story idea + parameters (genre, num_scenes, child_age)
  2. Call Groq with a strict JSON schema prompt → get back story title + scenes
  3. Parse and validate the JSON response
  4. For each scene sequentially:
     a. Generate image → immediately upload to GCP (before URL expires ~60s)
     b. Generate TTS audio → upload to GCP
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
downloaded and uploaded to GCP IMMEDIATELY after generation — before any TTS
call adds delay. If GCP upload fails, we fall back to the raw Replicate URL
so the caller always gets a usable image URL.
"""

import asyncio
import json
import logging
from typing import Any

from services.image_generation import get_image_service
from services.llm import get_llm_client
from services.tts_generation import get_tts_service
from services.storage import get_storage_service

logger = logging.getLogger("sakhi.story_orchestrator")

# ---------------------------------------------------------------------------
# Groq system prompt — enforces strict JSON output schema
# ---------------------------------------------------------------------------

_STORY_SYSTEM_PROMPT = """\
You are a world-class children's story writer specialising in Indian children aged 4–12.

Your task is to write a vivid, imaginative, age-appropriate short story based on the user's idea.

STRICT RULES:
1. Output ONLY valid JSON. No prose, no explanation, no markdown fences.
2. The JSON must conform EXACTLY to this schema:
   {
     "title": "string — a short, catchy story title",
     "scenes": [
       {
         "story_text": "string — one full narrative paragraph (60–120 words). Expressive, child-friendly language.",
         "image_prompt": "string — a HIGHLY DETAILED visual prompt for an illustration of this exact scene. Include: art style, mood, colours, characters, setting, action. Example: 'Vibrant gouache illustration of a brave 8-year-old Indian girl with braids, wearing a red kurti, standing at the edge of a misty rainforest, holding a glowing lantern, wide-eyed with wonder, lush green canopy above, fireflies in background, warm amber light, storybook style, rich saturated colours.'"
       }
     ]
   }

3. The story must be child-safe — no violence, no inappropriate content, ever.
4. Calibrate vocabulary and complexity to the child's age (provided by the user).
5. Each scene's image_prompt must be self-contained and visually specific — describe it as if the artist has never read the story.
6. Do NOT include any text outside the JSON object.
"""

_STORY_USER_PROMPT_TEMPLATE = """\
Write a {genre} story for a {child_age}-year-old child about: "{idea}"

Requirements:
- Exactly {num_scenes} scenes
- Each scene should advance the story naturally
- Age-appropriate vocabulary for a {child_age}-year-old
- Set in a {setting} context where possible
- End with a positive, uplifting conclusion

Return the story as a JSON object following the schema in your instructions.
"""

# ---------------------------------------------------------------------------
# Defaults and limits
# ---------------------------------------------------------------------------

DEFAULT_NUM_SCENES = 4
MIN_NUM_SCENES = 2
MAX_NUM_SCENES = 8
DEFAULT_CHILD_AGE = 8
DEFAULT_GENRE = "adventure"
DEFAULT_SETTING = "Indian or universal"
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
        self._storage = get_storage_service()

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
                      "image_url": "https://storage.googleapis.com/...",  # or raw Replicate URL
                      "audio_url": "https://storage.googleapis.com/..."   # or None
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

        if not scenes_data:
            raise RuntimeError("Groq returned zero scenes — cannot build story")

        logger.info(f"Story structure ready: '{title}' — {len(scenes_data)} scenes")

        # ── Step 2 & 3: Generate media sequentially ───────────────────────────
        # IMPORTANT: For each scene, generate the image and upload it to GCP
        # IMMEDIATELY before doing anything else (especially TTS). Replicate
        # delivery URLs expire in ~60 seconds — if TTS runs first, the image
        # URL may be dead by the time we try to download it for GCP upload.
        assembled_scenes = []
        for i, scene in enumerate(scenes_data, start=1):
            logger.info(f"Processing media for Scene {i}/{len(scenes_data)}...")
            story_text = scene.get("story_text", "")
            image_prompt = scene.get("image_prompt", "")

            # ── Image: generate → upload immediately ──────────────────────────
            image_url = await self._generate_and_cache_image(
                scene_number=i,
                image_prompt=image_prompt,
                aspect_ratio=aspect_ratio,
                output_format=output_format,
            )
            # await asyncio.sleep(12)
            # ── TTS: generate → upload (no expiry concern, safe to do after) ──
            audio_url = await self._generate_and_cache_audio(
                scene_number=i,
                story_text=story_text,
            )
            # await asyncio.sleep(12)
            assembled_scenes.append({
                "scene_number": i,
                "story_text": story_text,
                "image_prompt": image_prompt,
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

        return {
            "title": title,
            "scenes": assembled_scenes,
            "total_scenes": len(assembled_scenes),
            "images_generated": images_generated,
            "audio_generated": audio_generated,
        }

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
        Generate an image via Replicate and immediately upload it to GCP.

        The upload happens RIGHT AFTER generation because Replicate delivery
        URLs expire in ~60 seconds. Any delay (e.g. a TTS call) risks the
        URL becoming unreachable before we can download it.

        Falls back to the raw Replicate URL if GCP upload fails, so the
        caller always gets a usable URL when generation succeeds.

        Returns:
            GCP public URL (preferred), raw Replicate URL (fallback), or
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

        # Upload to GCP right away — before any other async work for this scene
        gcp_url = await self._storage.upload_from_url(
            url=raw_image_url,
            destination_folder="story_images",
            file_ext=f".{output_format}",
        )

        if gcp_url:
            logger.info(f"Scene {scene_number}: image cached at GCP → {gcp_url[:80]}...")
            return gcp_url

        # GCP upload failed — fall back to the raw Replicate URL.
        # It may still be alive for a short time; better than returning None.
        logger.warning(
            f"Scene {scene_number}: GCP upload failed, falling back to raw Replicate URL. "
            f"This URL will expire shortly: {raw_image_url[:80]}..."
        )
        return raw_image_url

    async def _generate_and_cache_audio(
        self,
        scene_number: int,
        story_text: str,
    ) -> str | None:
        """
        Generate TTS audio and upload it to GCP.

        TTS URLs also tend to be ephemeral, so we upload immediately.
        Falls back to the raw TTS URL if GCP upload fails.

        Returns:
            GCP public URL (preferred), raw TTS URL (fallback), or
            None if TTS generation itself failed.
        """
        if not story_text:
            logger.warning(f"Scene {scene_number}: no story_text, skipping TTS generation")
            return None

        raw_audio_url = await self._tts_service.generate_speech(
            text=story_text,
            voice="af_alloy",
            speed=1.0,
        )

        if not raw_audio_url:
            logger.error(f"Scene {scene_number}: TTS generation returned None")
            return None

        gcp_url = await self._storage.upload_from_url(
            url=raw_audio_url,
            destination_folder="story_audio",
            file_ext=".wav",
        )

        if gcp_url:
            logger.info(f"Scene {scene_number}: audio cached at GCP → {gcp_url[:80]}...")
            return gcp_url

        logger.warning(
            f"Scene {scene_number}: GCP audio upload failed, falling back to raw TTS URL. "
            f"URL: {raw_audio_url[:80]}..."
        )
        return raw_audio_url

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
                system_prompt=_STORY_SYSTEM_PROMPT,
                temperature=0.75,    # slightly higher for creative variation
                max_tokens=4096,     # enough for 8 scenes with detailed prompts
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