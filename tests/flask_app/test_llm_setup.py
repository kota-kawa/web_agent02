from __future__ import annotations

import os

# Add project root to path to allow absolute imports
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from browser_use.llm.base import BaseChatModel
from flask_app.llm_setup import ChatAnthropic, ChatGoogle, ChatGroq, ChatOpenAI, _create_selected_llm


# Mock environment variables for API keys
@pytest.fixture(autouse=True)
def mock_api_keys():
	original_environ = dict(os.environ)
	os.environ['GEMINI_API_KEY'] = 'test_gemini_key'
	os.environ['CLAUDE_API_KEY'] = 'test_claude_key'
	os.environ['GROQ_API_KEY'] = 'test_groq_key'
	os.environ['OPENAI_API_KEY'] = 'test_openai_key'
	yield
	os.environ.clear()
	os.environ.update(original_environ)


# Test cases for each provider
@pytest.mark.parametrize(
	'provider, model, expected_client',
	[
		('gemini', 'gemini-3-pro-preview', ChatGoogle),
		('gemini', 'gemini-2.5-flash-lite', ChatGoogle),
		('claude', 'claude-haiku-4-5', ChatAnthropic),
		('groq', 'llama-3.3-70b-versatile', ChatGroq),
		('openai', 'gpt-5.1', ChatOpenAI),
	],
)
def test_create_selected_llm_providers(provider, model, expected_client):
	"""
	Tests that _create_selected_llm returns the correct client instance
	for each specified provider.
	"""
	selection = {'provider': provider, 'model': model}

	# Use patch to mock the model_selection functions
	with (
		patch('flask_app.llm_setup.apply_model_selection') as mock_apply,
		patch('flask_app.llm_setup.update_override') as mock_update,
	):
		# Configure the mock to return the selection
		mock_apply.return_value = selection
		mock_update.return_value = selection

		llm_instance = _create_selected_llm(selection_override=selection)

		assert isinstance(llm_instance, BaseChatModel), f'Instance should be a BaseChatModel for {provider}'
		assert isinstance(llm_instance, expected_client), (
			f"Incorrect client for provider '{provider}'. Expected {expected_client.__name__}, got {type(llm_instance).__name__}"
		)


def test_openai_default_behavior():
	"""
	Tests that the function defaults to OpenAI client when the provider is not recognized.
	"""
	selection = {'provider': 'unknown_provider', 'model': 'some-model'}

	with (
		patch('flask_app.llm_setup.apply_model_selection') as mock_apply,
		patch('flask_app.llm_setup.update_override') as mock_update,
	):
		mock_apply.return_value = selection
		mock_update.return_value = selection

		llm_instance = _create_selected_llm(selection_override=selection)

		assert isinstance(llm_instance, ChatOpenAI), 'Should default to ChatOpenAI for unknown providers'


def test_gemini_with_openai_compatible_base_url():
	"""
	Ensures that Gemini provider returns ChatGoogle client even with an
	OpenAI-compatible base_url. This is the key fix being tested.
	"""
	selection = {
		'provider': 'gemini',
		'model': 'gemini-2.5-flash-lite',
		'base_url': 'https://generativelanguage.googleapis.com/openai/v1',
	}

	with (
		patch('flask_app.llm_setup.apply_model_selection') as mock_apply,
		patch('flask_app.llm_setup.update_override') as mock_update,
		patch('browser_use.model_selection.PROVIDER_DEFAULTS') as mock_provider_defaults,
	):
		mock_apply.return_value = selection
		mock_update.return_value = selection

		# Mock the provider defaults to simulate the real configuration
		mock_provider_defaults.get.return_value = {'api_key_env': 'GEMINI_API_KEY'}

		llm_instance = _create_selected_llm(selection_override=selection)

		assert isinstance(llm_instance, ChatGoogle), "Provider 'gemini' should always return a ChatGoogle client"
