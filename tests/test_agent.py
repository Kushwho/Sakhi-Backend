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

from agent import (
    SAKHI_SYSTEM_PROMPT,
    VALID_EXPRESSIONS,
    SakhiAgent,
)
from api import TokenRequest, app


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


class TestLogEmotionTool:
    """Tests for the log_emotion tool stub."""

    @pytest.mark.asyncio
    async def test_runs_without_error(self):
        agent = SakhiAgent()
        ctx = MagicMock()
        result = await agent.log_emotion(ctx, emotion="happy", intensity="high")
        # Returns None (silent tool)
        assert result is None


class TestSetAvatarExpressionTool:
    """Tests for the set_avatar_expression tool."""

    def test_valid_expressions_defined(self):
        assert "happy" in VALID_EXPRESSIONS
        assert "thinking" in VALID_EXPRESSIONS
        assert "excited" in VALID_EXPRESSIONS
        assert "concerned" in VALID_EXPRESSIONS
        assert "sad" in VALID_EXPRESSIONS
        assert "celebrating" in VALID_EXPRESSIONS

    @pytest.mark.asyncio
    async def test_invalid_expression_returns_error(self):
        agent = SakhiAgent()
        ctx = MagicMock()

        with patch("agent.get_job_context"):
            result = await agent.set_avatar_expression(ctx, expression="angry")
            assert "Invalid" in result

    @pytest.mark.asyncio
    async def test_valid_expression_without_frontend(self):
        agent = SakhiAgent()
        ctx = MagicMock()

        # Mock get_job_context to simulate no frontend connected
        mock_room = MagicMock()
        mock_room.remote_participants = {}
        mock_ctx = MagicMock()
        mock_ctx.room = mock_room

        with patch("agent.get_job_context", return_value=mock_ctx):
            result = await agent.set_avatar_expression(ctx, expression="happy")
            assert "happy" in result


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

    @patch.dict(
        os.environ,
        {
            "LIVEKIT_URL": "wss://test.livekit.cloud",
            "LIVEKIT_API_KEY": "test-key",
            "LIVEKIT_API_SECRET": "test-secret-that-is-long-enough-for-jwt-signing-purposes",
        },
    )
    def test_create_token_default(self, client):
        response = client.post("/api/token", json={})
        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert data["room_name"].startswith("sakhi-buddy")
        assert data["livekit_url"] == "wss://test.livekit.cloud"

    @patch.dict(
        os.environ,
        {
            "LIVEKIT_URL": "wss://test.livekit.cloud",
            "LIVEKIT_API_KEY": "test-key",
            "LIVEKIT_API_SECRET": "test-secret-that-is-long-enough-for-jwt-signing-purposes",
        },
    )
    def test_create_token_custom_child(self, client):
        response = client.post(
            "/api/token",
            json={"child_name": "Arjun", "child_age": 6, "child_language": "Hindi"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "arjun" in data["room_name"]

    @patch.dict(os.environ, {}, clear=True)
    def test_create_token_missing_credentials(self, client):
        response = client.post("/api/token", json={})
        assert response.status_code == 500
