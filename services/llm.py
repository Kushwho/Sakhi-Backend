"""
Sakhi — Central LLM Provider
=============================
Provides a central abstraction for LLM access across backend services.
Currently configured to use Groq with the Llama 3.1 8B instant model.

Exposes two interfaces:
  - ``generate_json()``  — raw AsyncGroq calls (session summariser, etc.)
  - ``get_chat_model()`` — LangChain ``ChatGroq`` for the LangGraph chat pipeline
"""

import logging
import os
from typing import Any

from groq import AsyncGroq
from langchain_groq import ChatGroq

logger = logging.getLogger("sakhi.llm")

# Default model used across the application
DEFAULT_MODEL = os.getenv("SAKHI_DEFAULT_LLM_MODEL", "llama-3.1-8b-instant")
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