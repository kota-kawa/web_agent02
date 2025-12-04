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


def test_claude_default_does_not_set_openrouter(monkeypatch, model_selection):
	monkeypatch.setenv('CLAUDE_API_KEY', 'claude-key')
	monkeypatch.setenv('OPENAI_BASE_URL', 'https://openrouter.ai/api/v1')

	applied = model_selection.apply_model_selection(override={'provider': 'claude', 'model': 'claude-3-haiku'})
	assert applied['base_url'] == ''
	assert 'OPENAI_BASE_URL' not in os.environ


def test_claude_explicit_openrouter_is_rejected(monkeypatch, model_selection):
	monkeypatch.setenv('CLAUDE_API_KEY', 'claude-key')

	applied = model_selection.apply_model_selection(
		override={'provider': 'claude', 'model': 'claude-3-7-sonnet', 'base_url': 'https://openrouter.ai/api/v1'}
	)
	assert applied['base_url'] == ''
	assert 'OPENAI_BASE_URL' not in os.environ


def test_gemini_default_uses_v1beta(monkeypatch, model_selection):
	monkeypatch.setenv('GEMINI_API_KEY', 'gemini-key')

	applied = model_selection.apply_model_selection(override={'provider': 'gemini', 'model': 'gemini-1.5-flash'})
	assert applied['base_url'].rstrip('/') == 'https://generativelanguage.googleapis.com/v1beta'
	assert os.environ['OPENAI_BASE_URL'].rstrip('/') == 'https://generativelanguage.googleapis.com/v1beta'


def test_gemini_openai_compat_url_is_cleaned(monkeypatch, model_selection):
	monkeypatch.setenv('GEMINI_API_KEY', 'gemini-key')
	monkeypatch.setenv('GEMINI_API_BASE', 'https://generativelanguage.googleapis.com/openai/v1')

	applied = model_selection.apply_model_selection(override={'provider': 'gemini', 'model': 'gemini-1.5-pro'})
	assert applied['base_url'].rstrip('/') == 'https://generativelanguage.googleapis.com/v1beta'
	assert os.environ['OPENAI_BASE_URL'].rstrip('/') == 'https://generativelanguage.googleapis.com/v1beta'


def test_switching_from_gemini_resets_openai_base_url(monkeypatch, model_selection):
	monkeypatch.setenv('GEMINI_API_KEY', 'gemini-key')
	monkeypatch.setenv('OPENAI_API_KEY', 'openai-key')

	model_selection.apply_model_selection(override={'provider': 'gemini', 'model': 'gemini-1.5-flash'})
	assert os.environ['OPENAI_BASE_URL'].rstrip('/') == 'https://generativelanguage.googleapis.com/v1beta'

	openai_applied = model_selection.apply_model_selection(override={'provider': 'openai', 'model': 'gpt-5.1'})
	assert openai_applied['base_url'] == ''
	assert 'OPENAI_BASE_URL' not in os.environ
