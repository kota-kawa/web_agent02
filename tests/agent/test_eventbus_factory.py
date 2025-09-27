import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from browser_use.agent.eventbus import EventBusFactory


@pytest.fixture(autouse=True)
def clear_eventbus_names():
    """Ensure tests run with a clean registry of EventBus identifiers."""

    EventBusFactory.clear_active_names()
    yield
    EventBusFactory.clear_active_names()


@pytest.mark.asyncio
async def test_create_eventbus_name_is_sanitized_and_identifier():
    bus, name = EventBusFactory.create(agent_id='81-76b6-8000-532ca6389eae')

    try:
        assert name == bus.name
        assert name.isidentifier()
        assert '-' not in name
    finally:
        await bus.stop()
        EventBusFactory.release(name)


@pytest.mark.asyncio
async def test_create_eventbus_name_is_unique_when_reserved():
    bus1, name1 = EventBusFactory.create(agent_id='duplicate-id')
    bus2, name2 = EventBusFactory.create(agent_id='duplicate-id')

    try:
        assert name1 != name2
    finally:
        await asyncio.gather(bus1.stop(), bus2.stop())
        EventBusFactory.release(name1)
        EventBusFactory.release(name2)


def test_sanitize_handles_problematic_input():
    sanitized = EventBusFactory.sanitize('Agent_-532ca6389eae')

    assert sanitized.startswith('Agent_')
    assert sanitized.isidentifier()
    assert '-' not in sanitized
