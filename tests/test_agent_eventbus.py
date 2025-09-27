import asyncio
import re
import sys
import types
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from browser_use.agent.service import Agent
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from bubus import EventBus


def create_agent_with_id(identifier: str) -> Agent:
    agent = Agent.__new__(Agent)
    agent.id = identifier
    return agent


def test_generate_eventbus_name_from_uuid_like_string() -> None:
    agent = create_agent_with_id("0193093a-75d0-7f28-bcf1-38cfaa028ba4")

    assert hasattr(agent, "_generate_eventbus_name")
    name = agent._generate_eventbus_name()

    assert name.startswith("Agent_")
    assert name.isidentifier()
    filtered_id = "".join(ch for ch in agent.id if ch.isalnum())
    assert name.endswith(filtered_id[-8:])


def test_generate_eventbus_name_from_non_alphanumeric_id() -> None:
    agent = create_agent_with_id("----")

    assert hasattr(agent, "_generate_eventbus_name")
    name = agent._generate_eventbus_name()

    assert name.startswith("Agent_")
    assert name.isidentifier()
    assert len(name) > len("Agent_")


def test_generate_eventbus_name_from_mixed_characters() -> None:
    agent = create_agent_with_id("😅-1234-ABCD-efgh")

    assert hasattr(agent, "_generate_eventbus_name")
    name = agent._generate_eventbus_name()

    assert name.startswith("Agent_")
    assert name.isidentifier()
    assert re.fullmatch(r"Agent_[0-9A-Za-z]+", name)


def test_create_eventbus_falls_back_when_sanitizer_is_disabled() -> None:
	agent = create_agent_with_id("irrelevant")

	call_counts = {"default": 0, "random": 0}

	def fake_generate(self, *, force_random: bool = False) -> str:  # noqa: D401
		if force_random:
			call_counts["random"] += 1
			return "Agent_validfallback"
		call_counts["default"] += 1
		return "Agent_invalid-name"

	def passthrough(self, name: str) -> str:  # noqa: D401
		return name

	agent._generate_eventbus_name = types.MethodType(fake_generate, agent)  # type: ignore[attr-defined]
	agent._sanitize_eventbus_name = types.MethodType(passthrough, agent)  # type: ignore[attr-defined]

	bus = agent._create_eventbus()

	assert isinstance(bus, EventBus)
	assert bus.name == "Agent_validfallback"
	assert call_counts["random"] == 1


class DummyLLM:
    """Minimal async LLM stub that exposes the attributes Agent expects."""

    model = "dummy-model"
    provider = "dummy-provider"

    async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:  # noqa: D401, ANN401
        class _Result:
            usage = None

        return _Result()


def _make_agent(**kwargs: Any) -> Agent:
    profile = BrowserProfile(cdp_url="http://example.com")
    session = BrowserSession(browser_profile=profile)
    return Agent(task="initial", llm=DummyLLM(), browser_session=session, **kwargs)


def _stop_eventbus(bus) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(bus.stop(clear=True))
    else:
        loop.run_until_complete(bus.stop(clear=True))


def test_add_new_task_assigns_unique_eventbus_names() -> None:
    agent = _make_agent()

    created_buses = [agent.eventbus]
    first_name = agent.eventbus.name
    assert first_name.isidentifier()

    agent.running = False
    agent.add_new_task("follow-up one")
    created_buses.append(agent.eventbus)
    second_name = agent.eventbus.name
    assert second_name.isidentifier()
    assert second_name != first_name

    agent.add_new_task("follow-up two")
    created_buses.append(agent.eventbus)
    third_name = agent.eventbus.name
    assert third_name.isidentifier()
    assert third_name not in {first_name, second_name}

    for bus in created_buses:
        _stop_eventbus(bus)


def test_add_new_task_with_hyphenated_task_id() -> None:
    agent = _make_agent()

    original_bus = agent.eventbus
    followup_bus = original_bus
    try:
        agent.id = "Agent_8ea-7274-8000-3eb4154ed3e4"
        agent.task_id = agent.id
        agent.add_new_task("follow-up hyphen id")
        followup_bus = agent.eventbus
        assert followup_bus is not original_bus
        assert followup_bus.name.isidentifier()
        assert "-" not in followup_bus.name
    finally:
        for bus in {original_bus, followup_bus}:
            _stop_eventbus(bus)


def test_agents_with_same_task_id_get_unique_eventbus_names() -> None:
    primary = _make_agent()
    secondary = _make_agent(task_id=primary.id)

    try:
        assert primary.eventbus.name.isidentifier()
        assert secondary.eventbus.name.isidentifier()
        assert primary.eventbus.name != secondary.eventbus.name
    finally:
        _stop_eventbus(primary.eventbus)
        _stop_eventbus(secondary.eventbus)


def test_add_new_task_during_run_defers_eventbus_refresh() -> None:
    agent = _make_agent()

    original_bus = agent.eventbus
    original_name = original_bus.name

    agent.running = True
    agent.add_new_task("defer refresh")

    assert agent._pending_eventbus_refresh is True
    assert agent.eventbus is original_bus

    agent.running = False
    _stop_eventbus(original_bus)
    agent._reset_eventbus()
    refreshed_bus = agent.eventbus

    assert refreshed_bus is not original_bus
    assert refreshed_bus.name != original_name
    assert refreshed_bus.name.isidentifier()
    assert agent._pending_eventbus_refresh is False

    _stop_eventbus(refreshed_bus)


def test_create_eventbus_sanitizes_invalid_names() -> None:
        agent = _make_agent()

        calls = {"random": 0}

        def always_invalid(self, *, force_random: bool = False) -> str:  # noqa: D401
                if force_random:
                        calls["random"] += 1
                return "Agent_c0c-5d6e-79d3-8000-5738eda3c6a7"

        agent._generate_eventbus_name = types.MethodType(always_invalid, agent)  # type: ignore[attr-defined]

        bus = agent._create_eventbus()

        try:
                assert isinstance(bus, EventBus)
                assert bus.name.isidentifier()
                assert "-" not in bus.name
                assert calls["random"] == 0
        finally:
                _stop_eventbus(bus)


def test_create_eventbus_recovers_when_all_candidates_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
        agent = _make_agent()

        def always_invalid(self, *, force_random: bool = False) -> str:  # noqa: D401
                return "Agent_invalid-name"

        def passthrough(self, name: str) -> str:  # noqa: D401
                return name

        agent._generate_eventbus_name = types.MethodType(always_invalid, agent)  # type: ignore[attr-defined]
        agent._sanitize_eventbus_name = types.MethodType(passthrough, agent)  # type: ignore[attr-defined]

        bus = agent._create_eventbus()

        try:
                assert isinstance(bus, EventBus)
                assert bus.name.startswith("EventBus_")
        finally:
                _stop_eventbus(bus)


def test_add_new_task_recovers_from_invalid_sanitizer(monkeypatch) -> None:
        call_count = {"sanitize": 0}

        def always_hyphenated(self, name: str) -> str:  # noqa: D401
                call_count["sanitize"] += 1
                return "Agent-invalid-name"

        monkeypatch.setattr(Agent, "_sanitize_eventbus_name", always_hyphenated, raising=False)

        agent = _make_agent()

        created_buses = [agent.eventbus]

        try:
                for index in range(3):
                        agent.running = False
                        agent.add_new_task(f"retry-{index}")
                        created_buses.append(agent.eventbus)
                        assert agent.eventbus.name.isidentifier()
        finally:
                for bus in set(created_buses):
                        _stop_eventbus(bus)

        assert call_count["sanitize"] > 0
