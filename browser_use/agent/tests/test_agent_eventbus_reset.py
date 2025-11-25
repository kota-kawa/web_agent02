from types import MethodType

import pytest

from browser_use.agent.service import Agent
from browser_use.agent.views import ActionResult, AgentHistory
from browser_use.browser.constants import DEFAULT_NEW_TAB_URL
from browser_use.browser.views import BrowserStateHistory
from browser_use.config import CONFIG


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


def test_add_new_task_clears_user_payloads_across_runs():
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

		first_result = [
			ActionResult(
				is_done=True,
				success=True,
				extracted_content='first run content',
				long_term_memory='memory',
				attachments=['file.txt'],
				error='boom',
				metadata={'source': 'first'},
				include_extracted_content_only_once=True,
				include_in_memory=True,
			)
		]

		agent.state.last_result = first_result
		agent.history.add_item(
			AgentHistory(
				model_output=None,
				result=first_result,
				state=_history_state(),
				metadata=None,
			)
		)

		agent.add_new_task('follow-up 1')

		cleared_result = agent.state.last_result[-1]
		assert cleared_result.extracted_content is None
		assert cleared_result.long_term_memory is None
		assert cleared_result.attachments == []
		assert cleared_result.error is None
		assert cleared_result.metadata is None
		assert cleared_result.include_extracted_content_only_once is False
		assert cleared_result.include_in_memory is False

		history_result = agent.history.history[-1].result[-1]
		assert history_result.extracted_content is None
		assert history_result.long_term_memory is None
		assert history_result.attachments == []
		assert history_result.error is None
		assert history_result.metadata is None

		second_result = [
			ActionResult(
				is_done=True,
				success=True,
				extracted_content='second run content',
				attachments=['second.txt'],
			)
		]

		agent.state.last_result = second_result
		agent.history.add_item(
			AgentHistory(
				model_output=None,
				result=second_result,
				state=_history_state(),
				metadata=None,
			)
		)

		agent.add_new_task('follow-up 2')

		cleared_second_result = agent.state.last_result[-1]
		assert cleared_second_result.extracted_content is None
		assert cleared_second_result.attachments == []

		second_history_result = agent.history.history[-1].result[-1]
		assert second_history_result.extracted_content is None
		assert second_history_result.attachments == []
	finally:
		CONFIG.BROWSER_USE_CLOUD_SYNC = original_cloud_sync


def test_add_new_task_rotates_legacy_eventbus_while_running():
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

		class LegacyEventBus:
			def __init__(self) -> None:
				self.name = 'Agent_000-legacy'
				self.handlers: dict[str, list] = {}
				self.stop_calls = 0

			async def stop(self, *, timeout: float = 3.0) -> None:
				self.stop_calls += 1

		agent.eventbus = LegacyEventBus()
		agent._reserved_eventbus_name = 'Agent_000-legacy'
		agent.running = True

		agent.add_new_task('follow-up instructions')

		assert agent.eventbus.name != 'Agent_000-legacy'
	finally:
		CONFIG.BROWSER_USE_CLOUD_SYNC = original_cloud_sync


def test_agent_starts_with_yahoo_initial_action():
	original_cloud_sync = CONFIG.BROWSER_USE_CLOUD_SYNC
	CONFIG.BROWSER_USE_CLOUD_SYNC = False

	try:
		agent = Agent(
			task='summarize the latest headlines',
			llm=DummyLLM(),
			browser_session=StubBrowserSession(),
			calculate_cost=False,
		)

		assert agent.initial_url == DEFAULT_NEW_TAB_URL
		assert agent.initial_actions is not None
		assert len(agent.initial_actions) == 1
		action = agent.initial_actions[0]
		assert hasattr(action, 'go_to_url')
		assert action.go_to_url.url == DEFAULT_NEW_TAB_URL
		assert action.go_to_url.new_tab is False
	finally:
		CONFIG.BROWSER_USE_CLOUD_SYNC = original_cloud_sync
