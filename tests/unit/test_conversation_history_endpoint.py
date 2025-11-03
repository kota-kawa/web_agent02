import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

# AgentHistoryList is used in StubController for mocking browser agent responses
from browser_use.agent.views import AgentHistoryList
from flask_app import app as app_module


class StubController:
    def __init__(self) -> None:
        self._running = False
        self.run_prompts: list[str] = []
        self._step_messages: dict[int, int] = {}

    def is_running(self) -> bool:
        return self._running

    def run(self, task: str) -> app_module.AgentRunResult:
        self.run_prompts.append(task)
        self._running = False
        return app_module.AgentRunResult(
            history=AgentHistoryList(history=[]),
            step_message_ids={},
            filtered_history=None,
        )

    def get_step_message_id(self, step_number: int) -> int | None:
        return self._step_messages.get(step_number)

    def remember_step_message_id(self, step_number: int, message_id: int) -> None:
        self._step_messages[step_number] = message_id


def test_check_conversation_history_endpoint_exists(monkeypatch):
    """Test that the check-conversation-history endpoint exists."""
    stub_controller = StubController()
    monkeypatch.setattr(app_module, '_AGENT_CONTROLLER', stub_controller, raising=False)
    monkeypatch.setattr(app_module, '_get_agent_controller', lambda: stub_controller)

    client = app_module.app.test_client()
    
    # Test with empty conversation history
    response = client.post('/api/check-conversation-history', json={})
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data


def test_check_conversation_history_with_valid_input(monkeypatch):
    """Test that the endpoint accepts valid conversation history."""
    stub_controller = StubController()
    monkeypatch.setattr(app_module, '_AGENT_CONTROLLER', stub_controller, raising=False)
    monkeypatch.setattr(app_module, '_get_agent_controller', lambda: stub_controller)

    client = app_module.app.test_client()
    
    conversation_history = [
        {'role': 'user', 'content': 'こんにちは'},
        {'role': 'assistant', 'content': 'こんにちは。何かお手伝いできることはありますか?'},
    ]
    
    # Mock the _analyze_conversation_history to avoid calling the real LLM
    def mock_analyze(history):
        return {
            'needs_action': False,
            'action_type': None,
            'task_description': None,
            'reason': 'テスト用のモック分析',
        }
    
    monkeypatch.setattr(app_module, '_analyze_conversation_history', mock_analyze)
    
    response = client.post('/api/check-conversation-history', json={'conversation_history': conversation_history})
    assert response.status_code == 200
    data = response.get_json()
    assert 'analysis' in data
    assert 'action_taken' in data
    assert data['action_taken'] is False


def test_check_conversation_history_triggers_action(monkeypatch):
    """Test that the endpoint triggers browser action when needed."""
    stub_controller = StubController()
    monkeypatch.setattr(app_module, '_AGENT_CONTROLLER', stub_controller, raising=False)
    monkeypatch.setattr(app_module, '_get_agent_controller', lambda: stub_controller)

    client = app_module.app.test_client()
    
    conversation_history = [
        {'role': 'user', 'content': 'Googleで天気を調べてください'},
        {'role': 'assistant', 'content': 'エラーが発生しました'},
    ]
    
    # Mock the _analyze_conversation_history to simulate a case where action is needed
    def mock_analyze(history):
        return {
            'needs_action': True,
            'action_type': 'search',
            'task_description': 'Googleで天気を検索する',
            'reason': 'ユーザーが天気の検索を要求しているが、エラーが発生している',
        }
    
    monkeypatch.setattr(app_module, '_analyze_conversation_history', mock_analyze)
    
    response = client.post('/api/check-conversation-history', json={'conversation_history': conversation_history})
    assert response.status_code == 200
    data = response.get_json()
    assert 'analysis' in data
    assert 'action_taken' in data
    assert data['action_taken'] is True
    assert len(stub_controller.run_prompts) == 1
    assert 'Googleで天気を検索する' == stub_controller.run_prompts[0]


def test_check_conversation_history_invalid_format(monkeypatch):
    """Test that the endpoint rejects invalid conversation history format."""
    client = app_module.app.test_client()
    
    # Test with non-list conversation history
    response = client.post('/api/check-conversation-history', json={'conversation_history': 'invalid'})
    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data
