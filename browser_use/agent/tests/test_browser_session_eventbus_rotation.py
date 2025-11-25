from __future__ import annotations

import asyncio
from dataclasses import dataclass

from bubus import EventBus

from browser_use.agent.service import Agent
from browser_use.config import CONFIG


class DummyLLM:
	model = 'dummy-model'
	_verified_api_keys = True

	@property
	def provider(self) -> str:
		return 'dummy'

	@property
	def name(self) -> str:
		return 'dummy'

	@property
	def model_name(self) -> str:
		return self.model

	async def ainvoke(self, messages, output_format=None):  # pragma: no cover - unused by this test
		raise NotImplementedError


@dataclass
class StubBrowserProfile:
	downloads_path: str | None = None
	keep_alive: bool = True
	allowed_domains: list[str] | None = None
	viewport: dict[str, int] | None = None
	user_agent: str = 'stub-agent'
	headless: bool = True
	wait_between_actions: int = 0


class FlakyBrowserSession:
	"""A BrowserSession stub whose handler registration fails on the first attempt."""

	def __init__(self) -> None:
		self.browser_profile = StubBrowserProfile(allowed_domains=[], viewport={'width': 1280, 'height': 720})
		self.id = 'flaky-session'
		self.cdp_url = None
		self.agent_focus = None
		self.event_bus: EventBus = EventBus()
		self.downloaded_files: list[str] = []
		self._watchdogs_attached = False
		self._cached_browser_state_summary = None
		self._failures_remaining = 1

		def _start_handler(event):  # pragma: no cover - handler never executed in test
			return None

		_start_handler.__name__ = 'on_BrowserStartEvent'
		self._start_handler = _start_handler

	async def start(self) -> None:  # pragma: no cover - unused by this test
		return None

	async def kill(self) -> None:  # pragma: no cover - unused by this test
		return None

	def model_post_init(self, __context) -> None:
		if self._failures_remaining > 0:
			self._failures_remaining -= 1
			raise RuntimeError('Simulated duplicate handler registration')

		handlers = self.event_bus.handlers.setdefault('BrowserStartEvent', [])
		if self._start_handler not in handlers:
			handlers.append(self._start_handler)

	async def attach_all_watchdogs(self) -> None:
		handlers = self.event_bus.handlers.setdefault('BrowserStartEvent', [])
		if self._start_handler not in handlers:
			handlers.append(self._start_handler)

	async def get_browser_state_summary(self, *args, **kwargs):
		handlers = self.event_bus.handlers.get('BrowserStartEvent', [])
		if not handlers:
			raise ValueError('Expected at least one handler to handle BrowserStartEvent')
		return object()


def test_browser_session_eventbus_rotates_after_handler_registration_failure():
	original_cloud_sync = CONFIG.BROWSER_USE_CLOUD_SYNC
	CONFIG.BROWSER_USE_CLOUD_SYNC = False

	async def _exercise() -> None:
		session = FlakyBrowserSession()
		agent = Agent(
			task='test task',
			llm=DummyLLM(),
			browser_session=session,
			calculate_cost=False,
			directly_open_url=False,
		)

		await session.get_browser_state_summary()

		session._failures_remaining = 1
		agent._refresh_browser_session_eventbus()

		await session.get_browser_state_summary()

		handlers = session.event_bus.handlers.get('BrowserStartEvent', [])
		assert handlers, 'Expected BrowserStartEvent handler to be registered after fallback rotation'

	try:
		asyncio.run(_exercise())
	finally:
		CONFIG.BROWSER_USE_CLOUD_SYNC = original_cloud_sync
