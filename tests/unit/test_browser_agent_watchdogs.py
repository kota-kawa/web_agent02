import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from browser_use.agent.views import AgentHistoryList
from flask_app import app as app_module


@pytest.mark.asyncio
async def test_follow_up_reattaches_watchdogs(monkeypatch):
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
        await controller._run_agent('最初のタスク')
        await controller._run_agent('フォローアップ')
    finally:
        controller.shutdown()

    session = StubSession.instances[0]
    assert session.events.count('attach') >= 2
    reset_index = max(i for i, value in enumerate(session.events) if value == 'reset')
    attach_index = max(i for i, value in enumerate(session.events) if value == 'attach')
    assert reset_index < attach_index
