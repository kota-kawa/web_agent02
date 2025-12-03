from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Generator

import pytest


@pytest.fixture
def model_selection(monkeypatch) -> Generator:
	"""
	Reload browser_use.model_selection with logging/setup side effects disabled
	so we can freely tweak environment variables.
	"""

	monkeypatch.setenv('BROWSER_USE_SETUP_LOGGING', 'false')
	monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
	monkeypatch.delenv('DEFAULT_LLM', raising=False)

	# Ensure we import a clean copy with the updated environment
	for name in [m for m in sys.modules if m.startswith('browser_use')]:
		sys.modules.pop(name)

	module = importlib.import_module('browser_use.model_selection')
	module.update_override(None)
	yield module


def test_switching_from_groq_resets_openai_base_url(monkeypatch, model_selection):
	monkeypatch.setenv('OPENAI_API_KEY', 'openai-key')
	monkeypatch.setenv('GROQ_API_KEY', 'groq-key')

	groq_applied = model_selection.apply_model_selection(override={'provider': 'groq', 'model': 'llama-3.1-8b-instant'})
	assert groq_applied['base_url'].rstrip('/') == model_selection.PROVIDER_DEFAULTS['groq']['default_base_url']
	assert os.environ['OPENAI_BASE_URL'].rstrip('/') == model_selection.PROVIDER_DEFAULTS['groq']['default_base_url']

	openai_applied = model_selection.apply_model_selection(override={'provider': 'openai', 'model': 'gpt-5.1'})
	assert openai_applied['base_url'] == ''
	assert 'OPENAI_BASE_URL' not in os.environ


def test_env_groq_url_is_not_reused(monkeypatch, model_selection):
	monkeypatch.setenv('OPENAI_API_KEY', 'openai-key')
	monkeypatch.setenv('OPENAI_BASE_URL', f'{model_selection.PROVIDER_DEFAULTS["groq"]["default_base_url"]}/')

	applied = model_selection.apply_model_selection(override={'provider': 'openai', 'model': 'gpt-5.1'})
	assert applied['base_url'] == ''
	assert 'OPENAI_BASE_URL' not in os.environ


def test_explicit_base_url_is_respected(monkeypatch, model_selection):
	monkeypatch.setenv('OPENAI_API_KEY', 'openai-key')
	custom_url = 'https://example.test/openai/v1/'

	applied = model_selection.apply_model_selection(override={'provider': 'openai', 'model': 'gpt-5.1', 'base_url': custom_url})
	assert applied['base_url'] == custom_url.rstrip('/')
	assert os.environ['OPENAI_BASE_URL'] == custom_url.rstrip('/')
