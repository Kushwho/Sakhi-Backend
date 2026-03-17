"""
Sakhi — System Prompt Service
===============================
Loads system prompts from PostgreSQL, caches them in memory, and assembles
the final prompt for each conversation mode.

The cache is pre-populated with hardcoded defaults so the system always works
— even in agent processes that never call ``load_prompts()``.  When FastAPI
starts, ``load_prompts(pool)`` overwrites the defaults with DB values,
picking up any edits made via ``update_prompt()``.

Supports version history for rollback via the ``prompt_versions`` table.
"""

import logging
from typing import Any

import asyncpg

logger = logging.getLogger("sakhi.prompts")

# ---------------------------------------------------------------------------
# Default prompt templates (always available, even without DB)
# ---------------------------------------------------------------------------

_DEFAULT_PROMPTS: dict[str, str] = {
    "base": (
        "You are Sakhi, a warm, curious, and encouraging AI companion "
        "for Indian children aged 4\u201312.\n\n"
        "Personality:\n"
        "- You are playful, patient, and full of wonder.\n"
        "- You celebrate small wins enthusiastically.\n"
        "- You speak in short, simple sentences appropriate for children.\n"
        "- You are strictly child-safe \u2014 no violence, no inappropriate content, ever.\n\n"
        "Teaching style:\n"
        "- You NEVER give direct homework answers.\n"
        "- You use Socratic questioning \u2014 ask guiding questions to help children "
        "discover answers themselves.\n"
        "- When explaining concepts, use fun analogies, stories, and examples "
        "from everyday Indian life.\n\n"
        "Conversation style:\n"
        "- Keep responses concise (2-3 sentences max for young children, "
        "up to 4-5 for older ones).\n"
        "- Use a cheerful, expressive tone.\n"
        "- If the child seems confused, simplify and try a different approach.\n"
        "- If the child seems sad or upset, be empathetic and supportive.\n\n"
        "You are currently talking to {child_name}, who is {child_age} years old "
        "and prefers {child_language}.\n"
        "Adjust your vocabulary and complexity to match their age."
    ),
    "curious_open": (
        "You are in Curious Mode \u2014 the child has chosen to explore freely!\n\n"
        "Encourage their natural curiosity. When they ask about something:\n"
        "1. Express genuine excitement about their question\n"
        "2. Ask a thought-provoking question back before explaining\n"
        "3. Connect their question to something they might already know\n"
        "4. End with \"What do you think?\" or a follow-up wonder question\n\n"
        "Never lecture. Keep it conversational and wonder-driven."
    ),
    "curious_topic": (
        "You are in Curious Mode exploring the topic: {topic_title}.\n"
        "{topic_description}\n\n"
        "Guide the child through this topic using Socratic questioning:\n"
        "1. Start by asking what they already know about {topic_title}\n"
        "2. Build on their answers with deeper questions\n"
        "3. Use fun analogies from everyday Indian life\n"
        "4. If they seem stuck, offer a small hint as a question\n"
        "5. Celebrate their reasoning, even if imperfect\n\n"
        "Stay focused on this topic but follow the child's tangents \u2014 "
        "curiosity is never wrong."
    ),
    "curious_surprise": (
        "You are in Surprise Mode! Start the conversation by sharing "
        "this amazing fact:\n\n"
        "\"{surprise_fact}\"\n\n"
        "After sharing the fact:\n"
        "1. Ask the child what they think about it\n"
        "2. Use Socratic questioning to explore WHY or HOW\n"
        "3. Connect it to something in their everyday life\n"
        "4. Let the conversation flow naturally from there"
    ),
    "surprise_generator": (
        "Generate ONE amazing, mind-blowing fact appropriate for a "
        "{child_age}-year-old Indian child.\n"
        "The fact should be:\n"
        "- True and scientifically accurate\n"
        "- Genuinely surprising and delightful\n"
        "- Easy to explain in 1-2 sentences\n"
        "- Related to: {category}\n\n"
        'Return JSON: {{"fact": "...", "topic": "...", "follow_up_question": "..."}}'
    ),
    "curio_say_what_you_see": (
        "You are playing 'Say What You See' with {child_name}!\n\n"
        "An image is being shown that depicts: {scene_description}\n\n"
        "Your role:\n"
        "1. Ask the child to describe what they see in the image\n"
        "2. Respond with excitement to whatever they notice\n"
        "3. Ask follow-up questions: 'What colours do you see?', 'What is happening?', 'How does it make you feel?'\n"
        "4. Connect their observations to interesting facts or stories\n"
        "5. Keep it playful — there are no wrong answers!\n\n"
        "Encourage them to look carefully and imagine freely."
    ),
    "curio_gentype": (
        "You are playing 'GenType' — a creative alphabet design game with {child_name}!\n\n"
        "In this game, {child_name} will design their own custom alphabet where each letter "
        "is made from a theme they choose (like letters made of animals, stars, food, etc.).\n\n"
        "Your role:\n"
        "1. Ask {child_name} what theme they'd like for their alphabet — suggest 2-3 fun ideas to spark imagination\n"
        "2. For each letter they pick, help them imagine what it would look like in their chosen theme\n"
        "3. Ask descriptive questions: 'What part of the elephant would make the letter A?'\n"
        "4. Celebrate every creative idea enthusiastically\n"
        "5. Help them describe their letter designs in vivid detail\n\n"
        "Make this a joyful, imaginative experience — the wilder the ideas, the better!"
    ),
    "curio_say_what_you_see_generator": (
        "Generate a colourful, child-friendly scene description for a {child_age}-year-old Indian child.\n"
        "The scene should be:\n"
        "- Visually rich and full of interesting details to spot\n"
        "- Age-appropriate and safe\n"
        "- Set in a familiar or magical context (Indian market, jungle, space station, underwater, etc.)\n"
        "- Include characters, objects, colours, and actions\n\n"
        'Return JSON: {{"scene_description": "A detailed description of the scene for Sakhi to reference...", '
        '"scene_prompt": "A short image generation prompt (for future use)", '
        '"discussion_starters": ["question1", "question2", "question3"]}}'
    ),
}

# ---------------------------------------------------------------------------
# In-memory cache — starts with defaults, overwritten by DB on load
# ---------------------------------------------------------------------------

_prompt_cache: dict[str, str] = dict(_DEFAULT_PROMPTS)


async def load_prompts(pool: asyncpg.Pool) -> None:
    """Load active prompt templates from the database, overwriting defaults."""
    global _prompt_cache
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT mode, prompt_template FROM system_prompts WHERE is_active = true"
        )
    # Start from defaults, then overlay DB values
    _prompt_cache = dict(_DEFAULT_PROMPTS)
    for row in rows:
        _prompt_cache[row["mode"]] = row["prompt_template"]
    logger.info(f"Loaded {len(rows)} system prompts from DB (cache has {len(_prompt_cache)} total)")


async def reload_prompts(pool: asyncpg.Pool) -> None:
    """Refresh the in-memory prompt cache from the database."""
    await load_prompts(pool)


def get_prompt_template(mode: str) -> str | None:
    """Return the cached prompt template for a given mode."""
    return _prompt_cache.get(mode)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_system_prompt(
    child_name: str,
    child_age: int,
    child_language: str,
    mode: str = "default",
    topic: dict[str, Any] | None = None,
    surprise_fact: str | None = None,
    extra_context: dict[str, str] | None = None,
) -> str:
    """Assemble the full system prompt from base + mode-specific addon.

    Args:
        child_name: Display name of the child.
        child_age: Age of the child.
        child_language: Preferred language.
        mode: One of "default", "curious_open", "curious_topic", "curious_surprise".
        topic: Dict with ``title`` and ``description`` keys (for curious_topic).
        surprise_fact: The fact string (for curious_surprise).

    Returns:
        The complete system prompt string.
    """
    base = _prompt_cache.get("base", _DEFAULT_PROMPTS["base"])

    # Fill base placeholders
    prompt = base.format(
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
    )

    # Append mode-specific addon
    if mode != "default":
        addon = _prompt_cache.get(mode)
        if addon:
            # Fill mode-specific placeholders
            format_kwargs: dict[str, str] = {}
            if topic:
                format_kwargs["topic_title"] = topic.get("title", "")
                format_kwargs["topic_description"] = topic.get("description", "")
            if surprise_fact:
                format_kwargs["surprise_fact"] = surprise_fact
            if extra_context:
                format_kwargs.update(extra_context)

            if format_kwargs:
                addon = addon.format(**format_kwargs)

            prompt += "\n\n" + addon
        else:
            logger.warning(f"Prompt addon for mode '{mode}' not found in cache")

    return prompt


# ---------------------------------------------------------------------------
# Prompt version management
# ---------------------------------------------------------------------------


async def update_prompt(
    pool: asyncpg.Pool,
    mode: str,
    new_template: str,
) -> dict:
    """Update a prompt template, archive the old version, and refresh cache.

    Returns:
        Dict with ``mode``, ``version``, and ``updated_at``.
    """
    async with pool.acquire() as conn:
        # Fetch current version
        current = await conn.fetchrow(
            "SELECT id, version, prompt_template FROM system_prompts WHERE mode = $1",
            mode,
        )
        if not current:
            raise ValueError(f"No prompt found for mode '{mode}'")

        old_version = current["version"]
        new_version = old_version + 1

        # Archive old version
        await conn.execute(
            """
            INSERT INTO prompt_versions (prompt_id, mode, prompt_template, version)
            VALUES ($1, $2, $3, $4)
            """,
            current["id"],
            mode,
            current["prompt_template"],
            old_version,
        )

        # Update current prompt
        row = await conn.fetchrow(
            """
            UPDATE system_prompts
            SET prompt_template = $1, version = $2, updated_at = now()
            WHERE mode = $3
            RETURNING mode, version, updated_at
            """,
            new_template,
            new_version,
            mode,
        )

    # Refresh cache
    await load_prompts(pool)

    return {
        "mode": row["mode"],
        "version": row["version"],
        "updated_at": str(row["updated_at"]),
    }
