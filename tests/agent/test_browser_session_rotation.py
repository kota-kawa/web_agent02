from __future__ import annotations

import asyncio
import logging
from types import MethodType, SimpleNamespace

import pytest
from bubus import EventBus
from bubus.service import QueueShutDown

import flask_app.app as controller_module
from flask_app.app import BrowserAgentController
from browser_use.browser.events import BrowserStateRequestEvent
from browser_use.browser.session import BrowserSession
from browser_use.browser.views import BrowserStateSummary


class _DummyProfile:
    def __init__(self, **kwargs: object) -> None:
        self.keep_alive = kwargs.get('keep_alive', True)
        self.cdp_url = kwargs.get('cdp_url')


class _DummyEventBus:
    async def stop(self, **_: object) -> None:  # pragma: no cover - simple stub
        await asyncio.sleep(0)


class _DummySession:
    _counter = 0

    def __init__(self, *, browser_profile: _DummyProfile) -> None:
        type(self)._counter += 1
        self.identifier = type(self)._counter
        self.browser_profile = browser_profile
        self.event_bus = _DummyEventBus()
        self.stop_calls: int = 0
        self.kill_calls: int = 0

    async def attach_all_watchdogs(self) -> None:  # pragma: no cover - simple stub
        await asyncio.sleep(0)

    async def drain_event_bus(self) -> bool:  # pragma: no cover - default stub
        return True

    async def stop(self) -> None:
        self.stop_calls += 1
        await asyncio.sleep(0)

    async def kill(self) -> None:
        self.kill_calls += 1
        await asyncio.sleep(0)


class _DummyAgent:
    def __init__(
        self,
        *,
        task: str,
        browser_session: _DummySession,
        llm: object,
        register_new_step_callback,
        extend_system_message: str,
    ) -> None:
        self.task = task
        self.browser_session = browser_session
        self.llm = llm
        self.register_new_step_callback = register_new_step_callback
        self.extend_system_message = extend_system_message
        self.initial_actions: list[object] | None = []
        self.initial_url: str | None = None
        self.state = SimpleNamespace(
            last_result=[],
            follow_up_task=False,
            n_steps=1,
            paused=False,
            stopped=False,
        )
        self.history = SimpleNamespace(history=[], usage=None)

    def _convert_initial_actions(self, actions: list[object]) -> list[object]:
        return actions

    def add_new_task(self, task: str) -> None:
        self.task = task

    async def run(self, max_steps: int) -> SimpleNamespace:  # pragma: no cover - deterministic stub
        return SimpleNamespace(history=[], usage=None)


@pytest.mark.asyncio
async def test_browser_session_rotates_after_event_bus_shutdown(monkeypatch) -> None:
    monkeypatch.setattr('flask_app.app._create_gemini_llm', lambda: object())
    monkeypatch.setattr('flask_app.app.BrowserProfile', _DummyProfile)
    monkeypatch.setattr('flask_app.app.BrowserSession', _DummySession)
    monkeypatch.setattr('flask_app.app.Agent', _DummyAgent)

    controller = BrowserAgentController(cdp_url='ws://example', max_steps=1)

    try:
        first_session = await controller._ensure_browser_session()

        async def _raise_shutdown(_: _DummySession) -> bool:
            raise QueueShutDown('event bus closed')

        monkeypatch.setattr(_DummySession, 'drain_event_bus', _raise_shutdown, raising=False)

        await controller._run_agent('do nothing')

        new_session = await controller._ensure_browser_session()

        assert new_session is not first_session
    finally:
        controller.shutdown()


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.stop_calls = 0

    async def stop(self, **_: object) -> None:
        self.stop_calls += 1

    def publish(self, event: str) -> None:
        self.events.append(event)


class _LegacySession:
    def __init__(self, *, browser_profile: _DummyProfile) -> None:
        self.browser_profile = browser_profile
        self.event_bus = controller_module.EventBus()
        self.stop_calls = 0
        self.model_post_init_calls = 0
        self.reset_event_bus_calls = 0

    async def attach_all_watchdogs(self) -> None:  # pragma: no cover - simple stub
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self.stop_calls += 1
        await asyncio.sleep(0)

    def model_post_init(self, _: object) -> None:
        self.model_post_init_calls += 1

    def _reset_event_bus_state(self) -> None:
        self.reset_event_bus_calls += 1
        self.event_bus = controller_module.EventBus()


class _RecordingAgent:
    def __init__(
        self,
        *,
        task: str,
        browser_session: _LegacySession,
        llm: object,
        register_new_step_callback,
        extend_system_message: str,
    ) -> None:
        self.task = task
        self.browser_session = browser_session
        self.llm = llm
        self.register_new_step_callback = register_new_step_callback
        self.extend_system_message = extend_system_message
        self.initial_actions: list[object] | None = []
        self.initial_url: str | None = None
        self.state = SimpleNamespace(
            last_result=[],
            follow_up_task=False,
            n_steps=1,
            paused=False,
            stopped=False,
        )
        self.history = SimpleNamespace(history=[], usage=None)
        self.run_calls = 0
        self._eventbus_history: list[_RecordingEventBus] = []
        self._reset_eventbus_calls = 0
        self._refresh_calls = 0
        self._refresh_kwargs: dict[str, object] | None = None
        self.eventbus = controller_module.EventBus()
        self.browser_session.event_bus = self.eventbus
        self._eventbus_history.append(self.eventbus)

    def _convert_initial_actions(self, actions: list[object]) -> list[object]:
        return actions

    def add_new_task(self, task: str) -> None:
        self.task = task

    async def run(self, max_steps: int) -> SimpleNamespace:  # pragma: no cover - deterministic stub
        self.run_calls += 1
        self.eventbus.publish(f'run-{self.run_calls}')
        return SimpleNamespace(history=[], usage=None)

    def _reset_eventbus(self) -> None:
        self._reset_eventbus_calls += 1
        new_bus = controller_module.EventBus()
        self.eventbus = new_bus
        self.browser_session.event_bus = new_bus
        self._eventbus_history.append(new_bus)

    def _refresh_browser_session_eventbus(self, *, reset_watchdogs: bool = True) -> None:
        self._refresh_calls += 1
        self._refresh_kwargs = {'reset_watchdogs': reset_watchdogs}
        self.browser_session.event_bus = self.eventbus


@pytest.mark.asyncio
async def test_legacy_session_resynchronises_agent_event_bus(monkeypatch) -> None:
    monkeypatch.setattr('flask_app.app._create_gemini_llm', lambda: object())
    monkeypatch.setattr(controller_module, 'EventBus', _RecordingEventBus)
    monkeypatch.setattr(controller_module, 'BrowserProfile', _DummyProfile)
    monkeypatch.setattr(controller_module, 'BrowserSession', _LegacySession)
    monkeypatch.setattr(controller_module, 'Agent', _RecordingAgent)

    controller = BrowserAgentController(cdp_url='ws://example', max_steps=1)

    try:
        await controller._run_agent('first task')
        agent = controller._agent
        assert agent is not None
        assert controller._browser_session is not None
        assert controller._browser_session.reset_event_bus_calls == 1
        assert agent.eventbus is agent.browser_session.event_bus
        assert agent._reset_eventbus_calls >= 1
        assert agent._eventbus_history[0].events == ['run-1']

        await controller._run_agent('second task')
        assert agent.eventbus is agent.browser_session.event_bus
        assert 'run-2' in agent._eventbus_history[1].events
    finally:
        controller.shutdown()


@pytest.mark.asyncio
async def test_get_browser_state_summary_recovers_from_value_error(caplog) -> None:
    session = BrowserSession()

    summary = BrowserStateSummary(
        dom_state=SimpleNamespace(selector_map={1: object()}),
        url='https://example.com',
        title='Example',
        tabs=[],
    )

    class _DummyEvent:
        def __init__(self, should_error: bool) -> None:
            self._should_error = should_error
            self._call_count = 0

        async def event_result(self, *_: object, **__: object) -> BrowserStateSummary:
            self._call_count += 1
            if self._should_error and self._call_count == 1:
                raise ValueError('Expected at least one handler to return BrowserStateSummary')
            return summary

    dispatch_count = 0

    def _fake_dispatch(self: EventBus, event: BrowserStateRequestEvent) -> _DummyEvent:
        nonlocal dispatch_count
        dispatch_count += 1
        return _DummyEvent(should_error=dispatch_count == 1)

    session.event_bus.handlers[BrowserStateRequestEvent.__name__] = [object()]
    session.event_bus.dispatch = MethodType(_fake_dispatch, session.event_bus)
    setattr(session, '_watchdogs_attached', True)

    attach_calls = 0

    async def _fake_attach_all_watchdogs(self: BrowserSession) -> None:
        nonlocal attach_calls
        attach_calls += 1

    session.__dict__['attach_all_watchdogs'] = MethodType(_fake_attach_all_watchdogs, session)

    caplog.set_level(logging.WARNING)

    result = await session.get_browser_state_summary(include_screenshot=False)

    assert result == summary
    assert attach_calls == 1
    assert 'handler_count=1' in caplog.text
