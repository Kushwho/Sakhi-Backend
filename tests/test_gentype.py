"""
Tests for the GenType feature — theme catalog, prompt builder, and API endpoints.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from services.image_gen import (
    get_themes,
    get_theme_by_id,
    build_letter_prompt,
    GENTYPE_THEMES,
    _THEME_INDEX,
)


# ---------------------------------------------------------------------------
# Theme catalog tests
# ---------------------------------------------------------------------------


class TestThemeCatalog:
    """Tests for the static theme catalog and lookup helpers."""

    def test_get_themes_returns_list(self):
        themes = get_themes()
        assert isinstance(themes, list)
        assert len(themes) == 8

    def test_theme_has_required_fields(self):
        for theme in get_themes():
            assert "id" in theme
            assert "name" in theme
            assert "emoji" in theme
            assert "description" in theme

    def test_theme_does_not_expose_flux_suffix(self):
        for theme in get_themes():
            assert "flux_style_suffix" not in theme

    def test_theme_ids_are_unique(self):
        ids = [t["id"] for t in get_themes()]
        assert len(ids) == len(set(ids))

    def test_get_theme_by_id_found(self):
        theme = get_theme_by_id("space")
        assert theme is not None
        assert theme["name"] == "Outer Space"
        assert "flux_style_suffix" in theme

    def test_get_theme_by_id_not_found(self):
        assert get_theme_by_id("nonexistent") is None

    def test_internal_themes_have_flux_suffix(self):
        for theme in GENTYPE_THEMES:
            assert "flux_style_suffix" in theme
            assert len(theme["flux_style_suffix"]) > 20


# ---------------------------------------------------------------------------
# Prompt builder tests
# ---------------------------------------------------------------------------


class TestBuildLetterPrompt:
    """Tests for the Flux prompt builder."""

    def test_prompt_contains_letter(self):
        prompt = build_letter_prompt("A", "space")
        assert '"A"' in prompt

    def test_prompt_contains_theme_style(self):
        prompt = build_letter_prompt("B", "candy")
        assert "lollipop" in prompt.lower() or "candy" in prompt.lower()

    def test_prompt_uppercases_letter(self):
        prompt = build_letter_prompt("a", "space")
        assert '"A"' in prompt
        assert '"a"' not in prompt

    def test_unknown_theme_raises(self):
        with pytest.raises(ValueError, match="Unknown theme_id"):
            build_letter_prompt("A", "nonexistent_theme")

    def test_prompt_has_child_safe_framing(self):
        prompt = build_letter_prompt("C", "jungle")
        assert "children" in prompt.lower() or "child" in prompt.lower()

    def test_prompt_has_no_other_text_instruction(self):
        prompt = build_letter_prompt("D", "robots")
        assert "no other text" in prompt.lower()

    def test_prompt_requests_white_background(self):
        prompt = build_letter_prompt("E", "flowers")
        assert "white background" in prompt.lower()


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestGentypeEndpoints:
    """Tests for the GenType API endpoints."""

    @pytest.fixture
    def client(self):
        from api.routes import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def override_auth(self):
        from api.routes import app
        from api.dependencies import require_profile_token
        app.dependency_overrides[require_profile_token] = lambda: {
            "profile_id": "test-profile-id",
            "profile_type": "child",
        }
        yield
        app.dependency_overrides.clear()

    def test_list_themes(self, client):
        response = client.get("/api/curio/gentype/themes")
        assert response.status_code == 200
        data = response.json()
        assert "themes" in data
        assert len(data["themes"]) == 8
        assert all("flux_style_suffix" not in t for t in data["themes"])

    @patch("api.gentype_routes.get_current_profile", new_callable=AsyncMock)
    @patch("api.gentype_routes.get_pool")
    @patch("api.gentype_routes.get_llm_client")
    def test_generate_cache_hit(self, mock_llm, mock_pool, mock_profile, client):
        mock_profile.return_value = {"display_name": "Riya", "age": 7}

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"image_url": "https://example.com/cached.webp"})
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool.return_value.acquire.return_value = mock_ctx

        response = client.post("/api/curio/gentype/generate", json={
            "theme_id": "space", "letter": "A", "force_regenerate": False
        })
        assert response.status_code == 200
        data = response.json()
        assert data["from_cache"] is True
        assert data["image_url"] == "https://example.com/cached.webp"
        mock_llm.return_value.generate_image.assert_not_called()

    @patch("api.gentype_routes.get_current_profile", new_callable=AsyncMock)
    @patch("api.gentype_routes.get_pool")
    @patch("api.gentype_routes.get_llm_client")
    def test_generate_cache_miss(self, mock_llm, mock_pool, mock_profile, client):
        mock_profile.return_value = {"display_name": "Riya", "age": 7}

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool.return_value.acquire.return_value = mock_ctx

        mock_llm.return_value.generate_image = AsyncMock(return_value="https://replicate.com/new.webp")

        response = client.post("/api/curio/gentype/generate", json={
            "theme_id": "space", "letter": "A", "force_regenerate": False
        })
        assert response.status_code == 200
        data = response.json()
        assert data["from_cache"] is False
        assert data["image_url"] == "https://replicate.com/new.webp"

    def test_generate_invalid_letter(self, client):
        response = client.post("/api/curio/gentype/generate", json={
            "theme_id": "space", "letter": "123"
        })
        assert response.status_code == 400

    def test_generate_invalid_theme(self, client):
        response = client.post("/api/curio/gentype/generate", json={
            "theme_id": "nonexistent", "letter": "A"
        })
        assert response.status_code == 400

    def test_parent_token_rejected(self, client):
        from api.routes import app
        from api.dependencies import require_profile_token
        app.dependency_overrides[require_profile_token] = lambda: {
            "profile_id": "test-id",
            "profile_type": "parent",
        }
        response = client.post("/api/curio/gentype/generate", json={
            "theme_id": "space", "letter": "A"
        })
        assert response.status_code == 403

    @patch("api.gentype_routes.get_current_profile", new_callable=AsyncMock)
    @patch("api.gentype_routes.get_pool")
    @patch("api.gentype_routes.get_llm_client")
    def test_spell_name_deduplicates(self, mock_llm, mock_pool, mock_profile, client):
        mock_profile.return_value = {"display_name": "Aanya", "age": 6}

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool.return_value.acquire.return_value = mock_ctx

        async def side_effect(prompt, **kwargs):
            # Extract letter from prompt
            letter = prompt.split('"')[1]
            return f"https://example.com/{letter}.webp"

        mock_llm.return_value.generate_image = AsyncMock(side_effect=side_effect)

        response = client.post("/api/curio/gentype/spell-name", json={"theme_id": "candy"})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Aanya"
        letters = [l["letter"] for l in data["letters"]]
        assert letters == ["A", "N", "Y"]
        assert data["has_errors"] is False
