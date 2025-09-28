import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from browser_use.agent.cloud_events import (
    CreateAgentSessionEvent,
    CreateAgentTaskEvent,
)
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
    agent._eventbus_cleanup_tasks = set()
    agent.enable_cloud_sync = False
    agent.cloud_sync = None
    agent.browser_session = SimpleNamespace(
        id='browser1234',
        agent_focus=None,
        event_bus=None,
        _watchdogs_attached=False,
    )
    agent.state = SimpleNamespace(follow_up_task=False)
    agent._message_manager = SimpleNamespace(add_new_task=lambda *_: None)
    agent.eventbus = EventBusFactory.create(agent_id=agent.id)[0]
    agent._refresh_browser_session_eventbus()
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
        assert agent.eventbus is not previous_bus
        assert agent.browser_session.event_bus is agent.eventbus
        assert agent._reserved_eventbus_name is None
        assert agent._pending_eventbus_refresh is False
    finally:
        asyncio.run(previous_bus.stop())
        asyncio.run(agent.eventbus.stop())


def test_reset_eventbus_stops_previous_bus_when_idle(monkeypatch) -> None:
    stop_calls: list[float | None] = []

    class DummyEventBus:
        def __init__(self, name: str) -> None:
            self.name = name

        async def stop(self, timeout: float | None = None) -> None:
            stop_calls.append(timeout)

        def on(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    agent = Agent.__new__(Agent)
    agent.id = 'test-agent-id'
    agent.task_id = agent.id
    agent._reserved_eventbus_name = 'Agent_old'
    agent._pending_eventbus_refresh = False
    agent.enable_cloud_sync = False
    agent.cloud_sync = None
    agent.browser_session = SimpleNamespace(id='browser1234', agent_focus=None)
    agent.state = SimpleNamespace(follow_up_task=False)
    agent._message_manager = SimpleNamespace(add_new_task=lambda *_: None)
    agent._eventbus_cleanup_tasks = set()

    previous_bus = DummyEventBus('OldBus')
    agent.eventbus = previous_bus

    releases: list[str | None] = []

    def fake_release(name: str | None) -> None:
        releases.append(name)

    new_bus = DummyEventBus('NewBus')

    monkeypatch.setattr('browser_use.agent.service.EventBusFactory.release', fake_release)
    monkeypatch.setattr(agent, '_create_eventbus', lambda force_random=True: (new_bus, 'Agent_new'))

    agent._reset_eventbus()

    assert stop_calls == [3.0]
    assert releases == ['Agent_old']
    assert agent.eventbus is new_bus
    assert agent.browser_session.event_bus is new_bus
    assert agent._reserved_eventbus_name == 'Agent_new'


def test_reset_eventbus_marks_watchdogs_for_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _dummy_agent()
    initial_bus = agent.eventbus

    session = SimpleNamespace(
        id='browser1234',
        agent_focus=None,
        event_bus=initial_bus,
        _watchdogs_attached=True,
    )
    for attr in Agent._WATCHDOG_ATTR_NAMES:
        setattr(session, attr, object())

    agent.browser_session = session

    class DummyEventBus:
        def __init__(self, name: str) -> None:
            self.name = name

        async def stop(self, *_: Any, **__: Any) -> None:
            pass

        def on(self, *_: Any, **__: Any) -> None:
            pass

    new_bus = DummyEventBus('Agent_new')

    monkeypatch.setattr(agent, '_create_eventbus', lambda force_random=True: (new_bus, 'Agent_new'))

    try:
        agent._reset_eventbus()

        assert agent.browser_session.event_bus is new_bus
        assert agent.browser_session._watchdogs_attached is False
        for attr in Agent._WATCHDOG_ATTR_NAMES:
            assert getattr(agent.browser_session, attr, None) is None
    finally:
        asyncio.run(initial_bus.stop())
        EventBusFactory.release('Agent_new')


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


def test_agent_identifier_is_normalised_for_eventbus() -> None:
    agent = Agent(task='男女比も知りたい', task_id='046-263f-7982-8000-7a6c04b16bcb', llm=None)

    try:
        assert agent.original_task_id == '046-263f-7982-8000-7a6c04b16bcb'
        assert agent.id == '046263f798280007a6c04b16bcb'
        assert agent.task_id == agent.id
        assert agent.eventbus.name.isidentifier()
        assert '-' not in agent.eventbus.name
        assert agent.browser_session.event_bus is agent.eventbus
    finally:
        reserved_name = agent._reserved_eventbus_name
        asyncio.run(agent.eventbus.stop())
        if reserved_name:
            EventBusFactory.release(reserved_name)


def test_follow_up_task_uses_identifier_eventbus() -> None:
    agent = Agent(task='男女比も知りたい', task_id='7101-8000-0582c16f66cc', llm=None)

    try:
        initial_bus = agent.eventbus
        assert initial_bus.name.isidentifier()
        assert '-' not in initial_bus.name
        assert agent.browser_session.event_bus is initial_bus

        agent.running = False
        agent.add_new_task('さらに詳しく')

        assert agent.eventbus is not initial_bus
        assert agent.eventbus.name.isidentifier()
        assert '-' not in agent.eventbus.name
        assert agent.browser_session.event_bus is agent.eventbus
    finally:
        final_name = agent._reserved_eventbus_name
        asyncio.run(initial_bus.stop())
        asyncio.run(agent.eventbus.stop())
        if final_name:
            EventBusFactory.release(final_name)


def test_add_new_task_renormalises_mutated_identifier() -> None:
    agent = Agent(task='人口推移を確認', task_id='95c0-2a1f-7f84', llm=None)

    try:
        initial_bus = agent.eventbus
        initial_name = initial_bus.name

        assert '-' not in initial_name
        assert agent.browser_session.event_bus is initial_bus

        agent.running = False
        agent.id = 'hyphenated-identifier'
        agent.task_id = agent.id

        agent.add_new_task('さらに詳しく教えて')

        assert agent.eventbus is not initial_bus
        assert agent.eventbus.name.isidentifier()
        assert '-' not in agent.eventbus.name
        assert agent.browser_session.event_bus is agent.eventbus
    finally:
        final_name = agent._reserved_eventbus_name
        asyncio.run(initial_bus.stop())
        asyncio.run(agent.eventbus.stop())
        EventBusFactory.release(initial_name)
        if final_name:
            EventBusFactory.release(final_name)


@pytest.mark.asyncio
async def test_run_recreates_eventbus_and_reemits_create_events(monkeypatch) -> None:
    class DummySignalHandler:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - simple stub
            self.reset_calls = 0

        def register(self) -> None:
            pass

        def unregister(self) -> None:
            pass

        def reset(self) -> None:
            self.reset_calls += 1

    class DummyEventBus:
        def __init__(self, name: str) -> None:
            self.name = name
            self.dispatched: list[Any] = []
            self.stopped_with: float | None = None

        async def stop(self, timeout: float | None = None) -> None:
            self.stopped_with = timeout

        def dispatch(self, event: Any) -> None:
            self.dispatched.append(event)

        def on(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    release_calls: list[str | None] = []

    def fake_release(name: str | None) -> None:
        release_calls.append(name)

    monkeypatch.setattr('browser_use.agent.service.SignalHandler', DummySignalHandler, raising=False)
    monkeypatch.setattr('browser_use.agent.service.EventBusFactory.release', fake_release)

    agent = Agent.__new__(Agent)
    agent.id = 'agent-run'
    agent.task_id = 'task-run'
    agent.session_id = 'session-run'
    agent.task = '調査して'
    agent.llm = SimpleNamespace(model_name='dummy-llm')
    agent.enable_cloud_sync = True
    agent._pending_eventbus_refresh = False
    agent._force_exit_telemetry_logged = False
    agent.telemetry = None
    agent._external_pause_event = asyncio.Event()

    class DummyState:
        def __init__(self) -> None:
            self.follow_up_task = False
            self.n_steps = 0
            self.session_initialized = False
            self.paused = False
            self.stopped = False
            self.consecutive_failures = 0
            self.last_result: list[Any] = []

        def model_dump(self) -> dict[str, Any]:
            return {}

    agent.state = DummyState()

    class DummyHistory:
        def __init__(self) -> None:
            self._done = False
            self._output_model_schema = None
            self.usage = None
            self.structured_output = None

        def is_done(self) -> bool:
            return self._done

        def add_item(self, *_: Any, **__: Any) -> None:
            pass

        def final_result(self) -> None:
            return None

    agent.history = DummyHistory()
    agent.output_model_schema = None
    agent.register_done_callback = None

    agent.settings = SimpleNamespace(
        max_failures=3,
        final_response_after_failure=True,
        step_timeout=0.1,
        generate_gif=False,
    )

    agent.token_cost_service = SimpleNamespace(
        get_usage_summary=lambda: asyncio.sleep(0, result={}),
        log_usage_summary=lambda: asyncio.sleep(0),
    )

    async def step_stub(_info: Any) -> None:
        agent.history._done = True
        agent.state.n_steps += 1

    agent.step = step_stub
    agent._log_agent_run = lambda: None
    agent._log_first_step_startup = lambda: None
    agent._log_agent_event = lambda *args, **kwargs: None
    agent.log_completion = lambda: asyncio.sleep(0)
    agent._execute_initial_actions = lambda: asyncio.sleep(0)

    browser_profile = SimpleNamespace(
        viewport={'width': 800, 'height': 600},
        user_agent='dummy-agent',
        headless=True,
        allowed_domains=[],
        downloads_path=None,
        keep_alive=True,
    )
    async def start_stub() -> None:
        return None

    agent.browser_session = SimpleNamespace(
        id='browser-session',
        cdp_url=None,
        agent_focus=None,
        start=start_stub,
        browser_profile=browser_profile,
    )

    agent.cloud_sync = SimpleNamespace(
        handle_event=lambda *_: None,
        auth_task=None,
        auth_client=SimpleNamespace(device_id='device-123'),
    )

    async def close_stub() -> None:
        return None

    agent.close = close_stub

    buses: list[DummyEventBus] = []

    def create_bus(*_: Any, **__: Any) -> tuple[DummyEventBus, str]:
        name = f'TestBus{len(buses)}'
        bus = DummyEventBus(name)
        buses.append(bus)
        return bus, name

    initial_bus, initial_name = create_bus()
    agent.eventbus = initial_bus
    agent._reserved_eventbus_name = initial_name

    monkeypatch.setattr(agent, '_create_eventbus', create_bus)

    await agent.run(max_steps=1)

    assert release_calls == [initial_name]
    assert isinstance(buses[0].dispatched[0], CreateAgentSessionEvent)
    assert isinstance(buses[0].dispatched[1], CreateAgentTaskEvent)

    assert buses[0].stopped_with == 3.0

    # Prepare for a follow-up run with a fresh session
    agent.history._done = False
    agent.state.session_initialized = False

    await agent.run(max_steps=1)

    assert release_calls == [initial_name, 'TestBus1']
    assert isinstance(buses[1].dispatched[0], CreateAgentSessionEvent)
    assert isinstance(buses[1].dispatched[1], CreateAgentTaskEvent)
    assert buses[1].stopped_with == 3.0

