"""
Sakhi — Central LLM & AI Provider
====================================
Provides a central abstraction for all AI services across the backend:
  - ``generate_json()``   — structured JSON from Groq text LLM
  - ``vision_json()``     — structured JSON from Groq vision LLM (image inputs)
  - ``generate_image()``  — image generation via Replicate
  - ``get_chat_model()``  — LangChain ``ChatGroq`` for LangGraph chat pipeline
"""

import json
import logging
import os
from typing import Any

import replicate
from groq import AsyncGroq
from langchain_groq import ChatGroq

logger = logging.getLogger("sakhi.llm")

# Default models
DEFAULT_MODEL = os.getenv("SAKHI_DEFAULT_LLM_MODEL", "llama-3.1-8b-instant")
DEFAULT_VISION_MODEL = os.getenv("SAKHI_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
DEFAULT_IMAGE_MODEL = os.getenv("SAKHI_IMAGE_MODEL", "black-forest-labs/flux-schnell")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


class SakhiLLM:
    """
    Central wrapper for LLM interactions.
    Manages the underlying AsyncGroq client and provides standard generation methods.
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        # The AsyncGroq client expects GROQ_API_KEY in the environment.
        # Pass dummy key if none exists so tests/imports don't crash.
        api_key = GROQ_API_KEY or "dummy_key_for_tests"
        self.client = AsyncGroq(api_key=api_key)

    # -----------------------------------------------------------------
    # Raw Groq interface (used by session_summarizer, etc.)
    # -----------------------------------------------------------------

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 500,
    ) -> Any:
        """
        Generate a JSON response from the LLM.

        Args:
            prompt: The user prompt or main instructions.
            system_prompt: Optional system instructions.
            temperature: Sampling temperature (default 0.3).
            max_tokens: Maximum tokens to generate (default 500).

        Returns:
            The parsed JSON response as a dictionary.

        Raises:
            Exception: If the underlying LLM call fails or returns invalid JSON.
        """
        import json

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return json.loads(content)

        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise

    # -----------------------------------------------------------------
    # Vision interface (used by SWYS judge, etc.)
    # -----------------------------------------------------------------

    async def vision_json(
        self,
        image_urls: list[str],
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 300,
        model: str | None = None,
    ) -> Any:
        """
        Send image(s) + text prompt to a Groq vision model and parse JSON.

        Args:
            image_urls: List of public image URLs to analyse.
            prompt: Text instructions for the vision model.
            system_prompt: Optional system instructions.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            model: Override vision model (defaults to SAKHI_VISION_MODEL).

        Returns:
            Parsed JSON dict from the model response.
        """
        vision_model = model or DEFAULT_VISION_MODEL

        user_content: list[dict] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": url}})

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})

        try:
            response = await self.client.chat.completions.create(
                model=vision_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            logger.error(f"Vision LLM call failed: {e}", exc_info=True)
            raise

    # -----------------------------------------------------------------
    # Image generation (Replicate)
    # -----------------------------------------------------------------

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_IMAGE_MODEL,
        width: int = 1024,
        height: int = 1024,
        output_format: str = "webp",
        output_quality: int = 80,
    ) -> str:
        """
        Generate an image via Replicate and return its public URL.

        Args:
            prompt: Text prompt describing the desired image.
            model: Replicate model identifier.
            width: Image width in pixels.
            height: Image height in pixels.
            output_format: "webp", "png", or "jpg".
            output_quality: Quality 1-100 (for lossy formats).

        Returns:
            Public URL of the generated image.

        Raises:
            RuntimeError: If generation fails or returns no output.
        """
        logger.info(f"Generating image: model={model}, prompt={prompt[:80]!r}")
        try:
            output = await replicate.async_run(
                model,
                input={
                    "prompt": prompt,
                    "width": width,
                    "height": height,
                    "output_format": output_format,
                    "output_quality": output_quality,
                },
            )
            if output is None:
                raise RuntimeError("Replicate returned no output")
            # Replicate returns a list of FileOutput objects.
            # Each FileOutput has a .url() method that returns the CDN URL.
            item = output[0] if isinstance(output, list) else output
            url = item.url() if hasattr(item, "url") and callable(item.url) else str(item)
            logger.info(f"Image generated: {url[:80]}")
            return url
        except Exception as e:
            logger.error(f"Image generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Image generation failed: {e}") from e

    # -----------------------------------------------------------------
    # LangChain interface (used by LangGraph chat pipeline)
    # -----------------------------------------------------------------

    def get_langchain_chat_model(self) -> ChatGroq:
        """Return a LangChain ``ChatGroq`` sharing this instance's config.

        The returned model is suitable for use inside a LangGraph
        ``StateGraph`` node and supports streaming out of the box.
        """
        return ChatGroq(
            model=self.model,
            api_key=GROQ_API_KEY or "dummy_key_for_tests",
            streaming=True,
        )


# Lazy singleton — instantiated on first access, not at import time.
# This prevents AsyncGroq() from being called during test collection
# before any mocks are in place.
_default_llm: SakhiLLM | None = None


def get_llm_client() -> SakhiLLM:
    """Get the default configured LLM client (lazy singleton)."""
    global _default_llm
    if _default_llm is None:
        _default_llm = SakhiLLM()
    return _default_llm


def get_chat_model() -> ChatGroq:
    """Get the default LangChain chat model via the centralized LLM layer."""
    return get_llm_client().get_langchain_chat_model()
