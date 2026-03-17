"""
Tests for Curious Mode
========================
Covers: prompt assembly, topic catalog, curious API endpoints, and mode
passthrough in chat routes.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.prompts import build_system_prompt, _prompt_cache
from services.topics import get_topics_for_age, get_topic_by_id, get_topics_response


# ---------------------------------------------------------------------------
# Prompt assembly tests
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    """Tests for build_system_prompt() with various modes."""

    def test_default_mode_returns_base_only(self):
        prompt = build_system_prompt("Arjun", 8, "English", mode="default")
        assert "Arjun" in prompt
        assert "8" in prompt
        assert "English" in prompt
        assert "Curious Mode" not in prompt

    def test_base_prompt_has_safety_rules(self):
        prompt = build_system_prompt("Priya", 10, "Hindi")
        assert "child-safe" in prompt
        assert "Socratic" in prompt
        assert "NEVER give direct homework answers" in prompt

    def test_curious_open_appends_addon(self):
        prompt = build_system_prompt("Riya", 7, "English", mode="curious_open")
        assert "Riya" in prompt
        assert "Curious Mode" in prompt
        assert "What do you think?" in prompt

    def test_curious_topic_fills_placeholders(self):
        topic = {"title": "Black Holes", "description": "Explore space mysteries!"}
        prompt = build_system_prompt(
            "Aarav", 10, "English",
            mode="curious_topic",
            topic=topic,
        )
        assert "Aarav" in prompt
        assert "Black Holes" in prompt
        assert "Explore space mysteries!" in prompt
        assert "Socratic questioning" in prompt

    def test_curious_surprise_fills_fact(self):
        prompt = build_system_prompt(
            "Meera", 6, "Tamil",
            mode="curious_surprise",
            surprise_fact="Octopuses have three hearts!",
        )
        assert "Meera" in prompt
        assert "Octopuses have three hearts!" in prompt
        assert "Surprise Mode" in prompt

    def test_unknown_mode_returns_base_with_warning(self):
        prompt = build_system_prompt("Test", 8, "English", mode="nonexistent_mode")
        # Should still return the base prompt without crashing
        assert "Test" in prompt
        assert "8" in prompt

    def test_curious_topic_without_topic_dict(self):
        """Mode is curious_topic but no topic provided — addon has raw placeholders."""
        prompt = build_system_prompt("Kid", 8, "English", mode="curious_topic")
        # Should not crash; addon is appended but placeholders remain unfilled
        assert "Kid" in prompt


# ---------------------------------------------------------------------------
# Topic catalog tests
# ---------------------------------------------------------------------------


class TestTopicCatalog:
    """Tests for the static topic catalog."""

    def test_get_topics_for_young_child(self):
        topics = get_topics_for_age(4)
        assert len(topics) > 0
        for t in topics:
            assert t["age_range"][0] <= 4 <= t["age_range"][1]

    def test_get_topics_for_older_child(self):
        topics = get_topics_for_age(12)
        assert len(topics) > 0
        for t in topics:
            assert t["age_range"][0] <= 12 <= t["age_range"][1]

    def test_age_filtering_excludes_out_of_range(self):
        topics_4 = get_topics_for_age(4)
        topics_12 = get_topics_for_age(12)
        # Some topics are only for older kids (e.g. black holes 8-12)
        ids_4 = {t["id"] for t in topics_4}
        ids_12 = {t["id"] for t in topics_12}
        assert "space-black-holes" not in ids_4
        assert "space-black-holes" in ids_12

    def test_get_topic_by_id_found(self):
        topic = get_topic_by_id("science-magnets")
        assert topic is not None
        assert topic["title"] == "The Magic of Magnets"
        assert topic["category"] == "Science"

    def test_get_topic_by_id_not_found(self):
        topic = get_topic_by_id("nonexistent-topic")
        assert topic is None

    def test_get_topics_response_format(self):
        topics = get_topics_response(8, limit=5)
        assert len(topics) <= 5
        for t in topics:
            assert "id" in t
            assert "title" in t
            assert "emoji" in t
            assert "description" in t
            assert "category" in t
            # Should NOT include internal fields
            assert "age_range" not in t
            assert "tags" not in t

    def test_get_topics_response_respects_limit(self):
        topics = get_topics_response(8, limit=3)
        assert len(topics) <= 3

    def test_every_topic_has_required_fields(self):
        from services.topics import TOPICS
        for t in TOPICS:
            assert "id" in t
            assert "title" in t
            assert "emoji" in t
            assert "description" in t
            assert "category" in t
            assert "age_range" in t
            assert len(t["age_range"]) == 2
            assert t["age_range"][0] <= t["age_range"][1]
            assert "tags" in t

    def test_topic_ids_are_unique(self):
        from services.topics import TOPICS
        ids = [t["id"] for t in TOPICS]
        assert len(ids) == len(set(ids)), "Duplicate topic IDs found"


# ---------------------------------------------------------------------------
# Prompt cache tests
# ---------------------------------------------------------------------------


class TestPromptCache:
    """Tests that the prompt cache is initialized with defaults."""

    def test_cache_has_base_prompt(self):
        assert "base" in _prompt_cache
        assert "{child_name}" in _prompt_cache["base"]

    def test_cache_has_all_curious_modes(self):
        assert "curious_open" in _prompt_cache
        assert "curious_topic" in _prompt_cache
        assert "curious_surprise" in _prompt_cache
        assert "surprise_generator" in _prompt_cache

    def test_curious_topic_template_has_placeholders(self):
        template = _prompt_cache["curious_topic"]
        assert "{topic_title}" in template
        assert "{topic_description}" in template

    def test_surprise_generator_template_has_placeholders(self):
        template = _prompt_cache["surprise_generator"]
        assert "{child_age}" in template
        assert "{category}" in template


# ---------------------------------------------------------------------------
# Curious API endpoint tests (mocked auth + LLM)
# ---------------------------------------------------------------------------


class TestCuriousEndpoints:
    """Tests for /api/curious/* endpoints."""

    @pytest.fixture
    def client(self):
        from api.routes import app
        return TestClient(app)

    @patch("api.curious_routes.require_profile_token")
    @patch("api.curious_routes.get_current_profile")
    def test_get_topics_returns_list(self, mock_profile, mock_auth, client):
        mock_auth.return_value = {"profile_id": "test-id", "profile_type": "child"}
        mock_profile.return_value = {"display_name": "Test", "age": 8}

        # Override the dependency
        from api.routes import app
        from api.dependencies import require_profile_token
        app.dependency_overrides[require_profile_token] = lambda: {
            "profile_id": "test-id", "profile_type": "child"
        }

        try:
            response = client.get("/api/curious/topics")
            assert response.status_code == 200
            data = response.json()
            assert "topics" in data
            assert isinstance(data["topics"], list)
            assert len(data["topics"]) > 0
            # Check topic structure
            topic = data["topics"][0]
            assert "id" in topic
            assert "title" in topic
            assert "emoji" in topic
        finally:
            app.dependency_overrides.clear()

    @patch("api.curious_routes.require_profile_token")
    @patch("api.curious_routes.get_current_profile")
    def test_get_topics_rejects_parent(self, mock_profile, mock_auth, client):
        from api.routes import app
        from api.dependencies import require_profile_token
        app.dependency_overrides[require_profile_token] = lambda: {
            "profile_id": "test-id", "profile_type": "parent"
        }
        try:
            response = client.get("/api/curious/topics")
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()

    @patch("services.llm.get_llm_client")
    @patch("api.curious_routes.get_current_profile")
    def test_get_surprise_calls_llm(self, mock_profile, mock_llm_fn, client):
        from api.routes import app
        from api.dependencies import require_profile_token
        app.dependency_overrides[require_profile_token] = lambda: {
            "profile_id": "test-id", "profile_type": "child"
        }

        mock_profile.return_value = {"display_name": "Test", "age": 8}
        mock_llm = MagicMock()
        mock_llm.generate_json = AsyncMock(return_value={
            "fact": "Honey never spoils!",
            "topic": "Food Science",
            "follow_up_question": "Why do you think that is?",
        })
        mock_llm_fn.return_value = mock_llm

        try:
            response = client.get("/api/curious/surprise")
            assert response.status_code == 200
            data = response.json()
            assert data["fact"] == "Honey never spoils!"
            assert data["topic"] == "Food Science"
            assert "follow_up_question" in data
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Chat send mode passthrough test
# ---------------------------------------------------------------------------


class TestChatModePassthrough:
    """Tests that mode/topic_id/surprise_fact are accepted by chat send."""

    def test_chat_send_request_accepts_mode_fields(self):
        from api.chat_routes import ChatSendRequest
        req = ChatSendRequest(
            message="Tell me about space",
            mode="curious_topic",
            topic_id="space-black-holes",
        )
        assert req.mode == "curious_topic"
        assert req.topic_id == "space-black-holes"

    def test_chat_send_request_defaults(self):
        from api.chat_routes import ChatSendRequest
        req = ChatSendRequest(message="Hello")
        assert req.mode == "default"
        assert req.topic_id is None
        assert req.surprise_fact is None

    def test_end_session_request_accepts_mode(self):
        from api.chat_routes import EndSessionRequest
        # There are two EndSessionRequest definitions; get the one with mode
        req = EndSessionRequest(thread_id="abc-123", mode="curious_open")
        assert req.mode == "curious_open"
