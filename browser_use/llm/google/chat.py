from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, overload

import httpx
from pydantic import BaseModel, ValidationError

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
		system_instruction: dict[str, Any] | None = None,
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
			except (json.JSONDecodeError, ValueError, ValidationError) as e:
				# JSON parse error - attempt retry with corrective prompt
				retry_result = await self._retry_json_parse(
					gemini_messages, generation_config, system_instruction, content_text, output_format, e
				)
				if retry_result is not None:
					return ChatInvokeCompletion(completion=retry_result, usage=None)
				raise ModelProviderError(f'Failed to parse model output as JSON: {e}', model=self.name) from e
		else:
			return ChatInvokeCompletion(completion=content_text, usage=None)

	async def _retry_json_parse(
		self,
		original_messages: list[dict[str, Any]],
		generation_config: dict[str, Any],
		system_instruction: dict[str, Any] | None,
		failed_output: str,
		output_format: type[T],
		original_error: Exception,
		max_retries: int = 2,
	) -> T | None:
		"""Retry JSON parsing with corrective prompts when initial parse fails."""
		# Truncate long outputs for the correction prompt
		truncated_output = failed_output[:2000] + '...' if len(failed_output) > 2000 else failed_output

		correction_prompt = f"""あなたの前回の出力はJSONとして不正でした。以下のエラーが発生しました:
{str(original_error)[:500]}

前回の出力（一部）:
{truncated_output}

以下の点に注意して、正しいJSONを再出力してください：
1. 文字列内の改行は \\n でエスケープする
2. 文字列内のダブルクォートは \\" でエスケープする
3. 制御文字（タブ等）は適切にエスケープする
4. JSONの構文（カンマ、括弧の対応）を確認する

正しいJSON形式のみを出力してください。説明や追加のテキストは不要です。"""

		retry_messages = original_messages + [{'role': 'user', 'parts': [{'text': correction_prompt}]}]

		for attempt in range(max_retries):
			try:
				response = await self._send_request(retry_messages, generation_config, system_instruction)
				response_data = response.json()
				content_text = response_data['candidates'][0]['content']['parts'][0]['text']
				return self._parse_json_output(content_text, output_format)
			except (json.JSONDecodeError, ValueError, KeyError, IndexError):
				if attempt == max_retries - 1:
					return None
				continue
			except Exception:
				return None

		return None

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

		def _sanitize_json_string(s: str) -> str:
			"""Sanitize JSON string by escaping problematic characters."""
			# Fix common JSON issues in LLM output
			# 1. Replace unescaped newlines within string values
			# 2. Replace unescaped tabs
			# 3. Fix unescaped quotes in string values

			# First, try to fix control characters within JSON string values
			# This regex finds string values and escapes control chars within them
			def escape_control_chars(match: re.Match) -> str:
				content = match.group(1)
				# Escape unescaped control characters
				content = content.replace('\n', '\\n')
				content = content.replace('\r', '\\r')
				content = content.replace('\t', '\\t')
				# Handle unescaped backslashes that aren't part of escape sequences
				# Be careful not to double-escape already escaped sequences
				return f'"{content}"'

			# Try to fix strings with unescaped control characters
			# Match JSON strings (simplified pattern)
			try:
				# Pattern to find string values in JSON
				result = re.sub(
					r'"((?:[^"\\]|\\.)*)(?:\n|\r|\t)((?:[^"\\]|\\.)*)"',
					lambda m: f'"{m.group(1)}\\n{m.group(2)}"',
					s,
				)
				return result
			except Exception:
				return s

		def _extract_json_candidate(blob: str) -> str | None:
			"""Pull a JSON object out of a mixed Gemini response."""
			# Prefer fenced code blocks if present
			fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', blob)
			if fence_match:
				return fence_match.group(1).strip()

			# Otherwise grab the first JSON-looking object
			brace_match = re.search(r'\{[\s\S]*\}', blob)
			if brace_match:
				return brace_match.group(0).strip()

			return None

		# Build list of candidates to try
		candidates: list[str] = [raw_text]
		extracted = _extract_json_candidate(raw_text)
		if extracted and extracted not in candidates:
			candidates.append(extracted)

		# Also try sanitized versions
		sanitized_raw = _sanitize_json_string(raw_text)
		if sanitized_raw != raw_text and sanitized_raw not in candidates:
			candidates.append(sanitized_raw)

		if extracted:
			sanitized_extracted = _sanitize_json_string(extracted)
			if sanitized_extracted != extracted and sanitized_extracted not in candidates:
				candidates.append(sanitized_extracted)

		last_error: Exception | None = None
		for candidate in candidates:
			try:
				# If the candidate is JSON and missing required keys (e.g., LLM returned {"error": {...}}),
				# coerce it into a minimal AgentOutput shape with a safe done action so the agent can continue.
				try:
					obj = json.loads(candidate)
					if isinstance(obj, dict) and 'action' not in obj:
						error_msg = None
						if 'error' in obj:
							err_val = obj['error']
							if isinstance(err_val, dict):
								error_msg = err_val.get('message') or err_val.get('detail') or str(err_val)
							else:
								error_msg = str(err_val)
						elif 'message' in obj:
							error_msg = str(obj.get('message'))

						coerced = {
							'evaluation_previous_goal': obj.get('evaluation_previous_goal') or '',
							'memory': obj.get('memory') or '',
							'next_goal': obj.get('next_goal') or '',
							'current_status': obj.get('current_status') or '',
							'action': [
								{
									'done': {
										'text': error_msg or 'LLM returned an error payload; converted to done action',
										'success': False,
										'files_to_display': [],
									}
								}
							],
						}
						candidate = json.dumps(coerced)
				except Exception:
					pass

				return output_format.model_validate_json(candidate)
			except (ValueError, json.JSONDecodeError, ValidationError) as e:
				last_error = e
				try:
					return output_format.model_validate(json.loads(candidate))
				except Exception as e2:
					last_error = e2
					continue

		raise ValueError(f'Failed to decode JSON from model output: {text[:500]}... Error: {last_error}')
