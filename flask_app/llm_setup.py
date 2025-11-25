from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import logger
from .env_utils import _get_env_trimmed
from .exceptions import AgentControllerError

# Correctly handle imports, including adding the project root to sys.path if necessary
try:
	from browser_use.llm.anthropic.chat import ChatAnthropic
	from browser_use.llm.base import BaseChatModel
	from browser_use.llm.google.chat import ChatGoogle
	from browser_use.llm.groq.chat import ChatGroq
	from browser_use.llm.openai.chat import ChatOpenAI
	from browser_use.model_selection import PROVIDER_DEFAULTS, apply_model_selection, update_override
except ModuleNotFoundError:
	import sys

	ROOT_DIR = Path(__file__).resolve().parents[1]
	if str(ROOT_DIR) not in sys.path:
		sys.path.insert(0, str(ROOT_DIR))
	from browser_use.llm.anthropic.chat import ChatAnthropic
	from browser_use.llm.base import BaseChatModel
	from browser_use.llm.google.chat import ChatGoogle
	from browser_use.llm.groq.chat import ChatGroq
	from browser_use.llm.openai.chat import ChatOpenAI
	from browser_use.model_selection import PROVIDER_DEFAULTS, apply_model_selection, update_override


def _create_selected_llm(selection_override: dict | None = None) -> BaseChatModel:
	"""Create an LLM instance based on the selected provider and model."""

	# Apply model selection to get the correct provider, model, and configuration
	applied = update_override(selection_override) if selection_override else apply_model_selection('browser')
	provider = applied.get('provider', 'openai')
	model = applied.get('model')
	base_url = applied.get('base_url')

	if not model:
		raise AgentControllerError('モデル名が設定されていません。設定モーダルから再保存してください。')

	# Get provider-specific settings from the defaults map
	provider_config = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS['openai'])
	api_key_env = provider_config.get('api_key_env', 'OPENAI_API_KEY')
	api_key = _get_env_trimmed(api_key_env)

	if not api_key:
		raise AgentControllerError(f'{api_key_env} が設定されていません。ブラウザエージェントの secrets.env を確認してください。')

	llm_kwargs: dict[str, Any] = {'model': model, 'api_key': api_key}
	if base_url:
		llm_kwargs['base_url'] = base_url

	# Instantiate the correct client based on the provider
	if provider == 'gemini':
		logger.info(f'Using Google (Gemini) model: {model}')
		return ChatGoogle(**llm_kwargs)
	if provider == 'claude':
		logger.info(f'Using Anthropic (Claude) model: {model}')
		return ChatAnthropic(**llm_kwargs)
	if provider == 'groq':
		logger.info(f'Using Groq model: {model}')
		return ChatGroq(**llm_kwargs)

	# Default to OpenAI for any other case, including 'openai' provider
	logger.info(f'Using OpenAI model: {model} with base_url: {base_url}')
	return ChatOpenAI(**llm_kwargs)
