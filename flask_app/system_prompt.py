from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import logger

_LANGUAGE_EXTENSION = (
    '### 追加の言語ガイドライン\n'
    '- すべての思考過程、行動の評価、メモリ、次の目標、最終報告などの文章は必ず自然な日本語で記述してください。\n'
    '- 成功や失敗などのステータスも日本語（例: 成功、失敗、未確定）で明示してください。\n'
    '- Webページ上の固有名詞や引用、ユーザーに提示する必要がある原文テキストは、そのままの言語で保持しても問題ありません。\n'
    '- GoogleやDuckDuckGoなどの検索エンジンは使用しないでください。yahoo.co.jpを基本的には使用してください。\n'
)

_SYSTEM_PROMPT_FILENAME = 'system_prompt_browser_agent.md'
_CUSTOM_SYSTEM_PROMPT_TEMPLATE: str | None = None
_DEFAULT_MAX_ACTIONS_PER_STEP = 10


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
