"""
Tests for Sakhi Voice Agent
============================
Covers: SakhiAgent instantiation, tool stubs, expression validation, and FastAPI endpoints.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agents.sakhi import (
    VALID_EXPRESSIONS,
    SakhiAgent,
)
from api.routes import app


# ---------------------------------------------------------------------------
# SakhiAgent unit tests
# ---------------------------------------------------------------------------


class TestSakhiAgent:
    """Tests for the SakhiAgent class."""

    def test_default_instantiation(self):
        agent = SakhiAgent()
        assert agent.child_name == "a child"
        assert agent.child_age == 8
        assert agent.child_language == "English"

    def test_custom_instantiation(self):
        agent = SakhiAgent(child_name="Arjun", child_age=6, child_language="Hindi")
        assert agent.child_name == "Arjun"
        assert agent.child_age == 6
        assert agent.child_language == "Hindi"

    def test_system_prompt_contains_child_info(self):
        agent = SakhiAgent(child_name="Priya", child_age=10, child_language="Tamil")
        assert "Priya" in agent._instructions
        assert "10" in agent._instructions
        assert "Tamil" in agent._instructions

    def test_system_prompt_has_safety_rules(self):
        agent = SakhiAgent()
        assert "child-safe" in agent._instructions
        assert "NEVER give direct homework answers" in agent._instructions
        assert "Socratic" in agent._instructions

    def test_emotion_state_initialized_to_none(self):
        agent = SakhiAgent()
        assert agent._current_emotion is None


# ---------------------------------------------------------------------------
# Tool stub tests
# ---------------------------------------------------------------------------


class TestExplainConceptTool:
    """Tests for the explain_concept tool stub."""

    @pytest.mark.asyncio
    async def test_returns_string_with_concept(self):
        agent = SakhiAgent()
        ctx = MagicMock()
        result = await agent.explain_concept(ctx, concept="photosynthesis", subject="Science")
        assert isinstance(result, str)
        assert "photosynthesis" in result
        assert "Science" in result

    @pytest.mark.asyncio
    async def test_returns_socratic_response(self):
        agent = SakhiAgent()
        ctx = MagicMock()
        result = await agent.explain_concept(ctx, concept="fractions", subject="Math")
        # Stub should ask the child what they already know (Socratic)
        assert "know" in result.lower()





class TestValidExpressions:
    """Tests for valid avatar expressions constant."""

    def test_valid_expressions_defined(self):
        assert "happy" in VALID_EXPRESSIONS
        assert "thinking" in VALID_EXPRESSIONS
        assert "excited" in VALID_EXPRESSIONS
        assert "concerned" in VALID_EXPRESSIONS
        assert "sad" in VALID_EXPRESSIONS
        assert "celebrating" in VALID_EXPRESSIONS


# ---------------------------------------------------------------------------
# Hume emotion mapping tests
# ---------------------------------------------------------------------------


class TestHumeEmotionMapping:
    """Tests for the Hume emotion → avatar expression mapping."""

    def test_joy_maps_to_happy(self):
        from services.hume import map_emotion_to_avatar

        assert map_emotion_to_avatar("Joy") == "happy"

    def test_sadness_maps_to_sad(self):
        from services.hume import map_emotion_to_avatar

        assert map_emotion_to_avatar("Sadness") == "sad"

    def test_excitement_maps_to_excited(self):
        from services.hume import map_emotion_to_avatar

        assert map_emotion_to_avatar("Excitement") == "excited"

    def test_anxiety_maps_to_concerned(self):
        from services.hume import map_emotion_to_avatar

        assert map_emotion_to_avatar("Anxiety") == "concerned"

    def test_pride_maps_to_celebrating(self):
        from services.hume import map_emotion_to_avatar

        assert map_emotion_to_avatar("Pride") == "celebrating"

    def test_unknown_emotion_defaults_to_happy(self):
        from services.hume import map_emotion_to_avatar

        assert map_emotion_to_avatar("SomeUnknownEmotion") == "happy"


# ---------------------------------------------------------------------------
# FastAPI endpoint tests
# ---------------------------------------------------------------------------


class TestFastAPIEndpoints:
    """Tests for the FastAPI HTTP endpoints."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_health_check(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "sakhi-backend"
        assert "timestamp" in data

    def test_create_token_placeholder(self, client):
        """
        TODO: The /api/token endpoint now requires a valid JWT profile token in the
        Authorization header and database access to fetch profile info.
        This test needs to be expanded to mock database calls and provide a mock JWT.
        """
        pass
