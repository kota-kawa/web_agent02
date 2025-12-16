from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
	from browser_use.agent.views import ActionResult, AgentHistoryList, AgentOutput
	from browser_use.browser.views import BrowserStateSummary
except ModuleNotFoundError:
	import sys

	ROOT_DIR = Path(__file__).resolve().parents[1]
	if str(ROOT_DIR) not in sys.path:
		sys.path.insert(0, str(ROOT_DIR))
	from browser_use.agent.views import ActionResult, AgentHistoryList, AgentOutput
	from browser_use.browser.views import BrowserStateSummary

_FINAL_RESPONSE_NOTICE = '※ ブラウザエージェントの応答はここで終了です。'
_FINAL_RESPONSE_MARKER = '[browser-agent-final]'


def _append_final_response_notice(message: str) -> str:
	"""Append a human/machine readable marker signalling that output is final."""

	base = (message or '').strip()
	if _FINAL_RESPONSE_MARKER in base:
		return base
	notice = f'{_FINAL_RESPONSE_NOTICE} {_FINAL_RESPONSE_MARKER}'.strip()
	if base:
		return f'{base}\n\n{notice}'
	return notice


def _compact_text(text: str) -> str:
	return text.strip()


def _stringify_value(value: Any) -> str:
	"""Format a value as string without truncation."""
	if isinstance(value, str):
		return value.strip()
	elif isinstance(value, (dict, list)):
		try:
			return json.dumps(value, ensure_ascii=False, indent=2)
		except TypeError:
			return str(value)
	else:
		return str(value)


def _format_action(action) -> str:
	action_dump = action.model_dump(exclude_none=True)
	if not action_dump:
		return '不明なアクション'

	name, params = next(iter(action_dump.items()))
	if not isinstance(params, dict) or not params:
		return name

	param_parts = []
	for key, value in params.items():
		if value is None:
			continue
		param_parts.append(f'{key}={_stringify_value(value)}')

	joined = ', '.join(param_parts)
	return f'{name}({joined})' if joined else name


def _format_result(result: ActionResult) -> str:
	"""Format action result without truncation."""
	if result.error:
		return _compact_text(result.error)

	segments: list[str] = []
	if result.is_done:
		status = '成功' if result.success else '失敗'
		segments.append(f'完了[{status}]')
	if result.extracted_content:
		segments.append(_compact_text(result.extracted_content))
	if result.long_term_memory:
		segments.append(_compact_text(result.long_term_memory))
	if not segments and result.metadata:
		try:
			metadata_text = json.dumps(result.metadata, ensure_ascii=False)
		except TypeError:
			metadata_text = str(result.metadata)
		segments.append(_compact_text(metadata_text))

	return ' / '.join(segments) if segments else ''


def _format_step_entry(index: int, step: Any) -> str:
	lines: list[str] = [f'ステップ{index}']
	state = getattr(step, 'state', None)
	if state:
		page_parts: list[str] = []
		if getattr(state, 'title', None):
			page_parts.append(_compact_text(state.title))
		if getattr(state, 'url', None):
			page_parts.append(state.url)
		# if page_parts:
		# 	lines.append('ページ: ' + ' / '.join(page_parts))

	model_output = getattr(step, 'model_output', None)
	if model_output:
		action_lines = [_format_action(action) for action in model_output.action]
		if action_lines:
			lines.append('アクション: ' + ' / '.join(action_lines))
		if model_output.evaluation_previous_goal:
			lines.append('評価: ' + _compact_text(model_output.evaluation_previous_goal))
		if model_output.next_goal:
			lines.append('次の目標: ' + _compact_text(model_output.next_goal))
		if model_output.current_status:
			lines.append('現在の状況: ' + _compact_text(model_output.current_status))

	result_lines = [text for text in (_format_result(r) for r in getattr(step, 'result', [])) if text]
	if result_lines:
		lines.append('結果: ' + ' / '.join(result_lines))

	return '\n'.join(lines)


def _format_history_messages(history: AgentHistoryList) -> list[tuple[int, str]]:
	formatted: list[tuple[int, str]] = []
	next_index = 1
	for step in history.history:
		metadata = getattr(step, 'metadata', None)
		step_number = getattr(metadata, 'step_number', None) if metadata else None
		if not isinstance(step_number, int) or step_number < 1:
			step_number = next_index
		formatted.append((step_number, _format_step_entry(step_number, step)))
		next_index = step_number + 1
	return formatted


def _format_step_plan(
	step_number: int,
	state: BrowserStateSummary,
	model_output: AgentOutput,
) -> str:
	"""Format a step plan without truncation."""
	lines: list[str] = [f'ステップ{step_number}']

	if model_output.evaluation_previous_goal:
		lines.append('評価: ' + _compact_text(model_output.evaluation_previous_goal))
	if model_output.memory:
		lines.append('メモリ: ' + _compact_text(model_output.memory))
	if model_output.next_goal:
		lines.append('次の目標: ' + _compact_text(model_output.next_goal))
	if model_output.current_status:
		lines.append('現在の状況: ' + _compact_text(model_output.current_status))
	if model_output.persistent_notes:
		lines.append('永続メモ: ' + _compact_text(model_output.persistent_notes))

	return '\n'.join(lines)


def _summarize_history(history: AgentHistoryList) -> str:
	total_steps = len(history.history)
	success = history.is_successful()
	if success is True:
		prefix, status = '✅', '成功'
	elif success is False:
		prefix, status = '⚠️', '失敗'
	else:
		prefix, status = 'ℹ️', '未確定'

	lines = [f'{prefix} {total_steps}ステップでエージェントが実行されました（結果: {status}）。']

	final_text = history.final_result()
	if final_text:
		lines.append('最終報告: ' + _compact_text(final_text))
	elif success is True:
		lines.append('最終報告: (詳細な結果テキストはありません)')

	if history.history:
		last_state = history.history[-1].state
		if last_state and last_state.url:
			lines.append(f'最終URL: {last_state.url}')

	return _append_final_response_notice('\n'.join(lines))
