import asyncio

import pytest

from browser_use.agent.eventbus import EventBusFactory


@pytest.fixture(autouse=True)
def clear_eventbus_names() -> None:
        """Ensure each test runs with a clean registry of active names."""

        EventBusFactory.clear_active_names()
        yield
        EventBusFactory.clear_active_names()


def test_create_eventbus_name_is_sanitized_and_identifier() -> None:
        async def _run() -> None:
                bus, name = EventBusFactory.create(agent_id='81-76b6-8000-532ca6389eae')

                try:
                        assert name == bus.name
                        assert name.startswith('Agent_')
                        assert name.isidentifier()
                        assert '-' not in name
                finally:
                        await bus.stop()
                        EventBusFactory.release(name)

        asyncio.run(_run())


def test_create_eventbus_name_is_unique_when_reserved() -> None:
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


def test_release_removes_name_from_registry() -> None:
        async def _run() -> None:
                bus, name = EventBusFactory.create(agent_id='same-id')

                try:
                        assert name in EventBusFactory._ACTIVE_NAMES
                finally:
                        await bus.stop()

                EventBusFactory.release(name)

                assert name not in EventBusFactory._ACTIVE_NAMES

        asyncio.run(_run())


def test_sanitize_handles_problematic_input() -> None:
        sanitized = EventBusFactory.sanitize('Agent_-532ca6389eae')

        assert sanitized.startswith('Agent_')
        assert sanitized.isidentifier()
        assert '-' not in sanitized


def test_sanitize_normalizes_unicode_variants() -> None:
        sanitized = EventBusFactory.sanitize('Agent_７８ｄＦ–8000–test')

        assert sanitized == 'Agent_78dF_8000_test'
        assert sanitized.isidentifier()


def test_sanitize_generates_random_for_empty_input() -> None:
        sanitized = EventBusFactory.sanitize('')

        assert sanitized.startswith('Agent_')
        assert sanitized.isidentifier()
