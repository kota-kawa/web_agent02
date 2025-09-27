import asyncio
from types import SimpleNamespace

from bubus import EventBus

from browser_use.agent.service import Agent


def _dummy_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent.id = 'test-agent-id'
    agent.task_id = agent.id
    agent._reserved_eventbus_name = 'Agent_old'
    agent._pending_eventbus_refresh = True
    agent.enable_cloud_sync = False
    agent.cloud_sync = None
    agent.browser_session = SimpleNamespace(id='browser1234', agent_focus=None)
    agent.eventbus = EventBus()
    return agent


def test_reset_eventbus_falls_back_to_anonymous(monkeypatch):
    agent = _dummy_agent()
    previous_bus = agent.eventbus

    release_calls: list[str | None] = []

    def fake_release(name: str | None) -> None:
        release_calls.append(name)

    def fail_create(*_, **__):
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
