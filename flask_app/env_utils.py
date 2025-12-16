from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .config import logger

try:
	from browser_use.browser.constants import DEFAULT_NEW_TAB_URL
except ModuleNotFoundError:
	DEFAULT_NEW_TAB_URL = 'https://www.yahoo.co.jp'


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


_DEFAULT_EMBED_BROWSER_URL = 'http://127.0.0.1:7900/vnc_lite.html?autoconnect=1&resize=scale&scale=auto&view_clip=false'
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


_DEFAULT_START_URL = (
	_normalize_start_url(
		_get_env_trimmed('BROWSER_DEFAULT_START_URL'),
	)
	or DEFAULT_NEW_TAB_URL
)

_BROWSER_URL = _normalize_embed_browser_url(os.environ.get('EMBED_BROWSER_URL', _DEFAULT_EMBED_BROWSER_URL))

_AGENT_MAX_STEPS = _env_int('AGENT_MAX_STEPS', 20)
_WEBARENA_MAX_STEPS = _env_int('WEBARENA_AGENT_MAX_STEPS', 20)
