"""Utility helpers for collaborating with external specialised agents.

This module centralises the configuration for the non-browser agents that the
demo Flask application can contact.  Each agent entry documents its
responsibility and default connection endpoints so that the behaviour mirrors
the Multi-Agent-Platform reference implementation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal, Mapping, MutableMapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

ExternalAgentKey = Literal["faq", "iot"]


class ExternalAgentError(RuntimeError):
    """Raised when a request to an external specialised agent fails."""


@dataclass(frozen=True)
class ExternalAgentConfig:
    """Connection information and role description for a helper agent."""

    key: ExternalAgentKey
    display_name: str
    description: str
    base_urls: tuple[str, ...]
    timeout: float
    chat_path: str

    def iter_base_urls(self) -> Iterable[str]:
        """Yield normalised base URLs in priority order."""

        for base in self.base_urls:
            cleaned = base.strip().rstrip("/")
            if cleaned:
                yield cleaned

    def build_url(self, base: str) -> str:
        """Return the absolute endpoint URL for the chat-style interaction."""

        path = self.chat_path if self.chat_path.startswith("/") else f"/{self.chat_path}"
        return f"{base}{path}"


def _parse_url_list(env_name: str, *defaults: str) -> tuple[str, ...]:
    """Parse a comma-separated environment variable into a tuple of URLs."""

    raw_value = os.environ.get(env_name)
    if raw_value:
        entries = [item.strip() for item in raw_value.split(",") if item.strip()]
        if entries:
            return tuple(entries)
    return tuple(defaults)


_FAQ_AGENT = ExternalAgentConfig(
    key="faq",
    display_name="FAQエージェント",
    description=(
        "家庭内の疑問や家電トラブルに関するナレッジベースを検索し、"
        "FAQ_Gemini バックエンドを通じて迅速に回答する専門エージェントです。"
        "ブラウザ操作ではなく既知のQ&Aからの回答が適している場合に利用します。"
    ),
    base_urls=_parse_url_list(
        "FAQ_AGENT_API_BASE",
        "http://faq_gemini:5000",
        "http://localhost:5000",
    ),
    timeout=float(os.environ.get("FAQ_AGENT_TIMEOUT", "30")),
    chat_path="/agent_rag_answer",
)


_IOT_AGENT = ExternalAgentConfig(
    key="iot",
    display_name="IoTエージェント",
    description=(
        "家庭のスマートデバイスの状態確認や操作を担当するエージェントです。"
        "IoT-Agent に委譲して、照明・エアコンなどの機器制御やセンサー状況の報告を行います。"
    ),
    base_urls=_parse_url_list(
        "IOT_AGENT_API_BASE",
        "https://iot-agent.project-kk.com",
    ),
    timeout=float(os.environ.get("IOT_AGENT_TIMEOUT", "30")),
    chat_path="/api/agents/respond",
)


EXTERNAL_AGENTS: dict[ExternalAgentKey, ExternalAgentConfig] = {
    "faq": _FAQ_AGENT,
    "iot": _IOT_AGENT,
}

_AGENT_ALIASES: dict[str, ExternalAgentKey] = {
    "faq": "faq",
    "faq_gemini": "faq",
    "knowledge": "faq",
    "docs": "faq",
    "iot": "iot",
    "device": "iot",
    "smart_home": "iot",
}


FAQ_KEYWORDS = (
    "faq",
    "レシピ",
    "掃除",
    "洗濯",
    "家事",
    "使い方",
    "理由",
    "how",
    "why",
)

IOT_KEYWORDS = (
    "iot",
    "デバイス",
    "家電",
    "電源",
    "照明",
    "エアコン",
    "温度",
    "スマート",
    "センサー",
)


def describe_external_agents() -> list[dict[str, Any]]:
    """Return serialisable metadata describing the configured agents."""

    metadata: list[dict[str, Any]] = []
    for agent in EXTERNAL_AGENTS.values():
        metadata.append(
            {
                "key": agent.key,
                "display_name": agent.display_name,
                "description": agent.description,
                "base_urls": list(agent.iter_base_urls()),
                "timeout": agent.timeout,
                "chat_path": agent.chat_path,
            }
        )
    return metadata


def resolve_agent_key(raw_value: str | None) -> ExternalAgentKey | None:
    """Resolve a user-provided agent identifier (supports aliases)."""

    if not raw_value:
        return None
    lowered = raw_value.strip().lower()
    if not lowered:
        return None
    if lowered in EXTERNAL_AGENTS:
        return lowered  # type: ignore[return-value]
    return _AGENT_ALIASES.get(lowered)


def select_agent_for_prompt(
    prompt: str,
    requested_key: str | None = None,
) -> ExternalAgentConfig:
    """Pick the most suitable agent for the given request text."""

    explicit = resolve_agent_key(requested_key)
    if explicit:
        return EXTERNAL_AGENTS[explicit]

    lowered = prompt.lower()
    if any(keyword in lowered for keyword in IOT_KEYWORDS):
        return _IOT_AGENT
    if any(keyword in lowered for keyword in FAQ_KEYWORDS):
        return _FAQ_AGENT

    # Default to FAQ agent when no clear keyword is present to provide guidance.
    return _FAQ_AGENT


def _post_json(agent: ExternalAgentConfig, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Send a JSON POST request to the agent and return the parsed body."""

    body_bytes: bytes
    last_error: ExternalAgentError | None = None

    for base in agent.iter_base_urls():
        url = agent.build_url(base)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=agent.timeout) as response:
                body_bytes = response.read()
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            error_body = exc.read()
            message = _decode_error_message(error_body) or f"HTTP {exc.code} {exc.reason}"
            last_error = ExternalAgentError(f"{agent.display_name} からエラー応答: {message}")
            logger.debug("Agent %s returned HTTP error %s", agent.key, message, exc_info=True)
            continue
        except (URLError, TimeoutError, OSError) as exc:  # pragma: no cover - network failures
            last_error = ExternalAgentError(
                f"{agent.display_name} への接続に失敗しました: {exc}"
            )
            logger.debug("Agent %s connection failure", agent.key, exc_info=True)
            continue

        if not 200 <= status < 300:
            message = _decode_error_message(body_bytes) or f"HTTP {status}"
            last_error = ExternalAgentError(f"{agent.display_name} からエラー応答: {message}")
            logger.debug("Agent %s returned non-success status %s", agent.key, status)
            continue

        try:
            decoded = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = ExternalAgentError(
                f"{agent.display_name} から無効なJSONレスポンスを受信しました: {exc}"
            )
            logger.debug("Agent %s response decoding failed", agent.key, exc_info=True)
            continue

        if isinstance(decoded, MutableMapping):
            return dict(decoded)

        last_error = ExternalAgentError(
            f"{agent.display_name} から想定外のレスポンス形式が返されました。"
        )
        logger.debug("Agent %s response had unexpected structure", agent.key)

    raise last_error or ExternalAgentError(
        f"{agent.display_name} に問い合わせできませんでした。接続設定を確認してください。"
    )


def _decode_error_message(raw_body: bytes | None) -> str | None:
    """Extract a human-readable error message from a response body."""

    if not raw_body:
        return None
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text.strip() or None

    if isinstance(parsed, Mapping):
        message = parsed.get("error")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return text.strip() or None


def call_external_agent(
    agent: ExternalAgentConfig,
    prompt: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    context: str | None = None,
    include_snapshot: bool = False,
) -> Dict[str, Any]:
    """Send a question to the given helper agent and return its response."""

    if agent.key == "faq":
        payload: Dict[str, Any] = {"question": prompt}
        if metadata:
            payload["metadata"] = dict(metadata)
        if context:
            payload["context"] = context
    elif agent.key == "iot":
        payload = {"request": prompt}
        if metadata:
            payload["metadata"] = dict(metadata)
        if context:
            payload["context"] = context
        if include_snapshot:
            payload["include_device_snapshot"] = True
    else:  # pragma: no cover - defensive fallback for future agents
        payload = {"prompt": prompt}

    return _post_json(agent, payload)


__all__ = [
    "ExternalAgentConfig",
    "ExternalAgentError",
    "ExternalAgentKey",
    "call_external_agent",
    "describe_external_agents",
    "select_agent_for_prompt",
    "resolve_agent_key",
]

