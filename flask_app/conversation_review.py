from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import logger
from .exceptions import AgentControllerError
from .llm_setup import _create_selected_llm


class ConversationAnalysis(BaseModel):
	"""Data model for the result of conversation analysis."""

	should_reply: bool = Field(
		..., description='True if the browser agent should provide a brief, helpful reply.'
	)
	reply: str = Field(
		default='',
		description='A short suggestion, alert, or mention of other agents. Should be empty if should_reply is False.',
	)
	addressed_agents: list[str] = Field(
		default_factory=list,
		description='A list of agent names that are addressed in the conversation (e.g., "Browser Agent").',
	)
	needs_action: bool = Field(..., description='True if the conversation requires a browser action.')
	action_type: Literal['search', 'navigate', 'form_fill', 'data_extract'] | None = Field(
		None, description='The type of browser action required.'
	)
	task_description: str | None = Field(
		None, description='A specific and concrete task description for the browser agent.'
	)
	reason: str = Field('', description='The reasoning behind the analysis and decision.')


def _build_error_response(reason: str) -> dict[str, Any]:
	"""Return a consistently shaped failure payload for the endpoint."""

	return {
		'should_reply': False,
		'reply': '',
		'addressed_agents': [],
		'needs_action': False,
		'action_type': None,
		'task_description': None,
		'reason': reason,
	}


def _normalize_analysis_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
	"""Fill missing or malformed fields with safe defaults before validation."""

	if payload is None:
		return {}

	normalized: dict[str, Any] = dict(payload)
	normalized.setdefault('should_reply', False)
	normalized.setdefault('reply', '')
	normalized.setdefault('addressed_agents', [])
	normalized.setdefault('needs_action', False)
	normalized.setdefault('action_type', None)
	normalized.setdefault('task_description', None)

	if not normalized.get('reason'):
		normalized['reason'] = 'LLM output did not include a reason.'

	# Guard action_type against unexpected values
	valid_action_types = {'search', 'navigate', 'form_fill', 'data_extract', None}
	if normalized.get('action_type') not in valid_action_types:
		normalized['action_type'] = None

	# Ensure reply is empty if should_reply is False
	if not normalized.get('should_reply'):
		normalized['reply'] = normalized.get('reply', '') or ''

	return normalized


def _extract_json_from_text(text: str) -> dict | None:
	"""Extracts JSON from text, tolerating markdown code blocks."""
	# Look for a JSON block ```json ... ```
	json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', text)
	if json_match:
		try:
			return json.loads(json_match.group(1))
		except json.JSONDecodeError:
			pass  # Fallback to the next method

	# Look for any JSON-like structure
	json_match = re.search(r'\{[\s\S]*\}', text)
	if json_match:
		try:
			return json.loads(json_match.group())
		except json.JSONDecodeError:
			return None
	return None


def _get_completion_payload(response: Any) -> Any:
	"""Extract the completion payload from an LLM response.

	Supports both the current `.completion` attribute and the legacy `.result`
	field to stay compatible with older `browser-use` builds or third-party
	clients.
	"""
	if hasattr(response, 'completion'):
		return response.completion
	if hasattr(response, 'result'):
		return response.result
	if isinstance(response, dict):
		return response.get('completion') or response.get('result')
	raise AttributeError('LLM response did not include a completion/result payload')


async def _analyze_conversation_history_async(conversation_history: list[dict[str, Any]]) -> dict[str, Any]:
	"""
	Analyze conversation history using LLM to determine if browser operations are needed
	and whether the browser agent should proactively speak up.
	"""
	llm = None
	try:
		llm = _create_selected_llm()
	except AgentControllerError as exc:
		logger.warning('Failed to create LLM for conversation analysis: %s', exc)
		return _build_error_response(f'LLMの初期化に失敗しました: {exc}')

	# Format conversation history for analysis
	conversation_text = ''
	for msg in conversation_history:
		role = msg.get('role', 'unknown')
		content = msg.get('content', '')
		conversation_text += f'{role}: {content}\n'

	# Create a prompt to analyze the conversation
	analysis_prompt = f"""以下の会話履歴を分析し、(1)ブラウザ操作が必要か、(2)ブラウザエージェントとして一言でも発言したほうがよいかを判断してください。

会話履歴:
{conversation_text}

判断ルール:
- エラー・行き詰まり・不明点・追加確認など、少しでも役立つ発言があるなら `should_reply` を true にして短く提案してください。
- 他のエージェント（Life-Assistant Agent, IoT Agent, Browser Agent）に任せる/呼びかける場合は、名前を明記してください。
- ブラウザ操作で解決できそうなら具体的なタスクを `task_description` に書き、`needs_action` を true にしてください。

JSONのみで出力:
{{
  "should_reply": true/false,
  "reply": "短い提案や注意喚起。他エージェントへの言及もここに含める。",
  "addressed_agents": ["Browser Agent", "Life-Assistant Agent", "IoT Agent"],
  "needs_action": true/false,
  "action_type": "search" | "navigate" | "form_fill" | "data_extract" | null,
  "task_description": "ブラウザに依頼する具体的タスク",
  "reason": "判断の理由"
}}

必ず有効なJSONだけを返してください。"""

	try:
		# Use LLM to generate structured analysis
		from browser_use.llm.exceptions import ModelProviderError
		from browser_use.llm.messages import SystemMessage, UserMessage
		from pydantic import ValidationError

		messages = [
			SystemMessage(content='You are an expert in analyzing conversations.'),
			UserMessage(role='user', content=analysis_prompt),
		]
		response = await llm.ainvoke(messages, output_format=ConversationAnalysis)

		# The response result should now be a Pydantic model instance
		analysis_result = _get_completion_payload(response)

		if isinstance(analysis_result, ConversationAnalysis):
			return analysis_result.model_dump()

		if isinstance(analysis_result, dict):
			normalized = _normalize_analysis_payload(analysis_result)
			return ConversationAnalysis.model_validate(normalized).model_dump()

		raise TypeError(f'Expected ConversationAnalysis, but got {type(analysis_result).__name__}')

	except ModelProviderError as exc:
		logger.warning('Structured output failed due to provider error: %s', exc)
		return _build_error_response(f'会話履歴の分析用LLM呼び出しに失敗しました: {exc}')
	except (ValidationError, TypeError, AttributeError) as exc:
		logger.warning('Structured output failed, falling back to text parsing: %s', exc)
		try:
			# Fallback to a regular text-based invocation
			response = await llm.ainvoke(messages)
			response_text = _get_completion_payload(response)
			extracted_json = None
			if isinstance(response_text, str):
				extracted_json = _extract_json_from_text(response_text)
			elif isinstance(response_text, dict):
				extracted_json = response_text

			if extracted_json:
				normalized = _normalize_analysis_payload(extracted_json)
				analysis_result = ConversationAnalysis.model_validate(normalized)
				return analysis_result.model_dump()

			raise ValueError('Fallback parsing failed to produce a valid model.')

		except ModelProviderError as fallback_exc:
			logger.warning('Provider error during conversation history analysis fallback: %s', fallback_exc)
			return _build_error_response(f'会話履歴の分析用LLM呼び出しに失敗しました: {fallback_exc}')
		except (ValidationError, ValueError) as fallback_exc:
			logger.warning('Error during conversation history analysis fallback: %s', fallback_exc)
			return _build_error_response(f'会話履歴の分析中にエラーが発生しました: {fallback_exc}')
	except Exception as exc:
		logger.exception('Unexpected error during conversation history analysis')
		return _build_error_response(f'予期しないエラーが発生しました: {exc}')
	finally:
		if llm:
			try:
				await llm.aclose()
			except Exception:
				logger.debug('Failed to close LLM client during conversation analysis', exc_info=True)


def _analyze_conversation_history(
	conversation_history: list[dict[str, Any]],
	loop: asyncio.AbstractEventLoop | None = None,
) -> dict[str, Any]:
	"""
	Synchronous wrapper for async conversation history analysis.

	Note: Uses asyncio.run() to create a new event loop since this is called
	from Flask's synchronous request context. Falls back to manual loop creation
	if an event loop is already running (e.g., in tests).
	"""
	if loop and loop.is_running():
		try:
			future = asyncio.run_coroutine_threadsafe(
				_analyze_conversation_history_async(conversation_history), loop
			)
			return future.result()
		except RuntimeError:
			logger.debug('Failed to run on provided loop, falling back to new loop.')

	try:
		return asyncio.run(_analyze_conversation_history_async(conversation_history))
	except RuntimeError as exc:
		# Handle case where event loop is already running
		logger.debug('Event loop already running, creating new loop: %s', exc)
		loop = asyncio.new_event_loop()
		try:
			return loop.run_until_complete(_analyze_conversation_history_async(conversation_history))
		finally:
			loop.close()
