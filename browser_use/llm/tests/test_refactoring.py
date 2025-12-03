from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import Message, TextBlock
from groq import APIStatusError
from pydantic import BaseModel

from browser_use.llm import ChatAnthropic, ChatGoogle, ChatGroq, ChatOpenAI
from browser_use.llm.messages import (
	AssistantMessage,
	BaseMessage,
	SystemMessage,
	UserMessage,
)


class CapitalResponse(BaseModel):
	"""Structured response for capital question"""

	country: str
	capital: str


class TestRefactoring:
	"""Test suite for the refactored chat models"""

	# Test Constants
	SYSTEM_MESSAGE = SystemMessage(content='You are a helpful assistant.')
	FRANCE_QUESTION = UserMessage(content='What is the capital of France? Answer in one word.')
	FRANCE_ANSWER = AssistantMessage(content='Paris')
	GERMANY_QUESTION = UserMessage(content='What is the capital of Germany? Answer in one word.')

	# Expected values
	EXPECTED_GERMANY_CAPITAL = 'berlin'
	EXPECTED_FRANCE_COUNTRY = 'france'
	EXPECTED_FRANCE_CAPITAL = 'paris'

	# Test messages for conversation
	CONVERSATION_MESSAGES: list[BaseMessage] = [
		SYSTEM_MESSAGE,
		FRANCE_QUESTION,
		FRANCE_ANSWER,
		GERMANY_QUESTION,
	]

	# Test messages for structured output
	STRUCTURED_MESSAGES: list[BaseMessage] = [UserMessage(content='What is the capital of France?')]

	@pytest.mark.asyncio
	@patch('browser_use.llm.openai.chat.ChatOpenAI.get_client')
	async def test_openai_ainvoke_normal(self, mock_get_client):
		"""Test normal text response from OpenAI"""
		mock_response = MagicMock()
		mock_response.choices = [MagicMock()]
		mock_response.choices[0].message.content = self.EXPECTED_GERMANY_CAPITAL
		mock_response.usage = None
		mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)

		chat = ChatOpenAI(model='gpt-4o-mini', temperature=0, api_key='test')
		response = await chat.ainvoke(self.CONVERSATION_MESSAGES)

		completion = response.completion

		assert isinstance(completion, str)
		assert self.EXPECTED_GERMANY_CAPITAL in completion.lower()

	@pytest.mark.asyncio
	@patch('browser_use.llm.openai.chat.ChatOpenAI.get_client')
	async def test_openai_ainvoke_structured(self, mock_get_client):
		"""Test structured output from OpenAI"""
		mock_response = MagicMock()
		mock_response.choices = [MagicMock()]
		mock_response.choices[
			0
		].message.content = f'{{"country": "{self.EXPECTED_FRANCE_COUNTRY}", "capital": "{self.EXPECTED_FRANCE_CAPITAL}"}}'
		mock_response.usage = None
		mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)

		chat = ChatOpenAI(model='gpt-4o-mini', temperature=0, api_key='test')
		response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)
		completion = response.completion

		assert isinstance(completion, CapitalResponse)
		assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
		assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL

	@pytest.mark.asyncio
	@patch('browser_use.llm.groq.chat.try_parse_groq_failed_generation')
	async def test_groq_ainvoke_structured_fallback(self, mock_parse_failed):
		"""Test structured output fallback from Groq"""
		mock_parse_failed.return_value = CapitalResponse(
			country=self.EXPECTED_FRANCE_COUNTRY, capital=self.EXPECTED_FRANCE_CAPITAL
		)

		with patch('browser_use.llm.groq.chat.ChatGroq.get_client') as mock_get_client:
			mock_get_client.return_value.chat.completions.create = AsyncMock(
				side_effect=APIStatusError('test', response=MagicMock(), body=None)
			)

			chat = ChatGroq(model='meta-llama/llama-4-maverick-17b-128e-instruct', temperature=0, api_key='test')
			response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)

			completion = response.completion

			assert isinstance(completion, CapitalResponse)
			assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
			assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL
			mock_parse_failed.assert_called_once()

	@pytest.mark.asyncio
	@patch('browser_use.llm.anthropic.chat.ChatAnthropic.get_client')
	async def test_anthropic_ainvoke_normal(self, mock_get_client):
		"""Test normal text response from Anthropic"""
		mock_response = MagicMock(spec=Message)
		mock_response.content = [MagicMock(spec=TextBlock)]
		mock_response.content[0].text = self.EXPECTED_GERMANY_CAPITAL
		mock_response.usage = MagicMock()
		mock_response.usage.input_tokens = 0
		mock_response.usage.output_tokens = 0
		mock_response.usage.cache_read_input_tokens = 0
		mock_response.usage.cache_creation_input_tokens = 0
		mock_get_client.return_value.messages.create = AsyncMock(return_value=mock_response)

		chat = ChatAnthropic(model='claude-3-5-haiku-latest', max_tokens=100, temperature=0, api_key='test')
		response = await chat.ainvoke(self.CONVERSATION_MESSAGES)
		completion = response.completion

		assert isinstance(completion, str)
		assert self.EXPECTED_GERMANY_CAPITAL in completion.lower()

	@pytest.mark.asyncio
	@patch('browser_use.llm.anthropic.chat.ChatAnthropic.get_client')
	async def test_anthropic_ainvoke_structured(self, mock_get_client):
		"""Test structured output from Anthropic"""
		mock_response = MagicMock(spec=Message)
		mock_response.content = [MagicMock()]
		mock_response.content[0].type = 'tool_use'
		mock_response.content[0].input = {'country': self.EXPECTED_FRANCE_COUNTRY, 'capital': self.EXPECTED_FRANCE_CAPITAL}
		mock_response.usage = MagicMock()
		mock_response.usage.input_tokens = 0
		mock_response.usage.output_tokens = 0
		mock_response.usage.cache_read_input_tokens = 0
		mock_response.usage.cache_creation_input_tokens = 0
		mock_get_client.return_value.messages.create = AsyncMock(return_value=mock_response)

		chat = ChatAnthropic(model='claude-3-5-haiku-latest', max_tokens=100, temperature=0, api_key='test')
		response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)
		completion = response.completion

		assert isinstance(completion, CapitalResponse)
		assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
		assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL

	@pytest.mark.asyncio
	@patch('browser_use.llm.google.chat.ChatGoogle._send_request', new_callable=AsyncMock)
	async def test_google_ainvoke_normal(self, mock_send_request):
		"""Test normal text response from Google Gemini"""
		mock_response = MagicMock()
		mock_response.json.return_value = {'candidates': [{'content': {'parts': [{'text': self.EXPECTED_GERMANY_CAPITAL}]}}]}
		mock_send_request.return_value = mock_response

		chat = ChatGoogle(model='gemini-2.0-flash', api_key='test', temperature=0)
		response = await chat.ainvoke(self.CONVERSATION_MESSAGES)
		completion = response.completion

		assert isinstance(completion, str)
		assert self.EXPECTED_GERMANY_CAPITAL in completion.lower()

	@pytest.mark.asyncio
	@patch('browser_use.llm.google.chat.ChatGoogle._send_request', new_callable=AsyncMock)
	async def test_google_ainvoke_structured(self, mock_send_request):
		"""Test structured output from Google Gemini"""
		mock_response = MagicMock()
		mock_response.json.return_value = {
			'candidates': [
				{
					'content': {
						'parts': [
							{
								'text': f'{{"country": "{self.EXPECTED_FRANCE_COUNTRY}", "capital": "{self.EXPECTED_FRANCE_CAPITAL}"}}'
							}
						]
					}
				}
			]
		}
		mock_send_request.return_value = mock_response

		chat = ChatGoogle(model='gemini-2.0-flash', api_key='test', temperature=0)
		response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)
		completion = response.completion

		assert isinstance(completion, CapitalResponse)
		assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
		assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL

	@pytest.mark.asyncio
	@patch('browser_use.llm.google.chat.ChatGoogle._send_request', new_callable=AsyncMock)
	async def test_google_structured_with_wrapped_json(self, mock_send_request):
		"""Gemini responses with preamble/code fences should still parse."""
		wrapped_text = (
			'thinking about the task first...\n'
			'```json\n'
			f'{{"country": "{self.EXPECTED_FRANCE_COUNTRY}", "capital": "{self.EXPECTED_FRANCE_CAPITAL}"}}\n'
			'```\n'
			'回答は上記です。'
		)
		mock_response = MagicMock()
		mock_response.json.return_value = {'candidates': [{'content': {'parts': [{'text': wrapped_text}]}}]}
		mock_send_request.return_value = mock_response

		chat = ChatGoogle(model='gemini-2.0-flash', api_key='test', temperature=0)
		response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)
		completion = response.completion

		assert isinstance(completion, CapitalResponse)
		assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
		assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL

	@pytest.mark.asyncio
	@patch('browser_use.llm.google.chat.ChatGoogle._send_request', new_callable=AsyncMock)
	async def test_google_structured_with_inline_json(self, mock_send_request):
		"""Gemini responses with trailing text should still parse."""
		inline_text = (
			'Here are the details you asked for:\n'
			f'{{"country": "{self.EXPECTED_FRANCE_COUNTRY}", "capital": "{self.EXPECTED_FRANCE_CAPITAL}"}}\n'
			'Let me know if you need more.'
		)
		mock_response = MagicMock()
		mock_response.json.return_value = {'candidates': [{'content': {'parts': [{'text': inline_text}]}}]}
		mock_send_request.return_value = mock_response

		chat = ChatGoogle(model='gemini-2.0-flash', api_key='test', temperature=0)
		response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)
		completion = response.completion

		assert isinstance(completion, CapitalResponse)
		assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
		assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL

	@pytest.mark.asyncio
	@patch('browser_use.llm.groq.chat.ChatGroq.get_client')
	async def test_groq_ainvoke_normal(self, mock_get_client):
		"""Test normal text response from Groq"""
		mock_response = MagicMock()
		mock_response.choices = [MagicMock()]
		mock_response.choices[0].message.content = self.EXPECTED_GERMANY_CAPITAL
		mock_response.usage = None
		mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)

		chat = ChatGroq(model='meta-llama/llama-4-maverick-17b-128e-instruct', temperature=0, api_key='test')
		response = await chat.ainvoke(self.CONVERSATION_MESSAGES)
		completion = response.completion

		assert isinstance(completion, str)
		assert self.EXPECTED_GERMANY_CAPITAL in completion.lower()

	@pytest.mark.asyncio
	@patch('browser_use.llm.groq.chat.ChatGroq.get_client')
	async def test_groq_ainvoke_structured(self, mock_get_client):
		"""Test structured output from Groq"""
		mock_response = MagicMock()
		mock_response.choices = [MagicMock()]
		mock_response.choices[
			0
		].message.content = f'{{"country": "{self.EXPECTED_FRANCE_COUNTRY}", "capital": "{self.EXPECTED_FRANCE_CAPITAL}"}}'
		mock_response.usage = None
		mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)

		chat = ChatGroq(model='meta-llama/llama-4-maverick-17b-128e-instruct', temperature=0, api_key='test')
		response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)

		completion = response.completion

		assert isinstance(completion, CapitalResponse)
		assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
		assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL

	@pytest.mark.asyncio
	@patch('browser_use.llm.groq.chat.ChatGroq.get_client')
	async def test_groq_ainvoke_structured_tool_calling(self, mock_get_client):
		"""Test structured output from Groq with tool calling"""
		mock_response = MagicMock()
		mock_response.choices = [MagicMock()]
		mock_response.choices[0].message.tool_calls = [MagicMock()]
		mock_response.choices[0].message.tool_calls[
			0
		].function.arguments = f'{{"country": "{self.EXPECTED_FRANCE_COUNTRY}", "capital": "{self.EXPECTED_FRANCE_CAPITAL}"}}'
		mock_response.usage = None
		mock_get_client.return_value.chat.completions.create = AsyncMock(return_value=mock_response)

		chat = ChatGroq(model='moonshotai/kimi-k2-instruct', temperature=0, api_key='test')
		response = await chat.ainvoke(self.STRUCTURED_MESSAGES, output_format=CapitalResponse)

		completion = response.completion

		assert isinstance(completion, CapitalResponse)
		assert completion.country.lower() == self.EXPECTED_FRANCE_COUNTRY
		assert completion.capital.lower() == self.EXPECTED_FRANCE_CAPITAL
