from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload

import httpx
from pydantic import BaseModel

from browser_use.llm.base import BaseChatModel
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import AssistantMessage, BaseMessage, SystemMessage, UserMessage
from browser_use.llm.views import ChatInvokeCompletion

T = TypeVar('T', bound=BaseModel)

VerifiedGeminiModels = Literal[
	'gemini-2.5-flash-lite',
	'gemini-3-pro-preview',
]


@dataclass
class ChatGoogle(BaseChatModel):
	"""Google Gemini chat wrapper."""

	model: VerifiedGeminiModels | str
	temperature: float | None = 0.2
	top_p: float | None = None
	max_output_tokens: int | None = 4096
	api_key: str | None = None
	base_url: str = 'https://generativelanguage.googleapis.com/v1beta'
	timeout: float | httpx.Timeout | None = None
	max_retries: int = 5
	http_client: httpx.AsyncClient | None = None

	_async_client: httpx.AsyncClient = field(init=False, repr=False)

	def __post_init__(self) -> None:
		google_api_key = self.api_key or os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
		if not google_api_key:
			raise ModelProviderError(
				message='Google API key not provided',
				status_code=401,
				model=str(self.model),
			)
		self.api_key = google_api_key
		self._async_client = self.http_client or httpx.AsyncClient(timeout=self.timeout)

	@property
	def provider(self) -> str:
		return 'google'

	@property
	def name(self) -> str:
		return str(self.model)

	def _prepare_messages(self, messages: list[BaseMessage]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
		gemini_messages = []
		system_instruction = None

		for msg in messages:
			if isinstance(msg, SystemMessage):
				# Gemini API handles system instructions separately
				# Usually there is only one system message, but if multiple, we concatenate?
				# The snippet shows {"systemInstruction": {"parts": [...]}}
				if system_instruction is None:
					system_instruction = {'role': 'system', 'parts': [{'text': msg.content}]}
				else:
					# Append to existing parts
					system_instruction['parts'].append({'text': msg.content})
				continue

			if isinstance(msg, UserMessage):
				role = 'user'
			elif isinstance(msg, AssistantMessage):
				role = 'model'
			else:
				continue

			content = []
			if isinstance(msg.content, str):
				content.append({'text': msg.content})
			elif isinstance(msg.content, list):
				for item in msg.content:
					if isinstance(item, str):
						content.append({'text': item})
					elif isinstance(item, dict):
						if item.get('type') == 'text':
							content.append({'text': item.get('text', '')})
						elif item.get('type') == 'image_url':
							# Assuming image_url is a dict with 'url' key
							# and url is a base64 encoded image
							image_data = item.get('image_url', {}).get('url', '')
							if 'base64,' in image_data:
								mime_type = image_data.split(';')[0].split(':')[1]
								data = image_data.split('base64,')[1]
								content.append({'inline_data': {'mime_type': mime_type, 'data': data}})

			gemini_messages.append({'role': role, 'parts': content})

		# If we have a system_instruction, it should be returned separately
		# Note: The API expects system_instruction to be a dict like {role: "system", parts: [...]} not inside contents
		# Wait, the official REST API doc says: "systemInstruction": { "parts": [...] } (role is optional or ignored? Snippet said "role": "string" in request body param list but "system" is not a standard role in contents).
		# Let's trust the snippet: "systemInstruction": { "role": string, "parts": [...] }

		# If system_instruction role is needed, 'system' seems appropriate or 'user' if forcing it?
		# Docs usually imply it's separate. We'll leave 'role': 'system' inside the object if we constructed it that way,
		# but strictly speaking it might just need 'parts'.
		# However, `systemInstruction` is a top-level field.

		return gemini_messages, system_instruction

	async def _send_request(
		self,
		gemini_messages: list[dict[str, Any]],
		generation_config: dict[str, Any],
		system_instruction: dict[str, Any] | None = None
	) -> httpx.Response:
		url = f'{self.base_url}/models/{self.model}:generateContent'
		headers = {
			'Content-Type': 'application/json',
		}
		json_payload = {
			'contents': gemini_messages,
			'generationConfig': generation_config,
		}

		if system_instruction:
			# Ensure role is not user/model if strictly systemInstruction?
			# Actually, the snippet showed: "systemInstruction": { "role": string, ... }
			# We'll include it as is.
			json_payload['systemInstruction'] = system_instruction

		params = {'key': self.api_key}

		for attempt in range(self.max_retries):
			try:
				response = await self._async_client.post(url, headers=headers, json=json_payload, params=params)
				response.raise_for_status()
				return response
			except httpx.HTTPStatusError as e:
				if e.response.status_code >= 500 and attempt < self.max_retries - 1:
					continue
				raise ModelProviderError(
					message=e.response.text,
					status_code=e.response.status_code,
					model=self.name,
				) from e

		raise ModelProviderError(f'Failed to get response after {self.max_retries} retries', model=self.name)

	@overload
	async def ainvoke(self, messages: list[BaseMessage], output_format: None = None) -> ChatInvokeCompletion[str]: ...

	@overload
	async def ainvoke(self, messages: list[BaseMessage], output_format: type[T]) -> ChatInvokeCompletion[T]: ...

	async def ainvoke(
		self, messages: list[BaseMessage], output_format: type[T] | None = None
	) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
		gemini_messages, system_instruction = self._prepare_messages(messages)

		generation_config = {
			'temperature': self.temperature,
			'topP': self.top_p,
			'maxOutputTokens': self.max_output_tokens,
		}
		if output_format:
			generation_config['response_mime_type'] = 'application/json'

		response = await self._send_request(gemini_messages, generation_config, system_instruction)

		response_data = response.json()

		try:
			content_text = response_data['candidates'][0]['content']['parts'][0]['text']
		except (KeyError, IndexError):
			# Better error handling for blocked content
			if response_data.get('promptFeedback', {}).get('blockReason'):
				block_reason = response_data['promptFeedback']['blockReason']
				raise ModelProviderError(f'Response blocked: {block_reason}', model=self.name)

			raise ModelProviderError('Invalid response structure from Gemini API', model=self.name)

		if output_format:
			try:
				parsed_content = self._parse_json_output(content_text, output_format)
				return ChatInvokeCompletion(completion=parsed_content, usage=None)
			except (json.JSONDecodeError, ValueError) as e:
				raise ModelProviderError(f'Failed to parse model output as JSON: {e}', model=self.name) from e
		else:
			return ChatInvokeCompletion(completion=content_text, usage=None)

	async def aclose(self) -> None:
		"""Close the underlying HTTP client."""
		if hasattr(self, '_async_client') and not self._async_client.is_closed:
			try:
				await self._async_client.aclose()
			except RuntimeError as e:
				# Ignore "Event loop is closed" error during cleanup
				if 'Event loop is closed' not in str(e):
					raise

	def _parse_json_output(self, text: str, output_format: type[T]) -> T:
		raw_text = text.strip()

		def _extract_json_candidate(blob: str) -> str | None:
			"""Pull a JSON object out of a mixed Gemini response."""
			# Prefer fenced code blocks if present
			fence_match = re.search(r'```(?:json)?\\s*([\\s\\S]*?)```', blob)
			if fence_match:
				return fence_match.group(1).strip()

			# Otherwise grab the first JSON-looking object
			brace_match = re.search(r'\\{[\\s\\S]*\\}', blob)
			if brace_match:
				return brace_match.group(0).strip()

			return None

		candidates: list[str] = [raw_text]
		extracted = _extract_json_candidate(raw_text)
		if extracted and extracted not in candidates:
			candidates.append(extracted)

		for candidate in candidates:
			try:
				return output_format.model_validate_json(candidate)
			except (ValueError, json.JSONDecodeError):
				try:
					return output_format.model_validate(json.loads(candidate))
				except Exception:
					continue

		raise ValueError(f'Failed to decode JSON from model output: {text}')
