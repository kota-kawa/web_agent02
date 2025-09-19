from __future__ import annotations

import atexit
import asyncio
import json
import logging
import os
import threading
from contextlib import suppress
from datetime import datetime
from itertools import count
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.agent.views import ActionResult, AgentHistoryList

load_dotenv()

logging.basicConfig(level=os.environ.get('FLASK_LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Return an integer environment variable with a fallback."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
        return parsed if parsed > 0 else default
    except ValueError:
        logger.warning('環境変数%sの値が無効のため既定値を使用します: %s', name, raw_value)
        return default


_AGENT_MAX_STEPS = _env_int('AGENT_MAX_STEPS', 30)
_CDP_PROBE_TIMEOUT = float(os.environ.get('BROWSER_USE_CDP_TIMEOUT', '2.0'))

app = Flask(__name__)
app.json.ensure_ascii = False

_message_sequence = count()


def _utc_timestamp() -> str:
    """Return a simple ISO 8601 timestamp in UTC."""
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def _make_message(role: str, content: str) -> dict[str, str | int]:
    return {
        'id': next(_message_sequence),
        'role': role,
        'content': content,
        'timestamp': _utc_timestamp(),
    }


_history: list[dict[str, str | int]] = [
    _make_message(
        'assistant',
        'ブラウザ操作エージェントへようこそ。APIキーとCDP URLを設定すると、左側のチャットから自然言語でChromeを操作できます。',
    )
]

_BROWSER_URL = os.environ.get(
    'EMBED_BROWSER_URL',
    'http://127.0.0.1:7900/?autoconnect=1&resize=scale',
)


class AgentControllerError(RuntimeError):
    """Raised when the browser agent cannot be executed."""


def _compact_text(text: str) -> str:
    return ' '.join(text.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


def _stringify_value(value: Any, limit: int = 60) -> str:
    if isinstance(value, str):
        cleaned = _compact_text(value)
    elif isinstance(value, (dict, list)):
        try:
            cleaned = _compact_text(json.dumps(value, ensure_ascii=False))
        except TypeError:
            cleaned = _compact_text(str(value))
    else:
        cleaned = _compact_text(str(value))
    return _truncate(cleaned, limit)


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
    if result.error:
        return _truncate(_compact_text(result.error), 160)

    segments: list[str] = []
    if result.is_done:
        status = '成功' if result.success else '失敗'
        segments.append(f'完了[{status}]')
    if result.extracted_content:
        segments.append(_truncate(_compact_text(result.extracted_content), 160))
    if result.long_term_memory:
        segments.append(_truncate(_compact_text(result.long_term_memory), 160))
    if not segments and result.metadata:
        try:
            metadata_text = json.dumps(result.metadata, ensure_ascii=False)
        except TypeError:
            metadata_text = str(result.metadata)
        segments.append(_truncate(_compact_text(metadata_text), 120))

    return ' / '.join(segments) if segments else ''


def _format_history_messages(history: AgentHistoryList) -> list[str]:
    formatted: list[str] = []
    for index, step in enumerate(history.history, start=1):
        lines: list[str] = [f'ステップ{index}']
        state = getattr(step, 'state', None)
        if state:
            page_parts: list[str] = []
            if state.title:
                page_parts.append(_truncate(_compact_text(state.title), 80))
            if state.url:
                page_parts.append(state.url)
            if page_parts:
                lines.append('ページ: ' + ' / '.join(page_parts))

        if step.model_output:
            action_lines = [_format_action(action) for action in step.model_output.action]
            if action_lines:
                lines.append('アクション: ' + ' / '.join(action_lines))
            if step.model_output.evaluation_previous_goal:
                lines.append(
                    '評価: '
                    + _truncate(_compact_text(step.model_output.evaluation_previous_goal), 120)
                )
            if step.model_output.next_goal:
                lines.append(
                    '次の目標: ' + _truncate(_compact_text(step.model_output.next_goal), 120)
                )

        result_lines = [text for text in (_format_result(r) for r in step.result) if text]
        if result_lines:
            lines.append('結果: ' + ' / '.join(result_lines))

        formatted.append('\n'.join(lines))
    return formatted


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
        lines.append('最終報告: ' + _truncate(_compact_text(final_text), 200))

    if history.history:
        last_state = history.history[-1].state
        if last_state and last_state.url:
            lines.append(f'最終URL: {last_state.url}')

    return '\n'.join(lines)


def _probe_cdp_candidate(base_url: str) -> str | None:
    base = base_url.rstrip('/')
    paths = ('/json/version', '/devtools/version', '/json')
    for path in paths:
        target = f'{base}{path}'
        try:
            with urlopen(target, timeout=_CDP_PROBE_TIMEOUT) as response:
                if response.status != 200:
                    continue
                try:
                    payload: Any = json.load(response)
                except json.JSONDecodeError:
                    continue
        except (URLError, HTTPError, TimeoutError, OSError):
            continue

        if isinstance(payload, dict):
            ws_url = (
                payload.get('webSocketDebuggerUrl')
                or payload.get('webSocketUrl')
                or payload.get('websocketUrl')
            )
            if ws_url:
                return ws_url
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    ws_url = item.get('webSocketDebuggerUrl')
                    if ws_url:
                        return ws_url
    return None


def _extract_cdp_url(capabilities: dict[str, Any]) -> str | None:
    for key in ('se:cdp', 'se:cdpUrl', 'se:cdpURL'):
        raw_value = capabilities.get(key)
        if isinstance(raw_value, str):
            trimmed = raw_value.strip()
            if trimmed:
                return trimmed
    return None


def _cleanup_webdriver_session(base_endpoint: str, session_id: str) -> None:
    delete_url = f'{base_endpoint}/session/{session_id}'
    request = Request(delete_url, method='DELETE')
    try:
        with urlopen(request, timeout=_CDP_PROBE_TIMEOUT):
            pass
    except (URLError, HTTPError, TimeoutError, OSError):
        logger.debug('Failed to clean up temporary WebDriver session %s', session_id, exc_info=True)


def _probe_webdriver_endpoint(base_endpoint: str) -> str | None:
    session_url = f'{base_endpoint}/session'
    payload = json.dumps({
        'capabilities': {
            'alwaysMatch': {
                'browserName': 'chrome',
            }
        }
    }).encode('utf-8')
    request = Request(
        session_url,
        data=payload,
        headers={'Content-Type': 'application/json'},
    )

    session_id: str | None = None
    capabilities: dict[str, Any] | None = None

    try:
        with urlopen(request, timeout=_CDP_PROBE_TIMEOUT) as response:
            if response.status not in (200, 201):
                return None
            try:
                data: Any = json.load(response)
            except json.JSONDecodeError:
                return None
    except (URLError, HTTPError, TimeoutError, OSError):
        return None
    else:
        if isinstance(data, dict):
            value = data.get('value')
            if isinstance(value, dict):
                maybe_caps = value.get('capabilities')
                if isinstance(maybe_caps, dict):
                    capabilities = maybe_caps
                raw_session = value.get('sessionId')
                if isinstance(raw_session, str) and raw_session.strip():
                    session_id = raw_session.strip()
            if capabilities is None:
                maybe_caps = data.get('capabilities')
                if isinstance(maybe_caps, dict):
                    capabilities = maybe_caps
            if not session_id:
                raw_session = data.get('sessionId')
                if isinstance(raw_session, str) and raw_session.strip():
                    session_id = raw_session.strip()
        cdp_url = _extract_cdp_url(capabilities) if capabilities else None
    finally:
        if session_id:
            _cleanup_webdriver_session(base_endpoint, session_id)

    return cdp_url


def _probe_cdp_via_webdriver(base_url: str) -> str | None:
    normalized = base_url.strip()
    if not normalized or not normalized.lower().startswith(('http://', 'https://')):
        return None

    normalized = normalized.rstrip('/')
    endpoints = []
    if normalized:
        endpoints.append(normalized)
        if not normalized.endswith('/wd/hub'):
            endpoints.append(f'{normalized}/wd/hub')

    seen: set[str] = set()
    for endpoint in endpoints:
        endpoint = endpoint.rstrip('/')
        if not endpoint or endpoint in seen:
            continue
        seen.add(endpoint)
        ws_url = _probe_webdriver_endpoint(endpoint)
        if ws_url:
            return ws_url
    return None


def _resolve_cdp_url() -> str | None:
    explicit_keys = ('BROWSER_USE_CDP_URL', 'CDP_URL', 'REMOTE_CDP_URL')
    for key in explicit_keys:
        value = os.environ.get(key)
        if value:
            logger.info('Using CDP URL from %s', key)
            return value.strip()

    candidate_env = os.environ.get('BROWSER_USE_CDP_CANDIDATES')
    if candidate_env:
        candidates = [entry.strip() for entry in candidate_env.split(',') if entry.strip()]
    else:
        candidates = [
            'http://browser:9222',
            'http://browser:4444',
            'http://browser:4444/wd/hub',
            'http://localhost:9222',
            'http://localhost:4444',
            'http://localhost:4444/wd/hub',
            'http://127.0.0.1:9222',
            'http://127.0.0.1:4444',
            'http://127.0.0.1:4444/wd/hub',
        ]

    for candidate in candidates:
        ws_url = _probe_cdp_candidate(candidate)
        if ws_url:
            logger.info('Detected Chrome DevTools endpoint at %s', candidate)
            return ws_url

    for candidate in candidates:
        ws_url = _probe_cdp_via_webdriver(candidate)
        if ws_url:
            logger.info('Detected Chrome DevTools endpoint via WebDriver at %s', candidate)
            return ws_url

    logger.warning('Chrome DevToolsのCDP URLを自動検出できませんでした。環境変数BROWSER_USE_CDP_URLを設定してください。')
    return None


class BrowserAgentController:
    """Manage a long-lived browser session controlled by browser-use."""

    def __init__(self, cdp_url: str | None, max_steps: int) -> None:
        self._cdp_url = cdp_url
        self._max_steps = max_steps
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name='browser-use-agent-loop',
            daemon=True,
        )
        self._thread.start()
        self._lock = threading.Lock()
        self._browser_session: BrowserSession | None = None
        self._shutdown = False
        self._logger = logging.getLogger('browser_use.flask.agent')
        atexit.register(self.shutdown)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _ensure_browser_session(self) -> BrowserSession:
        if self._browser_session is not None:
            return self._browser_session

        if not self._cdp_url:
            raise AgentControllerError(
                'Chrome DevToolsのCDP URLが検出できませんでした。BROWSER_USE_CDP_URL を設定してください。'
            )

        profile = BrowserProfile(
            cdp_url=self._cdp_url,
            keep_alive=True,
            highlight_elements=True,
            wait_between_actions=0.4,
        )
        self._browser_session = BrowserSession(browser_profile=profile)
        return self._browser_session

    async def _run_agent(self, task: str) -> AgentHistoryList:
        session = await self._ensure_browser_session()
        agent = Agent(task=task, browser_session=session)
        try:
            history = await agent.run(max_steps=self._max_steps)
            return history
        finally:
            if session.browser_profile.keep_alive:
                with suppress(Exception):
                    await session.stop()

    async def _async_shutdown(self) -> None:
        if self._browser_session is not None:
            with suppress(Exception):
                await self._browser_session.stop()
            self._browser_session = None

    def run(self, task: str) -> AgentHistoryList:
        if self._shutdown:
            raise AgentControllerError('エージェントコントローラーは停止済みです。')

        with self._lock:
            future = asyncio.run_coroutine_threadsafe(self._run_agent(task), self._loop)
            try:
                return future.result()
            except AgentControllerError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AgentControllerError(str(exc)) from exc

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True

        if self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._async_shutdown(), self._loop)
                future.result(timeout=5)
            except Exception:  # noqa: BLE001
                self._logger.debug('Failed to shut down agent loop cleanly', exc_info=True)

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=2)


_AGENT_CONTROLLER: BrowserAgentController | None = None


def _get_agent_controller() -> BrowserAgentController:
    global _AGENT_CONTROLLER
    if _AGENT_CONTROLLER is None:
        cdp_url = _resolve_cdp_url()
        _AGENT_CONTROLLER = BrowserAgentController(cdp_url=cdp_url, max_steps=_AGENT_MAX_STEPS)
    return _AGENT_CONTROLLER


@app.route('/')
def index() -> str:
    return render_template('index.html', browser_url=_BROWSER_URL)


@app.get('/api/history')
def history() -> ResponseReturnValue:
    return jsonify({'messages': _history}), 200


@app.post('/api/chat')
def chat() -> ResponseReturnValue:
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get('prompt') or '').strip()

    if not prompt:
        return jsonify({'error': 'プロンプトを入力してください。'}), 400

    _history.append(_make_message('user', prompt))

    try:
        controller = _get_agent_controller()
        agent_history = controller.run(prompt)
    except AgentControllerError as exc:
        message = f'エージェントの実行に失敗しました: {exc}'
        logger.warning(message)
        _history.append(_make_message('assistant', message))
        return jsonify({'messages': _history, 'run_summary': message}), 200
    except Exception as exc:  # noqa: BLE001
        logger.exception('Unexpected error while running browser agent')
        error_message = f'エージェントの実行中に予期しないエラーが発生しました: {exc}'
        _history.append(_make_message('assistant', error_message))
        return jsonify({'messages': _history, 'run_summary': error_message}), 200

    step_messages = _format_history_messages(agent_history)
    for content in step_messages:
        _history.append(_make_message('assistant', content))

    summary_message = _summarize_history(agent_history)
    _history.append(_make_message('assistant', summary_message))

    return jsonify({'messages': _history, 'run_summary': summary_message}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005)
