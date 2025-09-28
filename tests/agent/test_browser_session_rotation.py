from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from bubus.service import QueueShutDown

from flask_app.app import BrowserAgentController


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
