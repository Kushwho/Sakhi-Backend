"""
Sakhi — GenType Image Generation Catalog
==========================================
Theme catalog and prompt builder for the GenType activity.
Pure data + logic — no I/O, no DB, no API calls.
Image generation itself goes through ``services.llm.SakhiLLM.generate_image()``.
"""

from typing import Any

GENTYPE_THEMES: list[dict[str, Any]] = [
    {
        "id": "dinosaurs",
        "name": "Dinosaur World",
        "emoji": "\U0001f995",
        "description": "Letters made from mighty dinosaurs and ancient bones!",
        "flux_style_suffix": (
            "constructed from intertwined dinosaurs and prehistoric bones, "
            "Jurassic foliage in the background, vibrant illustration style, "
            "child-friendly, warm earthy greens and oranges"
        ),
    },
    {
        "id": "space",
        "name": "Outer Space",
        "emoji": "\U0001f680",
        "description": "Letters made from planets, stars, and rocket ships!",
        "flux_style_suffix": (
            "formed from glowing planets, shooting stars, rocket ships and "
            "nebula clouds, deep blue and purple cosmic background, "
            "luminous neon highlights, child-friendly illustration"
        ),
    },
    {
        "id": "candy",
        "name": "Candy Land",
        "emoji": "\U0001f36d",
        "description": "Letters made from sweets, lollipops, and sprinkles!",
        "flux_style_suffix": (
            "built from lollipops, candy canes, gummy bears and sprinkles, "
            "pastel pink and mint background, glossy shiny surface, "
            "kawaii style, child-friendly illustration"
        ),
    },
    {
        "id": "ocean",
        "name": "Under the Ocean",
        "emoji": "\U0001f420",
        "description": "Letters made from fish, coral, and waves!",
        "flux_style_suffix": (
            "sculpted from tropical fish, coral reefs, sea shells and "
            "gentle waves, aquamarine and turquoise underwater background, "
            "dappled light rays, child-friendly watercolour illustration"
        ),
    },
    {
        "id": "jungle",
        "name": "Jungle Adventure",
        "emoji": "\U0001f33f",
        "description": "Letters made from animals and tropical leaves!",
        "flux_style_suffix": (
            "formed from lush tropical leaves, jungle animals like parrots "
            "and monkeys, vines and exotic flowers, rich greens and golds, "
            "flat vector illustration style, child-friendly"
        ),
    },
    {
        "id": "robots",
        "name": "Robot Factory",
        "emoji": "\U0001f916",
        "description": "Letters made from gears, bolts, and friendly robots!",
        "flux_style_suffix": (
            "constructed from metallic gears, cogs, bolts and smiling cartoon "
            "robots, silver and neon blue palette, industrial tech background, "
            "shiny chrome finish, child-friendly illustration"
        ),
    },
    {
        "id": "flowers",
        "name": "Magical Garden",
        "emoji": "\U0001f338",
        "description": "Letters made from flowers, butterflies, and vines!",
        "flux_style_suffix": (
            "woven from blooming flowers, butterflies, dewdrop vines and "
            "floating petals, soft pastel rainbow background, watercolour "
            "texture, magical whimsical style, child-friendly illustration"
        ),
    },
    {
        "id": "animals",
        "name": "Animal Kingdom",
        "emoji": "\U0001f418",
        "description": "Letters made from friendly animals from around the world!",
        "flux_style_suffix": (
            "built from friendly cartoon animals \u2014 elephants, giraffes, "
            "tigers and birds \u2014 in stacked or interlocking poses, "
            "bright savannah colours, flat bold illustration style, child-friendly"
        ),
    },
]

_THEME_INDEX: dict[str, dict[str, Any]] = {t["id"]: t for t in GENTYPE_THEMES}


def get_themes() -> list[dict[str, Any]]:
    """Return themes for the frontend picker (without internal flux_style_suffix)."""
    return [
        {"id": t["id"], "name": t["name"], "emoji": t["emoji"], "description": t["description"]}
        for t in GENTYPE_THEMES
    ]


def get_theme_by_id(theme_id: str) -> dict[str, Any] | None:
    """Look up a theme by ID. Returns the full dict (including flux_style_suffix) or None."""
    return _THEME_INDEX.get(theme_id)


def build_letter_prompt(letter: str, theme_id: str) -> str:
    """Build the Flux 1.1 Pro prompt for a single letter in a given theme.

    Args:
        letter: A single alphabetic character.
        theme_id: One of the theme IDs from ``GENTYPE_THEMES``.

    Returns:
        The complete image generation prompt.

    Raises:
        ValueError: If ``theme_id`` is not recognized.
    """
    theme = _THEME_INDEX.get(theme_id)
    if not theme:
        raise ValueError(f"Unknown theme_id: {theme_id!r}. Valid: {list(_THEME_INDEX.keys())}")

    upper = letter.upper()
    suffix = theme["flux_style_suffix"]

    return (
        f'A single large capital letter "{upper}" filling the entire frame, {suffix}. '
        f"The letter shape must be clearly readable. "
        f"Pure white background. Isolated single letter, no other text, no alphabet series. "
        f"Children's book illustration quality, vivid colours, clean composition. "
        f"Square format, 1:1 aspect ratio."
    )
