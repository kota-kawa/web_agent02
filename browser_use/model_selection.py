"""Load shared model selection from Multi-Agent-Platform/model_settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_SELECTION = {'provider': 'openai', 'model': 'gpt-5.1'}

PROVIDER_DEFAULTS: dict[str, dict[str, str | None]] = {
	'openai': {'api_key_env': 'OPENAI_API_KEY', 'base_url_env': 'OPENAI_BASE_URL', 'default_base_url': None},
	'claude': {
		'api_key_env': 'CLAUDE_API_KEY',
		'base_url_env': 'CLAUDE_API_BASE',
		'default_base_url': None,
	},
	'gemini': {
		'api_key_env': 'GEMINI_API_KEY',
		'base_url_env': 'GEMINI_API_BASE',
		'default_base_url': 'https://generativelanguage.googleapis.com/v1beta',
	},
	'groq': {
		'api_key_env': 'GROQ_API_KEY',
		'base_url_env': 'GROQ_API_BASE',
		'default_base_url': 'https://api.groq.com/openai/v1',
	},
}

_OVERRIDE_SELECTION: dict[str, str] | None = None


def _load_selection(agent_key: str) -> dict[str, str]:
	# Try local cache first (for Docker/persistence)
	local_path = Path('local_model_settings.json')
	if local_path.is_file():
		try:
			data = json.loads(local_path.read_text(encoding='utf-8'))
			if isinstance(data, dict) and data.get('provider') and data.get('model'):
				return {'provider': data['provider'], 'model': data['model']}
		except (OSError, json.JSONDecodeError):
			pass

	platform_path = Path(__file__).resolve().parents[2] / 'Multi-Agent-Platform' / 'model_settings.json'
	try:
		data = json.loads(platform_path.read_text(encoding='utf-8'))
	except (FileNotFoundError, json.JSONDecodeError):
		return dict(DEFAULT_SELECTION)

	selection = data.get('selection') or data
	if not isinstance(selection, dict):
		return dict(DEFAULT_SELECTION)

	chosen = selection.get(agent_key)
	if not isinstance(chosen, dict):
		return dict(DEFAULT_SELECTION)

	provider = (chosen.get('provider') or DEFAULT_SELECTION['provider']).strip()
	model = (chosen.get('model') or DEFAULT_SELECTION['model']).strip()
	return {'provider': provider, 'model': model}


def _normalize_base_url(provider: str, base_url: str | None, explicit: bool = False) -> str:
	"""Strip provider-mismatched base URLs left over from previous selections.

	`explicit=True` means the caller intentionally set the base_url (e.g. via UI),
	so we preserve it unless it's empty. Environment-derived values are treated
	as best-effort hints and filtered if they clearly belong to a different
	provider (e.g. Groq URL while OpenAI is selected).
	"""

	if not base_url:
		return ''

	normalized = base_url.strip().rstrip('/')
	if not normalized:
		return ''

	provider_defaults = {
		key: (cfg.get('default_base_url') or '').rstrip('/')
		for key, cfg in PROVIDER_DEFAULTS.items()
		if cfg.get('default_base_url')
	}
	current_default = provider_defaults.get(provider, '')
	other_defaults = {val for key, val in provider_defaults.items() if key != provider}

	# Force cleanup of known provider mismatches, even if explicit=True
	if provider != 'groq' and 'api.groq.com' in normalized:
		return ''
	if provider != 'gemini' and 'generativelanguage.googleapis.com' in normalized:
		return ''
	if provider == 'claude' and 'openrouter.ai' in normalized:
		return ''
	if provider == 'gemini' and normalized.endswith('/openai/v1'):
		return ''

	if not explicit:
		# Avoid reusing obvious cross-provider URLs (e.g. Groq -> OpenAI)
		if normalized in other_defaults:
			return ''

	return normalized or current_default


def apply_model_selection(agent_key: str = 'browser', override: dict[str, str] | None = None) -> dict[str, str]:
	"""Set env vars DEFAULT_LLM/OPENAI_API_KEY/OPENAI_BASE_URL according to selection."""

	selection = override or _OVERRIDE_SELECTION or _load_selection(agent_key)
	provider = selection.get('provider') or DEFAULT_SELECTION['provider']
	model = selection.get('model') or DEFAULT_SELECTION['model']

	meta = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS['openai'])
	api_key_env = meta.get('api_key_env') or 'OPENAI_API_KEY'
	base_url_env = meta.get('base_url_env') or ''

	# Handle OPENAI_API_KEY backup/restore to prevent overwriting with other provider keys
	if provider == 'openai':
		if '_ORIGINAL_OPENAI_API_KEY' in os.environ:
			os.environ['OPENAI_API_KEY'] = os.environ['_ORIGINAL_OPENAI_API_KEY']

	api_key = os.getenv(api_key_env) or os.getenv(api_key_env.lower()) or os.getenv('OPENAI_API_KEY')
	if api_key:
		if provider != 'openai':
			# If we are switching away from OpenAI, backup the original key if it exists
			if 'OPENAI_API_KEY' in os.environ and '_ORIGINAL_OPENAI_API_KEY' not in os.environ:
				os.environ['_ORIGINAL_OPENAI_API_KEY'] = os.environ['OPENAI_API_KEY']

		os.environ['OPENAI_API_KEY'] = api_key

	base_url_raw = selection.get('base_url')
	base_url_provided = isinstance(base_url_raw, str) and base_url_raw.strip() != ''
	if not base_url_provided:
		base_url_raw = os.getenv(base_url_env, '') if base_url_env else ''

	base_url = _normalize_base_url(provider, base_url_raw, explicit=base_url_provided)

	# Avoid picking up leftover base_urls from other providers
	if not base_url and not base_url_provided:
		base_url = meta.get('default_base_url') or ''

	if base_url:
		os.environ['OPENAI_BASE_URL'] = base_url
	else:
		os.environ.pop('OPENAI_BASE_URL', None)

	# DEFAULT_LLM expects a provider prefix; convert model id to underscore form
	safe_model = model.replace('-', '_')
	os.environ['DEFAULT_LLM'] = f'{provider}_{safe_model}'

	return {'provider': provider, 'model': model, 'base_url': base_url}


def update_override(selection: dict[str, str] | None) -> dict[str, str]:
	"""Set in-memory override and apply immediately."""

	global _OVERRIDE_SELECTION
	_OVERRIDE_SELECTION = selection or None
	return apply_model_selection(override=_OVERRIDE_SELECTION or None)
