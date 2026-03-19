"""
Hume Expression Measurement — Streaming Prosody Client
======================================================
Wraps the Hume Streaming API for real-time prosody-based emotion detection
from audio. Used by the emotion detector programmatic participant.
"""

import base64
import io
import logging
import wave

from hume import AsyncHumeClient
from hume.expression_measurement.stream import Config

logger = logging.getLogger("sakhi.hume")

# ---------------------------------------------------------------------------
# Hume 48 emotions → 6 Sakhi avatar expressions
# ---------------------------------------------------------------------------

HUME_TO_AVATAR: dict[str, str] = {
    "Joy": "happy",
    "Amusement": "happy",
    "Contentment": "happy",
    "Excitement": "excited",
    "Surprise (positive)": "excited",
    "Ecstasy": "excited",
    "Interest": "thinking",
    "Contemplation": "thinking",
    "Concentration": "thinking",
    "Confusion": "thinking",
    "Realization": "thinking",
    "Pride": "celebrating",
    "Triumph": "celebrating",
    "Satisfaction": "celebrating",
    "Admiration": "celebrating",
    "Sadness": "sad",
    "Disappointment": "sad",
    "Nostalgia": "sad",
    "Distress": "concerned",
    "Anxiety": "concerned",
    "Fear": "concerned",
    "Awkwardness": "concerned",
    "Doubt": "concerned",
}

DEFAULT_AVATAR_EXPRESSION = "happy"


def map_emotion_to_avatar(emotion_name: str) -> str:
    """Map a Hume prosody emotion to a Sakhi avatar expression."""
    return HUME_TO_AVATAR.get(emotion_name, DEFAULT_AVATAR_EXPRESSION)


# ---------------------------------------------------------------------------
# Hume Streaming Client
# ---------------------------------------------------------------------------


class HumeEmotionClient:
    """Async client for the Hume Expression Measurement Streaming API (prosody)."""

    def __init__(self, api_key: str) -> None:
        self._client = AsyncHumeClient(api_key=api_key)
        self._socket = None
        self._ctx_mgr = None
        self._config = Config(prosody={})

    async def connect(self) -> None:
        """Open a persistent WebSocket connection to Hume."""
        self._ctx_mgr = self._client.expression_measurement.stream.connect()
        self._socket = await self._ctx_mgr.__aenter__()
        logger.info("Hume streaming connection established")

    async def analyze_audio(self, audio_bytes: bytes) -> dict | None:
        """Send raw audio bytes and return the top 3 prosody emotions.

        Returns:
            dict with "top_emotions" list of (name, score) tuples, or None.
        """
        if not self._socket:
            return None

        try:
            # Wrap raw PCM bytes in a WAV header so Hume recognizes the format
            wav_io = io.BytesIO()
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(48000)
                wav_file.writeframes(audio_bytes)

            wav_bytes = wav_io.getvalue()
            encoded = base64.b64encode(wav_bytes).decode("utf-8")
            response = await self._socket.send_file(file_=encoded, config=self._config)

            if hasattr(response, "prosody") and response.prosody and response.prosody.predictions:
                emotions: list[tuple[str, float]] = []
                for pred in response.prosody.predictions:
                    sorted_emo = sorted(
                        [(e.name, e.score) for e in pred.emotions if e.name and e.score is not None],
                        key=lambda x: x[1],
                        reverse=True,
                    )
                    emotions.extend(sorted_emo[:3])
                return {"top_emotions": emotions[:3]}
        except Exception as e:
            logger.warning(f"Hume analyze_audio failed: {e}")

        return None

    async def close(self) -> None:
        """Close the streaming connection."""
        if self._ctx_mgr:
            try:
                await self._ctx_mgr.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx_mgr = None
            self._socket = None
            logger.info("Hume streaming connection closed")
