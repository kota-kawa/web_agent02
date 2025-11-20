from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import logger
from .env_utils import _get_env_trimmed
from .exceptions import AgentControllerError

try:
    from browser_use.llm.google.chat import ChatGoogle
except ModuleNotFoundError:
    import sys

    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from browser_use.llm.google.chat import ChatGoogle

_DEFAULT_GEMINI_MODEL = 'gemini-2.5-flash'


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
