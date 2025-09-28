import asyncio
from types import SimpleNamespace

import pytest
from bubus import EventBus

from browser_use.agent.eventbus import EventBusFactory
from browser_use.agent.service import Agent


@pytest.fixture(autouse=True)
def _clear_eventbus_registry() -> None:
    EventBusFactory.clear_active_names()
    yield
    EventBusFactory.clear_active_names()


def _dummy_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent.id = 'test-agent-id'
    agent.task_id = agent.id
    agent._reserved_eventbus_name = 'Agent_old'
    agent._pending_eventbus_refresh = True
    agent.enable_cloud_sync = False
    agent.cloud_sync = None
    agent.browser_session = SimpleNamespace(id='browser1234', agent_focus=None)
    agent.state = SimpleNamespace(follow_up_task=False)
    agent._message_manager = SimpleNamespace(add_new_task=lambda *_: None)
    agent.eventbus = EventBus()
    return agent


def test_reset_eventbus_falls_back_to_anonymous(monkeypatch) -> None:
    agent = _dummy_agent()
    previous_bus = agent.eventbus

    release_calls: list[str | None] = []

    def fake_release(name: str | None) -> None:
        release_calls.append(name)

    def fail_create(*_: object, **__: object) -> None:
        raise AssertionError('invalid identifier')

    monkeypatch.setattr('browser_use.agent.service.EventBusFactory.release', fake_release)
    monkeypatch.setattr('browser_use.agent.service.EventBusFactory.create', fail_create)

    try:
        agent._reset_eventbus()

        assert release_calls == ['Agent_old']
        assert isinstance(agent.eventbus, EventBus)
        assert agent.eventbus is not previous_bus
        assert agent._reserved_eventbus_name is None
        assert agent._pending_eventbus_refresh is False
    finally:
        asyncio.run(previous_bus.stop())
        asyncio.run(agent.eventbus.stop())


def test_add_new_task_resets_eventbus_when_idle(monkeypatch) -> None:
    agent = _dummy_agent()
    agent.running = False

    reset_calls: list[None] = []

    def fake_reset() -> None:
        reset_calls.append(None)

    monkeypatch.setattr(agent, '_reset_eventbus', fake_reset)

    try:
        agent.add_new_task('さらに詳しく教えて')

        assert reset_calls == [None]
    finally:
        asyncio.run(agent.eventbus.stop())


def test_follow_up_task_uses_identifier_eventbus() -> None:
    agent = Agent(task='男女比も知りたい', task_id='7101-8000-0582c16f66cc', llm=None)

    try:
        initial_bus = agent.eventbus
        assert initial_bus.name.isidentifier()
        assert '-' not in initial_bus.name

        agent.running = False
        agent.add_new_task('さらに詳しく')

        assert agent.eventbus is not initial_bus
        assert agent.eventbus.name.isidentifier()
        assert '-' not in agent.eventbus.name
    finally:
        final_name = agent._reserved_eventbus_name
        asyncio.run(initial_bus.stop())
        asyncio.run(agent.eventbus.stop())
        EventBusFactory.release(final_name)
