import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from flask_app import app as flask_app_module


@pytest.fixture(autouse=True)
def reset_cdp_cleanup() -> None:
    cleanup = flask_app_module._consume_cdp_session_cleanup()
    if cleanup:
        cleanup()
    yield
    cleanup = flask_app_module._consume_cdp_session_cleanup()
    if cleanup:
        cleanup()


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200) -> None:
        super().__init__(payload)
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.close()


def _extract_request_info(request: Any) -> tuple[str, str]:
    if isinstance(request, str):
        return 'GET', request
    method = request.get_method()
    url = request.full_url
    return method, url


def test_resolve_cdp_url_prefers_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BROWSER_USE_CDP_URL', 'ws://explicit-host')

    result = flask_app_module._resolve_cdp_url()

    assert result == 'ws://explicit-host'
    assert flask_app_module._consume_cdp_session_cleanup() is None


def test_resolve_cdp_url_uses_webdriver_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('BROWSER_USE_CDP_URL', raising=False)

    monkeypatch.setattr(flask_app_module, '_probe_cdp_candidate', lambda candidate: None)

    seen: list[str] = []

    def fake_webdriver_probe(candidate: str) -> str | None:
        seen.append(candidate)
        if candidate == 'http://browser:4444':
            flask_app_module._replace_cdp_session_cleanup(lambda: None)
            return 'ws://via-webdriver'
        return None

    monkeypatch.setattr(flask_app_module, '_probe_cdp_via_webdriver', fake_webdriver_probe)

    result = flask_app_module._resolve_cdp_url()

    assert result == 'ws://via-webdriver'
    assert 'http://browser:4444' in seen
    cleanup = flask_app_module._consume_cdp_session_cleanup()
    assert cleanup is not None
    cleanup()


def test_probe_cdp_via_webdriver_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_urlopen(request: Any, timeout: float | int) -> FakeResponse:
        method, url = _extract_request_info(request)
        calls.append((method, url))

        if method == 'POST' and url.endswith('/session'):
            payload = json.dumps(
                {
                    'value': {
                        'sessionId': 'abc123',
                        'capabilities': {'se:cdp': ' ws://example.devtools '},
                    }
                }
            ).encode('utf-8')
            return FakeResponse(payload)

        if method == 'DELETE' and url.endswith('/session/abc123'):
            return FakeResponse(b'{}')

        raise AssertionError(f'unexpected request {method} {url}')

    monkeypatch.setattr(flask_app_module, 'urlopen', fake_urlopen)

    result = flask_app_module._probe_cdp_via_webdriver('http://selenium:4444')

    assert result == 'ws://example.devtools'
    assert calls[0][0] == 'POST'

    cleanup = flask_app_module._consume_cdp_session_cleanup()
    assert callable(cleanup)
    assert len(calls) == 1

    cleanup()

    assert len(calls) == 2
    assert calls[1][0] == 'DELETE'
    assert calls[1][1].endswith('/session/abc123')

    cleanup()

    assert len(calls) == 2


def test_resolve_cdp_url_retries_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('BROWSER_USE_CDP_URL', raising=False)
    monkeypatch.setattr(flask_app_module, '_CDP_DETECTION_RETRIES', 3)
    monkeypatch.setattr(flask_app_module, '_CDP_DETECTION_RETRY_DELAY', 0.0)
    monkeypatch.setenv('BROWSER_USE_CDP_CANDIDATES', 'http://browser:4444')

    monkeypatch.setattr(flask_app_module, '_probe_cdp_candidate', lambda candidate: None)

    attempts = {'count': 0}

    def fake_webdriver_probe(candidate: str) -> str | None:
        attempts['count'] += 1
        if attempts['count'] < 2:
            return None
        flask_app_module._replace_cdp_session_cleanup(lambda: None)
        return 'ws://retry-success'

    monkeypatch.setattr(flask_app_module, '_probe_cdp_via_webdriver', fake_webdriver_probe)
    monkeypatch.setattr(flask_app_module.time, 'sleep', lambda *_: None)

    result = flask_app_module._resolve_cdp_url()

    assert result == 'ws://retry-success'
    assert attempts['count'] == 2

    cleanup = flask_app_module._consume_cdp_session_cleanup()
    assert callable(cleanup)
    cleanup()


def test_resolve_cdp_url_returns_none_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('BROWSER_USE_CDP_URL', raising=False)
    monkeypatch.setattr(flask_app_module, '_CDP_DETECTION_RETRIES', 3)
    monkeypatch.setattr(flask_app_module, '_CDP_DETECTION_RETRY_DELAY', 1.0)
    monkeypatch.setenv('BROWSER_USE_CDP_CANDIDATES', 'http://browser:4444')

    monkeypatch.setattr(flask_app_module, '_probe_cdp_candidate', lambda candidate: None)

    attempts = 0

    def fake_webdriver_probe(candidate: str) -> str | None:
        nonlocal attempts
        attempts += 1
        return None

    monkeypatch.setattr(flask_app_module, '_probe_cdp_via_webdriver', fake_webdriver_probe)

    sleep_calls: list[float] = []

    def record_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(flask_app_module.time, 'sleep', record_sleep)

    result = flask_app_module._resolve_cdp_url()

    assert result is None
    assert attempts == 3
    assert len(sleep_calls) == 2
    assert all(delay == 1.0 for delay in sleep_calls)

    cleanup = flask_app_module._consume_cdp_session_cleanup()
    assert cleanup is None


def test_browser_agent_controller_preserves_agent_across_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BROWSER_USE_CDP_URL', 'ws://dummy-cdp')

    created_agents: list[Any] = []

    class FakeHistoryList:
        def __init__(self) -> None:
            self.history: list[Any] = []
            self._final_result = ''
            self._success = True

        def is_successful(self) -> bool:
            return self._success

        def final_result(self) -> str:
            return self._final_result

    class FakeBrowserProfile:
        def __init__(
            self,
            *,
            cdp_url: str | None,
            keep_alive: bool,
            highlight_elements: bool,
            wait_between_actions: float,
        ) -> None:
            self.cdp_url = cdp_url
            self.keep_alive = keep_alive
            self.highlight_elements = highlight_elements
            self.wait_between_actions = wait_between_actions

    class FakeBrowserSession:
        def __init__(self, browser_profile: FakeBrowserProfile) -> None:
            self.browser_profile = browser_profile
            self.start_calls = 0
            self.stop_calls = 0
            self.id = 'fake-session'

        async def start(self) -> None:
            self.start_calls += 1

        async def stop(self) -> None:
            self.stop_calls += 1

    class FakeAgent:
        def __init__(
            self,
            *,
            task: str,
            browser_session: FakeBrowserSession,
            llm: object,
            register_new_step_callback,
            extend_system_message: str,
            **_: Any,
        ) -> None:
            self.task = task
            self.browser_session = browser_session
            self.register_new_step_callback = register_new_step_callback
            self.extend_system_message = extend_system_message
            self.initial_actions: list[dict[str, Any]] | None = None
            self.initial_url: str | None = None
            self.history = FakeHistoryList()
            self.state = SimpleNamespace(follow_up_task=False, n_steps=0)
            self.running = False
            self.tasks_received = [task]
            created_agents.append(self)

        def _convert_initial_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
            self.initial_actions = actions
            return actions

        def add_new_task(self, new_task: str) -> None:
            self.tasks_received.append(new_task)
            self.task = new_task
            self.state.follow_up_task = True

        async def run(self, max_steps: int) -> FakeHistoryList:  # noqa: ARG002
            self.running = True
            await self.browser_session.start()
            step_number = len(self.history.history) + 1
            state = SimpleNamespace(title=f'Step {step_number}', url=f'https://example.com/{step_number}')
            model_output = SimpleNamespace(
                action=[],
                evaluation_previous_goal=None,
                next_goal=None,
                memory=None,
                long_term_memory=None,
            )
            result = [
                SimpleNamespace(
                    error=None,
                    is_done=True,
                    success=True,
                    extracted_content=f'result {step_number}',
                    long_term_memory=None,
                    metadata=None,
                )
            ]
            step = SimpleNamespace(state=state, model_output=model_output, result=result)
            self.history.history.append(step)
            self.history._final_result = f'Final {step_number}'
            self.history._success = True
            self.state.n_steps += 1
            if self.register_new_step_callback:
                self.register_new_step_callback(state, model_output, step_number)
            self.running = False
            return self.history

    monkeypatch.setattr(flask_app_module, '_create_gemini_llm', lambda: object())
    monkeypatch.setattr(flask_app_module, 'BrowserProfile', FakeBrowserProfile)
    monkeypatch.setattr(flask_app_module, 'BrowserSession', FakeBrowserSession)
    monkeypatch.setattr(flask_app_module, 'Agent', FakeAgent)

    controller = flask_app_module.BrowserAgentController(cdp_url='ws://dummy-cdp', max_steps=5)
    try:
        first_result = controller.run('最初の指示')
        assert len(created_agents) == 1
        first_agent = created_agents[0]
        assert first_agent.tasks_received == ['最初の指示']
        assert len(first_result.history.history) == 1
        assert controller.get_step_message_id(1) is not None

        second_result = controller.run('続きの指示')
        assert controller._agent is first_agent  # type: ignore[attr-defined]
        assert first_agent.tasks_received == ['最初の指示', '続きの指示']
        assert len(second_result.history.history) == 2
        assert len(created_agents) == 1
        assert controller.get_step_message_id(2) is not None
        assert second_result.history.history[1].state.url.endswith('/2')
        assert first_agent.initial_actions is None
        assert first_agent.state.follow_up_task is True
    finally:
        controller.shutdown()


def test_follow_up_recreated_session_reloads_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BROWSER_USE_CDP_URL', 'ws://dummy-cdp')

    created_agents: list[Any] = []

    class FakeHistoryList:
        def __init__(self) -> None:
            self.history: list[Any] = []
            self._final_result = ''
            self._success = True

        def is_successful(self) -> bool:
            return self._success

        def final_result(self) -> str:
            return self._final_result

    class FakeBrowserProfile:
        def __init__(
            self,
            *,
            cdp_url: str | None,
            keep_alive: bool,
            highlight_elements: bool,
            wait_between_actions: float,
        ) -> None:
            self.cdp_url = cdp_url
            self.keep_alive = keep_alive
            self.highlight_elements = highlight_elements
            self.wait_between_actions = wait_between_actions

    class FakeBrowserSession:
        def __init__(self, browser_profile: FakeBrowserProfile) -> None:
            self.browser_profile = browser_profile
            self.start_calls = 0

        async def start(self) -> None:
            self.start_calls += 1

        async def stop(self) -> None:  # noqa: D401
            pass

    class FakeAgent:
        def __init__(
            self,
            task: str,
            browser_session: FakeBrowserSession,
            llm: Any,
            register_new_step_callback: Any,
            extend_system_message: Any,
        ) -> None:
            self.task = task
            self.browser_session = browser_session
            self.register_new_step_callback = register_new_step_callback
            self.extend_system_message = extend_system_message
            self.initial_actions: list[dict[str, Any]] | None = None
            self.initial_url: str | None = None
            self.history = FakeHistoryList()
            self.state = SimpleNamespace(follow_up_task=False, n_steps=0)
            self.running = False
            self.tasks_received = [task]
            created_agents.append(self)

        def _convert_initial_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
            self.initial_actions = actions
            return actions

        def add_new_task(self, new_task: str) -> None:
            self.tasks_received.append(new_task)
            self.task = new_task
            self.state.follow_up_task = True

        async def run(self, max_steps: int) -> FakeHistoryList:  # noqa: ARG002
            self.running = True
            await self.browser_session.start()
            step_number = len(self.history.history) + 1
            state = SimpleNamespace(title=f'Step {step_number}', url=f'https://example.com/{step_number}')
            model_output = SimpleNamespace(
                action=[],
                evaluation_previous_goal=None,
                next_goal=None,
                memory=None,
                long_term_memory=None,
            )
            result = [
                SimpleNamespace(
                    error=None,
                    is_done=True,
                    success=True,
                    extracted_content=f'result {step_number}',
                    long_term_memory=None,
                    metadata=None,
                )
            ]
            step = SimpleNamespace(state=state, model_output=model_output, result=result)
            self.history.history.append(step)
            self.history._final_result = f'Final {step_number}'
            self.history._success = True
            self.state.n_steps += 1
            if self.register_new_step_callback:
                self.register_new_step_callback(state, model_output, step_number)
            self.running = False
            return self.history

    monkeypatch.setattr(flask_app_module, '_create_gemini_llm', lambda: object())
    monkeypatch.setattr(flask_app_module, 'BrowserProfile', FakeBrowserProfile)
    monkeypatch.setattr(flask_app_module, 'BrowserSession', FakeBrowserSession)
    monkeypatch.setattr(flask_app_module, 'Agent', FakeAgent)

    controller = flask_app_module.BrowserAgentController(cdp_url='ws://dummy-cdp', max_steps=5)
    try:
        first_result = controller.run('最初の指示')
        assert len(first_result.history.history) == 1
        first_agent = created_agents[0]
        controller._browser_session = None  # type: ignore[attr-defined]

        second_result = controller.run('続きの指示')
        assert len(second_result.history.history) == 2
        assert first_agent.initial_actions is not None
        prepared = first_agent.initial_actions[0].get('go_to_url', {})
        assert prepared.get('url') == 'https://example.com/1'
        assert prepared.get('new_tab') is False
    finally:
        controller.shutdown()


def test_keep_alive_sessions_drain_event_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BROWSER_USE_CDP_URL', 'ws://dummy-cdp')

    class FakeHistoryList:
        def __init__(self) -> None:
            self.history: list[Any] = []
            self._final_result = ''
            self._success = True

        def is_successful(self) -> bool:
            return self._success

        def final_result(self) -> str:
            return self._final_result

    class FakeEventBus:
        def __init__(self) -> None:
            self.pending = 0
            self.force_timeout = False
            self.cleanup_calls: list[float] = []
            self.stop_calls = 0

        async def wait_until_idle(self, timeout: float) -> None:
            if self.force_timeout:
                raise asyncio.TimeoutError
            self.pending = 0

        def cleanup_event_history(self) -> None:  # noqa: D401
            self.cleanup_calls.append(0.0)

        async def stop(self, *, clear: bool, timeout: float) -> None:  # noqa: D401
            self.stop_calls += 1
            self.pending = 0

    class FakeBrowserProfile:
        def __init__(
            self,
            *,
            cdp_url: str | None,
            keep_alive: bool,
            highlight_elements: bool,
            wait_between_actions: float,
        ) -> None:
            self.cdp_url = cdp_url
            self.keep_alive = keep_alive
            self.highlight_elements = highlight_elements
            self.wait_between_actions = wait_between_actions

    class FakeBrowserSession:
        def __init__(self, browser_profile: FakeBrowserProfile) -> None:
            self.browser_profile = browser_profile
            self.start_calls = 0
            self.stop_calls = 0
            self.event_bus = FakeEventBus()
            self.drain_timeouts: list[float] = []

        async def start(self) -> None:
            self.start_calls += 1

        async def stop(self) -> None:
            self.stop_calls += 1

        async def drain_event_bus(self, *, timeout: float = 5.0) -> bool:
            self.drain_timeouts.append(timeout)
            try:
                await self.event_bus.wait_until_idle(timeout)
            except asyncio.TimeoutError:
                await self.event_bus.stop(clear=True, timeout=timeout)
                self.event_bus = FakeEventBus()
                return False
            self.event_bus.cleanup_event_history()
            return True

    class FakeAgent:
        def __init__(
            self,
            *,
            task: str,
            browser_session: FakeBrowserSession,
            llm: Any,
            register_new_step_callback: Any,
            extend_system_message: Any,
        ) -> None:
            self.task = task
            self.browser_session = browser_session
            self.register_new_step_callback = register_new_step_callback
            self.extend_system_message = extend_system_message
            self.initial_actions: list[dict[str, Any]] | None = None
            self.initial_url: str | None = None
            self.history = FakeHistoryList()
            self.state = SimpleNamespace(
                follow_up_task=False,
                n_steps=0,
                last_result=[SimpleNamespace(is_done=True, success=True)],
            )
            self.running = False
            self.tasks_received = [task]

        def _convert_initial_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
            self.initial_actions = actions
            return actions

        def add_new_task(self, new_task: str) -> None:
            self.tasks_received.append(new_task)
            self.task = new_task
            self.state.follow_up_task = True

        async def run(self, max_steps: int) -> FakeHistoryList:  # noqa: ARG002
            bus = self.browser_session.event_bus
            if bus.pending >= 1:
                raise RuntimeError('EventBus at capacity: pending events remain')
            bus.pending += 1
            self.running = True
            step_number = len(self.history.history) + 1
            state = SimpleNamespace(title=f'Step {step_number}', url=f'https://example.com/{step_number}')
            model_output = SimpleNamespace(
                action=[],
                evaluation_previous_goal=None,
                next_goal=None,
                memory=None,
                long_term_memory=None,
            )
            result = [
                SimpleNamespace(
                    error=None,
                    is_done=True,
                    success=True,
                    extracted_content=f'result {step_number}',
                    long_term_memory=None,
                    metadata=None,
                )
            ]
            step = SimpleNamespace(state=state, model_output=model_output, result=result)
            self.history.history.append(step)
            self.history._final_result = f'Final {step_number}'
            self.history._success = True
            self.state.n_steps += 1
            if self.register_new_step_callback:
                self.register_new_step_callback(state, model_output, step_number)
            self.running = False
            return self.history

    monkeypatch.setattr(flask_app_module, '_create_gemini_llm', lambda: object())
    monkeypatch.setattr(flask_app_module, 'BrowserProfile', FakeBrowserProfile)
    monkeypatch.setattr(flask_app_module, 'BrowserSession', FakeBrowserSession)
    monkeypatch.setattr(flask_app_module, 'Agent', FakeAgent)

    controller = flask_app_module.BrowserAgentController(cdp_url='ws://dummy-cdp', max_steps=5)
    try:
        first_result = controller.run('最初の指示')
        session = controller._browser_session  # type: ignore[attr-defined]
        assert session is not None
        first_bus = session.event_bus
        assert len(first_result.history.history) == 1
        assert first_bus.pending == 0
        assert first_bus.cleanup_calls

        first_bus.force_timeout = True

        second_result = controller.run('続きの指示')
        second_bus = controller._browser_session.event_bus  # type: ignore[attr-defined]
        assert len(second_result.history.history) == 2
        assert second_bus is not first_bus
        assert first_bus.stop_calls == 1

        third_result = controller.run('さらに続きの指示')
        assert len(third_result.history.history) == 3
        assert controller._browser_session.event_bus is second_bus  # type: ignore[attr-defined]
    finally:
        controller.shutdown()
