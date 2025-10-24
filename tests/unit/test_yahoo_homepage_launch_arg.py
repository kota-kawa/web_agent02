"""Test that yahoo.co.jp is configured as the default new tab URL."""
import pathlib


def test_yahoo_homepage_constant_value():
	"""Test that DEFAULT_NEW_TAB_URL constant has the correct value."""
	# Read the constants file directly without importing
	constants_file = pathlib.Path(__file__).resolve().parents[2] / 'browser_use' / 'browser' / 'constants.py'
	
	assert constants_file.exists(), f"Constants file not found at {constants_file}"
	
	content = constants_file.read_text()
	
	# Check that DEFAULT_NEW_TAB_URL is set to yahoo.co.jp
	expected_line = 'DEFAULT_NEW_TAB_URL = "https://www.yahoo.co.jp"'
	
	assert expected_line in content, \
		f"Expected to find '{expected_line}' in constants.py but it was not found"
	
	print(f"✅ DEFAULT_NEW_TAB_URL constant is correctly set to https://www.yahoo.co.jp")


def test_yahoo_homepage_added_to_launch_args():
	"""Test that yahoo.co.jp URL is added to launch arguments."""
	# Read the local_browser_watchdog.py file to verify the URL is added
	watchdog_file = pathlib.Path(__file__).resolve().parents[2] / 'browser_use' / 'browser' / 'watchdogs' / 'local_browser_watchdog.py'
	
	assert watchdog_file.exists(), f"Watchdog file not found at {watchdog_file}"
	
	content = watchdog_file.read_text()
	
	# Check that DEFAULT_NEW_TAB_URL is imported and appended to launch_args
	assert 'from browser_use.browser.constants import DEFAULT_NEW_TAB_URL' in content, \
		"Expected DEFAULT_NEW_TAB_URL import in local_browser_watchdog.py"
	
	assert 'launch_args.append(DEFAULT_NEW_TAB_URL)' in content, \
		"Expected launch_args.append(DEFAULT_NEW_TAB_URL) in local_browser_watchdog.py"
	
	print("✅ DEFAULT_NEW_TAB_URL is correctly appended to launch arguments in local_browser_watchdog.py")


if __name__ == '__main__':
	test_yahoo_homepage_constant_value()
	test_yahoo_homepage_added_to_launch_args()
	print("\n✅ All tests passed!")
