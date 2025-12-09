import pytest

from flask_app.app import app


@pytest.fixture
def client():
	app.config['TESTING'] = True
	with app.test_client() as client:
		yield client


def test_check_history_no_action(client, mocker):
	"""Test that no action is taken when needs_action is False."""
	mocker.patch(
		'flask_app.app._analyze_conversation_history',
		return_value={
			'needs_action': False,
			'should_reply': False,
			'reply': '',
			'addressed_agents': [],
			'task_description': None,
			'reason': 'No action needed',
		},
	)

	response = client.post('/api/check-conversation-history', json={'history': [{'role': 'user', 'content': 'Hello'}]})
	assert response.status_code == 200
	data = response.get_json()
	assert data['action_taken'] is False
	assert data['should_reply'] is False


def test_check_history_action_proposed_but_not_executed(client, mocker):
	"""Test that action is proposed but NOT executed automatically."""
	mocker.patch(
		'flask_app.app._analyze_conversation_history',
		return_value={
			'needs_action': True,
			'should_reply': False,  # Initially False
			'reply': '',
			'addressed_agents': [],
			'task_description': 'Go to google.com',
			'reason': 'User asked to search',
		},
	)

	# Mock controller to ensure it's NOT called
	mock_controller = mocker.patch('flask_app.app._get_agent_controller')

	response = client.post('/api/check-conversation-history', json={'history': [{'role': 'user', 'content': 'Search google'}]})

	assert response.status_code == 200
	data = response.get_json()

	# Crucial assertions
	assert data['action_taken'] is False
	assert 'ブラウザ操作が提案されましたが、自動実行は無効化されています' in data['run_summary']
	assert 'Go to google.com' in data['run_summary']

	# Verify reply was updated
	assert data['should_reply'] is True
	assert 'ブラウザ操作が可能です: Go to google.com' in data['reply']

	# Verify controller was NOT instantiated or run
	mock_controller.assert_not_called()
