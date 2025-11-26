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
		...,
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
	reason: str = Field(..., description='The reasoning behind the analysis and decision.')


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
		return {
			'needs_action': False,
			'action_type': None,
			'task_description': None,
			'reason': f'LLMの初期化に失敗しました: {exc}',
		}

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
		from browser_use.llm.messages import UserMessage, SystemMessage

		messages = [
			SystemMessage(content='You are an expert in analyzing conversations.'),
			UserMessage(role='user', content=analysis_prompt),
		]
		response = await llm.ainvoke(messages, output_format=ConversationAnalysis)

		# The response result should now be a Pydantic model instance
		analysis_result = response.completion

		if not isinstance(analysis_result, ConversationAnalysis):
			raise TypeError(f'Expected ConversationAnalysis, but got {type(analysis_result).__name__}')

		# Convert the Pydantic model to a dictionary for the return value
		return analysis_result.model_dump()

	except (json.JSONDecodeError, TypeError) as exc:
		logger.warning('Failed to parse LLM response as JSON: %s', exc)
		return {
			'should_reply': False,
			'reply': '',
			'addressed_agents': [],
			'needs_action': False,
			'action_type': None,
			'task_description': None,
			'reason': f'LLMの応答をJSON形式で解析できませんでした: {exc}',
		}
	except (ValueError, TypeError, AttributeError) as exc:
		logger.warning('Error during conversation history analysis: %s', exc)
		return {
			'should_reply': False,
			'reply': '',
			'addressed_agents': [],
			'needs_action': False,
			'action_type': None,
			'task_description': None,
			'reason': f'会話履歴の分析中にエラーが発生しました: {exc}',
		}
	except Exception as exc:
		logger.exception('Unexpected error during conversation history analysis')
		return {
			'should_reply': False,
			'reply': '',
			'addressed_agents': [],
			'needs_action': False,
			'action_type': None,
			'task_description': None,
			'reason': f'予期しないエラーが発生しました: {exc}',
		}
	finally:
		if llm:
			try:
				await llm.aclose()
			except Exception:
				logger.debug('Failed to close LLM client during conversation analysis', exc_info=True)


def _analyze_conversation_history(conversation_history: list[dict[str, Any]]) -> dict[str, Any]:
	"""
	Synchronous wrapper for async conversation history analysis.

	Note: Uses asyncio.run() to create a new event loop since this is called
	from Flask's synchronous request context. Falls back to manual loop creation
	if an event loop is already running (e.g., in tests).
	"""
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
