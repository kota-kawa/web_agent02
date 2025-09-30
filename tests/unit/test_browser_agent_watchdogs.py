import asyncio
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from browser_use.agent.views import AgentHistoryList
from browser_use.browser.events import BrowserStateRequestEvent
from flask_app import app as app_module


def test_follow_up_reattaches_watchdogs(monkeypatch):
    stub_llm = object()
    monkeypatch.setattr(app_module, '_create_gemini_llm', lambda: stub_llm)

    class StubEventBus:
        def __init__(self) -> None:
            self.handlers: dict[str, list] = {}
            self.stopped = False

        async def stop(self, clear: bool = False, timeout: float = 1.0) -> None:  # noqa: ARG002
            self.stopped = True

    class StubSession:
        instances: list['StubSession'] = []

        def __init__(self, browser_profile) -> None:  # noqa: ANN001
            self.browser_profile = browser_profile
            self.browser_profile.keep_alive = True
            self.event_bus = StubEventBus()
            self.events: list[str] = []
            self.id = 'session-0001'
            self.agent_focus = SimpleNamespace(target_id='target-0001', target=None)
            self._watchdogs_attached = False
            StubSession.instances.append(self)

        async def attach_all_watchdogs(self) -> None:
            self.events.append('attach')
            self._watchdogs_attached = True

        async def stop(self) -> None:
            self.events.append('stop')

        async def drain_event_bus(self, *, timeout: float = 5.0) -> bool:  # noqa: ARG002
            self.events.append('drain')
            return True

    class FakeAgent:
        def __init__(self, task, browser_session, llm, register_new_step_callback, extend_system_message):  # noqa: ANN001
            self.task = task
            self.browser_session = browser_session
            self.llm = llm
            self.register_new_step_callback = register_new_step_callback
            self.extend_system_message = extend_system_message
            self.state = SimpleNamespace(
                n_steps=1,
                follow_up_task=False,
                paused=False,
                stopped=False,
                last_result=None,
            )
            self.history = AgentHistoryList(history=[])
            self.initial_actions = None
            self.initial_url = None

        def add_new_task(self, new_task: str) -> None:
            self.task = new_task
            self.browser_session.events.append('reset')

        def reset_completion_state(self) -> bool:
            return False

        def _convert_initial_actions(self, actions):  # noqa: ANN001
            return actions

        async def run(self, max_steps: int) -> AgentHistoryList:  # noqa: ARG002
            return self.history

    monkeypatch.setattr(app_module, 'BrowserSession', StubSession)
    monkeypatch.setattr(app_module, 'Agent', FakeAgent)

    controller = app_module.BrowserAgentController('ws://example', max_steps=3)
    try:
        asyncio.run(controller._run_agent('最初のタスク'))
        asyncio.run(controller._run_agent('フォローアップ'))
    finally:
        controller.shutdown()

    session = StubSession.instances[0]
    assert session.events.count('attach') >= 2
    reset_index = max(i for i, value in enumerate(session.events) if value == 'reset')
    attach_index = max(i for i, value in enumerate(session.events) if value == 'attach')
    assert reset_index < attach_index


def test_manual_event_bus_refresh_resets_watchdogs(monkeypatch):
    stub_llm = object()
    monkeypatch.setattr(app_module, '_create_gemini_llm', lambda: stub_llm)

    class StubEventBus:
        def __init__(self) -> None:
            self.handlers: dict[str, list] = {}
            self.stop_calls = 0

        async def stop(self, clear: bool = False, timeout: float = 1.0) -> None:  # noqa: ARG002
            self.stop_calls += 1
            if clear:
                self.handlers.clear()

        def on(self, event_class, handler) -> None:  # noqa: ANN001
            key = event_class if isinstance(event_class, str) else event_class.__name__
            self.handlers.setdefault(key, []).append(handler)

    class DOMWatchdog:
        def __init__(self, session: 'LegacySession') -> None:
            self.session = session

        async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent):  # noqa: ARG002
            self.session.dom_request_count += 1
            return f'summary-{self.session.dom_request_count}'

    class LegacySession:
        instances: list['LegacySession'] = []
        watchdog_attributes = (
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
        )

        def __init__(self, browser_profile) -> None:  # noqa: ANN001
            self.browser_profile = browser_profile
            self.browser_profile.keep_alive = True
            self.event_bus = StubEventBus()
            self.events: list[object] = []
            self.dom_request_count = 0
            self._watchdogs_attached = False
            self.model_post_init_calls = 0
            for attribute in self.watchdog_attributes:
                setattr(self, attribute, None)
            LegacySession.instances.append(self)

        async def attach_all_watchdogs(self) -> None:
            self.events.append('attach')
            key = BrowserStateRequestEvent.__name__
            self.event_bus.handlers[key] = []
            for attribute in self.watchdog_attributes:
                if attribute == '_dom_watchdog':
                    watchdog = DOMWatchdog(self)
                    setattr(self, attribute, watchdog)
                    self.event_bus.on(BrowserStateRequestEvent, watchdog.on_BrowserStateRequestEvent)
                else:
                    setattr(self, attribute, object())
            self._watchdogs_attached = True

        async def stop(self) -> None:
            self.events.append('stop')

        def model_post_init(self, __context) -> None:  # noqa: ANN001
            self.model_post_init_calls += 1
            self.events.append('model_post_init')

    class VerifyingAgent:
        def __init__(self, task, browser_session, llm, register_new_step_callback, extend_system_message):  # noqa: ANN001
            self.task = task
            self.browser_session = browser_session
            self.llm = llm
            self.register_new_step_callback = register_new_step_callback
            self.extend_system_message = extend_system_message
            self.state = SimpleNamespace(
                n_steps=1,
                follow_up_task=False,
                paused=False,
                stopped=False,
                last_result=None,
            )
            self.history = AgentHistoryList(history=[])
            self.initial_actions = None
            self.initial_url = None

        def add_new_task(self, new_task: str) -> None:
            self.task = new_task
            self.browser_session.events.append('reset')

        def reset_completion_state(self) -> bool:
            return False

        def _reset_eventbus(self) -> None:
            self.browser_session.events.append('agent_reset_bus')

        def _refresh_browser_session_eventbus(self, reset_watchdogs: bool = True) -> None:
            self.browser_session.events.append(('refresh', reset_watchdogs))

        async def run(self, max_steps: int) -> AgentHistoryList:  # noqa: ARG002
            handlers = self.browser_session.event_bus.handlers.get(BrowserStateRequestEvent.__name__, [])
            summaries: list[str] = []
            for handler in handlers:
                result = handler(BrowserStateRequestEvent())
                if inspect.isawaitable(result):
                    result = await result
                summaries.append(result)
            self.browser_session.events.append(('state', summaries))
            return self.history

    monkeypatch.setattr(app_module, 'BrowserSession', LegacySession)
    monkeypatch.setattr(app_module, 'Agent', VerifyingAgent)
    monkeypatch.setattr(app_module, 'EventBus', StubEventBus)

    controller = app_module.BrowserAgentController('ws://example', max_steps=3)
    try:
        asyncio.run(controller._run_agent('最初のタスク'))
        session = LegacySession.instances[0]

        assert session._watchdogs_attached is False
        for attribute in LegacySession.watchdog_attributes:
            assert getattr(session, attribute) is None

        asyncio.run(controller._run_agent('フォローアップ'))
    finally:
        controller.shutdown()

    attach_events = [event for event in session.events if event == 'attach']
    assert len(attach_events) >= 2

    state_events = [event for event in session.events if isinstance(event, tuple) and event[0] == 'state']
    assert state_events and state_events[-1][1] == [f'summary-{session.dom_request_count}']
    assert session.dom_request_count >= 2
