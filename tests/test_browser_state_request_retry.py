import types

import pytest

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.browser.views import BrowserStateSummary
from browser_use.dom.views import SerializedDOMState


@pytest.mark.asyncio
async def test_get_browser_state_summary_recovers_from_missing_results():
        session = BrowserSession(browser_profile=BrowserProfile())

        async def downloads_handler(event):
                return None

        downloads_handler.__name__ = 'DownloadsWatchdog.on_BrowserStateRequestEvent'
        session.event_bus.handlers['BrowserStateRequestEvent'] = [downloads_handler]
        session._watchdogs_attached = True  # type: ignore[attr-defined]

        async def fake_attach(self):
                async def dom_handler(event):
                        return BrowserStateSummary(
                                dom_state=SerializedDOMState(_root=None, selector_map={}),
                                url='https://example.com',
                                title='Example',
                                tabs=[],
                                screenshot=None,
                                page_info=None,
                        )

                dom_handler.__name__ = 'DOMWatchdog.on_BrowserStateRequestEvent'
                self.event_bus.handlers['BrowserStateRequestEvent'] = [downloads_handler, dom_handler]
                self._watchdogs_attached = True  # type: ignore[attr-defined]

        object.__setattr__(session, 'attach_all_watchdogs', types.MethodType(fake_attach, session))

        dispatch_results: list[BrowserStateSummary | Exception] = [
                ValueError(
                        'Expected at least one handler to return a non-None result, but none did! '
                        '?▶ BrowserStateRequestEvent#abcd ✅ -> {}'
                ),
                BrowserStateSummary(
                        dom_state=SerializedDOMState(_root=None, selector_map={}),
                        url='https://example.com',
                        title='Example',
                        tabs=[],
                        screenshot=None,
                        page_info=None,
                ),
        ]

        class FakeEvent:
                async def event_result(self, *args, **kwargs):
                        assert dispatch_results, 'No more dispatch results available'
                        result = dispatch_results.pop(0)
                        if isinstance(result, Exception):
                                raise result
                        return result

        def dispatch_stub(event):
                return FakeEvent()

        session.event_bus.dispatch = dispatch_stub  # type: ignore[attr-defined]

        result = await session.get_browser_state_summary(include_screenshot=False)

        assert not dispatch_results
        assert result.url == 'https://example.com'
        handler_names = [getattr(handler, '__name__', '') for handler in session.event_bus.handlers['BrowserStateRequestEvent']]
        assert any('DOMWatchdog' in name for name in handler_names)
