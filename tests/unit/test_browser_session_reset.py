import pytest

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession


class DummyCDPClient:
	def __init__(self):
		self.stopped = False

	async def stop(self):
		self.stopped = True


@pytest.mark.asyncio
async def test_reset_closes_root_cdp_client():
	profile = BrowserProfile(cdp_url='ws://localhost:9222', keep_alive=False)
	session = BrowserSession(browser_profile=profile)

	dummy_client = DummyCDPClient()
	session._cdp_client_root = dummy_client  # type: ignore[attr-defined]

	await session.reset()

	assert dummy_client.stopped is True
	assert session._cdp_client_root is None
