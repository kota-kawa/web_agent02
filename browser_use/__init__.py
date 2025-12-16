import inspect
import logging
import os
from typing import TYPE_CHECKING

from browser_use.logging_config import setup_logging
from browser_use.model_selection import apply_model_selection

# Ensure DEFAULT_LLM and OpenAI env vars reflect Multi-Agent-Platform settings
apply_model_selection('browser')

# Only set up logging if not in MCP mode or if explicitly requested
if os.environ.get('BROWSER_USE_SETUP_LOGGING', 'true').lower() != 'false':
	from browser_use.config import CONFIG

	# Get log file paths from config/environment
	debug_log_file = getattr(CONFIG, 'BROWSER_USE_DEBUG_LOG_FILE', None)
	info_log_file = getattr(CONFIG, 'BROWSER_USE_INFO_LOG_FILE', None)

	# Set up logging with file handlers if specified
	logger = setup_logging(debug_log_file=debug_log_file, info_log_file=info_log_file)
else:
	import logging

logger = logging.getLogger('browser_use')

# Relax EventBus recursion guard for long nested handler chains (e.g., CloudSync)
from bubus import EventBus
from bubus.service import get_handler_id, get_handler_name
from bubus.service import logger as eventbus_logger


def _patch_eventbus_recursion_limits() -> None:
	"""Allow deeper re-entrancy for safe handlers to avoid false loop errors.

	The default bubus limit raises at depth>2 which breaks long event chains when
	CloudSync listens to every event. We raise the ceiling (default 5) and skip
	depth checks for known side-effect-only handlers.
	"""

	if getattr(EventBus, '_browser_use_recursion_patched', False):
		return

	max_depth = int(os.environ.get('BROWSER_USE_EVENTBUS_RECURSION_DEPTH', '5'))
	warn_depth = max(1, max_depth - 1)
	ignore_handlers = {'CloudSync.handle_event'}

	def _patched_would_create_loop(self, event, handler):  # type: ignore[override]
		assert inspect.isfunction(handler) or inspect.iscoroutinefunction(handler) or inspect.ismethod(handler), (
			f'Handler {get_handler_name(handler)} must be a sync or async function, got: {type(handler)}'
		)

		# Forwarding loop check (unchanged)
		if hasattr(handler, '__self__') and isinstance(handler.__self__, EventBus) and handler.__name__ == 'dispatch':
			target_bus = handler.__self__
			if target_bus.name in event.event_path:
				eventbus_logger.debug(
					f'⚠️ {self} handler {get_handler_name(handler)}#{str(id(handler))[-4:]}({event}) skipped to prevent infinite forwarding loop with {target_bus.name}'
				)
				return True

		handler_id = get_handler_id(handler, self)
		if handler_id in event.event_results:
			existing_result = event.event_results[handler_id]
			if existing_result.status in ('pending', 'started'):
				eventbus_logger.debug(
					f'⚠️ {self} handler {get_handler_name(handler)}#{str(id(handler))[-4:]}({event}) is already {existing_result.status} for event {event.event_id} (preventing recursive call)'
				)
				return True
			elif existing_result.completed_at is not None:
				eventbus_logger.debug(
					f'⚠️ {self} handler {get_handler_name(handler)}#{str(id(handler))[-4:]}({event}) already completed @ {existing_result.completed_at} for event {event.event_id} (will not re-run)'
				)
				return True

		is_forwarding_handler = (
			inspect.ismethod(handler) and isinstance(handler.__self__, EventBus) and handler.__name__ == 'dispatch'
		)

		if not is_forwarding_handler:
			handler_name = get_handler_name(handler)
			if handler_name not in ignore_handlers:
				recursion_depth = self._handler_dispatched_ancestor(event, handler_id)
				if recursion_depth > max_depth:
					raise RuntimeError(
						f'Infinite loop detected: Handler {get_handler_name(handler)}#{str(id(handler))[-4:]} '
						f'has recursively processed {recursion_depth} levels of events (max {max_depth}). '
						f'Current event: {event}, Handler: {handler_id}'
					)
				elif recursion_depth >= warn_depth:
					eventbus_logger.warning(
						f'⚠️ {self} handler {get_handler_name(handler)}#{str(id(handler))[-4:]} '
						f'at recursion depth {recursion_depth}/{max_depth} - deeper nesting will raise'
					)

		return False

	EventBus._would_create_loop = _patched_would_create_loop  # type: ignore[assignment]
	EventBus._browser_use_recursion_patched = True


_patch_eventbus_recursion_limits()

# Monkeypatch BaseSubprocessTransport.__del__ to handle closed event loops gracefully
from asyncio import base_subprocess

_original_del = base_subprocess.BaseSubprocessTransport.__del__


def _patched_del(self):
	"""Patched __del__ that handles closed event loops without throwing noisy red-herring errors like RuntimeError: Event loop is closed"""
	try:
		# Check if the event loop is closed before calling the original
		if hasattr(self, '_loop') and self._loop and self._loop.is_closed():
			# Event loop is closed, skip cleanup that requires the loop
			return
		_original_del(self)
	except RuntimeError as e:
		if 'Event loop is closed' in str(e):
			# Silently ignore this specific error
			pass
		else:
			raise


base_subprocess.BaseSubprocessTransport.__del__ = _patched_del

# Type stubs for lazy imports - fixes linter warnings
if TYPE_CHECKING:
	from browser_use.agent.prompts import SystemPrompt
	from browser_use.agent.service import Agent
	from browser_use.agent.views import ActionModel, ActionResult, AgentHistoryList
	from browser_use.browser import BrowserProfile, BrowserSession
	from browser_use.browser import BrowserSession as Browser
	from browser_use.dom.service import DomService
	from browser_use.llm import models
	from browser_use.llm.anthropic.chat import ChatAnthropic
	from browser_use.llm.azure.chat import ChatAzureOpenAI
	from browser_use.llm.google.chat import ChatGoogle
	from browser_use.llm.groq.chat import ChatGroq
	from browser_use.llm.ollama.chat import ChatOllama
	from browser_use.llm.openai.chat import ChatOpenAI
	from browser_use.tools.service import Controller, Tools


# Lazy imports mapping - only import when actually accessed
_LAZY_IMPORTS = {
	# Agent service (heavy due to dependencies)
	'Agent': ('browser_use.agent.service', 'Agent'),
	# System prompt (moderate weight due to agent.views imports)
	'SystemPrompt': ('browser_use.agent.prompts', 'SystemPrompt'),
	# Agent views (very heavy - over 1 second!)
	'ActionModel': ('browser_use.agent.views', 'ActionModel'),
	'ActionResult': ('browser_use.agent.views', 'ActionResult'),
	'AgentHistoryList': ('browser_use.agent.views', 'AgentHistoryList'),
	'BrowserSession': ('browser_use.browser', 'BrowserSession'),
	'Browser': ('browser_use.browser', 'BrowserSession'),  # Alias for BrowserSession
	'BrowserProfile': ('browser_use.browser', 'BrowserProfile'),
	# Tools (moderate weight)
	'Tools': ('browser_use.tools.service', 'Tools'),
	'Controller': ('browser_use.tools.service', 'Controller'),  # alias
	# DOM service (moderate weight)
	'DomService': ('browser_use.dom.service', 'DomService'),
	# Chat models (very heavy imports)
	'ChatOpenAI': ('browser_use.llm.openai.chat', 'ChatOpenAI'),
	'ChatGoogle': ('browser_use.llm.google.chat', 'ChatGoogle'),
	'ChatAnthropic': ('browser_use.llm.anthropic.chat', 'ChatAnthropic'),
	'ChatGroq': ('browser_use.llm.groq.chat', 'ChatGroq'),
	'ChatAzureOpenAI': ('browser_use.llm.azure.chat', 'ChatAzureOpenAI'),
	'ChatOllama': ('browser_use.llm.ollama.chat', 'ChatOllama'),
	# LLM models module
	'models': ('browser_use.llm.models', None),
}


def __getattr__(name: str):
	"""Lazy import mechanism - only import modules when they're actually accessed."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			if attr_name is None:
				# For modules like 'models', return the module itself
				attr = module
			else:
				attr = getattr(module, attr_name)
			# Cache the imported attribute in the module's globals
			globals()[name] = attr
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
	'Agent',
	'BrowserSession',
	'Browser',  # Alias for BrowserSession
	'BrowserProfile',
	'Controller',
	'DomService',
	'SystemPrompt',
	'ActionResult',
	'ActionModel',
	'AgentHistoryList',
	# Chat models
	'ChatOpenAI',
	'ChatGoogle',
	'ChatAnthropic',
	'ChatGroq',
	'ChatAzureOpenAI',
	'ChatOllama',
	'Tools',
	'Controller',
	# LLM models module
	'models',
]
