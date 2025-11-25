from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import logger
from .env_utils import _env_int

_CDP_PROBE_TIMEOUT = float(os.environ.get('BROWSER_USE_CDP_TIMEOUT', '2.0'))
_CDP_DETECTION_RETRIES = _env_int('BROWSER_USE_CDP_RETRIES', 5)
_CDP_DETECTION_RETRY_DELAY = float(os.environ.get('BROWSER_USE_CDP_RETRY_DELAY', '1.5'))

_CDP_SESSION_CLEANUP: Callable[[], None] | None = None


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
			ws_url = payload.get('webSocketDebuggerUrl') or payload.get('webSocketUrl') or payload.get('websocketUrl')
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
	payload = json.dumps(
		{
			'capabilities': {
				'alwaysMatch': {
					'browserName': 'chrome',
				}
			}
		}
	).encode('utf-8')
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
