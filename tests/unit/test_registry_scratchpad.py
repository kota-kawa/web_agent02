import pytest

from browser_use.agent.scratchpad import Scratchpad
from browser_use.tools.registry.service import Registry


@pytest.mark.asyncio
async def test_execute_action_accepts_scratchpad():
	registry = Registry()
	scratchpad = Scratchpad()

	@registry.action('uses scratchpad')
	async def use_pad(scratchpad: Scratchpad):
		scratchpad.add_entry('note', {'content': 'value'})
		return 'ok'

	result = await registry.execute_action('use_pad', params={}, scratchpad=scratchpad)

	assert result == 'ok'
	entry = scratchpad.get_entry('note')
	assert entry is not None
	assert entry.data == {'content': 'value'}
