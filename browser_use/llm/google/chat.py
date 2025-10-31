from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload

import httpx
from openai import AsyncOpenAI
from openai.types.shared.chat_model import ChatModel
from openai.types.shared_params.reasoning_effort import ReasoningEffort
from pydantic import BaseModel

from browser_use.llm.base import BaseChatModel
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.views import ChatInvokeCompletion

T = TypeVar('T', bound=BaseModel)


VerifiedGeminiModels = Literal[
        'gemini-2.0-flash',
        'gemini-2.0-flash-exp',
        'gemini-2.0-flash-lite-preview-02-05',
        'Gemini-2.0-exp',
        'gemma-3-27b-it',
        'gemma-3-4b',
        'gemma-3-12b',
        'gemma-3n-e2b',
        'gemma-3n-e4b',
]


@dataclass
class ChatGoogle(BaseChatModel):
        """Google Gemini chat wrapper built on the OpenAI Python SDK."""

        model: VerifiedGeminiModels | ChatModel | str
        temperature: float | None = 0.2
        frequency_penalty: float | None = 0.3
        reasoning_effort: ReasoningEffort = 'low'
        seed: int | None = None
        service_tier: Literal['auto', 'default', 'flex', 'priority', 'scale'] | None = None
        top_p: float | None = None
        add_schema_to_system_prompt: bool = False
        max_output_tokens: int | None = 4096

        # Compatibility fields retained for API parity with the legacy client
        config: Mapping[str, Any] | None = None
        include_system_in_user: bool = False
        supports_structured_output: bool = True
        thinking_budget: int | None = None
        vertexai: bool | None = None
        credentials: Any | None = None
        project: str | None = None
        location: str | None = None
        http_options: Mapping[str, Any] | None = None

        # Client configuration
        api_key: str | None = None
        base_url: str | httpx.URL | None = 'https://generativelanguage.googleapis.com/v1beta/openai'
        default_headers: Mapping[str, str] | None = None
        default_query: Mapping[str, object] | None = None
        timeout: float | httpx.Timeout | None = None
        max_retries: int = 5
        http_client: httpx.AsyncClient | None = None

        _delegate: ChatOpenAI = field(init=False, repr=False)

        def __post_init__(self) -> None:
                google_api_key = self.api_key or os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
                if not google_api_key:
                        raise ModelProviderError(
                                message='Google API key not provided',
                                status_code=401,
                                model=str(self.model),
                        )

                headers = dict(self.default_headers or {})
                headers.setdefault('x-goog-api-key', google_api_key)

                query = dict(self.default_query or {})
                query.setdefault('key', google_api_key)

                base_url = self.base_url or 'https://generativelanguage.googleapis.com/v1beta/openai'

                self.api_key = google_api_key
                self._delegate = ChatOpenAI(
                        model=self.model,
                        temperature=self.temperature,
                        frequency_penalty=self.frequency_penalty,
                        reasoning_effort=self.reasoning_effort,
                        seed=self.seed,
                        service_tier=self.service_tier,
                        top_p=self.top_p,
                        add_schema_to_system_prompt=self.add_schema_to_system_prompt,
                        api_key=google_api_key,
                        base_url=base_url,
                        timeout=self.timeout,
                        max_retries=self.max_retries,
                        default_headers=headers,
                        default_query=query,
                        http_client=self.http_client,
                        max_completion_tokens=self.max_output_tokens,
                )

        @property
        def provider(self) -> str:
                return 'google'

        def get_client(self) -> AsyncOpenAI:
                return self._delegate.get_client()

        @property
        def name(self) -> str:
                return str(self.model)

        @overload
        async def ainvoke(self, messages: list[BaseMessage], output_format: None = None) -> ChatInvokeCompletion[str]: ...

        @overload
        async def ainvoke(self, messages: list[BaseMessage], output_format: type[T]) -> ChatInvokeCompletion[T]: ...

        async def ainvoke(
                self, messages: list[BaseMessage], output_format: type[T] | None = None
        ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
                return await self._delegate.ainvoke(messages, output_format=output_format)
