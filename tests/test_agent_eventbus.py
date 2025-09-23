import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_use.agent.service import Agent
from bubus import EventBus


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
        # Ensure the suffix is derived from the original identifier without hyphens
        filtered_id = ''.join(ch for ch in agent.id if ch.isalnum())
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
        assert re.fullmatch(r'Agent_[0-9A-Za-z]+', name)


def test_create_eventbus_falls_back_to_random_name():
        agent = create_agent_with_id('irrelevant')

        # Force _generate_eventbus_name to produce an invalid identifier first,
        # then a valid fallback when force_random=True is requested.
        def fake_generate(self, *, force_random: bool = False) -> str:
                return 'Agent_validfallback' if force_random else 'Agent_invalid-name'

        agent._generate_eventbus_name = types.MethodType(fake_generate, agent)  # type: ignore[attr-defined]

        bus = agent._create_eventbus()

        assert isinstance(bus, EventBus)
        assert bus.name == 'Agent_validfallback'
