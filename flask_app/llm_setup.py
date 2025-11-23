from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import logger
from .env_utils import _get_env_trimmed
from .exceptions import AgentControllerError
from browser_use.model_selection import apply_model_selection, update_override

try:
    from browser_use.llm.openai.chat import ChatOpenAI
except ModuleNotFoundError:
    import sys

    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from browser_use.llm.openai.chat import ChatOpenAI


def _create_selected_llm(selection_override: dict | None = None) -> ChatOpenAI:
    """Create an OpenAI-compatible LLM using the selected provider/model."""

    applied = update_override(selection_override) if selection_override else apply_model_selection("browser")
    api_key = _get_env_trimmed('OPENAI_API_KEY')
    if not api_key:
        raise AgentControllerError('OPENAI_API_KEY が設定されていません。ブラウザエージェントの secrets.env を確認してください。')

    model = applied.get('model') or _get_env_trimmed('DEFAULT_LLM') or ''
    base_url = applied.get('base_url') or _get_env_trimmed('OPENAI_BASE_URL') or None
    if not model:
        raise AgentControllerError('モデル名が設定されていません。設定モーダルから再保存してください。')

    llm_kwargs: dict[str, Any] = {'model': model, 'api_key': api_key}
    if base_url:
        llm_kwargs['base_url'] = base_url

    return ChatOpenAI(**llm_kwargs)
