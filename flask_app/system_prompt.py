from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from browser_use.model_selection import _load_selection

from .config import logger

# List of models that are not multimodal and should not receive vision inputs
NON_MULTIMODAL_MODELS = [
	'claude-haiku-4-5',
	'claude-opus-4-5',
	'llama-3.3-70b-versatile',
	'llama-3.1-8b-instant',
	'openai/gpt-oss-20b',
	'qwen/qwen3-32b',
]
VISION_CAPABLE_PROVIDERS = {'claude', 'gemini', 'openai'}
_NON_MULTIMODAL_MODELS_LOWER = {model.lower() for model in NON_MULTIMODAL_MODELS}

_LANGUAGE_EXTENSION = (
	'### Additional Language Guidelines\n'
	'- All thought processes, action evaluations, memories, next goals, final reports, etc., must be written in natural Japanese.\n'
	'- Statuses such as success or failure must also be explicitly stated in Japanese (e.g., 成功, 失敗, 不明).\n'
	'- Proper nouns, quotes, or original text on web pages that need to be presented to the user may be kept in their original language.\n'
	'- Do not use search engines like Google or DuckDuckGo. Basically use yahoo.co.jp.\n'
)

_SYSTEM_PROMPT_FILENAME = 'system_prompt_browser_agent.md'
_CUSTOM_SYSTEM_PROMPT_TEMPLATE: str | None = None
_DEFAULT_MAX_ACTIONS_PER_STEP = 10


def _should_disable_vision(provider: str | None, model: str | None) -> bool:
	"""Return True when the selected model/provider should not receive vision inputs."""

	provider_normalized = (provider or '').strip().lower()
	model_normalized = (model or '').strip().lower()

	if provider_normalized == 'groq':
		return True

	if provider_normalized and provider_normalized not in VISION_CAPABLE_PROVIDERS:
		return True

	return model_normalized in _NON_MULTIMODAL_MODELS_LOWER


def _system_prompt_candidate_paths() -> tuple[Path, ...]:
	script_path = Path(__file__).resolve()
	# Only allow the prompt that lives alongside this module
	return (script_path.parent / _SYSTEM_PROMPT_FILENAME,)


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
		'Custom system prompt file %s not found next to flask_app; no other prompt sources will be used.',
		_system_prompt_candidate_paths()[0],
	)
	_CUSTOM_SYSTEM_PROMPT_TEMPLATE = ''
	return None


def _build_custom_system_prompt(
	max_actions_per_step: int = _DEFAULT_MAX_ACTIONS_PER_STEP,
	force_disable_vision: bool = False,
	provider: str | None = None,
	model: str | None = None,
) -> str | None:
	template = _load_custom_system_prompt_template()
	if not template:
		return None

	selection = _load_selection('browser')
	provider = provider or selection.get('provider', '')
	model = model or selection.get('model', '')

	vision_disabled = force_disable_vision or _should_disable_vision(provider, model)

	if vision_disabled:
		# Remove vision-related sections for non-multimodal models
		template = re.sub(r'<browser_vision>.*?</browser_vision>\n', '', template, flags=re.DOTALL)
		# Adjust reasoning rules to remove dependency on screenshots
		reasoning_rules_pattern = re.compile(r'(<reasoning_rules>.*?</reasoning_rules>)', re.DOTALL)
		template = reasoning_rules_pattern.sub(
			lambda m: m.group(1).replace(
				'Always verify using <browser_vision> (screenshot) as the primary ground truth. If a screenshot is unavailable, fall back to <browser_state>.',
				'Always verify the result of your actions using <browser_state> as the primary ground truth.',
			),
			template,
		)

	now = datetime.now().astimezone()
	current_datetime_line = now.strftime('%Y-%m-%d %H:%M %Z (UTC%z, %A)')
	# Avoid str.format() so literal braces in the template (e.g., action schemas) are preserved
	# without triggering KeyError for names like "go_to_url".
	template = template.replace('{max_actions}', str(max_actions_per_step))
	template = template.replace('{current_datetime}', current_datetime_line)
	return template
