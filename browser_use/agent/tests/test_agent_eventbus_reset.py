from types import MethodType

import pytest

from browser_use.agent.service import Agent
from browser_use.agent.views import ActionResult, AgentHistory
from browser_use.config import CONFIG
from browser_use.browser.views import BrowserStateHistory


class DummyLLM:
    model = 'dummy-model'
    _verified_api_keys = True

    @property
    def provider(self) -> str:
        return 'dummy'

    @property
    def name(self) -> str:
        return 'dummy'

    @property
    def model_name(self) -> str:
        return self.model

    async def ainvoke(self, messages, output_format=None):  # pragma: no cover - unused by these tests
        raise NotImplementedError


class StubBrowserProfile:
    def __init__(self) -> None:
        self.downloads_path = None
        self.keep_alive = True
        self.allowed_domains: list[str] = []
        self.viewport = {'width': 1280, 'height': 720}
        self.user_agent = 'stub-agent'
        self.headless = True
        self.wait_between_actions = 0


class StubBrowserSession:
    def __init__(self) -> None:
        self.browser_profile = StubBrowserProfile()
        self.id = 'stub-session'
        self.cdp_url = None
        self.agent_focus = None
        self.event_bus = None
        self.downloaded_files: list[str] = []
        self._watchdogs_attached = False
        self._cached_browser_state_summary = None

    async def start(self) -> None:  # pragma: no cover - simple stub
        return None

    async def kill(self) -> None:  # pragma: no cover - simple stub
        return None

    def model_post_init(self, __context) -> None:  # pragma: no cover - simple stub
        return None


def _history_state() -> BrowserStateHistory:
    return BrowserStateHistory(
        url='',
        title='',
        tabs=[],
        interacted_element=[],
        screenshot_path=None,
    )


@pytest.mark.asyncio
async def test_follow_up_run_resets_done_flags_and_executes_steps():
    original_cloud_sync = CONFIG.BROWSER_USE_CLOUD_SYNC
    CONFIG.BROWSER_USE_CLOUD_SYNC = False

    try:
        agent = Agent(
            task='initial task',
            llm=DummyLLM(),
            browser_session=StubBrowserSession(),
            calculate_cost=False,
            directly_open_url=False,
        )

        async def noop_initial_actions(self):
            return None

        agent._execute_initial_actions = MethodType(noop_initial_actions, agent)

        async def first_run_step(self, step_info):
            result = [ActionResult(is_done=True, success=True)]
            self.state.last_result = result
            self.history.add_item(
                AgentHistory(
                    model_output=None,
                    result=result,
                    state=_history_state(),
                    metadata=None,
                )
            )

        agent.step = MethodType(first_run_step, agent)
        await agent.run(max_steps=1)

        assert agent.history.is_done()
        assert agent.state.last_result[-1].is_done is True
        assert agent.history.history[-1].result[-1].is_done is True

        agent.add_new_task('follow-up instructions')

        assert agent.state.follow_up_task is True
        assert agent.state.last_result[-1].is_done is False
        assert agent.state.last_result[-1].success is None
        assert agent.history.history[-1].result[-1].is_done is False
        assert agent.history.history[-1].result[-1].success is None

        assert agent.reset_completion_state() is False

        step_calls = {'count': 0}

        async def failing_step(self, step_info):
            step_calls['count'] += 1
            result = [ActionResult(error='boom', is_done=False)]
            self.state.last_result = result
            self.history.add_item(
                AgentHistory(
                    model_output=None,
                    result=result,
                    state=_history_state(),
                    metadata=None,
                )
            )

        agent.step = MethodType(failing_step, agent)
        await agent.run(max_steps=1)

        assert step_calls['count'] == 1
        assert agent.state.last_result[-1].error == 'boom'
    finally:
        CONFIG.BROWSER_USE_CLOUD_SYNC = original_cloud_sync
