from __future__ import annotations

from browser_use.browser import profile as profile_module



def test_remote_cdp_profile_defaults_headful(monkeypatch, tmp_path):
	"""Profiles connecting to remote Chrome over CDP should assume a headful display."""
	# Ensure the cached display size is cleared before patching
	profile_module.get_display_size.cache_clear()

	# Simulate a headless environment where local display detection fails
	monkeypatch.setattr(profile_module, 'get_display_size', lambda: None)

	# Instantiate a profile that connects to a remote (non-local) browser
	profile = profile_module.BrowserProfile(
		cdp_url='ws://selenium.example/devtools/browser/abc',
		is_local=False,
		user_data_dir=tmp_path / 'profile',
	)

	# Remote sessions should default to headful so fullscreen logic can run
	assert profile.headless is False
	assert profile.window_size is None

	args = profile.get_args()

	# Chrome should receive fullscreen/maximized flags instead of a fixed window size
	assert '--start-fullscreen' in args
	assert '--start-maximized' in args
	assert all(not arg.startswith('--window-size=') for arg in args)
