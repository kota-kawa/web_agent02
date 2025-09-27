import asyncio
from typing import Final

import pytest
from bubus import EventBus

from browser_use.agent.eventbus import EventBusFactory

@pytest.fixture(autouse=True)
def clear_eventbus_names():
	"""Ensure tests run with a clean registry of EventBus identifiers."""

	EventBusFactory.clear_active_names()
	yield
	EventBusFactory.clear_active_names()


def test_create_eventbus_name_is_sanitized_and_identifier():
	async def _run() -> None:
		bus, name = EventBusFactory.create(agent_id='81-76b6-8000-532ca6389eae')

		try:
			assert name == bus.name
			assert name.isidentifier()
			assert '-' not in name
		finally:
			await bus.stop()
			EventBusFactory.release(name)

	asyncio.run(_run())


def test_create_eventbus_name_is_unique_when_reserved():
	async def _run() -> None:
		bus1, name1 = EventBusFactory.create(agent_id='duplicate-id')
		bus2, name2 = EventBusFactory.create(agent_id='duplicate-id')

		try:
			assert name1 != name2
		finally:
			await asyncio.gather(bus1.stop(), bus2.stop())
			EventBusFactory.release(name1)
			EventBusFactory.release(name2)

	asyncio.run(_run())


def test_sanitize_handles_problematic_input():
	sanitized = EventBusFactory.sanitize('Agent_-532ca6389eae')

	assert sanitized.startswith('Agent_')
	assert sanitized.isidentifier()
	assert '-' not in sanitized


def test_sanitize_normalizes_unicode_variants():
	sanitized = EventBusFactory.sanitize('Agent_７８ｄＦ–8000–test')

	assert sanitized == 'Agent_78dF_8000_test'
	assert sanitized.isidentifier()


def test_eventbus_constructor_auto_sanitizes_invalid_identifier():
	problematic: Final[str] = 'Agent_7101-8000-0582c16f66cc'

	async def _run() -> None:
		bus = EventBus(name=problematic)

		try:
			assert bus.name == 'Agent_7101_8000_0582c16f66cc'
		finally:
			await bus.stop()

	asyncio.run(_run())
