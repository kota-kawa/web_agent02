from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.views import ChatInvokeCompletion
from flask_app.conversation_review import (
	ConversationAnalysis,
	_analyze_conversation_history_async,
)


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
		'flask_app.conversation_review._create_selected_llm',
		return_value=mock_llm_instance,
	)

	# 2. Simulate a structured output failure (e.g., Pydantic ValidationError)
	mock_llm_instance.ainvoke.side_effect = [
		ValidationError.from_exception_data(
			'ConversationAnalysis',
			[
				{
					'type': 'missing',
					'loc': ('should_reply',),
					'msg': 'Field required',
					'input': {},
				}
			],
		),
		# 3. Provide a successful fallback response (text with valid JSON)
		ChatInvokeCompletion(
			completion='```json\n{\n  "should_reply": true,\n  "reply": "This is a fallback reply.",\n  "addressed_agents": [],\n  "needs_action": false,\n  "action_type": null,\n  "task_description": null,\n  "reason": "Fallback mechanism was triggered."\n}\n```',
			usage=None,
		),
	]

	conversation_history = [{'role': 'user', 'content': 'Tell me about this page.'}]

	# Act
	result = await _analyze_conversation_history_async(conversation_history)

	# Assert
	# 4. Verify the result matches the fallback JSON
	assert result['should_reply'] is True
	assert result['reply'] == 'This is a fallback reply.'
	assert result['needs_action'] is False
	assert result['reason'] == 'Fallback mechanism was triggered.'
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
		'flask_app.conversation_review._create_selected_llm',
		return_value=mock_llm_instance,
	)

	# Simulate failures for both attempts
	mock_llm_instance.ainvoke.side_effect = [
		ModelProviderError('Initial provider error', status_code=500, model='test-model'),
		ModelProviderError('Fallback provider error', status_code=500, model='test-model'),
	]

	conversation_history = [{'role': 'user', 'content': 'Some query'}]

	# Act
	result = await _analyze_conversation_history_async(conversation_history)

	# Assert
	assert result['needs_action'] is False
	assert '会話履歴の分析用LLM呼び出しに失敗しました' in result['reason']
	assert mock_llm_instance.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_structured_output_with_legacy_result_field(mocker):
	"""
	Ensure compatibility with LLM clients that return the payload via `.result`
	instead of `.completion`.
	"""

	class LegacyResponse:
		def __init__(self, result):
			self.result = result

	analysis_model = ConversationAnalysis(
		should_reply=True,
		reply='Proceeding with browser action.',
		addressed_agents=['Browser Agent'],
		needs_action=True,
		action_type='search',
		task_description="Search for today's weather in Tokyo.",
		reason='User requested current weather information.',
	)

	mock_llm_instance = AsyncMock()
	mock_llm_instance.ainvoke.return_value = LegacyResponse(analysis_model)
	mocker.patch(
		'flask_app.conversation_review._create_selected_llm',
		return_value=mock_llm_instance,
	)

	conversation_history = [{'role': 'user', 'content': '東京の天気を教えて'}]

	result = await _analyze_conversation_history_async(conversation_history)

	assert result['needs_action'] is True
	assert result['task_description'] == "Search for today's weather in Tokyo."
	assert result['reply'] == 'Proceeding with browser action.'
	mock_llm_instance.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_provider_error_fallbacks_to_text_parsing(mocker):
	"""Provider errors during structured calls should still trigger the text fallback."""

	mock_llm_instance = AsyncMock()
	mock_llm_instance.ainvoke.side_effect = [
		ModelProviderError('Tool use not allowed', status_code=405, model='claude'),
		ChatInvokeCompletion(
			completion='{"should_reply": true, "reply": "Fallback executed.", "addressed_agents": ["Browser Agent"], "needs_action": false, "action_type": null, "task_description": null, "reason": "Structured output unavailable."}',
			usage=None,
		),
	]
	mocker.patch(
		'flask_app.conversation_review._create_selected_llm',
		return_value=mock_llm_instance,
	)

	conversation_history = [{'role': 'user', 'content': 'Hello'}]

	result = await _analyze_conversation_history_async(conversation_history)

	assert result['should_reply'] is True
	assert result['reply'] == 'Fallback executed.'
	assert result['needs_action'] is False
	assert mock_llm_instance.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_missing_fields_are_normalized(mocker):
	"""Ensure missing optional fields are filled with safe defaults instead of raising."""

	mock_llm_instance = AsyncMock()
	mock_llm_instance.ainvoke.return_value = {
		'completion': {
			'should_reply': False,
			'needs_action': True,
			'action_type': 'search',
			'task_description': 'Find pricing info for the product.',
		}
	}
	mocker.patch(
		'flask_app.conversation_review._create_selected_llm',
		return_value=mock_llm_instance,
	)

	conversation_history = [{'role': 'user', 'content': '商品価格を確認して'}]

	result = await _analyze_conversation_history_async(conversation_history)

	assert result['reply'] == ''
	assert result['addressed_agents'] == []
	assert result['reason']  # normalized default reason string
	assert result['action_type'] == 'search'
