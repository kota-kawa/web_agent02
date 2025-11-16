from __future__ import annotations

import pytest

from flask_app import app as flask_app_module


class _StubController:
	def __init__(self, *, paused: bool = False):
		self._paused = paused
		self.enqueue_calls: list[str] = []
		self.resume_called = False

	def is_running(self) -> bool:
		return True

	def is_paused(self) -> bool:
		return self._paused

	def enqueue_follow_up(self, prompt: str) -> None:
		self.enqueue_calls.append(prompt)

	def resume(self) -> None:
		self.resume_called = True
		self._paused = False


@pytest.mark.unit
def test_agent_relay_enqueues_follow_up_when_running(monkeypatch):
	controller = _StubController()
	monkeypatch.setattr(flask_app_module, '_get_agent_controller', lambda: controller)
	flask_app_module.app.config['TESTING'] = True

	with flask_app_module.app.test_client() as client:
		response = client.post('/api/agent-relay', json={'prompt': '最新ニュースをチェック'})

	assert response.status_code == 202
	payload = response.get_json()
	assert payload['status'] == 'follow_up_enqueued'
	assert payload['agent_running'] is True
	assert payload['queued'] is True
	assert controller.enqueue_calls == ['最新ニュースをチェック']
	assert controller.resume_called is False


@pytest.mark.unit
def test_agent_relay_resumes_when_paused(monkeypatch):
	controller = _StubController(paused=True)
	monkeypatch.setattr(flask_app_module, '_get_agent_controller', lambda: controller)
	flask_app_module.app.config['TESTING'] = True

	with flask_app_module.app.test_client() as client:
		response = client.post('/api/agent-relay', json={'prompt': '追加の操作を開始'})

	assert response.status_code == 202
	payload = response.get_json()
	assert payload['status'] == 'follow_up_enqueued'
	assert payload['agent_running'] is True
	assert controller.enqueue_calls == ['追加の操作を開始']
	assert controller.resume_called is True
