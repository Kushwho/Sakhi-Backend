"""
Sakhi — Image Generation Service
===================================
Standalone, reusable service for AI image generation via the Replicate API.

Model: black-forest-labs/flux-schnell (fast, high-quality Flux model)

This service is intentionally decoupled from any specific feature so it can
be imported and used by any other service (story generation, curio activities,
avatars, etc.).

Environment variable required:
  REPLICATE_API_TOKEN — your Replicate API token

Usage:
  from services.image_generation import ImageGenerationService

  service = ImageGenerationService()
  url = await service.generate_image(
      prompt="A vibrant watercolor painting of a young Indian girl flying a kite",
      aspect_ratio="16:9",
      output_format="webp",
  )
  # Returns image URL string, or None if generation failed.
"""

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("sakhi.image_generation")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Replicate API base URL
REPLICATE_API_BASE = "https://api.replicate.com/v1"

# Target model: Flux Schnell — fast, high-quality, great for illustrations
# FLUX_MODEL = "black-forest-labs/flux-1.1-pro"
FLUX_MODEL = "black-forest-labs/flux-schnell"
# Polling settings for async prediction completion
_POLL_INTERVAL_S = 1.5      # seconds between status checks
_MAX_POLL_ATTEMPTS = 40     # 40 × 1.5s = 60s max wait
_REQUEST_TIMEOUT_S = 30.0   # timeout for individual HTTP requests


# ---------------------------------------------------------------------------
# ImageGenerationService
# ---------------------------------------------------------------------------


class ImageGenerationService:
    """
    Replicate-powered image generation service using the Flux Schnell model.

    This is a stateless service — create one instance and reuse it, or
    call the module-level helper ``generate_image()`` for convenience.

    Error handling strategy:
    - Returns ``None`` instead of raising, so callers can decide how to
      handle missing images (e.g. return partial results to the client).
    - Logs all errors at ERROR level for observability.
    - Handles: missing API key, HTTP errors, API timeouts, content safety
      flags from Replicate (status='failed'), and JSON parse errors.
    """

    def __init__(self) -> None:
        self._api_token = os.getenv("REPLICATE_API_TOKEN")
        if not self._api_token:
            logger.warning(
                "REPLICATE_API_TOKEN not set — image generation will be unavailable. "
                "Set this environment variable to enable image generation."
            )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "16:9",
        output_format: str = "webp",
        num_outputs: int = 1,
        go_fast: bool = True,
    ) -> str | None:
        """
        Generate an image using Flux Schnell via the Replicate API.

        Args:
            prompt: Detailed textual description of the image to generate.
            aspect_ratio: Output aspect ratio (e.g. "16:9", "1:1", "4:3").
                          Supported values: "1:1", "16:9", "9:16", "4:3", "3:4",
                          "21:9", "9:21".
            output_format: Image format — "webp" (default, best compression),
                           "jpg", or "png".
            num_outputs: Number of images to generate (1–4). We return
                         only the first URL.
            go_fast: Enable Flux's fast mode for quicker generation.

        Returns:
            The URL of the generated image as a string, or ``None`` if
            generation failed for any reason.
        """
        print("Model used is ", FLUX_MODEL)
        if not self._api_token:
            logger.error("Cannot generate image: REPLICATE_API_TOKEN is not set")
            return None

        if not prompt or not prompt.strip():
            logger.error("Cannot generate image: prompt is empty")
            return None

        logger.info(f"Requesting image generation: '{prompt[:80]}...' [{aspect_ratio}, {output_format}]")

        try:
            prediction_id = await self._create_prediction(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                output_format=output_format,
                num_outputs=num_outputs,
                go_fast=go_fast,
            )

            if not prediction_id:
                return None

            image_url = await self._poll_for_result(prediction_id)
            if image_url:
                logger.info(f"Image generated successfully: {image_url[:80]}...")
                print("I have original image url ", image_url)
            return image_url

        except asyncio.TimeoutError:
            logger.error(f"Image generation timed out for prompt: '{prompt[:60]}...'")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during image generation: {e}", exc_info=True)
            return None

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _create_prediction(
        self,
        prompt: str,
        aspect_ratio: str,
        output_format: str,
        num_outputs: int,
        go_fast: bool,
    ) -> str | None:
        """
        Submit a new prediction to the Replicate API.

        Returns the prediction ID string, or None on failure.
        """
        headers = self._build_headers()
        payload: dict[str, Any] = {
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "output_format": output_format,
                "num_outputs": num_outputs,
                "go_fast": go_fast,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                response = await client.post(
                    f"{REPLICATE_API_BASE}/models/{FLUX_MODEL}/predictions",
                    headers=headers,
                    json=payload,
                )

            if response.status_code not in (200, 201):
                logger.error(
                    f"Replicate API error when creating prediction: "
                    f"HTTP {response.status_code} — {response.text[:200]}"
                )
                return None

            data = response.json()
            prediction_id = data.get("id")
            if not prediction_id:
                logger.error(f"Replicate response missing prediction ID: {data}")
                return None

            logger.debug(f"Prediction created: id={prediction_id}")
            return prediction_id

        except httpx.TimeoutException:
            logger.error("Timed out while creating Replicate prediction")
            return None
        except httpx.HTTPError as e:
            logger.error(f"HTTP error creating Replicate prediction: {e}")
            return None

    async def _poll_for_result(self, prediction_id: str) -> str | None:
        """
        Poll the Replicate API until the prediction succeeds, fails, or times out.

        Returns the first output image URL, or None on failure/timeout.
        """
        headers = self._build_headers()
        poll_url = f"{REPLICATE_API_BASE}/predictions/{prediction_id}"

        for attempt in range(1, _MAX_POLL_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                    response = await client.get(poll_url, headers=headers)

                if response.status_code != 200:
                    logger.error(
                        f"Poll attempt {attempt}: HTTP {response.status_code} — {response.text[:200]}"
                    )
                    return None

                data = response.json()
                status = data.get("status", "unknown")

                if status == "succeeded":
                    output = data.get("output")
                    if isinstance(output, list) and output:
                        return output[0]
                    if isinstance(output, str):
                        return output
                    logger.error(f"Prediction succeeded but output is empty: {data}")
                    return None

                elif status == "failed":
                    error_msg = data.get("error", "No error details provided")
                    logger.error(
                        f"Replicate prediction failed (content safety or model error): {error_msg}"
                    )
                    return None

                elif status == "canceled":
                    logger.warning(f"Replicate prediction was canceled: {prediction_id}")
                    return None

                # Status is 'starting' or 'processing' — keep polling
                logger.debug(f"Poll attempt {attempt}/{_MAX_POLL_ATTEMPTS}: status={status}")
                await asyncio.sleep(_POLL_INTERVAL_S)

            except httpx.TimeoutException:
                logger.warning(f"Poll attempt {attempt} timed out, retrying...")
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            except Exception as e:
                logger.error(f"Unexpected error polling prediction {prediction_id}: {e}")
                return None

        logger.error(
            f"Image generation timed out after {_MAX_POLL_ATTEMPTS} poll attempts "
            f"({_MAX_POLL_ATTEMPTS * _POLL_INTERVAL_S:.0f}s) for prediction {prediction_id}"
        )
        return None

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers for Replicate API requests."""
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }


# ---------------------------------------------------------------------------
# Module-level singleton + convenience function
# ---------------------------------------------------------------------------

_service: ImageGenerationService | None = None


def get_image_service() -> ImageGenerationService:
    """Get the module-level singleton ImageGenerationService instance."""
    global _service
    if _service is None:
        _service = ImageGenerationService()
    return _service


async def generate_image(
    prompt: str,
    aspect_ratio: str = "16:9",
    output_format: str = "webp",
    num_outputs: int = 1,
) -> str | None:
    """
    Module-level convenience wrapper for image generation.

    Equivalent to ``get_image_service().generate_image(...)``.
    Import this for simple use cases:

        from services.image_generation import generate_image
        url = await generate_image("A sunset over the Himalayas")
    """
    return await get_image_service().generate_image(
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        output_format=output_format,
        num_outputs=num_outputs,
    )
