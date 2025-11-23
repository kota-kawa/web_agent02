"""Load shared model selection from Multi-Agent-Platform/model_settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

DEFAULT_SELECTION = {"provider": "openai", "model": "gpt-4.1-2025-04-14"}

PROVIDER_DEFAULTS: Dict[str, Dict[str, str | None]] = {
    "openai": {"api_key_env": "OPENAI_API_KEY", "base_url_env": "OPENAI_BASE_URL", "default_base_url": None},
    "claude": {"api_key_env": "CLAUDE_API_KEY", "base_url_env": "CLAUDE_API_BASE", "default_base_url": "https://openrouter.ai/api/v1"},
    "gemini": {"api_key_env": "GEMINI_API_KEY", "base_url_env": "GEMINI_API_BASE", "default_base_url": "https://generativelanguage.googleapis.com/openai/v1"},
    "groq": {"api_key_env": "GROQ_API_KEY", "base_url_env": "GROQ_API_BASE", "default_base_url": "https://api.groq.com/openai/v1"},
}

_OVERRIDE_SELECTION: Dict[str, str] | None = None


def _load_selection(agent_key: str) -> Dict[str, str]:
    platform_path = Path(__file__).resolve().parents[2] / "Multi-Agent-Platform" / "model_settings.json"
    try:
        data = json.loads(platform_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_SELECTION)

    selection = data.get("selection") or data
    if not isinstance(selection, dict):
        return dict(DEFAULT_SELECTION)

    chosen = selection.get(agent_key)
    if not isinstance(chosen, dict):
        return dict(DEFAULT_SELECTION)

    provider = (chosen.get("provider") or DEFAULT_SELECTION["provider"]).strip()
    model = (chosen.get("model") or DEFAULT_SELECTION["model"]).strip()
    return {"provider": provider, "model": model}


def apply_model_selection(agent_key: str = "browser", override: Dict[str, str] | None = None) -> Dict[str, str]:
    """Set env vars DEFAULT_LLM/OPENAI_API_KEY/OPENAI_BASE_URL according to selection."""

    selection = override or _OVERRIDE_SELECTION or _load_selection(agent_key)
    provider = selection.get("provider") or DEFAULT_SELECTION["provider"]
    model = selection.get("model") or DEFAULT_SELECTION["model"]

    meta = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
    api_key_env = meta.get("api_key_env") or "OPENAI_API_KEY"
    base_url_env = meta.get("base_url_env") or ""

    api_key = os.getenv(api_key_env) or os.getenv(api_key_env.lower()) or os.getenv("OPENAI_API_KEY")
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    base_url = os.getenv(base_url_env, "") if base_url_env else ""
    if not base_url:
        base_url = meta.get("default_base_url") or ""
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url

    # DEFAULT_LLM expects a provider prefix; convert model id to underscore form
    safe_model = model.replace("-", "_")
    os.environ["DEFAULT_LLM"] = f"{provider}_{safe_model}"

    return {"provider": provider, "model": model, "base_url": base_url}


def update_override(selection: Dict[str, str] | None) -> Dict[str, str]:
    """Set in-memory override and apply immediately."""

    global _OVERRIDE_SELECTION
    _OVERRIDE_SELECTION = selection or None
    return apply_model_selection(override=_OVERRIDE_SELECTION or None)
