"""
Sakhi — Text To Speech Service
===================================
Standalone service for AI text-to-speech generation via Replicate API.

Model: inworld/tts-1.5-mini (Low-latency, expressive TTS — ~130ms P90)
Voice: Ashley (default)

Environment variable required:
  REPLICATE_API_TOKEN
"""

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("sakhi.tts_generation")

REPLICATE_API_BASE = "https://api.replicate.com/v1"
# Target model: Inworld TTS 1.5 Mini
TTS_MODEL = "inworld/tts-1.5-mini"

_POLL_INTERVAL_S = 1.0
_MAX_POLL_ATTEMPTS = 60
_REQUEST_TIMEOUT_S = 30.0

class TTSGenerationService:
    def __init__(self) -> None:
        self._api_token = os.getenv("REPLICATE_API_TOKEN")
        if not self._api_token:
            logger.warning("REPLICATE_API_TOKEN not set — TTS generation will be unavailable.")

    async def generate_speech(
        self,
        text: str,
        voice: str = "Ashley",
        speed: float = 1.0,
    ) -> str | None:
        """
        Generate speech from text using Inworld TTS 1.5 Mini via Replicate API.

        Args:
            text: Text to synthesize.
            voice: The voice ID to use (e.g., 'Ashley', 'Dennis', 'Alex', 'Darlene').
            speed: Speaking rate (0.5–1.5, default 1.0).

        Returns:
            The URL of the generated audio file (.mp3 or .wav), or None on failure.
        """
        if not self._api_token:
            logger.error("Cannot generate speech: REPLICATE_API_TOKEN missing")
            return None

        if not text or not text.strip():
            logger.error("Cannot generate speech: text is empty")
            return None

        logger.info(f"Requesting TTS generation: '{text[:80]}...' [voice={voice}]")

        try:
            prediction_id = await self._create_prediction(text, voice, speed)
            if not prediction_id:
                return None

            audio_url = await self._poll_for_result(prediction_id)
            if audio_url:
                logger.info(f"Speech generated successfully: {audio_url[:80]}...")
            return audio_url

        except asyncio.TimeoutError:
            logger.error(f"TTS generation timed out for text: '{text[:60]}...'")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during TTS generation: {e}", exc_info=True)
            return None

    async def _create_prediction(self, text: str, voice: str, speed: float) -> str | None:
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }
        payload = {
            "input": {
                "text": text,
                "voice": voice,
                "format": "mp3",
                "speaking_rate": speed,
            }
        }
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                response = await client.post(
                    f"{REPLICATE_API_BASE}/models/{TTS_MODEL}/predictions",
                    headers=headers,
                    json=payload,
                )
            if response.status_code not in (200, 201):
                logger.error(f"Replicate API error creating TTS prediction: HTTP {response.status_code} — {response.text[:200]}")
                return None
            return response.json().get("id")
        except httpx.HTTPError as e:
            logger.error(f"HTTP error creating Replicate prediction: {e}")
            return None

    async def _poll_for_result(self, prediction_id: str) -> str | None:
        headers = {"Authorization": f"Bearer {self._api_token}", "Content-Type": "application/json"}
        poll_url = f"{REPLICATE_API_BASE}/predictions/{prediction_id}"
        
        for attempt in range(1, _MAX_POLL_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                    response = await client.get(poll_url, headers=headers)

                if response.status_code != 200:
                    logger.error(f"Poll attempt {attempt}: HTTP {response.status_code} — {response.text[:200]}")
                    return None

                data = response.json()
                status = data.get("status", "unknown")

                if status == "succeeded":
                    output = data.get("output")
                    if isinstance(output, str):
                        return output
                    elif isinstance(output, list) and output:
                        return output[0]
                    return None
                elif status == "failed":
                    logger.error(f"Replicate TTS failed: {data.get('error')}")
                    return None
                elif status == "canceled":
                    logger.warning(f"Replicate TTS canceled: {prediction_id}")
                    return None

                await asyncio.sleep(_POLL_INTERVAL_S)

            except httpx.TimeoutException:
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            except Exception as e:
                logger.error(f"Unexpected error polling TTS prediction: {e}")
                return None

        logger.error(f"TTS generation timed out for {prediction_id}")
        return None

_tts_service: TTSGenerationService | None = None

def get_tts_service() -> TTSGenerationService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSGenerationService()
    return _tts_service
