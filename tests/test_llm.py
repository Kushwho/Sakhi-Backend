import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm import SakhiLLM, get_llm_client


class TestSakhiLLM:
    """Tests for the central SakhiLLM class."""

    @pytest.mark.asyncio
    @patch("services.llm.AsyncGroq")
    async def test_generate_json_success(self, mock_async_groq_class):
        """Happy path: system prompt + user prompt returns parsed JSON."""
        mock_client = MagicMock()
        mock_async_groq_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"topics": ["math"], "mood_summary": "happy", "alerts": []}'))
        ]
        # chat.completions.create must be an AsyncMock so it can be awaited
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        llm = SakhiLLM(model="test-model")
        result = await llm.generate_json(
            prompt="Test prompt",
            system_prompt="System instructions",
        )

        assert result == {"topics": ["math"], "mood_summary": "happy", "alerts": []}
        mock_client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=[
                {"role": "system", "content": "System instructions"},
                {"role": "user", "content": "Test prompt"},
            ],
            temperature=0.3,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

    @pytest.mark.asyncio
    @patch("services.llm.AsyncGroq")
    async def test_generate_json_no_system_prompt(self, mock_async_groq_class):
        """When no system_prompt is passed, messages list contains only the user turn."""
        mock_client = MagicMock()
        mock_async_groq_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"result": "success"}'))
        ]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        llm = SakhiLLM(model="test-model")
        result = await llm.generate_json(prompt="Just a prompt")

        assert result == {"result": "success"}
        mock_client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=[{"role": "user", "content": "Just a prompt"}],
            temperature=0.3,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

    @pytest.mark.asyncio
    @patch("services.llm.AsyncGroq")
    async def test_generate_json_custom_temperature_and_max_tokens(self, mock_async_groq_class):
        """Non-default temperature and max_tokens are forwarded correctly."""
        mock_client = MagicMock()
        mock_async_groq_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        llm = SakhiLLM(model="test-model")
        await llm.generate_json(prompt="Hi", temperature=0.9, max_tokens=1024)

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["temperature"] == 0.9
        assert kwargs["max_tokens"] == 1024

    @pytest.mark.asyncio
    @patch("services.llm.AsyncGroq")
    async def test_generate_json_raises_on_api_error(self, mock_async_groq_class):
        """If the Groq API call raises, generate_json re-raises the exception."""
        mock_client = MagicMock()
        mock_async_groq_class.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("API unavailable")
        )

        llm = SakhiLLM(model="test-model")
        with pytest.raises(RuntimeError, match="API unavailable"):
            await llm.generate_json(prompt="Will fail")

    @pytest.mark.asyncio
    @patch("services.llm.AsyncGroq")
    async def test_generate_json_raises_on_invalid_json(self, mock_async_groq_class):
        """If the model returns malformed JSON, generate_json raises a JSONDecodeError."""
        mock_client = MagicMock()
        mock_async_groq_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="not json at all"))]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        llm = SakhiLLM(model="test-model")
        with pytest.raises(json.JSONDecodeError):
            await llm.generate_json(prompt="Bad response")

    @patch("services.llm.AsyncGroq")
    def test_get_llm_client_returns_sakhi_llm_instance(self, _mock_groq):
        """get_llm_client() returns a SakhiLLM instance."""
        client = get_llm_client()
        assert isinstance(client, SakhiLLM)