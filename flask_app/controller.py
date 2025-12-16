from __future__ import annotations

import asyncio
import atexit
import copy
import inspect
import logging
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bubus import EventBus

from .env_utils import _DEFAULT_START_URL, _env_int, _normalize_start_url
from .exceptions import AgentControllerError
from .formatting import _format_step_plan
from .history import _append_history_message
from .llm_setup import _create_selected_llm
from .system_prompt import (
	_DEFAULT_MAX_ACTIONS_PER_STEP,
	_LANGUAGE_EXTENSION,
	_build_custom_system_prompt,
	_should_disable_vision,
)

try:
	from browser_use import Agent, BrowserProfile, BrowserSession, Tools
except ModuleNotFoundError:
	import sys

	ROOT_DIR = Path(__file__).resolve().parents[1]
	if str(ROOT_DIR) not in sys.path:
		sys.path.insert(0, str(ROOT_DIR))
	from browser_use import Agent, BrowserProfile, BrowserSession, Tools

from browser_use.agent.views import AgentHistoryList, AgentOutput
from browser_use.browser.events import TabClosedEvent
from browser_use.browser.profile import ViewportSize
from browser_use.browser.views import BrowserStateSummary
from browser_use.model_selection import _load_selection


@dataclass
class AgentRunResult:
	history: AgentHistoryList
	step_message_ids: dict[int, int] = field(default_factory=dict)
	filtered_history: AgentHistoryList | None = None


class BrowserAgentController:
	"""Manage a long-lived browser session controlled by browser-use."""

	def __init__(
		self,
		cdp_url: str | None,
		max_steps: int,
		cdp_cleanup: Callable[[], None] | None = None,
	) -> None:
		self._cdp_url = cdp_url
		self._max_steps = max_steps
		self._loop = asyncio.new_event_loop()
		self._thread = threading.Thread(
			target=self._run_loop,
			name='browser-use-agent-loop',
			daemon=True,
		)
		self._thread.start()
		self._lock = threading.Lock()
		self._state_lock = threading.Lock()
		self._browser_session: BrowserSession | None = None
		self._shutdown = False
		self._logger = logging.getLogger('browser_use.flask.agent')
		self._cdp_cleanup = cdp_cleanup
		self._llm = _create_selected_llm()
		self._agent: Agent | None = None
		self._current_agent: Agent | None = None
		self._is_running = False
		self._paused = False
		self._vision_enabled = True
		self._step_message_ids: dict[int, int] = {}
		self._step_message_lock = threading.Lock()
		self._resume_url: str | None = None
		self._session_recreated = False
		self._start_page_ready = False
		self._initial_prompt_handled = False
		atexit.register(self.shutdown)

	@property
	def loop(self) -> asyncio.AbstractEventLoop:
		return self._loop

	@staticmethod
	def _resolve_step_timeout() -> int | None:
		"""Resolve step timeout from environment.

		Returns:
		    int | None: Timeout in seconds, or None/<=0 to disable.
		"""

		raw = os.environ.get('BROWSER_USE_STEP_TIMEOUT')
		if raw is None:
			# Default: disable step timeout to allow long-running tasks.
			return None

		raw = raw.strip().lower()
		if raw in {'', 'none', 'no', 'off', 'false', '0'}:
			return None

		try:
			value = int(raw)
			return value if value > 0 else None
		except ValueError:
			# Fall back to no timeout on invalid input.
			return None

	def _run_loop(self) -> None:
		asyncio.set_event_loop(self._loop)
		self._loop.run_forever()

	async def _ensure_browser_session(self) -> BrowserSession:
		if self._browser_session is not None:
			with self._state_lock:
				self._session_recreated = False
			return self._browser_session

		if not self._cdp_url:
			raise AgentControllerError('Chrome DevToolsのCDP URLが検出できませんでした。BROWSER_USE_CDP_URL を設定してください。')

		def _viewport_from_env(
			width_key: str,
			height_key: str,
			default_width: int,
			default_height: int,
		) -> ViewportSize | None:
			"""Create a viewport from environment variables if either is defined."""

			width_raw = os.environ.get(width_key)
			height_raw = os.environ.get(height_key)

			if width_raw is None and height_raw is None:
				return None

			width = _env_int(width_key, default_width)
			height = _env_int(height_key, default_height)

			return ViewportSize(width=width, height=height)

		window_size: ViewportSize | None = None
		screen_size: ViewportSize | None = None

		browser_window = _viewport_from_env('BROWSER_WINDOW_WIDTH', 'BROWSER_WINDOW_HEIGHT', 1920, 1080)
		if browser_window is not None:
			window_size = browser_window
			screen_size = browser_window
		else:
			selenium_window = _viewport_from_env('SE_SCREEN_WIDTH', 'SE_SCREEN_HEIGHT', 1920, 1080)
			if selenium_window is not None:
				window_size = selenium_window
				screen_size = selenium_window

		profile = BrowserProfile(
			cdp_url=self._cdp_url,
			keep_alive=True,
			highlight_elements=True,
			wait_between_actions=0.4,
			window_size=window_size,
			screen=screen_size,
		)
		session = BrowserSession(browser_profile=profile)
		with self._state_lock:
			self._browser_session = session
			self._session_recreated = True
			self._start_page_ready = False
		return session

	def _consume_session_recreated(self) -> bool:
		with self._state_lock:
			recreated = self._session_recreated
			self._session_recreated = False
		return recreated

	async def _run_agent(
		self,
		task: str,
		record_history: bool = True,
		additional_system_message: str | None = None,
		max_steps_override: int | None = None,
	) -> AgentRunResult:
		session = await self._ensure_browser_session()
		session_recreated = self._consume_session_recreated()
		effective_max_steps = max_steps_override if max_steps_override and max_steps_override > 0 else self._max_steps

		step_message_ids: dict[int, int] = {}
		starting_step_number = 1
		history_start_index = 0

		def handle_new_step(
			state_summary: BrowserStateSummary,
			model_output: AgentOutput,
			step_number: int,
		) -> None:
			if not record_history:
				return
			try:
				relative_step = step_number - starting_step_number + 1
				if relative_step < 1:
					relative_step = 1
				content = _format_step_plan(relative_step, state_summary, model_output)
				message = _append_history_message('assistant', content)
				message_id = int(message['id'])
				step_message_ids[relative_step] = message_id
				self.remember_step_message_id(relative_step, message_id)
			except Exception:
				self._logger.debug('Failed to broadcast step update', exc_info=True)

		register_callback = handle_new_step if record_history else None

		def _create_new_agent(initial_task: str) -> Agent:
			selection = _load_selection('browser')
			provider = selection.get('provider', '')
			model = str(selection.get('model', ''))
			provider_from_llm = getattr(self._llm, 'provider', '') or provider
			model_from_llm = str(getattr(self._llm, 'model', model) or model)

			with self._state_lock:
				vision_pref = self._vision_enabled

			vision_disabled = (not vision_pref) or _should_disable_vision(provider_from_llm, model_from_llm)
			if vision_disabled:
				self._logger.info(
					'Disabling vision because provider/model are not in the supported list: provider=%s model=%s',
					provider_from_llm,
					model_from_llm,
				)

			custom_system_prompt = _build_custom_system_prompt(
				force_disable_vision=vision_disabled,
				provider=provider_from_llm,
				model=model_from_llm,
			)
			if custom_system_prompt:
				if additional_system_message:
					custom_system_prompt += f'\n\n{additional_system_message}'
				extend_system_message = None
			else:
				base_extension = _LANGUAGE_EXTENSION
				if additional_system_message:
					base_extension += f'\n\n{additional_system_message}'
				extend_system_message = base_extension

			tools = Tools(exclude_actions=['read_file'])
			step_timeout = self._resolve_step_timeout()
			fresh_agent = Agent(
				task=initial_task,
				browser_session=session,
				llm=self._llm,
				tools=tools,
				register_new_step_callback=register_callback,
				max_actions_per_step=_DEFAULT_MAX_ACTIONS_PER_STEP,
				override_system_message=custom_system_prompt,
				extend_system_message=extend_system_message,
				max_history_items=6,
				use_vision=not vision_disabled,
				step_timeout=step_timeout,
			)
			start_url = self._get_resume_url() or _DEFAULT_START_URL
			if start_url and not fresh_agent.initial_actions:
				try:
					fresh_agent.initial_url = start_url
					fresh_agent.initial_actions = fresh_agent._convert_initial_actions(
						[{'go_to_url': {'url': start_url, 'new_tab': False}}]
					)
				except Exception:
					self._logger.debug(
						'Failed to apply start URL %s',
						start_url,
						exc_info=True,
					)
			return fresh_agent

		with self._state_lock:
			existing_agent = self._agent
			agent_running = self._is_running

		if agent_running:
			raise AgentControllerError('エージェントは実行中です。')

		if existing_agent is None:
			agent = _create_new_agent(task)
			with self._state_lock:
				self._agent = agent
		else:
			agent = existing_agent
			agent.browser_session = session
			agent.register_new_step_callback = register_callback
			try:
				agent.add_new_task(task)
				self._prepare_agent_for_follow_up(agent, force_resume_navigation=session_recreated)
			except (AssertionError, ValueError) as exc:
				self._logger.exception('Failed to apply follow-up task %r; recreating agent.', task)
				with self._state_lock:
					self._agent = None
					self._current_agent = None
				agent = _create_new_agent(task)
				with self._state_lock:
					self._agent = agent
				self._logger.info('Recreated agent after failure and retrying task %r.', task)
			except Exception as exc:
				raise AgentControllerError(f'追加の指示の適用に失敗しました: {exc}') from exc

		history_items = getattr(agent, 'history', None)
		if history_items is not None:
			history_start_index = len(history_items.history)
		starting_step_number = getattr(getattr(agent, 'state', None), 'n_steps', 1) or 1
		self._clear_step_message_ids()

		attach_watchdogs = getattr(session, 'attach_all_watchdogs', None)
		if attach_watchdogs is not None:
			try:
				await attach_watchdogs()
			except Exception:
				self._logger.debug('Failed to pre-attach browser watchdogs', exc_info=True)

		with self._state_lock:
			self._current_agent = agent
			self._is_running = True
			self._paused = False
		try:
			history = await agent.run(max_steps=effective_max_steps)
			self._update_resume_url_from_history(history)
			new_entries = history.history[history_start_index:]
			filtered_entries = [
				entry
				for entry in new_entries
				if not getattr(entry, 'metadata', None) or getattr(entry.metadata, 'step_number', None) != 0
			]
			if filtered_entries or not new_entries:
				relevant_entries = filtered_entries
			else:
				relevant_entries = new_entries
			if isinstance(history, AgentHistoryList):
				history_kwargs = {'history': relevant_entries}
				if hasattr(history, 'usage'):
					history_kwargs['usage'] = getattr(history, 'usage')
				filtered_history = history.__class__(**history_kwargs)
				if hasattr(history, '_output_model_schema'):
					filtered_history._output_model_schema = history._output_model_schema
			else:
				filtered_history = copy.copy(history)
				setattr(filtered_history, 'history', relevant_entries)
			return AgentRunResult(
				history=history,
				step_message_ids=step_message_ids,
				filtered_history=filtered_history,
			)
		finally:
			keep_alive = session.browser_profile.keep_alive
			rotate_session = False
			if keep_alive:
				drain_method = getattr(type(session), 'drain_event_bus', None)
				if callable(drain_method):
					try:
						drained_cleanly = await drain_method(session)
					except Exception:
						rotate_session = True
						self._logger.warning(
							'Failed to drain browser event bus; rotating for safety.',
							exc_info=True,
						)
					else:
						if not drained_cleanly:
							rotate_session = True
							self._logger.warning(
								'Browser event bus rotated after drain timeout; pending events cleared.',
							)
				else:
					self._logger.debug(
						'Browser session implementation does not expose drain_event_bus(); applying compatibility cleanup.',
					)
					with suppress(Exception):
						await session.event_bus.stop(clear=True, timeout=1.0)

					def _resync_agent_event_bus() -> None:
						with self._state_lock:
							candidate = self._agent or self._current_agent
						if candidate is None:
							return
						if getattr(candidate, 'browser_session', None) is not session:
							return

						reset_agent_bus = getattr(candidate, '_reset_eventbus', None)
						if callable(reset_agent_bus):
							try:
								reset_agent_bus()
							except Exception:
								self._logger.warning(
									'Failed to reset agent event bus after legacy session refresh; attempting manual synchronisation.',
									exc_info=True,
								)
							else:
								return

						refresh_agent_bus = getattr(
							candidate,
							'_refresh_browser_session_eventbus',
							None,
						)
						if callable(refresh_agent_bus):
							try:
								refresh_agent_bus(reset_watchdogs=True)
							except Exception:
								self._logger.warning(
									'Failed to refresh agent event bus after legacy session refresh.',
									exc_info=True,
								)

					reset_method = getattr(session, '_reset_event_bus_state', None)
					if callable(reset_method):
						try:
							reset_method()
						except Exception:
							self._logger.debug(
								'Legacy browser session failed to reset event bus state cleanly.',
								exc_info=True,
							)
						else:
							_resync_agent_event_bus()
					else:
						self._logger.debug(
							'Legacy browser session missing _reset_event_bus_state(); refreshing EventBus manually.',
						)
						try:
							session.event_bus = EventBus()
							try:
								session._watchdogs_attached = False  # type: ignore[attr-defined]
							except Exception:
								self._logger.debug(
									'Unable to reset watchdog attachment flag during manual event bus refresh.',
									exc_info=True,
								)
							for attribute in (
								'_crash_watchdog',
								'_downloads_watchdog',
								'_aboutblank_watchdog',
								'_security_watchdog',
								'_storage_state_watchdog',
								'_local_browser_watchdog',
								'_default_action_watchdog',
								'_dom_watchdog',
								'_screenshot_watchdog',
								'_permissions_watchdog',
								'_recording_watchdog',
							):
								if hasattr(session, attribute):
									try:
										setattr(session, attribute, None)
									except Exception:
										self._logger.debug(
											'Unable to clear %s during manual event bus refresh.',
											attribute,
											exc_info=True,
										)
							session.model_post_init(None)
						except Exception:
							rotate_session = True
							self._logger.warning(
								'Failed to refresh EventBus on legacy browser session; scheduling full rotation.',
								exc_info=True,
							)
						else:
							_resync_agent_event_bus()
			else:
				with suppress(Exception):
					await session.stop()

			if rotate_session:
				with suppress(Exception):
					await session.stop()
				kill_method = getattr(session, 'kill', None)
				if callable(kill_method):
					with suppress(Exception):
						maybe_kill = kill_method()
						if inspect.isawaitable(maybe_kill):
							await maybe_kill

			with self._state_lock:
				if self._browser_session is session:
					if rotate_session:
						self._browser_session = None
						self._logger.info(
							'Browser session rotated after event bus drain failure; a fresh session will be created on the next run.',
						)
					elif keep_alive:
						self._logger.debug(
							'Browser session kept alive for follow-up runs.',
						)
					else:
						self._logger.debug(
							'Browser session stopped; a new session will be created on the next run.',
						)
						self._browser_session = None
				self._current_agent = None
				self._is_running = False
				self._paused = False

	def _pop_browser_session(self) -> BrowserSession | None:
		with self._state_lock:
			session = self._browser_session
			self._browser_session = None
			self._session_recreated = False
			self._start_page_ready = False
		return session

	def _stop_browser_session(self) -> None:
		session = self._pop_browser_session()
		if session is None:
			return

		async def _shutdown() -> None:
			with suppress(Exception):
				await session.stop()

		future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
		try:
			future.result(timeout=5)
		except Exception:
			future.cancel()
			self._logger.warning(
				'Failed to stop browser session cleanly; a fresh session will be created on the next run.',
				exc_info=True,
			)

	async def _async_shutdown(self) -> None:
		session = self._pop_browser_session()
		if session is not None:
			with suppress(Exception):
				await session.stop()
		await self._close_llm()

	async def _close_llm(self) -> None:
		"""Close the shared LLM client to avoid late AsyncClient cleanup errors."""

		llm = self._llm
		if llm is None:
			return

		aclose = getattr(llm, 'aclose', None)
		if not callable(aclose):
			return

		try:
			await aclose()
			self._llm = None
		except RuntimeError as exc:
			# httpx/anyio will raise if the event loop is already shutting down.
			if 'Event loop is closed' in str(exc):
				self._logger.debug('LLM client close skipped because event loop is closed.')
			else:
				self._logger.debug('Failed to close LLM client cleanly', exc_info=True)
		except Exception:
			self._logger.debug('Unexpected error while closing LLM client', exc_info=True)

	def _call_in_loop(self, func: Callable[[], None]) -> None:
		async def _invoke() -> None:
			func()

		future = asyncio.run_coroutine_threadsafe(_invoke(), self._loop)
		future.result()

	def enqueue_follow_up(self, task: str) -> None:
		with self._state_lock:
			agent = self._current_agent
			running = self._is_running

		if not agent or not running:
			raise AgentControllerError('エージェントは実行中ではありません。')

		def _apply() -> None:
			agent.add_new_task(task)

		try:
			self._call_in_loop(_apply)
		except AgentControllerError:
			raise
		except Exception as exc:
			raise AgentControllerError(f'追加の指示の適用に失敗しました: {exc}') from exc

	def _prepare_agent_for_follow_up(self, agent: Agent, *, force_resume_navigation: bool = False) -> None:
		"""Clear completion flags so follow-up runs can execute new steps."""

		cleared = False

		with suppress(AttributeError):
			cleared = agent.reset_completion_state()
			agent.state.stopped = False
			agent.state.paused = False

		if cleared:
			self._logger.debug('Cleared completion state for follow-up agent run.')

		resume_url = self._get_resume_url()
		prepared_resume = False

		if force_resume_navigation and resume_url:
			try:
				agent.initial_url = resume_url
				agent.initial_actions = agent._convert_initial_actions([{'go_to_url': {'url': resume_url, 'new_tab': False}}])
				agent.state.follow_up_task = False
				prepared_resume = True
				self._logger.debug('Prepared follow-up run to resume at %s.', resume_url)
			except Exception:
				self._logger.debug(
					'Failed to prepare resume navigation to %s',
					resume_url,
					exc_info=True,
				)
				agent.initial_actions = None

		if not prepared_resume:
			agent.initial_url = None
			agent.initial_actions = None
			agent.state.follow_up_task = True

	def _record_step_message_id(self, step_number: int, message_id: int) -> None:
		with self._step_message_lock:
			self._step_message_ids[step_number] = message_id

	def _lookup_step_message_id(self, step_number: int) -> int | None:
		with self._step_message_lock:
			return self._step_message_ids.get(step_number)

	def _clear_step_message_ids(self) -> None:
		with self._step_message_lock:
			self._step_message_ids.clear()

	def _set_resume_url(self, url: str | None) -> None:
		with self._state_lock:
			self._resume_url = url

	def set_start_page(self, url: str | None) -> None:
		"""Override the next start/resume URL and reset warmup state."""

		normalized = _normalize_start_url(url) if url else None
		with self._state_lock:
			self._resume_url = normalized
			self._start_page_ready = False
		if normalized:
			self._logger.debug('Start page overridden for next run: %s', normalized)
		else:
			self._logger.debug('Start page override cleared; default will be used.')

	def _get_resume_url(self) -> str | None:
		with self._state_lock:
			return self._resume_url

	def _update_resume_url_from_history(self, history: AgentHistoryList) -> None:
		resume_url: str | None = None
		try:
			for entry in reversed(history.history):
				state = getattr(entry, 'state', None)
				if state is None:
					continue
				url = getattr(state, 'url', None)
				if not url:
					continue
				normalized = url.strip()
				if not normalized:
					continue
				lowered = normalized.lower()
				if lowered.startswith('about:') or lowered.startswith('chrome-error://'):
					continue
				if lowered.startswith('chrome://') or lowered.startswith('devtools://'):
					continue
				resume_url = normalized
				break
		except Exception:
			self._logger.debug('Failed to derive resume URL from agent history.', exc_info=True)
			return

		self._set_resume_url(resume_url)
		if resume_url:
			self._logger.debug('Recorded resume URL for follow-up runs: %s', resume_url)

	def remember_step_message_id(self, step_number: int, message_id: int) -> None:
		self._record_step_message_id(step_number, message_id)

	def get_step_message_id(self, step_number: int) -> int | None:
		return self._lookup_step_message_id(step_number)

	def pause(self) -> None:
		with self._state_lock:
			agent = self._current_agent
			running = self._is_running
			already_paused = self._paused

		if not agent or not running:
			raise AgentControllerError('エージェントは実行されていません。')
		if already_paused:
			raise AgentControllerError('エージェントは既に一時停止中です。')

		try:
			self._call_in_loop(agent.pause)
		except Exception as exc:
			raise AgentControllerError(f'一時停止に失敗しました: {exc}') from exc

		with self._state_lock:
			self._paused = True

	def resume(self) -> None:
		with self._state_lock:
			agent = self._current_agent
			running = self._is_running
			paused = self._paused

		if not agent or not running:
			raise AgentControllerError('エージェントは実行されていません。')
		if not paused:
			raise AgentControllerError('エージェントは一時停止状態ではありません。')

		try:
			self._call_in_loop(agent.resume)
		except Exception as exc:
			raise AgentControllerError(f'再開に失敗しました: {exc}') from exc

		with self._state_lock:
			self._paused = False

	def is_running(self) -> bool:
		with self._state_lock:
			return self._is_running

	def is_paused(self) -> bool:
		with self._state_lock:
			return self._paused

	def ensure_start_page_ready(self) -> None:
		"""Ensure the embedded browser session opens the configured start URL."""

		start_url = self._get_resume_url() or _DEFAULT_START_URL
		if not start_url:
			return

		with self._state_lock:
			if self._start_page_ready and self._browser_session is not None:
				return
			running = self._is_running
			shutdown = self._shutdown

		if running or shutdown:
			return

		async def _warmup() -> str | None:
			session = await self._ensure_browser_session()
			try:
				await session.start()
			except Exception:
				self._logger.debug('Failed to start browser session during warmup', exc_info=True)
				raise

			try:
				await session.attach_all_watchdogs()
			except Exception:
				self._logger.debug('Failed to pre-attach browser watchdogs during warmup', exc_info=True)

			try:
				await session.navigate_to(start_url, new_tab=False)
			except Exception:
				self._logger.debug('Failed to warm up start URL %s', start_url, exc_info=True)
				raise

			try:
				return await session.get_current_page_url()
			except Exception:
				self._logger.debug('Failed to verify browser location after warmup', exc_info=True)
				return None

		try:
			future = asyncio.run_coroutine_threadsafe(_warmup(), self._loop)
			current_url = future.result(timeout=20)
		except Exception:
			self._logger.debug('Failed to prepare browser start page', exc_info=True)
			return

		if current_url and current_url.rstrip('/') != start_url.rstrip('/'):
			self._logger.debug(
				'Browser start page warmup navigated to %s instead of configured %s',
				current_url,
				start_url,
			)

		with self._state_lock:
			if self._browser_session is not None:
				self._start_page_ready = True

	def close_additional_tabs(self, refresh_url: str | None = None) -> None:
		"""
		Close all open tabs except the current focus and optionally refresh that tab.

		This is primarily used by the WebArena runner to guarantee that each task
		starts from a single, freshly loaded page even if the previous task spawned
		extra tabs.
		"""

		async def _close() -> None:
			session = await self._ensure_browser_session()
			# Enumerate tabs using the CDP helper for speed
			try:
				tabs = await session.get_tabs()
			except Exception:
				self._logger.debug('Failed to enumerate tabs before cleanup', exc_info=True)
				return

			current_target_id = session.agent_focus.target_id if session.agent_focus else None

			for tab in tabs:
				target_id = getattr(tab, 'target_id', None)
				if not target_id:
					continue
				if current_target_id and target_id == current_target_id:
					continue

				with suppress(Exception):
					await session._cdp_close_page(target_id)
					await session.event_bus.dispatch(TabClosedEvent(target_id=target_id))

			# If requested, reload the retained tab to ensure a fresh state
			if refresh_url:
				try:
					await session.navigate_to(refresh_url, new_tab=False)
				except Exception:
					self._logger.debug('Failed to refresh start page after tab cleanup', exc_info=True)

		future = asyncio.run_coroutine_threadsafe(_close(), self._loop)
		try:
			future.result(timeout=10)
		except Exception:
			self._logger.debug('Tab cleanup encountered an error', exc_info=True)

	def update_llm(self) -> None:
		"""Update the LLM instance based on current global settings."""
		try:
			new_llm = _create_selected_llm()
		except Exception as exc:
			raise AgentControllerError(f'新しいモデルの作成に失敗しました: {exc}') from exc

		async def _apply_update() -> None:
			with self._state_lock:
				old_llm = self._llm
				self._llm = new_llm

				if self._agent:
					self._agent.llm = new_llm
				if self._current_agent and self._current_agent is not self._agent:
					self._current_agent.llm = new_llm

			if old_llm:
				aclose = getattr(old_llm, 'aclose', None)
				if callable(aclose):
					with suppress(Exception):
						await aclose()

		future = asyncio.run_coroutine_threadsafe(_apply_update(), self._loop)
		try:
			future.result(timeout=10)
		except Exception as exc:
			raise AgentControllerError(f'モデルの更新処理に失敗しました: {exc}') from exc

	def reset(self) -> None:
		with self._state_lock:
			if self._is_running:
				raise AgentControllerError('エージェント実行中はリセットできません。')
		self._stop_browser_session()
		with self._state_lock:
			self._agent = None
			self._current_agent = None
			self._paused = False
			self._initial_prompt_handled = False
		self._set_resume_url(None)
		self._clear_step_message_ids()

	def set_vision_enabled(self, enabled: bool) -> None:
		with self._state_lock:
			self._vision_enabled = bool(enabled)

	def is_vision_enabled(self) -> bool:
		with self._state_lock:
			return self._vision_enabled

	def prepare_for_new_task(self) -> None:
		with self._state_lock:
			if self._is_running:
				raise AgentControllerError('エージェント実行中は新しいタスクを開始できません。')
			self._agent = None
			self._current_agent = None
			self._paused = False
			self._initial_prompt_handled = False
		self._clear_step_message_ids()

	def run(
		self,
		task: str,
		record_history: bool = True,
		additional_system_message: str | None = None,
		max_steps: int | None = None,
		background: bool = False,
		completion_callback: Callable[[AgentRunResult | Exception], None] | None = None,
	) -> AgentRunResult | None:
		if self._shutdown:
			raise AgentControllerError('エージェントコントローラーは停止済みです。')

		with self._lock:
			future = asyncio.run_coroutine_threadsafe(
				self._run_agent(
					task,
					record_history=record_history,
					additional_system_message=additional_system_message,
					max_steps_override=max_steps,
				),
				self._loop,
			)
			with self._state_lock:
				self._initial_prompt_handled = True

			if background:

				def _on_complete(f: Any) -> None:
					if not completion_callback:
						return
					try:
						result = f.result()
						completion_callback(result)
					except Exception as exc:
						completion_callback(exc)

				future.add_done_callback(_on_complete)
				return None

			try:
				return future.result()
			except AgentControllerError:
				raise
			except Exception as exc:
				raise AgentControllerError(str(exc)) from exc

	def has_handled_initial_prompt(self) -> bool:
		with self._state_lock:
			return self._initial_prompt_handled

	def evaluate_in_browser(self, script: str) -> Any:
		"""Execute JavaScript in the current browser session."""
		if not self._browser_session:
			raise AgentControllerError('ブラウザセッションが存在しません。')

		async def _eval() -> Any:
			try:
				session = await self._ensure_browser_session()
				# Ensure we have an active CDP session
				cdp_session = await session.get_or_create_cdp_session()
				result = await cdp_session.cdp_client.send.Runtime.evaluate(
					params={'expression': script, 'returnByValue': True, 'awaitPromise': True}, session_id=cdp_session.session_id
				)
				if 'exceptionDetails' in result:
					raise Exception(f'JS Evaluation failed: {result["exceptionDetails"]}')
				return result.get('result', {}).get('value')
			except Exception as e:
				self._logger.error(f'Failed to evaluate javascript: {e}')
				raise

		future = asyncio.run_coroutine_threadsafe(_eval(), self._loop)
		try:
			return future.result(timeout=10)
		except Exception as exc:
			raise AgentControllerError(f'JavaScriptの実行に失敗しました: {exc}') from exc

	def mark_initial_prompt_handled(self) -> None:
		with self._state_lock:
			self._initial_prompt_handled = True

	def shutdown(self) -> None:
		if self._shutdown:
			return
		self._shutdown = True
		with self._state_lock:
			self._agent = None
			self._current_agent = None
			self._paused = False
		self._set_resume_url(None)
		self._clear_step_message_ids()

		if self._loop.is_running():
			try:
				future = asyncio.run_coroutine_threadsafe(self._async_shutdown(), self._loop)
				future.result(timeout=5)
			except Exception:
				self._logger.debug('Failed to shut down agent loop cleanly', exc_info=True)
			finally:
				if self._loop.is_running():
					self._loop.call_soon_threadsafe(self._loop.stop)

		if self._thread.is_alive():
			self._thread.join(timeout=2)

		if self._cdp_cleanup:
			try:
				self._cdp_cleanup()
			finally:
				self._cdp_cleanup = None
