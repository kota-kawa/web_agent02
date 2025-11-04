import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from flask_app import app as app_module


class DummyHistory:
    def __init__(self) -> None:
        self.history: list[object] = []
        self.usage = None

    def is_successful(self) -> bool:
        return True

    def final_result(self) -> str:
        return 'テスト出力'


class RelayStubController:
    def __init__(self) -> None:
        self._running = False
        self.run_calls: list[tuple[str, bool]] = []
        self.history = DummyHistory()

    def is_running(self) -> bool:
        return self._running

    def run(self, task: str, record_history: bool = True) -> app_module.AgentRunResult:
        self.run_calls.append((task, record_history))
        return app_module.AgentRunResult(
            history=self.history,
            step_message_ids={},
            filtered_history=self.history,
        )


def test_agent_relay_requires_prompt(monkeypatch):
    stub_controller = RelayStubController()
    monkeypatch.setattr(app_module, '_AGENT_CONTROLLER', stub_controller, raising=False)
    monkeypatch.setattr(app_module, '_get_agent_controller', lambda: stub_controller)

    client = app_module.app.test_client()
    response = client.post('/api/agent-relay', json={})

    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data


def test_agent_relay_does_not_touch_conversation_history(monkeypatch):
    stub_controller = RelayStubController()
    monkeypatch.setattr(app_module, '_AGENT_CONTROLLER', stub_controller, raising=False)
    monkeypatch.setattr(app_module, '_get_agent_controller', lambda: stub_controller)

    client = app_module.app.test_client()
    before_history = app_module._copy_history()

    response = client.post('/api/agent-relay', json={'prompt': '別のエージェントからの依頼'})
    assert response.status_code == 200

    data = response.get_json()
    assert data['success'] is True
    assert data['final_result'] == 'テスト出力'
    assert 'summary' in data
    assert isinstance(data['steps'], list)

    # Verify that run() was invoked with record_history disabled
    assert stub_controller.run_calls == [('別のエージェントからの依頼', False)]

    after_history = app_module._copy_history()
    assert after_history == before_history
