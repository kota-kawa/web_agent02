from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession


class FullscreenSessionTestDouble(BrowserSession):
        """Lightweight BrowserSession for exercising fullscreen behaviour."""

        def model_post_init(self, __context) -> None:  # pragma: no cover - avoid watchdog wiring
                return None

        async def attach_all_watchdogs(self) -> None:  # pragma: no cover - not needed for tests
                return None


@pytest.mark.asyncio
async def test_apply_initial_window_state_respects_opt_out(tmp_path):
        profile = BrowserProfile(
                user_data_dir=tmp_path / 'profile',
                headless=False,
                request_initial_window_state=False,
        )
        session = FullscreenSessionTestDouble(browser_profile=profile)

        browser_stub = SimpleNamespace(
                getWindowForTarget=AsyncMock(return_value={'windowId': 42}),
                setWindowBounds=AsyncMock(),
                getWindowBounds=AsyncMock(return_value={'bounds': {'windowState': 'fullscreen'}}),
                bringToFront=AsyncMock(),
        )
        session._cdp_client_root = SimpleNamespace(send=SimpleNamespace(Browser=browser_stub))

        await session._apply_initial_window_state('target-1')

        browser_stub.getWindowForTarget.assert_not_called()
        assert session._fullscreen_requested is False


@pytest.mark.asyncio
async def test_apply_initial_window_state_defaults_to_remote(tmp_path):
        profile = BrowserProfile(
                user_data_dir=tmp_path / 'profile',
                headless=False,
                cdp_url='ws://selenium.example/devtools/browser/abc',
                is_local=False,
                request_initial_window_state=None,
        )
        session = FullscreenSessionTestDouble(browser_profile=profile)
        session.browser_profile.is_local = False

        browser_stub = SimpleNamespace(
                getWindowForTarget=AsyncMock(return_value={'windowId': 7}),
                setWindowBounds=AsyncMock(),
                getWindowBounds=AsyncMock(return_value={'bounds': {'windowState': 'fullscreen'}}),
                bringToFront=AsyncMock(),
        )
        session._cdp_client_root = SimpleNamespace(send=SimpleNamespace(Browser=browser_stub))

        await session._apply_initial_window_state('target-1')

        assert browser_stub.setWindowBounds.await_count >= 1
        first_call_kwargs = browser_stub.setWindowBounds.await_args_list[0].kwargs
        assert first_call_kwargs['params']['bounds']['windowState'] == 'fullscreen'
        assert session._fullscreen_requested is True
