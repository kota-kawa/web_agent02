import json
import logging
from dataclasses import dataclass, field
from typing import Literal, TypeVar, overload

from groq import (
	APIError,
	APIResponseValidationError,
	APIStatusError,
	AsyncGroq,
	NotGiven,
	RateLimitError,
	Timeout,
)
from groq.types.chat import ChatCompletion, ChatCompletionToolChoiceOptionParam, ChatCompletionToolParam
from httpx import URL
from pydantic import BaseModel

from browser_use.llm.base import BaseChatModel, ChatInvokeCompletion
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
from browser_use.llm.groq.parser import try_parse_groq_failed_generation
from browser_use.llm.groq.serializer import GroqMessageSerializer
from browser_use.llm.messages import BaseMessage, UserMessage
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeUsage

GroqVerifiedModels = Literal[
	'llama-3.3-70b-versatile',
	'llama-3.1-8b-instant',
	'openai/gpt-oss-20b',
	'qwen/qwen3-32b',
]

JsonSchemaModels = [
	'llama-3.3-70b-versatile',
	'llama-3.1-8b-instant',
	'openai/gpt-oss-20b',
	'qwen/qwen3-32b',
]

ToolCallingModels = []

T = TypeVar('T', bound=BaseModel)

logger = logging.getLogger(__name__)


@dataclass
class ChatGroq(BaseChatModel):
	"""
	A wrapper around AsyncGroq that implements the BaseLLM protocol.
	"""

	# Model configuration
	model: GroqVerifiedModels | str

	# Model params
	temperature: float | None = None
	service_tier: Literal['auto', 'on_demand', 'flex'] | None = None
	top_p: float | None = None
	seed: int | None = None

	# Client initialization parameters
	api_key: str | None = None
	base_url: str | URL | None = None
	timeout: float | Timeout | NotGiven | None = None
	max_retries: int = 10

	_async_client: AsyncGroq = field(init=False, repr=False)

	def __post_init__(self) -> None:
		# The Groq SDK automatically appends '/openai/v1', so we remove it from the base_url if present
		client_base_url = self.base_url
		if isinstance(client_base_url, str) and client_base_url.endswith('/openai/v1'):
			client_base_url = client_base_url.removesuffix('/openai/v1')
		elif isinstance(client_base_url, URL) and client_base_url.path.endswith('/openai/v1'):
			client_base_url = client_base_url.copy_with(path=client_base_url.path.removesuffix('/openai/v1'))

		self._async_client = AsyncGroq(
			api_key=self.api_key, base_url=client_base_url, timeout=self.timeout, max_retries=self.max_retries
		)

	def get_client(self) -> AsyncGroq:
		return self._async_client

	@property
	def provider(self) -> str:
		return 'groq'

	@property
	def name(self) -> str:
		return str(self.model)

	def _is_vision_model(self) -> bool:
		"""Check if the current model supports vision."""
		# User instruction: "In Groq-type models, vision is completely unsupported."
		# Therefore, always return False.
		return False

	def _filter_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
		"""Filter out image content for non-vision models."""
		if self._is_vision_model():
			return messages

		filtered_messages = []
		for msg in messages:
			if isinstance(msg, UserMessage) and isinstance(msg.content, list):
				# Filter content parts
				new_content = []
				for part in msg.content:
					if part.type == 'text':
						new_content.append(part)
					elif part.type == 'image_url':
						logger.warning(f'Removing image from message for non-vision model {self.model}')
						# We simply drop the image part.

				# Create a new message with filtered content
				# We use model_copy to preserve other attributes like name, but replace content
				new_msg = msg.model_copy(update={'content': new_content})
				filtered_messages.append(new_msg)
			else:
				filtered_messages.append(msg)

		return filtered_messages

	def _get_usage(self, response: ChatCompletion) -> ChatInvokeUsage | None:
		usage = (
			ChatInvokeUsage(
				prompt_tokens=response.usage.prompt_tokens,
				completion_tokens=response.usage.completion_tokens,
				total_tokens=response.usage.total_tokens,
				prompt_cached_tokens=None,  # Groq doesn't support cached tokens
				prompt_cache_creation_tokens=None,
				prompt_image_tokens=None,
			)
			if response.usage is not None
			else None
		)
		return usage

	@overload
	async def ainvoke(self, messages: list[BaseMessage], output_format: None = None) -> ChatInvokeCompletion[str]: ...

	@overload
	async def ainvoke(self, messages: list[BaseMessage], output_format: type[T]) -> ChatInvokeCompletion[T]: ...

	async def ainvoke(
		self, messages: list[BaseMessage], output_format: type[T] | None = None
	) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
		# Filter messages based on model capabilities
		messages = self._filter_messages(messages)

		groq_messages = GroqMessageSerializer.serialize_messages(messages)

		try:
			if output_format is None:
				return await self._invoke_regular_completion(groq_messages)
			else:
				return await self._invoke_structured_output(groq_messages, output_format)

		except RateLimitError as e:
			raise ModelRateLimitError(message=e.response.text, status_code=e.response.status_code, model=self.name) from e

		except APIResponseValidationError as e:
			raise ModelProviderError(message=e.response.text, status_code=e.response.status_code, model=self.name) from e

		except APIStatusError as e:
			if output_format is None:
				raise ModelProviderError(message=e.response.text, status_code=e.response.status_code, model=self.name) from e
			else:
				try:
					logger.debug(f'Groq failed generation: {e.response.text}; fallback to manual parsing')

					parsed_response = try_parse_groq_failed_generation(e, output_format)

					logger.debug('Manual error parsing successful ✅')

					return ChatInvokeCompletion(
						completion=parsed_response,
						usage=None,  # because this is a hacky way to get the outputs
						# TODO: @groq needs to fix their parsers and validators
					)
				except Exception as _:
					raise ModelProviderError(message=str(e), status_code=e.response.status_code, model=self.name) from e

		except APIError as e:
			raise ModelProviderError(message=e.message, model=self.name) from e
		except Exception as e:
			raise ModelProviderError(message=str(e), model=self.name) from e

	async def _invoke_regular_completion(self, groq_messages) -> ChatInvokeCompletion[str]:
		"""Handle regular completion without structured output."""
		chat_completion = await self.get_client().chat.completions.create(
			messages=groq_messages,
			model=self.model,
			service_tier=self.service_tier,
			temperature=self.temperature,
			top_p=self.top_p,
			seed=self.seed,
		)
		usage = self._get_usage(chat_completion)
		return ChatInvokeCompletion(
			completion=chat_completion.choices[0].message.content or '',
			usage=usage,
		)

	async def _invoke_structured_output(self, groq_messages, output_format: type[T]) -> ChatInvokeCompletion[T]:
		"""Handle structured output using either tool calling or JSON schema."""
		try:
			if self.model in ToolCallingModels:
				schema = SchemaOptimizer.create_optimized_json_schema(output_format)
				response = await self._invoke_with_tool_calling(groq_messages, output_format, schema)
				response_text = response.choices[0].message.tool_calls[0].function.arguments
			else:
				response = await self.get_client().chat.completions.create(
					model=self.model,
					messages=groq_messages,
					temperature=self.temperature,
					top_p=self.top_p,
					seed=self.seed,
					response_format={'type': 'json_object'},
					service_tier=self.service_tier,
				)
				response_text = response.choices[0].message.content or ''

			if not response_text:
				raise ModelProviderError(
					message='No content in response',
					status_code=500,
					model=self.name,
				)

			parsed_response = output_format.model_validate_json(response_text)
			usage = self._get_usage(response)

			return ChatInvokeCompletion(
				completion=parsed_response,
				usage=usage,
			)
		except (APIStatusError, json.JSONDecodeError, ModelProviderError) as e:
			try:
				logger.debug(f'Groq failed generation: {e}; fallback to manual parsing')
				parsed_response = try_parse_groq_failed_generation(e, output_format)
				logger.debug('Manual error parsing successful ✅')
				return ChatInvokeCompletion(
					completion=parsed_response,
					usage=None,
				)
			except Exception as e:
				raise ModelProviderError(message=str(e), model=self.name) from e

	async def _invoke_with_tool_calling(self, groq_messages, output_format: type[T], schema) -> ChatCompletion:
		"""Handle structured output using tool calling."""
		tool = ChatCompletionToolParam(
			function={
				'name': output_format.__name__,
				'description': f'Extract information in the format of {output_format.__name__}',
				'parameters': schema,
			},
			type='function',
		)
		tool_choice: ChatCompletionToolChoiceOptionParam = 'required'

		return await self.get_client().chat.completions.create(
			model=self.model,
			messages=groq_messages,
			temperature=self.temperature,
			top_p=self.top_p,
			seed=self.seed,
			tools=[tool],
			tool_choice=tool_choice,
			service_tier=self.service_tier,
		)

	async def aclose(self) -> None:
		"""Close the underlying HTTP client."""
		if hasattr(self, '_async_client') and not self._async_client.is_closed:
			try:
				await self._async_client.aclose()
			except RuntimeError as e:
				# Ignore "Event loop is closed" error during cleanup
				if 'Event loop is closed' not in str(e):
					raise
