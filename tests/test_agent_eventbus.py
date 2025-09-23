import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_use.agent.service import Agent


def create_agent_with_id(identifier: str) -> Agent:
        agent = Agent.__new__(Agent)
        agent.id = identifier
        return agent


def test_generate_eventbus_name_from_uuid_like_string():
        agent = create_agent_with_id('0193093a-75d0-7f28-bcf1-38cfaa028ba4')

        assert hasattr(agent, '_generate_eventbus_name')
        name = agent._generate_eventbus_name()

        assert name.startswith('Agent_')
        assert name.isidentifier()
        # Ensure the suffix is derived from the sanitized identifier without hyphens
        filtered_id = re.sub(r'[^0-9A-Za-z_]', '', agent.id)
        assert name.endswith(filtered_id[-8:])


def test_generate_eventbus_name_from_non_alphanumeric_id():
        agent = create_agent_with_id('----')

        assert hasattr(agent, '_generate_eventbus_name')
        name = agent._generate_eventbus_name()

        assert name.startswith('Agent_')
        assert name.isidentifier()
        # With no alphanumeric characters we should get at least one character suffix from fallback UUID
        assert len(name) > len('Agent_')


def test_generate_eventbus_name_from_mixed_characters():
        agent = create_agent_with_id('😅-1234-ABCD-efgh')

        assert hasattr(agent, '_generate_eventbus_name')
        name = agent._generate_eventbus_name()

        assert name.startswith('Agent_')
        assert name.isidentifier()
        # Ensure only valid identifier characters remain after sanitization
        assert re.fullmatch(r'Agent_[0-9A-Za-z_]+', name)


def test_generate_eventbus_name_with_agent_prefix():
        agent = create_agent_with_id('Agent_-6855e970a142')

        name = agent._generate_eventbus_name()

        assert name.startswith('Agent_')
        assert name.isidentifier()
        assert name != 'Agent_-6855e970a142'
        assert '-6855' not in name


def test_generate_eventbus_name_with_prefixed_uuid_like_agent_id():
        agent = create_agent_with_id('Agent_7e30-8000-f091b1c8050b')

        name = agent._generate_eventbus_name()

        assert name.startswith('Agent_')
        assert name.isidentifier()

        sanitized = re.sub(r'[^0-9A-Za-z_]', '', agent.id)
        assert name == f'Agent_{sanitized[-8:]}'


def test_create_eventbus_recovers_from_invalid_name():
        agent = create_agent_with_id('ignored')

        def bad_name(self):
                return 'Agent_-invalid-name'

        agent._generate_eventbus_name = bad_name.__get__(agent, Agent)

        event_bus = agent._create_eventbus()

        assert event_bus.name.isidentifier()
        assert event_bus.name != 'Agent_-invalid-name'

        # Clean up to avoid leaking EventBus instances across tests
        asyncio.run(event_bus.stop(clear=True))
