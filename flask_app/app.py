from __future__ import annotations

import asyncio
import atexit
import copy
import inspect
import json
import logging
import os
import queue
import re
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from itertools import count
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from bubus import EventBus
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context
from flask.typing import ResponseReturnValue

try:
    from browser_use import Agent, BrowserProfile, BrowserSession
except ModuleNotFoundError:
    import sys

    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from browser_use import Agent, BrowserProfile, BrowserSession

from browser_use.agent.views import ActionResult, AgentHistoryList, AgentOutput

try:
    from browser_use.browser.constants import DEFAULT_NEW_TAB_URL
except ModuleNotFoundError:
    DEFAULT_NEW_TAB_URL = "https://www.yahoo.co.jp"
from browser_use.browser.profile import ViewportSize
from browser_use.browser.views import BrowserStateSummary
from browser_use.llm.google.chat import ChatGoogle

load_dotenv()

logging.basicConfig(level=os.environ.get('FLASK_LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

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
_CDP_DETECTION_RETRIES = _env_int('BROWSER_USE_CDP_RETRIES', 5)
_CDP_DETECTION_RETRY_DELAY = float(os.environ.get('BROWSER_USE_CDP_RETRY_DELAY', '1.5'))

_CDP_SESSION_CLEANUP: Callable[[], None] | None = None

APP_STATIC_DIR = Path(__file__).resolve().parent / "static"

app = Flask(__name__)
app.json.ensure_ascii = False


@app.route("/favicon.ico")
def favicon() -> ResponseReturnValue:
    """Serve the browser agent favicon for root requests."""

    return send_from_directory(
        APP_STATIC_DIR / "icons",
        "browser-agent.ico",
        mimetype="image/x-icon",
    )


@app.route("/favicon.png")
def favicon_png() -> ResponseReturnValue:
    """Serve the png favicon variant for clients that request it."""

    return send_from_directory(APP_STATIC_DIR / "icons", "browser-agent.png")


@app.before_request
def _handle_cors_preflight():
    """Return an empty response for CORS preflight requests."""

    if request.method == 'OPTIONS':
        response = Response(status=204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = (
            request.headers.get('Access-Control-Request-Headers', '*')
        )
        response.headers['Access-Control-Allow-Methods'] = (
            request.headers.get(
                'Access-Control-Request-Method',
                'GET, POST, PUT, PATCH, DELETE, OPTIONS',
            )
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
    response.headers.setdefault(
        'Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    )
    return response

_message_sequence = count()


class MessageBroadcaster:
    """Simple pub/sub helper for Server-Sent Events."""

    def __init__(self) -> None:
        self._listeners: list[queue.SimpleQueue[dict[str, Any]]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.SimpleQueue[dict[str, Any]]:
        listener: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        with self._lock:
            self._listeners.append(listener)
        return listener

    def unsubscribe(self, listener: queue.SimpleQueue[dict[str, Any]]) -> None:
        with self._lock:
            with suppress(ValueError):
                self._listeners.remove(listener)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(event)

    def publish_message(self, message: dict[str, Any]) -> None:
        self.publish({'type': 'message', 'payload': message})

    def publish_update(self, message: dict[str, Any]) -> None:
        self.publish({'type': 'update', 'payload': message})

    def publish_reset(self) -> None:
        self.publish({'type': 'reset'})


def _replace_cdp_session_cleanup(cleanup: Callable[[], None] | None) -> None:
    """Store a cleanup callback, closing any previously registered session."""

    global _CDP_SESSION_CLEANUP

    previous = _CDP_SESSION_CLEANUP
    _CDP_SESSION_CLEANUP = cleanup
    if previous and previous is not cleanup:
        with suppress(Exception):
            previous()


def _consume_cdp_session_cleanup() -> Callable[[], None] | None:
    """Return and clear the currently registered CDP cleanup callback."""

    global _CDP_SESSION_CLEANUP

    cleanup = _CDP_SESSION_CLEANUP
    _CDP_SESSION_CLEANUP = None
    return cleanup


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


def _initial_history() -> list[dict[str, str | int]]:
    return [
        _make_message(
            'assistant',
            'ブラウザ操作エージェントへようこそ。GeminiのAPIキー（環境変数 GOOGLE_API_KEY または GEMINI_API_KEY）とCDP URLを設定すると、左側のチャットから自然言語でChromeを操作できます。',
        )
    ]


_history_lock = threading.Lock()
_history: list[dict[str, str | int]] = _initial_history()
_broadcaster = MessageBroadcaster()


def _copy_history() -> list[dict[str, str | int]]:
    with _history_lock:
        return [dict(message) for message in _history]


def _append_history_message(role: str, content: str) -> dict[str, str | int]:
    message = _make_message(role, content)
    with _history_lock:
        _history.append(message)
        stored = dict(message)
    _broadcaster.publish_message(stored)
    return stored


def _update_history_message(message_id: int, new_content: str) -> dict[str, str | int] | None:
    with _history_lock:
        for entry in _history:
            if entry['id'] == message_id:
                entry['content'] = new_content
                updated = dict(entry)
                break
        else:
            return None
    _broadcaster.publish_update(updated)
    return updated


def _reset_history() -> list[dict[str, str | int]]:
    global _history, _message_sequence
    with _history_lock:
        _message_sequence = count()
        _history = _initial_history()
        snapshot = [dict(message) for message in _history]
    _broadcaster.publish_reset()
    return snapshot

_DEFAULT_EMBED_BROWSER_URL = (
    'http://127.0.0.1:7900/vnc_lite.html?autoconnect=1&resize=scale&scale=auto&view_clip=false'
)
_ALLOWED_RESIZE_VALUES = {'scale', 'remote', 'off'}


def _normalize_embed_browser_url(value: str) -> str:
    """Ensure the embedded noVNC URL fills the container on first load."""

    if not value:
        return value

    parsed = urlparse(value)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)

    has_scale = any(key == 'scale' for key, _ in query_items)
    if not has_scale:
        query_items.append(('scale', 'auto'))

    normalized_items: list[tuple[str, str]] = []
    resize_present = False
    for key, value in query_items:
        if key == 'resize':
            resize_present = True
            if value not in _ALLOWED_RESIZE_VALUES:
                normalized_items.append(('resize', 'scale'))
            else:
                normalized_items.append((key, value))
        else:
            normalized_items.append((key, value))

    if not resize_present:
        normalized_items.append(('resize', 'scale'))

    normalized_query = urlencode(normalized_items)
    return urlunparse(parsed._replace(query=normalized_query))


_BROWSER_URL = _normalize_embed_browser_url(
    os.environ.get('EMBED_BROWSER_URL', _DEFAULT_EMBED_BROWSER_URL)
)


class AgentControllerError(RuntimeError):
    """Raised when the browser agent cannot be executed."""


_DEFAULT_GEMINI_MODEL = 'gemini-2.5-flash'


def _get_env_trimmed(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _normalize_start_url(value: str | None) -> str | None:
    """Normalize a configured start URL for the embedded browser."""

    if not value:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered in {'none', 'off', 'false'}:
        return None

    if normalized.startswith('//'):
        normalized = normalized[2:]

    if '://' not in normalized and not normalized.startswith(('about:', 'chrome:', 'file:')):
        normalized = f'https://{normalized}'

    return normalized


_DEFAULT_START_URL = _normalize_start_url(
    _get_env_trimmed('BROWSER_DEFAULT_START_URL'),
) or DEFAULT_NEW_TAB_URL

_LANGUAGE_EXTENSION = (
    '### 追加の言語ガイドライン\n'
    '- すべての思考過程、行動の評価、メモリ、次の目標、最終報告などの文章は必ず自然な日本語で記述してください。\n'
    '- 成功や失敗などのステータスも日本語（例: 成功、失敗、未確定）で明示してください。\n'
    '- Webページ上の固有名詞や引用、ユーザーに提示する必要がある原文テキストは、そのままの言語で保持しても問題ありません。\n'
    '- GoogleやDuckDuckGoなどの検索エンジンは使用しないでください。\n'
)

_SYSTEM_PROMPT_FILENAME = 'system_prompt_browser_agent.md'
_CUSTOM_SYSTEM_PROMPT_TEMPLATE: str | None = None
_DEFAULT_MAX_ACTIONS_PER_STEP = 10


def _system_prompt_candidate_paths() -> tuple[Path, ...]:
    script_path = Path(__file__).resolve()
    candidates: list[Path] = [script_path.parent / _SYSTEM_PROMPT_FILENAME]
    try:
        candidates.append(script_path.parents[1] / _SYSTEM_PROMPT_FILENAME)
    except IndexError:
        pass
    # Preserve order but remove duplicates
    unique_candidates: list[Path] = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return tuple(unique_candidates)


def _load_custom_system_prompt_template() -> str | None:
    global _CUSTOM_SYSTEM_PROMPT_TEMPLATE
    if _CUSTOM_SYSTEM_PROMPT_TEMPLATE is not None:
        return _CUSTOM_SYSTEM_PROMPT_TEMPLATE or None

    for candidate in _system_prompt_candidate_paths():
        if candidate.exists():
            try:
                _CUSTOM_SYSTEM_PROMPT_TEMPLATE = candidate.read_text(encoding='utf-8')
                logger.info('Loaded system prompt template from %s', candidate)
                return _CUSTOM_SYSTEM_PROMPT_TEMPLATE
            except OSError:
                logger.exception('Failed to read system prompt template at %s', candidate)
                break

    logger.warning(
        'Custom system prompt file %s not found; falling back to the packaged prompt template.',
        _SYSTEM_PROMPT_FILENAME,
    )
    _CUSTOM_SYSTEM_PROMPT_TEMPLATE = ''
    return None


def _build_custom_system_prompt(max_actions_per_step: int = _DEFAULT_MAX_ACTIONS_PER_STEP) -> str | None:
    template = _load_custom_system_prompt_template()
    if not template:
        return None

    current_datetime_line = datetime.now().strftime('現在の日時ー%Y年%m月%d日%H時%M分')
    try:
        return template.format(max_actions=max_actions_per_step, current_datetime=current_datetime_line)
    except Exception:  # noqa: BLE001
        logger.exception('Failed to format custom system prompt template; using raw template contents.')
        return template


def _resolve_gemini_api_key() -> str:
    for key in ('GOOGLE_API_KEY', 'GEMINI_API_KEY'):
        value = _get_env_trimmed(key)
        if value:
            return value
    return ''


def _create_gemini_llm() -> ChatGoogle:
    api_key = _resolve_gemini_api_key()
    if not api_key:
        raise AgentControllerError(
            'GeminiのAPIキーが設定されていません。環境変数 GOOGLE_API_KEY または GEMINI_API_KEY にキーを設定してください。',
        )

    model = (
        _get_env_trimmed('GOOGLE_GEMINI_MODEL')
        or _get_env_trimmed('GEMINI_MODEL')
        or _DEFAULT_GEMINI_MODEL
    )

    temperature_value = os.environ.get('GOOGLE_GEMINI_TEMPERATURE')
    llm_kwargs: dict[str, Any] = {'model': model, 'api_key': api_key}
    if temperature_value is not None:
        try:
            llm_kwargs['temperature'] = float(temperature_value)
        except ValueError:
            logger.warning(
                '環境変数GOOGLE_GEMINI_TEMPERATUREの値が無効のため既定値を使用します: %s',
                temperature_value,
            )

    return ChatGoogle(**llm_kwargs)


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


def _format_step_entry(index: int, step: Any) -> str:
    lines: list[str] = [f'ステップ{index}']
    state = getattr(step, 'state', None)
    if state:
        page_parts: list[str] = []
        if getattr(state, 'title', None):
            page_parts.append(_truncate(_compact_text(state.title), 80))
        if getattr(state, 'url', None):
            page_parts.append(state.url)
        if page_parts:
            lines.append('ページ: ' + ' / '.join(page_parts))

    model_output = getattr(step, 'model_output', None)
    if model_output:
        action_lines = [_format_action(action) for action in model_output.action]
        if action_lines:
            lines.append('アクション: ' + ' / '.join(action_lines))
        if model_output.evaluation_previous_goal:
            lines.append(
                '評価: ' + _truncate(_compact_text(model_output.evaluation_previous_goal), 120)
            )
        if model_output.next_goal:
            lines.append('次の目標: ' + _truncate(_compact_text(model_output.next_goal), 120))

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
    lines: list[str] = [f'ステップ{step_number} 計画']

    page_parts: list[str] = []
    if state.title:
        page_parts.append(_truncate(_compact_text(state.title), 80))
    if state.url:
        page_parts.append(state.url)
    if page_parts:
        lines.append('ページ: ' + ' / '.join(page_parts))

    action_lines = [_format_action(action) for action in model_output.action]
    if action_lines:
        lines.append('アクション候補: ' + ' / '.join(action_lines))
    if model_output.evaluation_previous_goal:
        lines.append(
            '評価: ' + _truncate(_compact_text(model_output.evaluation_previous_goal), 120)
        )
    if model_output.memory:
        lines.append('メモリ: ' + _truncate(_compact_text(model_output.memory), 120))
    if model_output.next_goal:
        lines.append('次の目標: ' + _truncate(_compact_text(model_output.next_goal), 120))

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
        lines.append('最終報告: ' + _truncate(_compact_text(final_text), 200))

    if history.history:
        last_state = history.history[-1].state
        if last_state and last_state.url:
            lines.append(f'最終URL: {last_state.url}')

    return _append_final_response_notice('\n'.join(lines))


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
                candidate_url = ws_url.strip()
                if candidate_url:
                    _replace_cdp_session_cleanup(None)
                    return candidate_url
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    ws_url = item.get('webSocketDebuggerUrl')
                    if ws_url:
                        candidate_url = ws_url.strip()
                        if candidate_url:
                            _replace_cdp_session_cleanup(None)
                            return candidate_url
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
    if cdp_url:
        cdp_url = cdp_url.strip()

    if not cdp_url:
        if session_id:
            _cleanup_webdriver_session(base_endpoint, session_id)
        return None

    if session_id:
        cleaned = False

        def cleanup_session() -> None:
            nonlocal cleaned
            if cleaned:
                return
            cleaned = True
            _cleanup_webdriver_session(base_endpoint, session_id)

        _replace_cdp_session_cleanup(cleanup_session)
    else:
        _replace_cdp_session_cleanup(None)

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


def _detect_cdp_from_candidates(candidates: list[str]) -> str | None:
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

    return None


def _resolve_cdp_url() -> str | None:
    explicit_keys = ('BROWSER_USE_CDP_URL', 'CDP_URL', 'REMOTE_CDP_URL')
    for key in explicit_keys:
        value = os.environ.get(key)
        if value:
            logger.info('Using CDP URL from %s', key)
            _replace_cdp_session_cleanup(None)
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

    retries = max(1, _CDP_DETECTION_RETRIES)
    delay = _CDP_DETECTION_RETRY_DELAY if _CDP_DETECTION_RETRY_DELAY > 0 else 0.0

    for attempt in range(1, retries + 1):
        ws_url = _detect_cdp_from_candidates(candidates)
        if ws_url:
            return ws_url

        cleanup = _consume_cdp_session_cleanup()
        if cleanup:
            with suppress(Exception):
                cleanup()

        if attempt < retries:
            logger.info(
                'Chrome DevToolsのCDP URLの検出に失敗しました。リトライします (%s/%s)...',
                attempt + 1,
                retries,
            )
            if delay:
                time.sleep(delay)

    logger.warning('Chrome DevToolsのCDP URLを自動検出できませんでした。環境変数BROWSER_USE_CDP_URLを設定してください。')
    return None


@dataclass
class AgentRunResult:
    history: AgentHistoryList
    step_message_ids: dict[int, int] = field(default_factory=dict)
    filtered_history: AgentHistoryList | None = None


class BrowserAgentController:
    """Manage a long-lived browser session controlled by browser-use."""

    def __init__(
        self,
        cdp_url: str | None,
        max_steps: int,
        cdp_cleanup: Callable[[], None] | None = None,
    ) -> None:
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
        self._state_lock = threading.Lock()
        self._browser_session: BrowserSession | None = None
        self._shutdown = False
        self._logger = logging.getLogger('browser_use.flask.agent')
        self._cdp_cleanup = cdp_cleanup
        self._llm = _create_gemini_llm()
        self._agent: Agent | None = None
        self._current_agent: Agent | None = None
        self._is_running = False
        self._paused = False
        self._step_message_ids: dict[int, int] = {}
        self._step_message_lock = threading.Lock()
        self._resume_url: str | None = None
        self._session_recreated = False
        self._start_page_ready = False
        atexit.register(self.shutdown)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _ensure_browser_session(self) -> BrowserSession:
        if self._browser_session is not None:
            with self._state_lock:
                self._session_recreated = False
            return self._browser_session

        if not self._cdp_url:
            raise AgentControllerError(
                'Chrome DevToolsのCDP URLが検出できませんでした。BROWSER_USE_CDP_URL を設定してください。'
            )

        def _viewport_from_env(
            width_key: str,
            height_key: str,
            default_width: int,
            default_height: int,
        ) -> ViewportSize | None:
            """Create a viewport from environment variables if either is defined."""

            width_raw = os.environ.get(width_key)
            height_raw = os.environ.get(height_key)

            if width_raw is None and height_raw is None:
                return None

            width = _env_int(width_key, default_width)
            height = _env_int(height_key, default_height)

            return ViewportSize(width=width, height=height)

        window_size: ViewportSize | None = None
        screen_size: ViewportSize | None = None

        browser_window = _viewport_from_env('BROWSER_WINDOW_WIDTH', 'BROWSER_WINDOW_HEIGHT', 1920, 1080)
        if browser_window is not None:
            window_size = browser_window
            screen_size = browser_window
        else:
            selenium_window = _viewport_from_env('SE_SCREEN_WIDTH', 'SE_SCREEN_HEIGHT', 1920, 1080)
            if selenium_window is not None:
                window_size = selenium_window
                screen_size = selenium_window

        profile = BrowserProfile(
            cdp_url=self._cdp_url,
            keep_alive=True,
            highlight_elements=True,
            wait_between_actions=0.4,
            window_size=window_size,
            screen=screen_size,
        )
        session = BrowserSession(browser_profile=profile)
        with self._state_lock:
            self._browser_session = session
            self._session_recreated = True
            self._start_page_ready = False
        return session

    def _consume_session_recreated(self) -> bool:
        with self._state_lock:
            recreated = self._session_recreated
            self._session_recreated = False
        return recreated

    async def _run_agent(self, task: str, record_history: bool = True) -> AgentRunResult:
        session = await self._ensure_browser_session()
        session_recreated = self._consume_session_recreated()

        step_message_ids: dict[int, int] = {}
        starting_step_number = 1
        history_start_index = 0

        def handle_new_step(
            state_summary: BrowserStateSummary,
            model_output: AgentOutput,
            step_number: int,
        ) -> None:
            if not record_history:
                return
            try:
                relative_step = step_number - starting_step_number + 1
                if relative_step < 1:
                    relative_step = 1
                content = _format_step_plan(relative_step, state_summary, model_output)
                message = _append_history_message('assistant', content)
                message_id = int(message['id'])
                step_message_ids[relative_step] = message_id
                self.remember_step_message_id(relative_step, message_id)
            except Exception:  # noqa: BLE001
                self._logger.debug('Failed to broadcast step update', exc_info=True)

        register_callback = handle_new_step if record_history else None

        def _create_new_agent(initial_task: str) -> Agent:
            custom_system_prompt = _build_custom_system_prompt()
            extend_system_message = None if custom_system_prompt else _LANGUAGE_EXTENSION
            fresh_agent = Agent(
                task=initial_task,
                browser_session=session,
                llm=self._llm,
                register_new_step_callback=register_callback,
                max_actions_per_step=_DEFAULT_MAX_ACTIONS_PER_STEP,
                override_system_message=custom_system_prompt,
                extend_system_message=extend_system_message,
            )
            start_url = self._get_resume_url() or _DEFAULT_START_URL
            if start_url and not fresh_agent.initial_actions:
                try:
                    fresh_agent.initial_url = start_url
                    fresh_agent.initial_actions = fresh_agent._convert_initial_actions(
                        [{'go_to_url': {'url': start_url, 'new_tab': False}}]
                    )
                except Exception:  # noqa: BLE001
                    self._logger.debug(
                        'Failed to apply start URL %s',
                        start_url,
                        exc_info=True,
                    )
            return fresh_agent

        with self._state_lock:
            existing_agent = self._agent
            agent_running = self._is_running

        if agent_running:
            raise AgentControllerError('エージェントは実行中です。')

        if existing_agent is None:
            agent = _create_new_agent(task)
            with self._state_lock:
                self._agent = agent
        else:
            agent = existing_agent
            agent.browser_session = session
            agent.register_new_step_callback = register_callback
            try:
                agent.add_new_task(task)
                self._prepare_agent_for_follow_up(agent, force_resume_navigation=session_recreated)
            except (AssertionError, ValueError) as exc:
                self._logger.exception('Failed to apply follow-up task %r; recreating agent.', task)
                with self._state_lock:
                    self._agent = None
                    self._current_agent = None
                agent = _create_new_agent(task)
                with self._state_lock:
                    self._agent = agent
                self._logger.info('Recreated agent after failure and retrying task %r.', task)
            except Exception as exc:  # noqa: BLE001
                raise AgentControllerError(f'追加の指示の適用に失敗しました: {exc}') from exc

        history_items = getattr(agent, 'history', None)
        if history_items is not None:
            history_start_index = len(history_items.history)
        starting_step_number = getattr(getattr(agent, 'state', None), 'n_steps', 1) or 1
        self._clear_step_message_ids()

        attach_watchdogs = getattr(session, 'attach_all_watchdogs', None)
        if attach_watchdogs is not None:
            try:
                await attach_watchdogs()
            except Exception:  # noqa: BLE001
                self._logger.debug('Failed to pre-attach browser watchdogs', exc_info=True)

        with self._state_lock:
            self._current_agent = agent
            self._is_running = True
            self._paused = False
        try:
            history = await agent.run(max_steps=self._max_steps)
            self._update_resume_url_from_history(history)
            new_entries = history.history[history_start_index:]
            filtered_entries = [
                entry
                for entry in new_entries
                if not getattr(entry, 'metadata', None)
                or getattr(entry.metadata, 'step_number', None) != 0
            ]
            if filtered_entries or not new_entries:
                relevant_entries = filtered_entries
            else:
                relevant_entries = new_entries
            if isinstance(history, AgentHistoryList):
                history_kwargs = {'history': relevant_entries}
                if hasattr(history, 'usage'):
                    history_kwargs['usage'] = getattr(history, 'usage')
                filtered_history = history.__class__(**history_kwargs)
                if hasattr(history, '_output_model_schema'):
                    filtered_history._output_model_schema = history._output_model_schema
            else:
                filtered_history = copy.copy(history)
                setattr(filtered_history, 'history', relevant_entries)
            return AgentRunResult(
                history=history,
                step_message_ids=step_message_ids,
                filtered_history=filtered_history,
            )
        finally:
            keep_alive = session.browser_profile.keep_alive
            rotate_session = False
            if keep_alive:
                drain_method = getattr(type(session), 'drain_event_bus', None)
                if callable(drain_method):
                    try:
                        drained_cleanly = await drain_method(session)
                    except Exception:  # noqa: BLE001
                        rotate_session = True
                        self._logger.warning(
                            'Failed to drain browser event bus; rotating for safety.',
                            exc_info=True,
                        )
                    else:
                        if not drained_cleanly:
                            rotate_session = True
                            self._logger.warning(
                                'Browser event bus rotated after drain timeout; pending events cleared.',
                            )
                else:
                    self._logger.debug(
                        'Browser session implementation does not expose drain_event_bus(); applying compatibility cleanup.',
                    )
                    with suppress(Exception):
                        await session.event_bus.stop(clear=True, timeout=1.0)

                    def _resync_agent_event_bus() -> None:
                        with self._state_lock:
                            candidate = self._agent or self._current_agent
                        if candidate is None:
                            return
                        if getattr(candidate, 'browser_session', None) is not session:
                            return

                        reset_agent_bus = getattr(candidate, '_reset_eventbus', None)
                        if callable(reset_agent_bus):
                            try:
                                reset_agent_bus()
                            except Exception:  # noqa: BLE001
                                self._logger.warning(
                                    'Failed to reset agent event bus after legacy session refresh; attempting manual synchronisation.',
                                    exc_info=True,
                                )
                            else:
                                return

                        refresh_agent_bus = getattr(
                            candidate,
                            '_refresh_browser_session_eventbus',
                            None,
                        )
                        if callable(refresh_agent_bus):
                            try:
                                refresh_agent_bus(reset_watchdogs=True)
                            except Exception:  # noqa: BLE001
                                self._logger.warning(
                                    'Failed to refresh agent event bus after legacy session refresh.',
                                    exc_info=True,
                                )

                    reset_method = getattr(session, '_reset_event_bus_state', None)
                    if callable(reset_method):
                        try:
                            reset_method()
                        except Exception:  # noqa: BLE001
                            self._logger.debug(
                                'Legacy browser session failed to reset event bus state cleanly.',
                                exc_info=True,
                            )
                        else:
                            _resync_agent_event_bus()
                    else:
                        self._logger.debug(
                            'Legacy browser session missing _reset_event_bus_state(); refreshing EventBus manually.',
                        )
                        try:
                            session.event_bus = EventBus()
                            try:
                                session._watchdogs_attached = False  # type: ignore[attr-defined]
                            except Exception:  # noqa: BLE001
                                self._logger.debug(
                                    'Unable to reset watchdog attachment flag during manual event bus refresh.',
                                    exc_info=True,
                                )
                            for attribute in (
                                '_crash_watchdog',
                                '_downloads_watchdog',
                                '_aboutblank_watchdog',
                                '_security_watchdog',
                                '_storage_state_watchdog',
                                '_local_browser_watchdog',
                                '_default_action_watchdog',
                                '_dom_watchdog',
                                '_screenshot_watchdog',
                                '_permissions_watchdog',
                                '_recording_watchdog',
                            ):
                                if hasattr(session, attribute):
                                    try:
                                        setattr(session, attribute, None)
                                    except Exception:  # noqa: BLE001
                                        self._logger.debug(
                                            'Unable to clear %s during manual event bus refresh.',
                                            attribute,
                                            exc_info=True,
                                        )
                            session.model_post_init(None)
                        except Exception:  # noqa: BLE001
                            rotate_session = True
                            self._logger.warning(
                                'Failed to refresh EventBus on legacy browser session; scheduling full rotation.',
                                exc_info=True,
                            )
                        else:
                            _resync_agent_event_bus()
            else:
                with suppress(Exception):
                    await session.stop()

            if rotate_session:
                with suppress(Exception):
                    await session.stop()
                kill_method = getattr(session, 'kill', None)
                if callable(kill_method):
                    with suppress(Exception):
                        maybe_kill = kill_method()
                        if inspect.isawaitable(maybe_kill):
                            await maybe_kill

            with self._state_lock:
                if self._browser_session is session:
                    if rotate_session:
                        self._browser_session = None
                        self._logger.info(
                            'Browser session rotated after event bus drain failure; a fresh session will be created on the next run.',
                        )
                    elif keep_alive:
                        self._logger.debug(
                            'Browser session kept alive for follow-up runs.',
                        )
                    else:
                        self._logger.debug(
                            'Browser session stopped; a new session will be created on the next run.',
                        )
                        self._browser_session = None
                self._current_agent = None
                self._is_running = False
                self._paused = False

    def _pop_browser_session(self) -> BrowserSession | None:
        with self._state_lock:
            session = self._browser_session
            self._browser_session = None
            self._session_recreated = False
            self._start_page_ready = False
        return session

    def _stop_browser_session(self) -> None:
        session = self._pop_browser_session()
        if session is None:
            return

        async def _shutdown() -> None:
            with suppress(Exception):
                await session.stop()

        future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
        try:
            future.result(timeout=5)
        except Exception:  # noqa: BLE001
            future.cancel()
            self._logger.warning(
                'Failed to stop browser session cleanly; a fresh session will be created on the next run.',
                exc_info=True,
            )

    async def _async_shutdown(self) -> None:
        session = self._pop_browser_session()
        if session is not None:
            with suppress(Exception):
                await session.stop()

    def _call_in_loop(self, func: Callable[[], None]) -> None:
        async def _invoke() -> None:
            func()

        future = asyncio.run_coroutine_threadsafe(_invoke(), self._loop)
        future.result()

    def enqueue_follow_up(self, task: str) -> None:
        with self._state_lock:
            agent = self._current_agent
            running = self._is_running

        if not agent or not running:
            raise AgentControllerError('エージェントは実行中ではありません。')

        def _apply() -> None:
            agent.add_new_task(task)

        try:
            self._call_in_loop(_apply)
        except AgentControllerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AgentControllerError(f'追加の指示の適用に失敗しました: {exc}') from exc

    def _prepare_agent_for_follow_up(self, agent: Agent, *, force_resume_navigation: bool = False) -> None:
        """Clear completion flags so follow-up runs can execute new steps."""

        cleared = False

        with suppress(AttributeError):
            cleared = agent.reset_completion_state()
            agent.state.stopped = False
            agent.state.paused = False

        if cleared:
            self._logger.debug('Cleared completion state for follow-up agent run.')

        resume_url = self._get_resume_url()
        prepared_resume = False

        if force_resume_navigation and resume_url:
            try:
                agent.initial_url = resume_url
                agent.initial_actions = agent._convert_initial_actions(
                    [{'go_to_url': {'url': resume_url, 'new_tab': False}}]
                )
                agent.state.follow_up_task = False
                prepared_resume = True
                self._logger.debug('Prepared follow-up run to resume at %s.', resume_url)
            except Exception:  # noqa: BLE001
                self._logger.debug(
                    'Failed to prepare resume navigation to %s',
                    resume_url,
                    exc_info=True,
                )
                agent.initial_actions = None

        if not prepared_resume:
            agent.initial_url = None
            agent.initial_actions = None
            agent.state.follow_up_task = True

    def _record_step_message_id(self, step_number: int, message_id: int) -> None:
        with self._step_message_lock:
            self._step_message_ids[step_number] = message_id

    def _lookup_step_message_id(self, step_number: int) -> int | None:
        with self._step_message_lock:
            return self._step_message_ids.get(step_number)

    def _clear_step_message_ids(self) -> None:
        with self._step_message_lock:
            self._step_message_ids.clear()

    def _set_resume_url(self, url: str | None) -> None:
        with self._state_lock:
            self._resume_url = url

    def _get_resume_url(self) -> str | None:
        with self._state_lock:
            return self._resume_url

    def _update_resume_url_from_history(self, history: AgentHistoryList) -> None:
        resume_url: str | None = None
        try:
            for entry in reversed(history.history):
                state = getattr(entry, 'state', None)
                if state is None:
                    continue
                url = getattr(state, 'url', None)
                if not url:
                    continue
                normalized = url.strip()
                if not normalized:
                    continue
                lowered = normalized.lower()
                if lowered.startswith('about:') or lowered.startswith('chrome-error://'):
                    continue
                if lowered.startswith('chrome://') or lowered.startswith('devtools://'):
                    continue
                resume_url = normalized
                break
        except Exception:  # noqa: BLE001
            self._logger.debug('Failed to derive resume URL from agent history.', exc_info=True)
            return

        self._set_resume_url(resume_url)
        if resume_url:
            self._logger.debug('Recorded resume URL for follow-up runs: %s', resume_url)

    def remember_step_message_id(self, step_number: int, message_id: int) -> None:
        self._record_step_message_id(step_number, message_id)

    def get_step_message_id(self, step_number: int) -> int | None:
        return self._lookup_step_message_id(step_number)

    def pause(self) -> None:
        with self._state_lock:
            agent = self._current_agent
            running = self._is_running
            already_paused = self._paused

        if not agent or not running:
            raise AgentControllerError('エージェントは実行されていません。')
        if already_paused:
            raise AgentControllerError('エージェントは既に一時停止中です。')

        try:
            self._call_in_loop(agent.pause)
        except Exception as exc:  # noqa: BLE001
            raise AgentControllerError(f'一時停止に失敗しました: {exc}') from exc

        with self._state_lock:
            self._paused = True

    def resume(self) -> None:
        with self._state_lock:
            agent = self._current_agent
            running = self._is_running
            paused = self._paused

        if not agent or not running:
            raise AgentControllerError('エージェントは実行されていません。')
        if not paused:
            raise AgentControllerError('エージェントは一時停止状態ではありません。')

        try:
            self._call_in_loop(agent.resume)
        except Exception as exc:  # noqa: BLE001
            raise AgentControllerError(f'再開に失敗しました: {exc}') from exc

        with self._state_lock:
            self._paused = False

    def is_running(self) -> bool:
        with self._state_lock:
            return self._is_running

    def is_paused(self) -> bool:
        with self._state_lock:
            return self._paused

    def ensure_start_page_ready(self) -> None:
        """Ensure the embedded browser session opens the configured start URL."""

        start_url = self._get_resume_url() or _DEFAULT_START_URL
        if not start_url:
            return

        with self._state_lock:
            if self._start_page_ready and self._browser_session is not None:
                return
            running = self._is_running
            shutdown = self._shutdown

        if running or shutdown:
            return

        async def _warmup() -> str | None:
            session = await self._ensure_browser_session()
            try:
                await session.start()
            except Exception:  # noqa: BLE001
                self._logger.debug('Failed to start browser session during warmup', exc_info=True)
                raise

            try:
                await session.attach_all_watchdogs()
            except Exception:  # noqa: BLE001
                self._logger.debug('Failed to pre-attach browser watchdogs during warmup', exc_info=True)

            try:
                await session.navigate_to(start_url, new_tab=False)
            except Exception:  # noqa: BLE001
                self._logger.debug('Failed to warm up start URL %s', start_url, exc_info=True)
                raise

            try:
                return await session.get_current_page_url()
            except Exception:  # noqa: BLE001
                self._logger.debug('Failed to verify browser location after warmup', exc_info=True)
                return None

        try:
            future = asyncio.run_coroutine_threadsafe(_warmup(), self._loop)
            current_url = future.result(timeout=20)
        except Exception:  # noqa: BLE001
            self._logger.debug('Failed to prepare browser start page', exc_info=True)
            return

        if current_url and current_url.rstrip('/') != start_url.rstrip('/'):
            self._logger.debug(
                'Browser start page warmup navigated to %s instead of configured %s',
                current_url,
                start_url,
            )

        with self._state_lock:
            if self._browser_session is not None:
                self._start_page_ready = True

    def reset(self) -> None:
        with self._state_lock:
            if self._is_running:
                raise AgentControllerError('エージェント実行中はリセットできません。')
        self._stop_browser_session()
        with self._state_lock:
            self._agent = None
            self._current_agent = None
            self._paused = False
        self._set_resume_url(None)
        self._clear_step_message_ids()

    def prepare_for_new_task(self) -> None:
        with self._state_lock:
            if self._is_running:
                raise AgentControllerError('エージェント実行中は新しいタスクを開始できません。')
            self._agent = None
            self._current_agent = None
            self._paused = False
        self._clear_step_message_ids()

    def run(self, task: str, record_history: bool = True) -> AgentRunResult:
        if self._shutdown:
            raise AgentControllerError('エージェントコントローラーは停止済みです。')

        with self._lock:
            future = asyncio.run_coroutine_threadsafe(
                self._run_agent(task, record_history=record_history),
                self._loop,
            )
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
        with self._state_lock:
            self._agent = None
            self._current_agent = None
            self._paused = False
        self._set_resume_url(None)
        self._clear_step_message_ids()

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

        if self._cdp_cleanup:
            try:
                self._cdp_cleanup()
            finally:
                self._cdp_cleanup = None


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
            raise AgentControllerError(
                'Chrome DevToolsのCDP URLが検出できませんでした。BROWSER_USE_CDP_URL を設定してください。'
            )
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
    except Exception:  # noqa: BLE001
        logger.debug('Unexpected error while preparing browser controller on index load', exc_info=True)
        controller = None

    if controller is not None:
        try:
            controller.ensure_start_page_ready()
        except Exception:  # noqa: BLE001
            logger.debug('Failed to warm up browser start page on index load', exc_info=True)

    return render_template('index.html', browser_url=_BROWSER_URL)


@app.get('/api/history')
def history() -> ResponseReturnValue:
    return jsonify({'messages': _copy_history()}), 200


@app.get('/api/stream')
def stream() -> ResponseReturnValue:
    listener = _broadcaster.subscribe()

    def event_stream() -> Any:
        try:
            while True:
                event = listener.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            pass
        finally:
            _broadcaster.unsubscribe(listener)

    headers = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    return Response(stream_with_context(event_stream()), mimetype='text/event-stream', headers=headers)


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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        logger.exception('Unexpected error while preparing agent controller for agent relay')
        return jsonify({'error': f'エージェントの初期化中に予期しないエラーが発生しました: {exc}'}), 500

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
        except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        logger.exception('Unexpected error while executing agent relay request')
        return jsonify({'error': f'予期しないエラーが発生しました: {exc}'}), 500

    agent_history = run_result.filtered_history or run_result.history
    summary_message = _summarize_history(agent_history)
    step_messages = [
        {'step_number': number, 'content': content}
        for number, content in _format_history_messages(agent_history)
    ]

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
        except Exception as exc:  # noqa: BLE001
            logger.exception('Failed to reset agent controller')
            return jsonify({'error': f'エージェントのリセットに失敗しました: {exc}'}), 500

    try:
        snapshot = _reset_history()
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        logger.exception('Failed to resume agent')
        return jsonify({'error': f'再開に失敗しました: {exc}'}), 500
    return jsonify({'status': 'resumed'}), 200


async def _analyze_conversation_history_async(conversation_history: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Analyze conversation history using LLM to determine if browser operations are needed.
    
    Returns a dict with:
    - needs_action: bool - whether browser operations are needed
    - action_type: str - type of action (if needed)
    - task_description: str - description of the task to perform (if needed)
    - reason: str - explanation of the decision
    """
    try:
        llm = _create_gemini_llm()
    except AgentControllerError as exc:
        logger.warning('Failed to create Gemini LLM for conversation analysis: %s', exc)
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
    analysis_prompt = f"""以下の会話履歴を分析して、問題が発生していてブラウザ操作で解決できそうかどうかを判断してください。

会話履歴:
{conversation_text}

以下の条件に基づいて判断してください:
1. ユーザーが困っている、またはエラーが発生している
2. その問題がWebブラウザの操作（検索、ページ遷移、情報取得、フォーム入力など）で解決できる可能性がある
3. 問題が明確で、具体的なタスクとして実行可能である

結果を以下のJSON形式で出力してください:
{{
  "needs_action": true/false,
  "action_type": "search" | "navigate" | "form_fill" | "data_extract" | null,
  "task_description": "実行すべき具体的なタスクの説明（needs_actionがtrueの場合）",
  "reason": "判断の理由"
}}

JSON形式で回答してください。その他の説明は不要です。"""
    
    try:
        # Use LLM to generate structured analysis
        from browser_use.llm.messages import BaseMessage
        
        messages = [BaseMessage(role='user', content=analysis_prompt)]
        response = await llm.ainvoke(messages)
        
        # Extract JSON from response
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        # Try to parse JSON from the response
        # Sometimes LLM wraps JSON in markdown code blocks
        # Note: These regex patterns work for simple JSON objects but may not handle
        # deeply nested structures. The LLM is prompted to output simple JSON.
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if json_match:
            json_text = json_match.group(1)
        else:
            # Try to find raw JSON
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(0)
            else:
                json_text = response_text
        
        result = json.loads(json_text)
        
        # Validate the response structure
        if not isinstance(result, dict):
            raise ValueError('LLM response is not a dictionary')
        
        # Ensure required fields exist with defaults
        return {
            'needs_action': bool(result.get('needs_action', False)),
            'action_type': result.get('action_type'),
            'task_description': result.get('task_description'),
            'reason': result.get('reason', '理由が提供されていません'),
        }
        
    except json.JSONDecodeError as exc:
        logger.warning('Failed to parse LLM response as JSON: %s', exc)
        return {
            'needs_action': False,
            'action_type': None,
            'task_description': None,
            'reason': f'LLMの応答をJSON形式で解析できませんでした: {exc}',
        }
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning('Error during conversation history analysis: %s', exc)
        return {
            'needs_action': False,
            'action_type': None,
            'task_description': None,
            'reason': f'会話履歴の分析中にエラーが発生しました: {exc}',
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception('Unexpected error during conversation history analysis')
        return {
            'needs_action': False,
            'action_type': None,
            'task_description': None,
            'reason': f'予期しないエラーが発生しました: {exc}',
        }


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


@app.post('/api/check-conversation-history')
def check_conversation_history() -> ResponseReturnValue:
    """
    Endpoint to receive and analyze conversation history from other agents.
    
    Expects JSON payload with:
    - conversation_history: list of message objects with 'role' and 'content' fields
    
    Returns:
    - analysis: the LLM analysis result
    - action_taken: whether any browser action was initiated
    - run_summary: summary of the action taken (if any)
    """
    payload = request.get_json(silent=True) or {}
    conversation_history = payload.get('conversation_history', [])
    
    if not conversation_history:
        return jsonify({'error': '会話履歴が提供されていません。'}), 400
    
    if not isinstance(conversation_history, list):
        return jsonify({'error': '会話履歴はリスト形式である必要があります。'}), 400
    
    # Analyze the conversation history
    analysis = _analyze_conversation_history(conversation_history)
    
    response_data = {
        'analysis': analysis,
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
        except Exception as exc:  # noqa: BLE001
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
            
        except AgentControllerError as exc:
            logger.warning('Failed to execute browser agent task: %s', exc)
            response_data['run_summary'] = f'エージェントの実行に失敗しました: {exc}'
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            logger.warning('System error during browser agent execution: %s', exc)
            response_data['run_summary'] = f'エージェントの実行中にシステムエラーが発生しました: {exc}'
        except Exception as exc:  # noqa: BLE001
            logger.exception('Unexpected error while executing browser agent task')
            response_data['run_summary'] = f'予期しないエラーが発生しました: {exc}'
    
    return jsonify(response_data), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005)
