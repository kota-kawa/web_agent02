from flask import render_template, request, jsonify
from . import webarena_bp
import logging
import json
import os
import re
import subprocess
import urllib.request
import urllib.error
from flask_app.env_utils import _BROWSER_URL
from flask_app.formatting import _format_history_messages

logger = logging.getLogger(__name__)

# Only these environments are provisioned locally
SUPPORTED_SITES = {"shopping", "shopping_admin", "reddit", "gitlab"}

# Load WebArena tasks from JSON and filter to supported environments
TASKS_FILE = os.path.join(os.path.dirname(__file__), 'tasks_data/test.json')


def _load_tasks():
    try:
        with open(TASKS_FILE, 'r') as f:
            all_tasks = json.load(f)
    except Exception:
        logger.warning("Could not load WebArena tasks from %s", TASKS_FILE)
        return [], []

    def _is_supported(task):
        sites = task.get('sites', []) or []
        # Keep tasks that only reference environments we actually have
        return bool(sites) and all(site in SUPPORTED_SITES for site in sites)

    supported_tasks = [t for t in all_tasks if _is_supported(t)]

    if len(supported_tasks) != len(all_tasks):
        logger.info(
            "WebArena tasks filtered: %s supported of %s total (allowed sites: %s)",
            len(supported_tasks), len(all_tasks), ",".join(sorted(SUPPORTED_SITES)),
        )

    return all_tasks, supported_tasks


ALL_WEBARENA_TASKS, WEBARENA_TASKS = _load_tasks()

# Optional external reset hooks
RESET_COMMAND = os.getenv("WEBARENA_RESET_COMMAND")  # e.g., "docker compose -f bin/webarena/docker-compose.webarena.yml restart shopping shopping_admin gitlab forum"
RESET_URL = os.getenv("WEBARENA_RESET_URL")  # e.g., "http://localhost:7000/reset"

# Default base URLs for WebArena environments
DEFAULT_ENV_URLS = {
    "shopping": os.getenv("WEBARENA_SHOPPING_URL", "http://shopping:80"),
    "shopping_admin": os.getenv("WEBARENA_SHOPPING_ADMIN_URL", "http://shopping_admin:80"),
    "gitlab": os.getenv("WEBARENA_GITLAB_URL", "http://gitlab:8023"),
    "reddit": os.getenv("WEBARENA_REDDIT_URL", "http://forum:80"),
    "wikipedia": os.getenv("WEBARENA_WIKIPEDIA_URL", "http://wikipedia:80")
}

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
        tasks = [t for t in tasks if site_filter in (t.get('sites') or [])]

    start = (page - 1) * per_page
    end = start + per_page
    return jsonify({
        'tasks': tasks[start:end],
        'total': len(tasks),
        'page': page,
        'per_page': per_page
    })

def _resolve_start_url(task, env_urls_override=None):
    start_url = task.get('start_url', '')
    env_urls = DEFAULT_ENV_URLS.copy()
    if env_urls_override:
        env_urls.update(env_urls_override)

    replacements = {
        "__SHOPPING__": env_urls.get("shopping"),
        "__SHOPPING_ADMIN__": env_urls.get("shopping_admin"),
        "__GITLAB__": env_urls.get("gitlab"),
        "__REDDIT__": env_urls.get("reddit"),
        "__WIKIPEDIA__": env_urls.get("wikipedia", "https://en.wikipedia.org/wiki"),
    }

    for placeholder, base_url in replacements.items():
        if base_url and placeholder in start_url:
            start_url = start_url.replace(placeholder, base_url)

    return start_url


def _reset_state(controller, sites):
    """
    Best-effort reset for WebArena between tasks.
    - Always reset the browser controller session (clears cookies/storage).
    - Optionally call external reset hook via command or HTTP endpoint for backend state.
    """
    # 1) Reset browser session
    try:
        controller.reset()
        controller.ensure_start_page_ready()
    except Exception as e:
        logger.warning("Browser reset failed: %s", e)

    # 2) External reset hook (if configured)
    sites_csv = ",".join(sites or [])

    if RESET_URL:
        try:
            data = json.dumps({"sites": sites or []}).encode("utf-8")
            req = urllib.request.Request(RESET_URL, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=180) as resp:
                logger.info("Reset URL responded with status %s", resp.getcode())
        except Exception as e:
            logger.warning("External reset via URL failed: %s", e)
    elif RESET_COMMAND:
        cmd = RESET_COMMAND.format(sites=sites_csv)
        try:
            subprocess.run(cmd, shell=True, check=True, timeout=300)
            logger.info("Executed reset command: %s", cmd)
        except Exception as e:
            logger.warning("External reset command failed: %s", e)
    else:
        if sites:
            logger.warning("No WEBARENA_RESET_COMMAND/URL configured. Only browser session was reset for sites: %s", sites_csv)
        else:
            logger.warning("No WEBARENA_RESET_COMMAND/URL configured. Only browser session was reset.")


def _run_single_task(task, controller, env_urls_override=None):
    """
    Execute one WebArena task with the shared controller and return the result payload.
    """
    intent = task.get('intent', '')
    if not intent:
        raise ValueError("Task is missing intent.")

    start_url = _resolve_start_url(task, env_urls_override)

    full_prompt = intent
    if start_url:
        full_prompt = f"Navigate to {start_url} first. Then, {intent}"

    if task.get('require_login'):
        full_prompt += " (Note: This task may require logging in. If credentials are unknown, fail gracefully.)"

    _reset_state(controller, task.get('sites') or [])

    result = controller.run(full_prompt)
    history = result.history

    evaluation_msg = _evaluate_result(history, task, controller)
    success = history.is_successful()
    if "Failure" in evaluation_msg:
        success = False
    elif "Success" in evaluation_msg and not success:
        # evaluation says success but agent flagged failure; keep agent status unless string match succeeded
        pass

    return {
        'task_id': task.get('task_id'),
        'success': success,
        'summary': history.final_result(),
        'steps': [{'step': n, 'content': c} for n, c in _format_history_messages(history)],
        'evaluation': evaluation_msg,
    }

def _evaluate_result(history, task, controller):
    """
    Evaluation logic based on WebArena 'eval' fields.
    """
    if not task:
        return "Custom task - no automated evaluation"

    final_result = history.final_result() or ""
    eval_config = task.get('eval', {})
    eval_types = eval_config.get('eval_types', [])
    reference_answers = eval_config.get('reference_answers', {})

    results = []

    # 1. String Match
    if 'string_match' in eval_types:
        exact_match = reference_answers.get('exact_match')
        must_include = reference_answers.get('must_include')
        fuzzy_match = reference_answers.get('fuzzy_match')

        if exact_match:
            if final_result.strip() == exact_match.strip():
                results.append("Success: Exact match found.")
            else:
                results.append(f"Failure: Expected exact match '{exact_match}'.")

        if must_include:
            missing = [phrase for phrase in must_include if phrase.lower() not in final_result.lower()]
            if not missing:
                results.append("Success: All required phrases found.")
            else:
                results.append(f"Failure: Missing phrases: {', '.join(missing)}")

        if fuzzy_match:
             # Basic implementation: check if any fuzzy match string is present
             if isinstance(fuzzy_match, list):
                 found = any(phrase.lower() in final_result.lower() for phrase in fuzzy_match)
                 match_str = ", ".join(fuzzy_match)
             else:
                 found = fuzzy_match.lower() in final_result.lower()
                 match_str = fuzzy_match

             if found:
                 results.append("Success: Fuzzy match found.")
             else:
                 results.append(f"Failure: No fuzzy match found for {match_str}")

    # 2. URL Match
    if 'url_match' in eval_types:
        reference_url = eval_config.get('reference_url')
        if reference_url:
            # Try to get the actual current URL from the browser
            try:
                current_url = controller.evaluate_in_browser("window.location.href")
                if reference_url in current_url:
                    results.append(f"Success: Current URL matches reference '{reference_url}'")
                else:
                     # Fallback to checking text output if browser check fails or doesn't match
                    if reference_url in final_result:
                        results.append(f"Success: Reference URL found in output text.")
                    else:
                        results.append(f"Failure: URL '{reference_url}' not found in current location ({current_url}) or output.")
            except Exception as e:
                results.append(f"Warning: Could not verify browser URL: {e}")

    # 3. Program HTML (DOM Check)
    if 'program_html' in eval_types:
        program_html = eval_config.get('program_html', [])
        for check in program_html:
            # We assume 'url' key might specify a page, but usually it checks the current page 'last'
            locator_js = check.get('locator') # This is JS code to execute
            required_contents = check.get('required_contents', {})

            if locator_js:
                try:
                    # WebArena locators often use document.querySelector... which returns an element or string.
                    # We need to execute this JS in the browser.
                    # The locator string might be like "document.querySelector(...).outerText"

                    # We wrap it to ensure it returns a value we can capture
                    js_code = f"(() => {{ return {locator_js}; }})()"

                    execution_result = controller.evaluate_in_browser(js_code)
                    execution_result_str = str(execution_result) if execution_result is not None else ""

                    # Check against required contents
                    exact_match = required_contents.get('exact_match')
                    must_include = required_contents.get('must_include')

                    if exact_match:
                        if execution_result_str.strip() == exact_match.strip():
                             results.append(f"Success (DOM): Exact match for locator.")
                        else:
                             results.append(f"Failure (DOM): Expected '{exact_match}', got '{execution_result_str}'")

                    if must_include:
                        missing = [phrase for phrase in must_include if phrase.lower() not in execution_result_str.lower()]
                        if not missing:
                             results.append(f"Success (DOM): Required content found in locator result.")
                        else:
                             results.append(f"Failure (DOM): Missing content in DOM: {', '.join(missing)}")

                except Exception as e:
                    results.append(f"Failure (DOM): Error executing locator '{locator_js}': {e}")
            else:
                 results.append("Warning: program_html check missing locator")

    if not results:
        return "No automated evaluation criteria met or supported."

    return "\n".join(results)

@webarena_bp.route('/webarena/run', methods=['POST'])
def run_task():
    data = request.json or {}
    task_id = data.get('task_id')
    custom_task = data.get('custom_task')
    env_urls_override = data.get('env_urls', {})

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

        intent = ""
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

        if custom_task:
            # Execute ad-hoc task without filtering
            temp_task = {
                'task_id': 'custom',
                'intent': custom_task.get('intent'),
                'start_url': custom_task.get('start_url'),
                'require_login': False,
            }
            payload = _run_single_task(temp_task, controller, env_urls_override)
        else:
            if not current_task:
                return jsonify({'error': '有効なタスクではありません。'}), 400
            payload = _run_single_task(current_task, controller, env_urls_override)

        return jsonify(payload)

    except Exception as e:
        logger.exception("WebArena evaluation failed")
        return jsonify({'error': str(e)}), 500


@webarena_bp.route('/webarena/run_batch', methods=['POST'])
def run_batch():
    """
    Run a batch of supported WebArena tasks sequentially (no manual prompt input).
    """
    data = request.json or {}
    env_urls_override = data.get('env_urls', {})
    task_ids = data.get('task_ids')

    # If caller didn't provide explicit IDs, run all supported tasks
    selected_tasks = WEBARENA_TASKS
    if task_ids:
        allowed = set(int(t) for t in task_ids if str(t).isdigit())
        selected_tasks = [t for t in WEBARENA_TASKS if t.get('task_id') in allowed]

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

    results = []
    success_count = 0

    for task in selected_tasks:
        try:
            result = _run_single_task(task, controller, env_urls_override)
            success_count += 1 if result.get('success') else 0
            results.append(result)
        except Exception as e:
            results.append({
                'task_id': task.get('task_id'),
                'success': False,
                'summary': f'Error: {e}',
                'steps': [],
                'evaluation': 'Batch runner caught an exception.',
            })

    total = len(selected_tasks)
    score = round((success_count / total) * 100, 2)

    return jsonify({
        'total_tasks': total,
        'success_count': success_count,
        'score': score,
        'results': results,
    })
