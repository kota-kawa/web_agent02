import datetime
import json
import logging
import math
import os
import statistics
import string
import subprocess
import urllib.error
import urllib.request
from contextlib import suppress
from difflib import SequenceMatcher
from urllib.parse import urlparse

from flask import jsonify, render_template, request

from flask_app.env_utils import _BROWSER_URL, _WEBARENA_MAX_STEPS, _normalize_start_url
from flask_app.formatting import _format_history_messages

from . import webarena_bp

logger = logging.getLogger(__name__)

# Only these environments are provisioned locally
SUPPORTED_SITES = {'shopping', 'shopping_admin', 'reddit', 'gitlab'}

# Load WebArena tasks from JSON and filter to supported environments
TASKS_FILE = os.path.join(os.path.dirname(__file__), 'tasks_data/test.json')


def _load_tasks():
	try:
		with open(TASKS_FILE) as f:
			all_tasks = json.load(f)
	except Exception:
		logger.warning('Could not load WebArena tasks from %s', TASKS_FILE)
		return [], []

	def _is_supported(task):
		sites = task.get('sites', []) or []
		# Keep tasks that only reference environments we actually have
		return bool(sites) and all(site in SUPPORTED_SITES for site in sites)

	supported_tasks = [t for t in all_tasks if _is_supported(t)]

	if len(supported_tasks) != len(all_tasks):
		logger.info(
			'WebArena tasks filtered: %s supported of %s total (allowed sites: %s)',
			len(supported_tasks),
			len(all_tasks),
			','.join(sorted(SUPPORTED_SITES)),
		)

	return all_tasks, supported_tasks


ALL_WEBARENA_TASKS, WEBARENA_TASKS = _load_tasks()

# Optional external reset hooks
RESET_COMMAND = os.getenv(
	'WEBARENA_RESET_COMMAND'
)  # e.g., "docker compose -f bin/webarena/docker-compose.webarena.yml restart shopping shopping_admin gitlab forum"

# Set a sensible default for RESET_COMMAND if not provided and file exists
if not RESET_COMMAND and not os.getenv('WEBARENA_RESET_URL'):
	_default_compose_path = os.path.join(os.getcwd(), 'bin/webarena/docker-compose.webarena.yml')
	if os.path.exists(_default_compose_path):
		RESET_COMMAND = f'docker compose -f {_default_compose_path} restart shopping shopping_admin gitlab forum'
		logger.info(f'Configured default WebArena reset command: {RESET_COMMAND}')

RESET_URL = os.getenv('WEBARENA_RESET_URL')  # e.g., "http://localhost:7000/reset"


def _build_default_env_urls():
	"""
	When the Flask app runs inside Docker (/.dockerenv present), the agent and the
	browser containers share the same user-defined network (`multi_agent_network`).
	In that case the WebArena backends are reachable via their service names
	(shopping, shopping_admin, gitlab, forum) rather than localhost:*.

	When running the Flask app directly on the host machine, we still want to keep
	the existing localhost-based defaults so the UI works without Docker.
	"""
	in_container = os.path.exists('/.dockerenv') or os.environ.get('CONTAINERIZED') == '1'

	container_defaults = {
		'shopping': 'http://shopping:80',
		'shopping_admin': 'http://shopping_admin:80',
		'gitlab': 'http://gitlab:8023',
		'reddit': 'http://forum:80',
		'wikipedia': 'http://wikipedia:80',
	}

	host_defaults = {
		'shopping': 'http://localhost:7770',
		'shopping_admin': 'http://localhost:7780',
		'gitlab': 'http://localhost:8023',
		'reddit': 'http://localhost:9999',
		'wikipedia': 'http://wikipedia:80',
	}

	defaults = container_defaults if in_container else host_defaults

	return {
		'shopping': os.getenv('WEBARENA_SHOPPING_URL', defaults['shopping']),
		'shopping_admin': os.getenv('WEBARENA_SHOPPING_ADMIN_URL', defaults['shopping_admin']),
		'gitlab': os.getenv('WEBARENA_GITLAB_URL', defaults['gitlab']),
		'reddit': os.getenv('WEBARENA_REDDIT_URL', defaults['reddit']),
		'wikipedia': os.getenv('WEBARENA_WIKIPEDIA_URL', defaults['wikipedia']),
	}


# Default base URLs for WebArena environments (auto-select host vs container)
DEFAULT_ENV_URLS = _build_default_env_urls()


@webarena_bp.route('/webarena')
def index():
	return render_template(
		'webarena.html',
		browser_url=_BROWSER_URL,
		env_urls=DEFAULT_ENV_URLS,
		supported_sites=sorted(SUPPORTED_SITES),
	)


@webarena_bp.route('/webarena/tasks')
def get_tasks():
	page = int(request.args.get('page', 1))
	per_page = int(request.args.get('per_page', 50))
	site_filter = request.args.get('site')

	tasks = WEBARENA_TASKS
	if site_filter and site_filter in SUPPORTED_SITES:
		# User request: Only show tasks that are exclusively for this site.
		# Multi-site tasks should only appear in "ALL".
		tasks = [t for t in tasks if t.get('sites') and len(t['sites']) == 1 and site_filter in t['sites']]

	start = (page - 1) * per_page
	end = start + per_page
	return jsonify({'tasks': tasks[start:end], 'total': len(tasks), 'page': page, 'per_page': per_page})


def _resolve_start_url(task, env_urls_override=None):
	start_url = task.get('start_url', '')
	env_urls = DEFAULT_ENV_URLS.copy()
	if env_urls_override:
		env_urls.update(env_urls_override)

	replacements = {
		'__SHOPPING__': env_urls.get('shopping'),
		'__SHOPPING_ADMIN__': env_urls.get('shopping_admin'),
		'__GITLAB__': env_urls.get('gitlab'),
		'__REDDIT__': env_urls.get('reddit'),
		'__WIKIPEDIA__': env_urls.get('wikipedia', 'https://en.wikipedia.org/wiki'),
	}

	for placeholder, base_url in replacements.items():
		if base_url and placeholder in start_url:
			start_url = start_url.replace(placeholder, base_url)

	return start_url


def _reset_state(controller, sites, start_url: str | None = None):
	"""
	Best-effort reset for WebArena between tasks.
	- Always reset the browser controller session (clears cookies/storage).
	- Optionally call external reset hook via command or HTTP endpoint for backend state.
	"""
	# 1) Reset browser session
	try:
		controller.reset()
		# Apply the next start page (task-specific when provided) so warmup/cleanup
		# happens on the correct environment.
		if start_url:
			controller.set_start_page(start_url)
		else:
			controller.set_start_page(None)

		controller.ensure_start_page_ready()
		with suppress(Exception):
			controller.close_additional_tabs(start_url)
	except Exception as e:
		logger.warning('Browser reset failed: %s', e)

	# 2) External reset hook (if configured)
	sites_csv = ','.join(sites or [])

	if RESET_URL:
		try:
			data = json.dumps({'sites': sites or []}).encode('utf-8')
			req = urllib.request.Request(RESET_URL, data=data, headers={'Content-Type': 'application/json'}, method='POST')
			with urllib.request.urlopen(req, timeout=180) as resp:
				logger.info('Reset URL responded with status %s', resp.getcode())
		except Exception as e:
			logger.warning('External reset via URL failed: %s', e)
	elif RESET_COMMAND:
		cmd = RESET_COMMAND.format(sites=sites_csv)
		try:
			subprocess.run(cmd, shell=True, check=True, timeout=300)
			logger.info('Executed reset command: %s', cmd)
		except Exception as e:
			logger.warning('External reset command failed: %s', e)
	else:
		if sites:
			logger.warning('No WEBARENA_RESET_COMMAND/URL configured. Only browser session was reset for sites: %s', sites_csv)
		else:
			logger.warning('No WEBARENA_RESET_COMMAND/URL configured. Only browser session was reset.')


WEBARENA_CREDENTIALS_PROMPT = """
### WebArena Environment Credentials
For tasks in the WebArena environment, use the following credentials if required:
- Shopping (Magento): Email "emma.lopez@gmail.com", Password "Password.123"
- Shopping Admin: Username "admin", Password "admin1234"
- GitLab: Username "root", Password "5iveL!fe"
- Reddit (PostMill): Username "user1", Password "password"
"""


def _run_single_task(task, controller, env_urls_override=None, start_url_override=None):
	"""
	Execute one WebArena task with the shared controller and return the result payload.
	"""
	intent = task.get('intent', '')
	if not intent:
		raise ValueError('Task is missing intent.')

	start_url = _resolve_start_url(task, env_urls_override)

	full_prompt = intent
	if start_url:
		full_prompt = f'Navigate to {start_url} first. Then, {intent}'

	if task.get('require_login'):
		full_prompt += ' (Note: This task may require logging in. If credentials are unknown, fail gracefully.)'

	start_url_for_reset = start_url_override or start_url
	_reset_state(controller, task.get('sites') or [], start_url_for_reset)

	result = controller.run(
		full_prompt,
		additional_system_message=WEBARENA_CREDENTIALS_PROMPT,
		max_steps=_WEBARENA_MAX_STEPS,
	)
	history = result.history

	evaluation_msg = _evaluate_result(history, task, controller)
	success = history.is_successful()
	if 'Failure' in evaluation_msg:
		success = False
	elif success is None or success is False:
		# 評価で失敗が出ていなくても、エージェントの自己申告が未設定/失敗なら失敗扱い
		success = False

	return {
		'task_id': task.get('task_id'),
		'success': success,
		'summary': history.final_result(),
		'steps': [{'step': n, 'content': c} for n, c in _format_history_messages(history)],
		'evaluation': evaluation_msg,
	}


def _compute_aggregate_metrics(results: list[dict], selected_tasks: list[dict], max_steps: int) -> dict:
	"""Compute extended aggregate statistics for a WebArena batch run."""

	if not results:
		return {}

	total = len(results)
	success_flags = [bool(r.get('success')) for r in results]
	success_count = sum(success_flags)
	sr = success_count / total if total else 0.0

	# 95% CI for success rate (normal approximation, clamped to [0,1])
	if total:
		se = math.sqrt(sr * (1 - sr) / total)
		margin = 1.96 * se
		ci_lower = max(0.0, sr - margin)
		ci_upper = min(1.0, sr + margin)
	else:
		ci_lower = ci_upper = 0.0

	# Template-macro SR (average of per-template success rates)
	template_results: dict[str | int, list[bool]] = {}
	for task, result in zip(selected_tasks, results):
		template_id = task.get('intent_template_id')
		if template_id is None:
			continue
		template_results.setdefault(template_id, []).append(bool(result.get('success')))

	if template_results:
		template_rates = [sum(flags) / len(flags) for flags in template_results.values() if flags]
		template_macro_sr = sum(template_rates) / len(template_rates) if template_rates else 0.0
	else:
		template_macro_sr = 0.0

	# Step statistics
	step_counts_success = [len(r.get('steps') or []) for r in results if r.get('success')]
	step_counts_overall = [len(r.get('steps') or []) if r.get('success') else max_steps for r in results]

	def _safe_mean(values: list[int]) -> float:
		return statistics.mean(values) if values else 0.0

	def _safe_median(values: list[int]) -> float:
		return float(statistics.median(values)) if values else 0.0

	def _p90(values: list[int]) -> float:
		if not values:
			return 0.0
		sorted_vals = sorted(values)
		idx = max(0, min(len(sorted_vals) - 1, math.ceil(0.9 * len(sorted_vals)) - 1))
		return float(sorted_vals[idx])

	return {
		'success_rate': round(sr * 100, 2),
		'success_rate_ci_95': {'lower': round(ci_lower * 100, 2), 'upper': round(ci_upper * 100, 2)},
		'template_macro_sr': round(template_macro_sr * 100, 2),
		'average_steps_success_only': round(_safe_mean(step_counts_success), 2),
		'average_steps_overall_with_failures_as_max': round(_safe_mean(step_counts_overall), 2),
		'median_steps_success_only': round(_safe_median(step_counts_success), 2),
		'p90_steps_success_only': round(_p90(step_counts_success), 2),
		'max_steps': max_steps,
	}


def _apply_start_page_override(selected_site: str | None, env_urls_override: dict | None = None) -> str | None:
	"""
	When the WebArena UI is used with an environment filter, start the agent on that site's base URL.
	This affects only the WebArena flow (root / is unchanged).
	"""
	if not selected_site or selected_site not in SUPPORTED_SITES:
		return None

	env_urls = DEFAULT_ENV_URLS.copy()
	if env_urls_override:
		env_urls.update(env_urls_override)

	start_url = env_urls.get(selected_site)
	normalized = _normalize_start_url(start_url) if start_url else None
	return normalized


def _evaluate_result(history, task, controller):
	"""
	Evaluation logic based on WebArena 'eval' fields.
	"""
	if not task:
		return 'Custom task - no automated evaluation'

	def _normalize_text(text: str) -> str:
		"""Lowercase, remove punctuation, collapse whitespace for robust matching."""
		if not text:
			return ''
		translator = str.maketrans('', '', string.punctuation)
		no_punct = text.translate(translator)
		collapsed = ' '.join(no_punct.lower().split())
		return collapsed

	def _similarity(a: str, b: str) -> float:
		return SequenceMatcher(None, a, b).ratio()

	final_result = history.final_result() or ''
	normalized_output = _normalize_text(final_result)
	eval_config = task.get('eval', {})
	eval_types = eval_config.get('eval_types', [])
	reference_answers = eval_config.get('reference_answers', {})

	results = []

	def _ensure_page_ready():
		"""Wait briefly for document readiness to reduce false negatives."""
		for _ in range(3):
			try:
				ready = controller.evaluate_in_browser('document.readyState')
				if ready == 'complete':
					return True
			except Exception:
				break
		return False

	def _url_matches(reference_url: str, current_url: str | None, ignore_query: bool = True) -> bool:
		if not reference_url or not current_url:
			return False
		ref = urlparse(reference_url)
		cur = urlparse(current_url)
		if ignore_query:
			return (ref.scheme, ref.netloc, ref.path.rstrip('/')) == (cur.scheme, cur.netloc, cur.path.rstrip('/'))
		return (ref.scheme, ref.netloc, ref.path, ref.query) == (cur.scheme, cur.netloc, cur.path, cur.query)

	# 1. String Match
	if 'string_match' in eval_types:
		exact_match = reference_answers.get('exact_match')
		must_include = reference_answers.get('must_include')
		fuzzy_match = reference_answers.get('fuzzy_match')

		if exact_match:
			normalized_expected = _normalize_text(exact_match)
			similarity = _similarity(normalized_output, normalized_expected)
			if normalized_output == normalized_expected or similarity >= 0.9:
				results.append(f'Success: Exact/near-exact match (similarity {similarity:.2f}).')
			else:
				results.append(f"Failure: Expected near match to '{exact_match}' (similarity {similarity:.2f}).")

		if must_include:
			missing = []
			for phrase in must_include:
				norm_phrase = _normalize_text(phrase)
				if norm_phrase not in normalized_output and _similarity(norm_phrase, normalized_output) < 0.7:
					missing.append(phrase)
			if not missing:
				results.append('Success: All required phrases found.')
			else:
				results.append(f'Failure: Missing phrases: {", ".join(missing)}')

		if fuzzy_match:
			# Basic implementation: check if any fuzzy match string is present
			if isinstance(fuzzy_match, list):
				similarities = []
				for phrase in fuzzy_match:
					norm_phrase = _normalize_text(phrase)
					similarities.append(_similarity(norm_phrase, normalized_output))
				max_sim = max(similarities) if similarities else 0.0
				found = max_sim >= 0.8
				match_str = ', '.join(fuzzy_match)
			else:
				norm_phrase = _normalize_text(fuzzy_match)
				max_sim = _similarity(norm_phrase, normalized_output)
				found = max_sim >= 0.8
				match_str = fuzzy_match

			if found:
				results.append(f'Success: Fuzzy match found (max similarity {max_sim:.2f}).')
			else:
				results.append(f'Failure: No fuzzy match found for {match_str}')

	# 2. URL Match
	if 'url_match' in eval_types:
		reference_url = eval_config.get('reference_url')
		if reference_url:
			url_found = False
			current_url = None

			# Try to get the actual current URL from the browser
			try:
				_ensure_page_ready()
				current_url = controller.evaluate_in_browser('window.location.href')
				if _url_matches(reference_url, current_url):
					results.append(f"Success: Current URL matches reference '{reference_url}'")
					url_found = True
			except Exception as e:
				# Log but don't fail yet - try text fallback
				logger.warning(f'Could not verify browser URL directly: {e}')

			if not url_found:
				# Fallback to checking text output if browser check fails or doesn't match
				if reference_url in final_result:
					results.append('Success: Reference URL found in output text.')
				else:
					msg = f"Failure: URL '{reference_url}' not found"
					if current_url:
						msg += f' in current location ({current_url})'
					msg += ' or output.'
					results.append(msg)

	# 3. Program HTML (DOM Check)
	if 'program_html' in eval_types:
		program_html = eval_config.get('program_html', [])
		for check in program_html:
			# We assume 'url' key might specify a page, but usually it checks the current page 'last'
			locator_js = check.get('locator')  # This is JS code to execute
			required_contents = check.get('required_contents', {})

			if locator_js:
				try:
					_ensure_page_ready()
					# WebArena locators often use document.querySelector... which returns an element or string.
					# We need to execute this JS in the browser.
					# The locator string might be like "document.querySelector(...).outerText"

					# We wrap it to ensure it returns a value we can capture
					js_code = f'(() => {{ return {locator_js}; }})()'

					execution_result = controller.evaluate_in_browser(js_code)
					execution_result_str = str(execution_result) if execution_result is not None else ''

					# Check against required contents
					exact_match = required_contents.get('exact_match')
					must_include = required_contents.get('must_include')

					if exact_match:
						if execution_result_str.strip() == exact_match.strip():
							results.append('Success (DOM): Exact match for locator.')
						else:
							results.append(f"Failure (DOM): Expected '{exact_match}', got '{execution_result_str}'")

					if must_include:
						missing = [phrase for phrase in must_include if phrase.lower() not in execution_result_str.lower()]
						if not missing:
							results.append('Success (DOM): Required content found in locator result.')
						else:
							results.append(f'Failure (DOM): Missing content in DOM: {", ".join(missing)}')

				except Exception as e:
					results.append(f"Failure (DOM): Error executing locator '{locator_js}': {e}")
			else:
				results.append('Failure (DOM): program_html check missing locator')

	if not results:
		return 'No automated evaluation criteria met or supported.'

	return '\n'.join(results)


@webarena_bp.route('/webarena/run', methods=['POST'])
def run_task():
	data = request.json or {}
	task_id = data.get('task_id')
	custom_task = data.get('custom_task')
	env_urls_override = data.get('env_urls', {})
	selected_site = data.get('selected_site')

	try:
		# Import here to avoid circular dependency
		try:
			from flask_app.app import _get_agent_controller
		except ImportError:
			# Fallback for testing where app might not be initialized as expected
			from flask import current_app

			if hasattr(current_app, '_get_agent_controller'):
				_get_agent_controller = current_app._get_agent_controller
			else:
				raise
		controller = _get_agent_controller()

		intent = ''
		current_task = None

		if task_id is not None:
			try:
				t_id = int(task_id)
				current_task = next((t for t in WEBARENA_TASKS if t.get('task_id') == t_id), None)
			except ValueError:
				pass
			if current_task:
				intent = current_task['intent']
		elif custom_task:
			intent = custom_task.get('intent')

		if not intent:
			return jsonify({'error': '有効なタスクではありません。'}), 400

		if controller.is_running():
			return jsonify({'error': 'エージェントは既に実行中です。'}), 409

		start_override = _apply_start_page_override(selected_site, env_urls_override)

		if custom_task:
			# Execute ad-hoc task without filtering
			temp_task = {
				'task_id': 'custom',
				'intent': custom_task.get('intent'),
				'start_url': custom_task.get('start_url'),
				'require_login': False,
			}
			payload = _run_single_task(temp_task, controller, env_urls_override, start_override)
		else:
			if not current_task:
				return jsonify({'error': '有効なタスクではありません。'}), 400
			payload = _run_single_task(current_task, controller, env_urls_override, start_override)

		return jsonify(payload)

	except Exception as e:
		logger.exception('WebArena evaluation failed')
		return jsonify({'error': str(e)}), 500


@webarena_bp.route('/webarena/run_batch', methods=['POST'])
def run_batch():
	"""
	Run a batch of supported WebArena tasks sequentially (no manual prompt input).
	"""
	data = request.json or {}
	env_urls_override = data.get('env_urls', {})
	selected_site = data.get('selected_site')
	task_ids = data.get('task_ids')

	# If caller didn't provide explicit IDs, run all supported tasks
	selected_tasks = WEBARENA_TASKS
	if task_ids:
		allowed = {int(t) for t in task_ids if str(t).isdigit()}
		selected_tasks = [t for t in WEBARENA_TASKS if t.get('task_id') in allowed]
	elif selected_site and selected_site in SUPPORTED_SITES:
		# Apply strict filtering: only tasks exclusive to this site
		selected_tasks = [t for t in WEBARENA_TASKS if t.get('sites') and len(t['sites']) == 1 and selected_site in t['sites']]

	if not selected_tasks:
		return jsonify({'error': '実行可能なタスクがありません。'}), 400

	try:
		from flask_app.app import _get_agent_controller
	except ImportError:
		from flask import current_app

		if hasattr(current_app, '_get_agent_controller'):
			_get_agent_controller = current_app._get_agent_controller
		else:
			raise

	controller = _get_agent_controller()

	if controller.is_running():
		return jsonify({'error': 'エージェントは既に実行中です。'}), 409

	start_override = _apply_start_page_override(selected_site, env_urls_override)

	results = []
	success_count = 0

	for task in selected_tasks:
		try:
			result = _run_single_task(task, controller, env_urls_override, start_override)
			success_count += 1 if result.get('success') else 0
			results.append(result)
		except Exception as e:
			results.append(
				{
					'task_id': task.get('task_id'),
					'success': False,
					'summary': f'Error: {e}',
					'steps': [],
					'evaluation': 'Batch runner caught an exception.',
				}
			)

	total = len(selected_tasks)
	score = round((success_count / total) * 100, 2)

	metrics = _compute_aggregate_metrics(results, selected_tasks, _WEBARENA_MAX_STEPS)

	response_data = {
		'total_tasks': total,
		'success_count': success_count,
		'score': score,
		'aggregate_metrics': metrics,
		'results': results,
	}

	# Save results to disk
	try:
		output_dir = 'webarena_data'
		if not os.path.exists(output_dir):
			os.makedirs(output_dir)

		output_file = os.path.join(output_dir, 'results.json')
		with open(output_file, 'w', encoding='utf-8') as f:
			json.dump({**response_data, 'timestamp': datetime.datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
		logger.info('Saved batch results to %s', output_file)
	except Exception as e:
		logger.error('Failed to save batch results to file: %s', e)

	return jsonify(response_data)


@webarena_bp.route('/webarena/save_results', methods=['POST'])
def save_results():
	data = request.json or {}
	results = data.get('results', [])

	if not results:
		return jsonify({'error': 'No results provided'}), 400

	# Reconstruct selected_tasks from result IDs for metric calculation
	task_map = {t['task_id']: t for t in WEBARENA_TASKS}
	ordered_tasks = []
	for r in results:
		tid = r.get('task_id')
		# If custom task or unknown, might be None, handle gracefully
		ordered_tasks.append(task_map.get(tid, {}))

	metrics = _compute_aggregate_metrics(results, ordered_tasks, _WEBARENA_MAX_STEPS)

	success_count = sum(1 for r in results if r.get('success'))
	total = len(results)
	score = round((success_count / total) * 100, 2) if total > 0 else 0.0

	response_data = {
		'total_tasks': total,
		'success_count': success_count,
		'score': score,
		'aggregate_metrics': metrics,
		'results': results,
	}

	try:
		output_dir = 'webarena_data'
		if not os.path.exists(output_dir):
			os.makedirs(output_dir)

		output_file = os.path.join(output_dir, 'results.json')
		with open(output_file, 'w', encoding='utf-8') as f:
			json.dump({**response_data, 'timestamp': datetime.datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
		logger.info('Saved batch results to %s', output_file)
		return jsonify({'success': True, 'path': output_file})
	except Exception as e:
		logger.error('Failed to save batch results to file: %s', e)
		return jsonify({'error': str(e)}), 500
