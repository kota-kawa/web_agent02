import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from browser_use.agent.views import AgentHistoryList
from flask_app import app as app_module


class StubController:
    def __init__(self) -> None:
        self._running = False
        self.prepare_calls = 0
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

    def enqueue_follow_up(self, task: str) -> None:  # noqa: ARG002
        raise AssertionError('enqueue_follow_up should not be called in this test')

    def prepare_for_new_task(self) -> None:
        self.prepare_calls += 1
        self._step_messages.clear()

    def get_step_message_id(self, step_number: int) -> int | None:
        return self._step_messages.get(step_number)

    def remember_step_message_id(self, step_number: int, message_id: int) -> None:
        self._step_messages[step_number] = message_id


def test_new_task_submission_keeps_history(monkeypatch):
    stub_controller = StubController()
    monkeypatch.setattr(app_module, '_AGENT_CONTROLLER', stub_controller, raising=False)
    monkeypatch.setattr(app_module, '_get_agent_controller', lambda: stub_controller)
    monkeypatch.setattr(app_module, '_get_existing_controller', lambda: stub_controller)

    app_module._reset_history()
    client = app_module.app.test_client()

    first_response = client.post('/api/chat', json={'prompt': '最初のタスク'})
    assert first_response.status_code == 200
    first_data = first_response.get_json()
    assert isinstance(first_data, dict)
    first_contents = ' '.join(entry['content'] for entry in first_data.get('messages', []))
    assert '最初のタスク' in first_contents

    second_response = client.post('/api/chat', json={'prompt': '別のタスク', 'new_task': True})
    assert second_response.status_code == 200
    second_data = second_response.get_json()
    assert isinstance(second_data, dict)
    contents_after_second = ' '.join(entry['content'] for entry in second_data.get('messages', []))
    assert '最初のタスク' in contents_after_second
    assert '別のタスク' in contents_after_second
    assert 'ブラウザ操作エージェントへようこそ' in contents_after_second

    assert stub_controller.prepare_calls == 1
    assert stub_controller.run_prompts == ['最初のタスク', '別のタスク']
