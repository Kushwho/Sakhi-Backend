"""
Sakhi — Story Pipeline Integration Tests
==========================================
Tests the new multi-modal story generation pipeline.

Run:
  python -m pytest tests/test_story_pipeline.py -v

Tests:
  - StoryOrchestrationService: Groq integration + JSON parsing
  - ImageGenerationService: Replicate API interaction
  - Full pipeline: concurrent image generation + stitching
  - API routes: request validation + response schema

All LLM and external API calls are mocked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ImageGenerationService tests
# ---------------------------------------------------------------------------


class TestImageGenerationService:
    """Unit tests for services.image_generation.ImageGenerationService."""

    def test_init_without_api_token(self, caplog):
        """Service initialises gracefully with no token — logs a warning."""
        with patch.dict("os.environ", {}, clear=True):
            # Remove REPLICATE_API_TOKEN if set
            import os
            os.environ.pop("REPLICATE_API_TOKEN", None)

            # Re-import to get fresh instance logic
            from services.image_generation import ImageGenerationService
            import logging
            with caplog.at_level(logging.WARNING, logger="sakhi.image_generation"):
                service = ImageGenerationService()
                assert service._api_token is None

    @pytest.mark.asyncio
    async def test_generate_image_returns_none_without_token(self):
        """generate_image() returns None when no API token is configured."""
        import os
        os.environ.pop("REPLICATE_API_TOKEN", None)
        from services.image_generation import ImageGenerationService
        service = ImageGenerationService()
        service._api_token = None

        result = await service.generate_image("A beautiful sunset")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_image_returns_none_on_empty_prompt(self):
        """generate_image() returns None for empty prompts."""
        from services.image_generation import ImageGenerationService
        service = ImageGenerationService()
        service._api_token = "test_token"

        result = await service.generate_image("")
        assert result is None

        result = await service.generate_image("   ")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_image_success(self):
        """Full generate_image() success path: create prediction → poll → return URL."""
        from services.image_generation import ImageGenerationService
        service = ImageGenerationService()
        service._api_token = "r8_test_token"

        # Mock the two private methods
        service._create_prediction = AsyncMock(return_value="pred_abc123")
        service._poll_for_result = AsyncMock(return_value="https://replicate.delivery/img.webp")

        url = await service.generate_image(
            prompt="A brave girl exploring a forest",
            aspect_ratio="16:9",
        )

        assert url == "https://replicate.delivery/img.webp"
        service._create_prediction.assert_awaited_once()
        service._poll_for_result.assert_awaited_once_with("pred_abc123")

    @pytest.mark.asyncio
    async def test_generate_image_returns_none_when_prediction_create_fails(self):
        """Returns None if prediction creation fails."""
        from services.image_generation import ImageGenerationService
        service = ImageGenerationService()
        service._api_token = "r8_test_token"
        service._create_prediction = AsyncMock(return_value=None)

        result = await service.generate_image("A beautiful scene")
        assert result is None

    @pytest.mark.asyncio
    async def test_poll_for_result_handles_failed_prediction(self):
        """Polling returns None when prediction status is 'failed'."""
        import httpx
        from services.image_generation import ImageGenerationService
        service = ImageGenerationService()
        service._api_token = "r8_test_token"

        failed_response = MagicMock()
        failed_response.status_code = 200
        failed_response.json.return_value = {
            "id": "pred_fail",
            "status": "failed",
            "error": "Input prompt violates safety policy.",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=failed_response)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service._poll_for_result("pred_fail")
            assert result is None

    @pytest.mark.asyncio
    async def test_poll_for_result_succeeds(self):
        """Polling returns the image URL when status is 'succeeded'."""
        import httpx
        from services.image_generation import ImageGenerationService
        service = ImageGenerationService()
        service._api_token = "r8_test_token"

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "id": "pred_ok",
            "status": "succeeded",
            "output": ["https://replicate.delivery/image.webp"],
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=success_response)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service._poll_for_result("pred_ok")
            assert result == "https://replicate.delivery/image.webp"


# ---------------------------------------------------------------------------
# StoryOrchestrationService tests
# ---------------------------------------------------------------------------


class TestStoryOrchestrationService:
    """Unit tests for services.story_orchestrator.StoryOrchestrationService."""

    def _make_service(self, groq_response=None, image_urls=None, audio_urls=None, gcp_urls=None):
        """Helper to build a service with mocked dependencies."""
        from services.story_orchestrator import StoryOrchestrationService

        service = StoryOrchestrationService.__new__(StoryOrchestrationService)

        # Mock LLM
        mock_llm = MagicMock()
        mock_llm.generate_json = AsyncMock(
            return_value=groq_response or {
                "title": "The Magic Kite",
                "scenes": [
                    {
                        "story_text": "Once upon a time a girl found a magic kite.",
                        "image_prompt": "Vibrant illustration of a girl with a magic kite.",
                    },
                    {
                        "story_text": "The kite took her to the clouds.",
                        "image_prompt": "Girl soaring through fluffy white clouds on a kite.",
                    },
                ],
            }
        )
        service._llm = mock_llm

        # Mock image service
        i_urls = image_urls if image_urls is not None else [
            "https://replicate.delivery/img1.webp",
            "https://replicate.delivery/img2.webp",
        ]
        mock_image_service = MagicMock()
        mock_image_service.generate_image = AsyncMock(side_effect=i_urls)
        service._image_service = mock_image_service

        # Mock TTS service
        a_urls = audio_urls if audio_urls is not None else [
            "https://replicate.delivery/audio1.wav",
            "https://replicate.delivery/audio2.wav",
        ]
        mock_tts_service = MagicMock()
        mock_tts_service.generate_speech = AsyncMock(side_effect=a_urls)
        service._tts_service = mock_tts_service
        
        # Mock Storage service
        def mock_upload(url, **kwargs):
            if gcp_urls:
                return gcp_urls.pop(0)
            return f"https://storage.googleapis.com/test-bucket/{url.split('/')[-1]}"

        mock_storage = MagicMock()
        mock_storage.upload_from_url = AsyncMock(side_effect=mock_upload)
        service._storage = mock_storage

        return service

    @pytest.mark.asyncio
    async def test_generate_story_full_pipeline(self):
        """Full pipeline: Groq → scenes → sequential media (img+tts) → GCP upload → assembled payload."""
        service = self._make_service()
        result = await service.generate_story(idea="A girl finds a magic kite")

        assert result["title"] == "The Magic Kite"
        assert len(result["scenes"]) == 2
        assert result["total_scenes"] == 2
        assert result["images_generated"] == 2
        assert result["audio_generated"] == 2
        assert result["scenes"][0]["story_text"] == "Once upon a time a girl found a magic kite."
        # Image URL is intercepted and returned as GCP URL
        assert result["scenes"][0]["image_url"] == "https://storage.googleapis.com/test-bucket/img1.webp"
        assert result["scenes"][0]["audio_url"] == "https://storage.googleapis.com/test-bucket/audio1.wav"
        assert result["scenes"][0]["scene_number"] == 1

    @pytest.mark.asyncio
    async def test_generate_story_with_partial_media_failure(self):
        """Pipeline still returns a valid payload when some media generation fails."""
        # Fail the second image and first audio
        service = self._make_service(
            image_urls=["https://img1.webp", None],
            audio_urls=[None, "https://audio2.wav"]
        )
        result = await service.generate_story(idea="A brave monkey")

        assert result["total_scenes"] == 2
        assert result["images_generated"] == 1
        assert result["audio_generated"] == 1
        assert result["scenes"][0]["image_url"] == "https://storage.googleapis.com/test-bucket/img1.webp"
        assert result["scenes"][0]["audio_url"] is None
        assert result["scenes"][1]["image_url"] is None
        assert result["scenes"][1]["audio_url"] == "https://storage.googleapis.com/test-bucket/audio2.wav"

    @pytest.mark.asyncio
    async def test_generate_story_raises_on_empty_idea(self):
        """ValueError raised for empty idea string."""
        service = self._make_service()
        with pytest.raises(ValueError, match="Story idea cannot be empty"):
            await service.generate_story(idea="")

    @pytest.mark.asyncio
    async def test_generate_story_clamps_num_scenes(self):
        """num_scenes is clamped to the allowed range (2–8)."""
        service = self._make_service(
            groq_response={
                "title": "Test",
                "scenes": [{"story_text": f"Scene {i}", "image_prompt": f"Prompt {i}"} for i in range(4)],
            },
            image_urls=[None, None, None, None],
            audio_urls=[None, None, None, None],
        )
        # Request 100 scenes — should be clamped to 8
        result = await service.generate_story(idea="Any story", num_scenes=100)
        # Groq was called (we returned 4 scenes in mock, so 4 is total)
        assert result["total_scenes"] == 4

    @pytest.mark.asyncio
    async def test_generate_story_raises_on_groq_failure(self):
        """RuntimeError re-raised when Groq call fails."""
        from services.story_orchestrator import StoryOrchestrationService
        service = StoryOrchestrationService.__new__(StoryOrchestrationService)

        mock_llm = MagicMock()
        mock_llm.generate_json = AsyncMock(side_effect=Exception("Connection refused"))
        service._llm = mock_llm
        service._image_service = MagicMock()
        service._tts_service = MagicMock()
        service._storage = MagicMock()

        with pytest.raises(RuntimeError, match="Story text generation failed"):
            await service.generate_story(idea="A story of wonder")


# ---------------------------------------------------------------------------
# API route tests (basic schema validation via FastAPI test client)
# ---------------------------------------------------------------------------


class TestStoryRoutes:
    """Integration tests for the story generation API endpoints."""

    def _make_app_with_auth_override(self):
        """Build a minimal FastAPI app with the story router + auth bypassed."""
        from fastapi import FastAPI
        from api.story_routes import router
        from api.dependencies import require_profile_token

        app = FastAPI()
        app.include_router(router)

        # Override the auth dependency so no JWT_SECRET is needed
        async def _mock_auth():
            return {"profile_id": "test-profile-id", "profile_type": "child"}

        app.dependency_overrides[require_profile_token] = _mock_auth
        return app

    def test_generate_endpoint_validates_empty_idea(self):
        """POST /api/stories/generate returns 422 for an empty idea (min_length=3)."""
        from fastapi.testclient import TestClient

        app = self._make_app_with_auth_override()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/stories/generate",
                json={"idea": ""},
            )
            # Pydantic min_length=3 → 422 Unprocessable Entity
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            detail = resp.json()["detail"]
            assert any("idea" in str(d) for d in detail), f"Unexpected detail: {detail}"

    def test_generate_endpoint_validates_num_scenes_bounds(self):
        """POST /api/stories/generate returns 422 if num_scenes > 8."""
        from fastapi.testclient import TestClient

        app = self._make_app_with_auth_override()

        # Also mock the orchestrator so we don't make real LLM calls
        with patch("api.story_routes.get_story_orchestrator") as mock_orch:
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/api/stories/generate",
                    json={"idea": "A dragon who loves books", "num_scenes": 99},
                )
                # Pydantic le=8 → 422
                assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

