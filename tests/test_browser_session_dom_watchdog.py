import types

import pytest

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.browser.views import BrowserStateSummary
from browser_use.dom.views import SerializedDOMState


@pytest.mark.asyncio
async def test_get_browser_state_summary_reattaches_dom_watchdog(monkeypatch):
	session = BrowserSession(browser_profile=BrowserProfile())

	async def downloads_handler(event):  # pragma: no cover - simple stub
		return None

	downloads_handler.__name__ = 'DownloadsWatchdog.on_BrowserStateRequestEvent'
	session.event_bus.handlers['BrowserStateRequestEvent'] = [downloads_handler]

	async def dom_handler(event):  # pragma: no cover - simple stub
		return BrowserStateSummary(
			dom_state=SerializedDOMState(_root=None, selector_map={}),
			url='',
			title='',
			tabs=[],
			screenshot=None,
			page_info=None,
		)

	dom_handler.__name__ = 'DOMWatchdog.on_BrowserStateRequestEvent'

	async def fake_attach(self):
		self.event_bus.handlers['BrowserStateRequestEvent'] = [downloads_handler, dom_handler]
		self._watchdogs_attached = True  # type: ignore[attr-defined]

	object.__setattr__(session, 'attach_all_watchdogs', types.MethodType(fake_attach, session))

	class FakeEvent:
		async def event_result(self, *args, **kwargs):  # pragma: no cover - simple stub
			return BrowserStateSummary(
				dom_state=SerializedDOMState(_root=None, selector_map={}),
				url='',
				title='',
				tabs=[],
				screenshot=None,
				page_info=None,
			)

	def dispatch_stub(event):  # pragma: no cover - simple stub
		return FakeEvent()

	monkeypatch.setattr(session.event_bus, 'dispatch', dispatch_stub)

	original_handlers = session.event_bus.handlers['BrowserStateRequestEvent']

	await session.get_browser_state_summary(include_screenshot=False)

	handlers_after_first_call = session.event_bus.handlers['BrowserStateRequestEvent']
	assert len(handlers_after_first_call) == 2
	assert downloads_handler in handlers_after_first_call
	assert any('DOMWatchdog' in getattr(handler, '__name__', '') for handler in handlers_after_first_call)

	await session.get_browser_state_summary(include_screenshot=False)

	handlers_after_second_call = session.event_bus.handlers['BrowserStateRequestEvent']
	assert handlers_after_second_call is handlers_after_first_call
