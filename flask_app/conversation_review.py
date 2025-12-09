from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import SystemMessage, UserMessage

from .config import logger
from .exceptions import AgentControllerError
from .llm_setup import _create_selected_llm


class ConversationAnalysis(BaseModel):
	"""Data model for the result of conversation analysis."""

	should_reply: bool = Field(..., description='True if the browser agent should provide a brief, helpful reply.')
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
	task_description: str | None = Field(None, description='A specific and concrete task description for the browser agent.')
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


def _sanitize_json_string(text: str) -> str:
	"""Sanitize a string for JSON parsing by fixing common LLM output issues."""
	# Fix unescaped newlines in string values
	# This is a simplified approach - replace literal newlines with escaped ones
	# within what appears to be JSON string content
	try:
		result = re.sub(
			r'"((?:[^"\\]|\\.)*)(?:\n|\r|\t)((?:[^"\\]|\\.)*)"',
			lambda m: f'"{m.group(1)}\\n{m.group(2)}"',
			text,
		)
		return result
	except Exception:
		return text


def _extract_json_from_text(text: str) -> dict | None:
	"""Extracts JSON from text, tolerating markdown code blocks and sanitizing."""
	# Try to sanitize the text first
	sanitized_text = _sanitize_json_string(text)

	candidates = [text]
	if sanitized_text != text:
		candidates.append(sanitized_text)

	for candidate in candidates:
		# Look for a JSON block ```json ... ```
		json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', candidate)
		if json_match:
			try:
				return json.loads(json_match.group(1))
			except json.JSONDecodeError:
				pass  # Fallback to the next method

		# Look for any JSON-like structure
		json_match = re.search(r'\{[\s\S]*\}', candidate)
		if json_match:
			try:
				return json.loads(json_match.group())
			except json.JSONDecodeError:
				continue

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


async def _retry_with_json_correction(
	llm: Any, messages: list[Any], failed_output: str, error: Exception, max_retries: int = 2
) -> dict[str, Any] | None:
	"""Retry LLM call with JSON correction prompt after parse failure."""
	truncated_output = failed_output[:1500] + '...' if len(failed_output) > 1500 else failed_output

	correction_message = UserMessage(
		role='user',
		content=f"""あなたの出力はJSONとして不正でした。エラー: {str(error)[:300]}

前回の出力（一部）:
{truncated_output}

以下の点に注意して、正しいJSONのみを再出力してください：
1. 文字列内の改行は \\n でエスケープする
2. 文字列内のダブルクォートは \\" でエスケープする
3. 制御文字（タブ等）は適切にエスケープする
4. JSONの構文（カンマ、括弧の対応）を確認する
5. 説明やマークダウンは含めず、純粋なJSONのみを出力する""",
	)

	retry_messages = messages + [correction_message]

	for attempt in range(max_retries):
		try:
			response = await llm.ainvoke(retry_messages)
			response_text = _get_completion_payload(response)

			if isinstance(response_text, str):
				extracted_json = _extract_json_from_text(response_text)
				if extracted_json:
					normalized = _normalize_analysis_payload(extracted_json)
					return ConversationAnalysis.model_validate(normalized).model_dump()
			elif isinstance(response_text, dict):
				normalized = _normalize_analysis_payload(response_text)
				return ConversationAnalysis.model_validate(normalized).model_dump()

		except Exception as retry_error:
			logger.debug(f'JSON correction retry {attempt + 1}/{max_retries} failed: {retry_error}')
			if attempt == max_retries - 1:
				return None
			continue

	return None


async def _fallback_to_text_parsing(llm: Any, messages: list[Any]) -> dict[str, Any]:
	"""Fallback path when structured output is unavailable."""
	try:
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

		# If JSON extraction failed, try retry with correction prompt
		if isinstance(response_text, str):
			retry_result = await _retry_with_json_correction(llm, messages, response_text, ValueError('JSON extraction failed'))
			if retry_result:
				return retry_result

		raise ValueError('Fallback parsing failed to produce a valid model.')

	except ModelProviderError as fallback_exc:
		logger.warning('Provider error during conversation history analysis fallback: %s', fallback_exc)
		return _build_error_response(f'会話履歴の分析用LLM呼び出しに失敗しました: {fallback_exc}')
	except (ValidationError, ValueError) as fallback_exc:
		logger.warning('Error during conversation history analysis fallback: %s', fallback_exc)
		return _build_error_response(f'会話履歴の分析中にエラーが発生しました: {fallback_exc}')


def _trim_conversation_history(
	conversation_history: list[dict[str, Any]],
	window_size: int = 5,
) -> list[dict[str, Any]]:
	"""Keep the first user input and the most recent messages up to ``window_size``.

	The first user message anchors context, and the tail keeps the latest turns
	for the LLM. Duplicates are removed using message ids when present.
	"""
	if not conversation_history or window_size <= 0:
		return conversation_history

	first_user_message = next((msg for msg in conversation_history if msg.get('role') == 'user'), None)
	anchor = first_user_message or conversation_history[0]
	tail = conversation_history[-window_size:]

	def _normalize(value: Any) -> Any:
		if isinstance(value, (str, int, float, type(None))):
			return value
		return repr(value)

	def _key(msg: dict[str, Any]) -> tuple[str, Any, Any, Any]:
		if isinstance(msg, dict) and 'id' in msg:
			return ('id', msg.get('id'), None, None)
		return (
			'content',
			_normalize(msg.get('role')),
			_normalize(msg.get('content')),
			_normalize(msg.get('timestamp')),
		)

	seen: set[tuple[str, Any, Any, Any]] = set()
	selected: list[dict[str, Any]] = []
	for msg in [anchor, *tail]:
		if not isinstance(msg, dict):
			continue
		msg_key = _key(msg)
		if msg_key in seen:
			continue
		seen.add(msg_key)
		selected.append(msg)

	return selected


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
	conversation_history = _trim_conversation_history(conversation_history)
	conversation_text = ''
	for msg in conversation_history:
		role = msg.get('role', 'unknown')
		content = msg.get('content', '')
		conversation_text += f'{role}: {content}\n'

	# Create a prompt to analyze the conversation
	analysis_prompt = f"""あなたは「ブラウザ操作専門」のアシスタントです。

【あなたの専門分野（発言可能な範囲）】
- Web検索: 情報検索、調査、ニュース確認
- Webページ操作: ナビゲーション、フォーム入力、データ抽出
- オンラインサービス: 予約、購入、申し込み手続き
- Webサイトの閲覧支援: 特定ページへのアクセス

【発言してはいけない場合】
- IoTデバイス操作 → IoT Agentの専門
- 料理・洗濯・家庭科の知識 → Life-Style Agentの専門
- スケジュール・予定・タスク管理 → Scheduler Agentの専門
- ブラウザ操作が不要な一般的な質問

【判断ルール】
1. `needs_action: true` は、Webブラウザでの操作が「必須」な場合のみ
2. `should_reply: true` は、ブラウザ操作の文脈で有益な情報がある場合のみ
3. 他エージェントへの呼びかけ・任せる判断は禁止
4. 自分の専門外の話題は完全に無視する

【発言する例】
- 「東京の天気を調べて」→ needs_action: true, action_type: "search"
- 「Amazonで商品を検索して」→ needs_action: true

【発言しない例】
- 「エアコンをつけて」→ 発言しない（IoT Agentの専門）
- 「夕食のレシピを教えて」→ 発言しない（Life-Assistantの専門）
- 「明日の予定を追加して」→ 発言しない（Schedulerの専門）

会話履歴:
{conversation_text}

JSONのみで出力:
{{
  "should_reply": true/false,
  "reply": "短い提案（ブラウザ操作の文脈に限定）",
  "addressed_agents": [],
  "needs_action": true/false,
  "action_type": "search" | "navigate" | "form_fill" | "data_extract" | null,
  "task_description": "ブラウザに依頼する具体的タスク",
  "reason": "判断の理由"
}}
"""

	try:
		# Use LLM to generate structured analysis
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
		logger.warning('Structured output failed due to provider error, retrying without schema: %s', exc)
		return await _fallback_to_text_parsing(llm, messages)
	except (ValidationError, TypeError, AttributeError) as exc:
		logger.warning('Structured output failed, falling back to text parsing: %s', exc)
		return await _fallback_to_text_parsing(llm, messages)
	except Exception as exc:
		logger.exception('Unexpected error during conversation history analysis')
		return _build_error_response(f'予期しないエラーが発生しました: {exc}')
	finally:
		if llm:
			aclose = getattr(llm, 'aclose', None)
			if callable(aclose):
				try:
					await aclose()
				except RuntimeError as close_exc:
					# Suppress "Event loop is closed" errors from httpx/anyio during cleanup
					if 'Event loop is closed' not in str(close_exc):
						logger.debug('Failed to close LLM client during conversation analysis', exc_info=True)
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
			future = asyncio.run_coroutine_threadsafe(_analyze_conversation_history_async(conversation_history), loop)
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
