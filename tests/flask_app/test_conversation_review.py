import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest
from pydantic import ValidationError

from flask_app.conversation_review import (
    _analyze_conversation_history_async,
    ConversationAnalysis,
)
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.views import ChatInvokeCompletion


@pytest.fixture
def mock_llm():
    """Fixture for a mocked LLM client."""
    return AsyncMock()


@pytest.mark.asyncio
async def test_fallback_mechanism_on_structured_output_failure(mocker):
    """
    Verify that the fallback to text parsing is triggered when structured output fails,
    and that it successfully parses a valid JSON string from the text.
    """
    # Arrange
    # 1. Mock the LLM client creation
    mock_llm_instance = AsyncMock()
    mocker.patch(
        "flask_app.conversation_review._create_selected_llm",
        return_value=mock_llm_instance,
    )

    # 2. Simulate a structured output failure (e.g., Pydantic ValidationError)
    mock_llm_instance.ainvoke.side_effect = [
        ValidationError.from_exception_data(
            "ConversationAnalysis",
            [
                {
                    "type": "missing",
                    "loc": ("should_reply",),
                    "msg": "Field required",
                    "input": {},
                }
            ],
        ),
        # 3. Provide a successful fallback response (text with valid JSON)
        ChatInvokeCompletion(
            completion='```json\n{\n  "should_reply": true,\n  "reply": "This is a fallback reply.",\n  "addressed_agents": [],\n  "needs_action": false,\n  "action_type": null,\n  "task_description": null,\n  "reason": "Fallback mechanism was triggered."\n}\n```',
            usage=None,
        ),
    ]

    conversation_history = [
        {"role": "user", "content": "Tell me about this page."}
    ]

    # Act
    result = await _analyze_conversation_history_async(conversation_history)

    # Assert
    # 4. Verify the result matches the fallback JSON
    assert result["should_reply"] is True
    assert result["reply"] == "This is a fallback reply."
    assert result["needs_action"] is False
    assert result["reason"] == "Fallback mechanism was triggered."
    assert mock_llm_instance.ainvoke.call_count == 2  # First call (failed) + Fallback call (success)


@pytest.mark.asyncio
async def test_fallback_failure_returns_error_dict(mocker):
    """
    Verify that if both structured output and the fallback mechanism fail,
    a dictionary with `needs_action: False` and an error reason is returned.
    """
    # Arrange
    mock_llm_instance = AsyncMock()
    mocker.patch(
        "flask_app.conversation_review._create_selected_llm",
        return_value=mock_llm_instance,
    )

    # Simulate failures for both attempts
    mock_llm_instance.ainvoke.side_effect = [
        ModelProviderError("Initial provider error", status_code=500, model="test-model"),
        ModelProviderError("Fallback provider error", status_code=500, model="test-model"),
    ]

    conversation_history = [{"role": "user", "content": "Some query"}]

    # Act
    result = await _analyze_conversation_history_async(conversation_history)

    # Assert
    assert result["needs_action"] is False
    assert "会話履歴の分析中にエラーが発生しました" in result["reason"]
    assert mock_llm_instance.ainvoke.call_count == 2
