"""
Simple tests for agent configuration module.

These tests verify that the agent_config module is working correctly.
"""

import sys
from pathlib import Path

# Add parent directory to path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from agent_config import (
	get_agent_info,
	get_all_agents,
	get_agent_description,
	get_agent_display_name,
	get_agent_endpoint,
	suggest_agent_for_task,
)


def test_get_all_agents():
	"""Test getting all agents."""
	agents = get_all_agents()
	assert len(agents) == 3
	assert 'browser' in agents
	assert 'faq' in agents
	assert 'iot' in agents
	print('✓ test_get_all_agents passed')


def test_get_agent_info():
	"""Test getting specific agent info."""
	browser_agent = get_agent_info('browser')
	assert browser_agent is not None
	assert browser_agent.agent_id == 'browser'
	assert browser_agent.display_name == 'ブラウザエージェント'
	assert 'Web検索' in browser_agent.description
	
	# Test non-existent agent
	unknown = get_agent_info('unknown')  # type: ignore[arg-type]
	assert unknown is None
	print('✓ test_get_agent_info passed')


def test_get_agent_description():
	"""Test getting agent description."""
	faq_desc = get_agent_description('faq')
	assert 'ナレッジベース' in faq_desc
	assert '家電' in faq_desc
	
	# Test non-existent agent
	unknown_desc = get_agent_description('unknown')  # type: ignore[arg-type]
	assert unknown_desc == ''
	print('✓ test_get_agent_description passed')


def test_get_agent_display_name():
	"""Test getting agent display name."""
	iot_name = get_agent_display_name('iot')
	assert iot_name == 'IoTエージェント'
	
	# Test non-existent agent
	unknown_name = get_agent_display_name('unknown')  # type: ignore[arg-type]
	assert unknown_name == 'unknown'
	print('✓ test_get_agent_display_name passed')


def test_get_agent_endpoint():
	"""Test getting agent endpoint."""
	browser_endpoint = get_agent_endpoint('browser')
	assert browser_endpoint is not None
	assert 'http' in browser_endpoint.lower()
	
	# Test non-existent agent
	unknown_endpoint = get_agent_endpoint('unknown')  # type: ignore[arg-type]
	assert unknown_endpoint is None
	print('✓ test_get_agent_endpoint passed')


def test_suggest_agent_for_task():
	"""Test agent suggestion based on task description."""
	# Test IoT-related task
	iot_task = 'IoTデバイスの状態を確認して'
	suggestions = suggest_agent_for_task(iot_task)
	assert 'iot' in suggestions
	assert suggestions[0] == 'iot'  # Should be first suggestion
	
	# Test FAQ-related task
	faq_task = '家電の使い方を教えて'
	suggestions = suggest_agent_for_task(faq_task)
	assert 'faq' in suggestions
	assert suggestions[0] == 'faq'  # Should be first suggestion
	
	# Test browser-related task
	browser_task = 'Webで検索して情報を取得'
	suggestions = suggest_agent_for_task(browser_task)
	assert 'browser' in suggestions
	assert suggestions[0] == 'browser'  # Should be first suggestion
	
	# Test generic task (should return all agents)
	generic_task = 'これをやってください'
	suggestions = suggest_agent_for_task(generic_task)
	assert len(suggestions) == 3
	
	print('✓ test_suggest_agent_for_task passed')


def main():
	"""Run all tests."""
	print('Running agent_config tests...\n')
	
	try:
		test_get_all_agents()
		test_get_agent_info()
		test_get_agent_description()
		test_get_agent_display_name()
		test_get_agent_endpoint()
		test_suggest_agent_for_task()
		
		print('\n✓ All tests passed!')
		return 0
	except AssertionError as e:
		print(f'\n✗ Test failed: {e}')
		return 1
	except Exception as e:
		print(f'\n✗ Unexpected error: {e}')
		import traceback
		traceback.print_exc()
		return 1


if __name__ == '__main__':
	sys.exit(main())
