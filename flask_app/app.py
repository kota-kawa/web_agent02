from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context
from flask.typing import ResponseReturnValue

from browser_use.model_selection import update_override

from .cdp import _consume_cdp_session_cleanup, _resolve_cdp_url
from .config import APP_STATIC_DIR, logger
from .controller import BrowserAgentController
from .conversation_review import _analyze_conversation_history
from .env_utils import _AGENT_MAX_STEPS, _BROWSER_URL
from .exceptions import AgentControllerError
from .formatting import _format_history_messages, _summarize_history
from .history import (
	_append_history_message,
	_broadcaster,
	_copy_history,
	_reset_history,
	_update_history_message,
)
from .models import SUPPORTED_MODELS

app = Flask(__name__)
app.json.ensure_ascii = False


@app.route('/favicon.ico')
def favicon() -> ResponseReturnValue:
	"""Serve the browser agent favicon for root requests."""

	return send_from_directory(
		APP_STATIC_DIR / 'icons',
		'browser-agent.ico',
		mimetype='image/x-icon',
	)


@app.route('/favicon.png')
def favicon_png() -> ResponseReturnValue:
	"""Serve the png favicon variant for clients that request it."""

	return send_from_directory(APP_STATIC_DIR / 'icons', 'browser-agent.png')


@app.before_request
def _handle_cors_preflight():
	"""Return an empty response for CORS preflight requests."""

	if request.method == 'OPTIONS':
		response = Response(status=204)
		response.headers['Access-Control-Allow-Origin'] = '*'
		response.headers['Access-Control-Allow-Headers'] = request.headers.get('Access-Control-Request-Headers', '*')
		response.headers['Access-Control-Allow-Methods'] = request.headers.get(
			'Access-Control-Request-Method',
			'GET, POST, PUT, PATCH, DELETE, OPTIONS',
		)
		return response


@app.after_request
def _set_cors_headers(response: Response):
	"""Attach permissive CORS headers to all responses."""

	response.headers.setdefault('Access-Control-Allow-Origin', '*')
	response.headers.setdefault(
		'Access-Control-Allow-Headers',
		request.headers.get('Access-Control-Request-Headers', 'Content-Type, Authorization'),
	)
	response.headers.setdefault('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS')
	return response


_AGENT_CONTROLLER: BrowserAgentController | None = None


def _get_agent_controller() -> BrowserAgentController:
	global _AGENT_CONTROLLER
	if _AGENT_CONTROLLER is None:
		cdp_url = _resolve_cdp_url()
		cleanup = _consume_cdp_session_cleanup()
		if not cdp_url:
			if cleanup:
				with suppress(Exception):
					cleanup()
			raise AgentControllerError('Chrome DevToolsのCDP URLが検出できませんでした。BROWSER_USE_CDP_URL を設定してください。')
		try:
			_AGENT_CONTROLLER = BrowserAgentController(
				cdp_url=cdp_url,
				max_steps=_AGENT_MAX_STEPS,
				cdp_cleanup=cleanup,
			)
		except Exception:
			if cleanup:
				with suppress(Exception):
					cleanup()
			raise
	return _AGENT_CONTROLLER


def _reset_agent_controller() -> None:
	"""Shutdown existing controller so the next request uses refreshed LLM settings."""

	global _AGENT_CONTROLLER
	if _AGENT_CONTROLLER is not None:
		try:
			_AGENT_CONTROLLER.shutdown()
		except Exception:
			logger.debug('Failed to shutdown controller during model refresh', exc_info=True)
	_AGENT_CONTROLLER = None


def _get_existing_controller() -> BrowserAgentController:
	if _AGENT_CONTROLLER is None:
		raise AgentControllerError('エージェントはまだ初期化されていません。')
	return _AGENT_CONTROLLER


@app.route('/')
def index() -> str:
	try:
		controller = _get_agent_controller()
	except AgentControllerError:
		controller = None
	except Exception:
		logger.debug('Unexpected error while preparing browser controller on index load', exc_info=True)
		controller = None

	if controller is not None:
		try:
			controller.ensure_start_page_ready()
		except Exception:
			logger.debug('Failed to warm up browser start page on index load', exc_info=True)

	return render_template('index.html', browser_url=_BROWSER_URL)


@app.get('/api/history')
def history() -> ResponseReturnValue:
	return jsonify({'messages': _copy_history()}), 200


@app.get('/api/models')
def get_models() -> ResponseReturnValue:
	return jsonify(SUPPORTED_MODELS)


@app.get('/api/stream')
def stream() -> ResponseReturnValue:
	listener = _broadcaster.subscribe()

	def event_stream() -> Any:
		try:
			while True:
				event = listener.get()
				yield f'data: {json.dumps(event, ensure_ascii=False)}\n\n'
		except GeneratorExit:
			pass
		finally:
			_broadcaster.unsubscribe(listener)

	headers = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
	return Response(stream_with_context(event_stream()), mimetype='text/event-stream', headers=headers)


@app.post('/model_settings')
def update_model_settings() -> ResponseReturnValue:
	"""Update LLM selection and recycle controller without restart."""

	payload = request.get_json(silent=True) or {}
	selection = payload if isinstance(payload, dict) else {}
	try:
		update_override(selection if selection else None)
		_reset_agent_controller()
	except Exception as exc:
		logger.exception('Failed to apply model settings: %s', exc)
		return jsonify({'error': 'モデル設定の更新に失敗しました。'}), 500
	return jsonify({'status': 'ok', 'applied': selection or 'from_file'})


@app.post('/api/chat')
def chat() -> ResponseReturnValue:
	payload = request.get_json(silent=True) or {}
	prompt = (payload.get('prompt') or '').strip()
	start_new_task = bool(payload.get('new_task'))

	if not prompt:
		return jsonify({'error': 'プロンプトを入力してください。'}), 400

	try:
		controller = _get_agent_controller()
	except AgentControllerError as exc:
		_append_history_message('user', prompt)
		message = f'エージェントの実行に失敗しました: {exc}'
		logger.warning(message)
		_append_history_message('assistant', message)
		_broadcaster.publish(
			{
				'type': 'status',
				'payload': {
					'agent_running': False,
					'run_summary': message,
				},
			}
		)
		return jsonify({'messages': _copy_history(), 'run_summary': message}), 200
	except Exception as exc:
		_append_history_message('user', prompt)
		logger.exception('Unexpected error while running browser agent')
		error_message = f'エージェントの実行中に予期しないエラーが発生しました: {exc}'
		_append_history_message('assistant', error_message)
		_broadcaster.publish(
			{
				'type': 'status',
				'payload': {
					'agent_running': False,
					'run_summary': error_message,
				},
			}
		)
		return jsonify({'messages': _copy_history(), 'run_summary': error_message}), 200

	if start_new_task:
		if controller.is_running():
			_append_history_message('user', prompt)
			message = 'エージェント実行中は新しいタスクを開始できません。現在の実行が完了するまでお待ちください。'
			_append_history_message('assistant', message)
			return (
				jsonify(
					{
						'messages': _copy_history(),
						'run_summary': message,
						'agent_running': True,
					}
				),
				409,
			)
		try:
			controller.prepare_for_new_task()
		except AgentControllerError as exc:
			_append_history_message('user', prompt)
			message = f'新しいタスクを開始できませんでした: {exc}'
			_append_history_message('assistant', message)
			return jsonify({'messages': _copy_history(), 'run_summary': message}), 400

	_append_history_message('user', prompt)

	# First prompt of a task: decide if browser actions are needed
	if not controller.is_running() and not controller.has_handled_initial_prompt():
		analysis = _analyze_conversation_history(_copy_history())
		if not analysis.get('needs_action'):
			reply = analysis.get('reply') or analysis.get('reason') or 'ブラウザ操作は不要と判断しました。'
			_append_history_message('assistant', reply)
			controller.mark_initial_prompt_handled()
			_broadcaster.publish(
				{
					'type': 'status',
					'payload': {
						'agent_running': False,
						'run_summary': reply,
					},
				}
			)
			return jsonify({'messages': _copy_history(), 'run_summary': reply}), 200

	if controller.is_running():
		was_paused = controller.is_paused()
		try:
			controller.enqueue_follow_up(prompt)
			if was_paused:
				controller.resume()
		except AgentControllerError as exc:
			message = f'フォローアップの指示の適用に失敗しました: {exc}'
			logger.warning(message)
			_append_history_message('assistant', message)
			return (
				jsonify({'messages': _copy_history(), 'run_summary': message, 'queued': False}),
				200,
			)

		if was_paused:
			ack_message = 'エージェントは一時停止中でした。新しい指示で実行を再開します。'
		else:
			ack_message = 'フォローアップの指示を受け付けました。現在の実行に反映します。'
		_append_history_message('assistant', ack_message)
		return (
			jsonify(
				{
					'messages': _copy_history(),
					'run_summary': ack_message,
					'queued': True,
					'agent_running': True,
				}
			),
			202,
		)

	try:
		run_result = controller.run(prompt)
		agent_history = run_result.filtered_history or run_result.history
	except AgentControllerError as exc:
		message = f'エージェントの実行に失敗しました: {exc}'
		logger.warning(message)
		_append_history_message('assistant', message)
		_broadcaster.publish(
			{
				'type': 'status',
				'payload': {
					'agent_running': False,
					'run_summary': message,
				},
			}
		)
		return jsonify({'messages': _copy_history(), 'run_summary': message}), 200
	except Exception as exc:
		logger.exception('Unexpected error while running browser agent')
		error_message = f'エージェントの実行中に予期しないエラーが発生しました: {exc}'
		_append_history_message('assistant', error_message)
		_broadcaster.publish(
			{
				'type': 'status',
				'payload': {
					'agent_running': False,
					'run_summary': error_message,
				},
			}
		)
		return jsonify({'messages': _copy_history(), 'run_summary': error_message}), 200

	step_messages = _format_history_messages(agent_history)
	for step_number, content in step_messages:
		message_id = run_result.step_message_ids.get(step_number)
		if message_id is None:
			message_id = controller.get_step_message_id(step_number)
		if message_id is not None:
			_update_history_message(message_id, content)
			controller.remember_step_message_id(step_number, message_id)
		else:
			appended = _append_history_message('assistant', content)
			new_id = int(appended['id'])
			controller.remember_step_message_id(step_number, new_id)
			run_result.step_message_ids[step_number] = new_id

	summary_message = _summarize_history(agent_history)
	_append_history_message('assistant', summary_message)
	_broadcaster.publish(
		{
			'type': 'status',
			'payload': {
				'agent_running': False,
				'run_summary': summary_message,
			},
		}
	)

	return jsonify({'messages': _copy_history(), 'run_summary': summary_message}), 200


@app.post('/api/agent-relay')
def agent_relay() -> ResponseReturnValue:
	"""
	Endpoint for receiving requests from external agents without updating the main chat history.
	Expected JSON payload:
	- prompt: instruction for the browser agent
	"""
	payload = request.get_json(silent=True) or {}
	prompt = (payload.get('prompt') or '').strip()

	if not prompt:
		return jsonify({'error': 'プロンプトを入力してください。'}), 400

	try:
		controller = _get_agent_controller()
	except AgentControllerError as exc:
		logger.warning('Failed to initialize agent controller for agent relay: %s', exc)
		return jsonify({'error': f'エージェントの初期化に失敗しました: {exc}'}), 503
	except Exception as exc:
		logger.exception('Unexpected error while preparing agent controller for agent relay')
		return jsonify({'error': f'エージェントの初期化中に予期しないエラーが発生しました: {exc}'}), 500

	# First prompt of a task: decide if browser actions are needed
	if not controller.is_running() and not controller.has_handled_initial_prompt():
		analysis = _analyze_conversation_history([{'role': 'user', 'content': prompt}])
		if not analysis.get('needs_action'):
			reply = analysis.get('reply') or analysis.get('reason') or 'ブラウザ操作は不要と判断しました。'
			controller.mark_initial_prompt_handled()
			return (
				jsonify(
					{
						'summary': reply,
						'steps': [],
						'success': True,
						'final_result': reply,
						'analysis': analysis,
						'action_taken': False,
					}
				),
				200,
			)

	if controller.is_running():
		was_paused = controller.is_paused()
		try:
			controller.enqueue_follow_up(prompt)
			if was_paused:
				controller.resume()
		except AgentControllerError as exc:
			logger.warning('Failed to enqueue follow-up instruction via agent relay: %s', exc)
			return (
				jsonify({'error': f'フォローアップの指示の適用に失敗しました: {exc}'}),
				400,
			)
		except Exception as exc:
			logger.exception('Unexpected error while enqueueing follow-up instruction via agent relay')
			return (
				jsonify({'error': f'フォローアップ指示の処理中に予期しないエラーが発生しました: {exc}'}),
				500,
			)

		ack_message = 'フォローアップの指示を受け付けました。現在の実行に反映します。'
		return (
			jsonify(
				{
					'status': 'follow_up_enqueued',
					'message': ack_message,
					'agent_running': True,
					'queued': True,
				}
			),
			202,
		)

	try:
		run_result = controller.run(prompt, record_history=False)
	except AgentControllerError as exc:
		logger.warning('Failed to execute agent relay request: %s', exc)
		return jsonify({'error': f'エージェントの実行に失敗しました: {exc}'}), 500
	except Exception as exc:
		logger.exception('Unexpected error while executing agent relay request')
		return jsonify({'error': f'予期しないエラーが発生しました: {exc}'}), 500

	agent_history = run_result.filtered_history or run_result.history
	summary_message = _summarize_history(agent_history)
	step_messages = [{'step_number': number, 'content': content} for number, content in _format_history_messages(agent_history)]

	response_data: dict[str, Any] = {
		'summary': summary_message,
		'steps': step_messages,
		'success': agent_history.is_successful(),
		'final_result': agent_history.final_result(),
	}

	usage = getattr(agent_history, 'usage', None)
	if usage is not None:
		try:
			response_data['usage'] = usage.model_dump()
		except AttributeError:
			response_data['usage'] = usage

	return jsonify(response_data), 200


@app.post('/api/reset')
def reset_conversation() -> ResponseReturnValue:
	controller = _AGENT_CONTROLLER
	if controller is not None:
		try:
			controller.reset()
		except AgentControllerError as exc:
			return jsonify({'error': str(exc)}), 400
		except Exception as exc:
			logger.exception('Failed to reset agent controller')
			return jsonify({'error': f'エージェントのリセットに失敗しました: {exc}'}), 500

	try:
		snapshot = _reset_history()
	except Exception as exc:
		logger.exception('Failed to reset history')
		return jsonify({'error': f'履歴のリセットに失敗しました: {exc}'}), 500
	return jsonify({'messages': snapshot}), 200


@app.post('/api/pause')
def pause_agent() -> ResponseReturnValue:
	try:
		controller = _get_existing_controller()
		controller.pause()
	except AgentControllerError as exc:
		return jsonify({'error': str(exc)}), 400
	except Exception as exc:
		logger.exception('Failed to pause agent')
		return jsonify({'error': f'一時停止に失敗しました: {exc}'}), 500
	return jsonify({'status': 'paused'}), 200


@app.post('/api/resume')
def resume_agent() -> ResponseReturnValue:
	try:
		controller = _get_existing_controller()
		controller.resume()
	except AgentControllerError as exc:
		return jsonify({'error': str(exc)}), 400
	except Exception as exc:
		logger.exception('Failed to resume agent')
		return jsonify({'error': f'再開に失敗しました: {exc}'}), 500
	return jsonify({'status': 'resumed'}), 200


@app.post('/api/conversations/review')
@app.post('/api/check-conversation-history')  # backward compatibility
def check_conversation_history() -> ResponseReturnValue:
	"""
	Endpoint to receive and analyze conversation history from other agents.

	Expects JSON payload with:
	- history (preferred) or conversation_history: list of message objects with 'role' and 'content' fields

	Returns:
	- analysis: the LLM analysis result
	- action_taken: whether any browser action was initiated
	- run_summary: summary of the action taken (if any)
	"""
	payload = request.get_json(silent=True) or {}
	conversation_history = payload.get('history') or payload.get('conversation_history') or payload.get('messages') or []

	if not conversation_history:
		return jsonify({'error': '会話履歴が提供されていません。'}), 400

	if not isinstance(conversation_history, list):
		return jsonify({'error': '会話履歴はリスト形式である必要があります。'}), 400

	# Analyze the conversation history
	analysis = _analyze_conversation_history(conversation_history)

	response_data = {
		'analysis': analysis,
		'should_reply': bool(analysis.get('should_reply')),
		'reply': analysis.get('reply') or '',
		'addressed_agents': analysis.get('addressed_agents') or [],
		'action_taken': False,
		'run_summary': None,
	}

	# If action is needed, trigger the browser agent
	if analysis.get('needs_action') and analysis.get('task_description'):
		task_description = analysis['task_description']

		try:
			controller = _get_agent_controller()
		except AgentControllerError as exc:
			logger.warning('Failed to initialize agent controller: %s', exc)
			response_data['run_summary'] = f'エージェントの初期化に失敗しました: {exc}'
			return jsonify(response_data), 200
		except (OSError, RuntimeError, ValueError) as exc:
			logger.warning('System error while initializing agent controller: %s', exc)
			response_data['run_summary'] = f'エージェントの初期化中にシステムエラーが発生しました: {exc}'
			return jsonify(response_data), 200
		except Exception as exc:
			logger.exception('Unexpected error while initializing agent controller')
			response_data['run_summary'] = f'予期しないエラーが発生しました: {exc}'
			return jsonify(response_data), 200

		# Check if agent is already running
		if controller.is_running():
			response_data['run_summary'] = 'エージェントは既に実行中です。後でもう一度お試しください。'
			return jsonify(response_data), 409

		# Execute the task
		try:
			run_result = controller.run(task_description)
			agent_history = run_result.filtered_history or run_result.history

			# Format the result
			summary_message = _summarize_history(agent_history)
			response_data['action_taken'] = True
			response_data['run_summary'] = summary_message
			response_data['agent_history'] = {
				'steps': len(agent_history.history),
				'success': agent_history.is_successful(),
				'final_result': agent_history.final_result(),
			}
			if not response_data.get('reply'):
				response_data['reply'] = summary_message
				response_data['should_reply'] = True

		except AgentControllerError as exc:
			logger.warning('Failed to execute browser agent task: %s', exc)
			response_data['run_summary'] = f'エージェントの実行に失敗しました: {exc}'
		except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
			logger.warning('System error during browser agent execution: %s', exc)
			response_data['run_summary'] = f'エージェントの実行中にシステムエラーが発生しました: {exc}'
		except Exception as exc:
			logger.exception('Unexpected error while executing browser agent task')
			response_data['run_summary'] = f'予期しないエラーが発生しました: {exc}'

	return jsonify(response_data), 200


if __name__ == '__main__':
	app.run(host='0.0.0.0', port=5005)
