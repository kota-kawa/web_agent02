from playwright.sync_api import sync_playwright


def verify_webarena_ui():
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		# Use a large viewport to see the layout
		page = browser.new_page(viewport={'width': 1400, 'height': 900})

		try:
			print('Navigating to WebArena...')
			page.goto('http://127.0.0.1:5005/webarena')

			# Wait for content to load
			page.wait_for_selector('.wa-layout')

			print('Taking initial screenshot...')
			page.screenshot(path='verification/webarena_initial.png')

			# Expand Config
			print('Expanding Config...')
			page.click('summary')
			page.wait_for_timeout(500)  # Wait for animation
			page.screenshot(path='verification/webarena_config_expanded.png')

			# Select a task if any exist (Mocking/Waiting for tasks to load)
			# The tasks load async, wait for them
			try:
				page.wait_for_selector('.wa-task-card', timeout=5000)
				print('Selecting a task...')
				# Click the first real task (not custom)
				tasks = page.locator('.wa-task-card')
				count = tasks.count()
				if count > 1:  # Assuming custom is last or separate logic in my code
					tasks.first.click()
					page.wait_for_timeout(200)
					page.screenshot(path='verification/webarena_task_selected.png')
			except Exception as e:
				print(f'No tasks loaded or error selecting: {e}')

			print('Verification script finished.')

		except Exception as e:
			print(f'Error during verification: {e}')
		finally:
			browser.close()


if __name__ == '__main__':
	verify_webarena_ui()
