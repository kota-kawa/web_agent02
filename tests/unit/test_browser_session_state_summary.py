import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from browser_use.browser.events import BrowserStateRequestEvent
from browser_use.browser.session import BrowserSession, ensure_browser_state_handler_registered
from browser_use.browser.views import BrowserStateSummary, TabInfo
from browser_use.dom.views import SerializedDOMState


class SessionTestDouble(BrowserSession):
        """Test double that avoids heavy watchdog setup while exercising summary logic."""

        def __init__(self) -> None:
                super().__init__()
                self._watchdogs_attached = False
                object.__setattr__(self, '_attach_invocations', 0)
                object.__setattr__(self, '_last_handler', None)
                summary = BrowserStateSummary(
                        dom_state=SerializedDOMState(_root=None, selector_map={}),
                        url='https://example.com',
                        title='Example',
                        tabs=[TabInfo(url='https://example.com', title='Example', target_id='target-1')],
                        screenshot=None,
                )
                object.__setattr__(self, '_summary', summary)

        def model_post_init(self, __context) -> None:  # pragma: no cover - base wiring skipped for tests
                return None

        async def attach_all_watchdogs(self) -> None:  # pragma: no cover - exercised via get_browser_state_summary
                object.__setattr__(self, '_attach_invocations', self._attach_invocations + 1)
                self.event_bus.handlers.pop(BrowserStateRequestEvent.__name__, None)

                async def _handler(event: BrowserStateRequestEvent) -> BrowserStateSummary:
                        return self._summary

                _handler.__name__ = 'DOMWatchdog.on_BrowserStateRequestEvent'

                self.event_bus.on(BrowserStateRequestEvent, _handler)
                object.__setattr__(self, '_last_handler', _handler)
                self._watchdogs_attached = True


def _run(coro):
        loop = asyncio.new_event_loop()
        try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(coro)
        finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                asyncio.set_event_loop(None)
                loop.close()


def test_browser_state_summary_initializes_handler_before_dispatch():
        session = SessionTestDouble()
        session.event_bus.handlers.pop(BrowserStateRequestEvent.__name__, None)

        _run(ensure_browser_state_handler_registered(session))

        assert session._attach_invocations == 1
        handlers = session.event_bus.handlers.get(BrowserStateRequestEvent.__name__, [])
        assert handlers and handlers[0] is session._last_handler
        assert session._watchdogs_attached is True


def test_browser_state_summary_recovers_missing_handler_between_runs():
        session = SessionTestDouble()

        _run(ensure_browser_state_handler_registered(session))
        assert session._attach_invocations == 1
        first_handler = session._last_handler

        session.event_bus.handlers.pop(BrowserStateRequestEvent.__name__, None)
        session._watchdogs_attached = True

        _run(ensure_browser_state_handler_registered(session))

        assert session._attach_invocations == 2
        handlers = session.event_bus.handlers.get(BrowserStateRequestEvent.__name__, [])
        assert handlers and handlers[0] is session._last_handler
        assert session._last_handler is not first_handler
        assert session._watchdogs_attached is True


def test_browser_state_summary_rebinds_when_handler_returns_none():
        session = SessionTestDouble()

        async def stale_handler(event: BrowserStateRequestEvent):
                return None

        stale_handler.__name__ = 'StaleHandler'

        session.event_bus.handlers[BrowserStateRequestEvent.__name__] = [stale_handler]
        session._watchdogs_attached = True

        _run(ensure_browser_state_handler_registered(session))

        assert session._attach_invocations == 1
        handlers = session.event_bus.handlers.get(BrowserStateRequestEvent.__name__, [])
        assert handlers and handlers[0] is session._last_handler
        assert session._watchdogs_attached is True

        summary = _run(handlers[0](BrowserStateRequestEvent()))
        assert summary is session._summary
