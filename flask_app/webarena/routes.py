from flask import render_template, request, jsonify
from . import webarena_bp
import logging
import json
import os
from flask_app.env_utils import _BROWSER_URL
from flask_app.formatting import _format_history_messages

logger = logging.getLogger(__name__)

# Load WebArena tasks from JSON
TASKS_FILE = os.path.join(os.path.dirname(__file__), 'tasks_data/test.json')
try:
    with open(TASKS_FILE, 'r') as f:
        WEBARENA_TASKS = json.load(f)
except Exception:
    WEBARENA_TASKS = []
    logger.warning("Could not load WebArena tasks from %s", TASKS_FILE)

# Default base URLs for WebArena environments
# In a real deployment, these should be configurable via env vars or UI
DEFAULT_ENV_URLS = {
    "shopping": "http://shopping.webarena.local",
    "shopping_admin": "http://shopping_admin.webarena.local",
    "gitlab": "http://gitlab.webarena.local",
    "reddit": "http://reddit.webarena.local",
    "map": "http://map.webarena.local",
    "wikipedia": "http://wikipedia.webarena.local"
}

@webarena_bp.route('/webarena')
def index():
    # Only send a subset or light version of tasks to frontend to avoid heavy load
    # For now, we send all but maybe we should paginate in the future
    return render_template('webarena.html', tasks=WEBARENA_TASKS, browser_url=_BROWSER_URL, env_urls=DEFAULT_ENV_URLS)

@webarena_bp.route('/webarena/tasks')
def get_tasks():
    # API to fetch tasks if we move to client-side pagination
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    start = (page - 1) * per_page
    end = start + per_page
    return jsonify({
        'tasks': WEBARENA_TASKS[start:end],
        'total': len(WEBARENA_TASKS),
        'page': page,
        'per_page': per_page
    })

def _resolve_start_url(task, env_urls_override=None):
    """
    Resolves the start URL for a task, replacing placeholders like __SHOPPING__
    with the actual configured base URL.
    """
    start_url = task.get('start_url', '')
    env_urls = DEFAULT_ENV_URLS.copy()
    if env_urls_override:
        env_urls.update(env_urls_override)

    # Replacements based on WebArena conventions
    replacements = {
        "__SHOPPING__": env_urls.get("shopping"),
        "__SHOPPING_ADMIN__": env_urls.get("shopping_admin"),
        "__GITLAB__": env_urls.get("gitlab"),
        "__REDDIT__": env_urls.get("reddit"),
        "__MAP__": env_urls.get("map"),
        "__WIKIPEDIA__": env_urls.get("wikipedia", "https://en.wikipedia.org/wiki"), # Default fallback
    }

    for placeholder, base_url in replacements.items():
        if base_url and placeholder in start_url:
            start_url = start_url.replace(placeholder, base_url)

    return start_url

def _evaluate_result(history, task):
    """
    Evaluation logic based on WebArena 'eval' fields.
    Supports 'string_match' and 'url_match'.
    """
    if not task:
        return "Custom task - no automated evaluation"

    final_result = history.final_result() or ""
    eval_config = task.get('eval', {})
    eval_types = eval_config.get('eval_types', [])
    reference_answers = eval_config.get('reference_answers', {})

    # 1. String Match
    if 'string_match' in eval_types:
        exact_match = reference_answers.get('exact_match')
        must_include = reference_answers.get('must_include')
        fuzzy_match = reference_answers.get('fuzzy_match')

        if exact_match:
            if final_result.strip() == exact_match.strip():
                return "Success: Exact match found."
            return f"Failure: Expected exact match '{exact_match}'."

        if must_include:
            missing = [phrase for phrase in must_include if phrase.lower() not in final_result.lower()]
            if not missing:
                return "Success: All required phrases found."
            return f"Failure: Missing phrases: {', '.join(missing)}"

        if fuzzy_match:
             # Basic implementation: check if any fuzzy match string is present
             # Real WebArena uses more complex logic (F1 score etc)
             matches = [phrase for phrase in fuzzy_match if phrase.lower() in final_result.lower()]
             if matches:
                 return "Success: Fuzzy match found."
             return f"Failure: No fuzzy match found for {fuzzy_match}"

    # 2. URL Match (Checking final URL)
    if 'url_match' in eval_types:
        # Note: We'd need the final URL from the browser state.
        # Currently the 'history' object might not strictly expose the final URL in a structured way
        # unless we parse the last message or agent state.
        # Assuming we might not have it easily, we skip or do a best effort if it's in the text.
        reference_url = eval_config.get('reference_url')
        if reference_url and reference_url in final_result:
             return "Success: Reference URL found in output."
        # If we can't check the actual browser URL, we warn.
        return "Inconclusive: URL match required but cannot verify browser URL state directly."

    return "Evaluation type not fully supported or inconclusive."

@webarena_bp.route('/webarena/run', methods=['POST'])
def run_task():
    data = request.json or {}
    task_id = data.get('task_id')
    custom_task = data.get('custom_task')
    env_urls_override = data.get('env_urls', {})

    intent = ""
    start_url = ""
    current_task = None

    if task_id is not None:
        # WebArena tasks in JSON use integer IDs, but we might receive string
        try:
            t_id = int(task_id)
            current_task = next((t for t in WEBARENA_TASKS if t.get('task_id') == t_id), None)
        except ValueError:
            pass

        if current_task:
            intent = current_task['intent']
            start_url = _resolve_start_url(current_task, env_urls_override)
    elif custom_task:
        intent = custom_task.get('intent')
        start_url = custom_task.get('start_url')

    if not intent:
        return jsonify({'error': '有効なタスクではありません。'}), 400

    try:
        from flask_app.app import _get_agent_controller
        controller = _get_agent_controller()

        full_prompt = intent
        if start_url:
            full_prompt = f"Navigate to {start_url} first. Then, {intent}"

        if controller.is_running():
             return jsonify({'error': 'エージェントは既に実行中です。'}), 409

        # We pass the prompt. The controller handles the browser loop.
        # Note: WebArena often requires authentication (login).
        # The 'require_login' field in task tells us this.
        # Automating login is complex without credential injection.
        # We assume the user might be logged in or the agent can handle it if creds are provided in prompt.
        if current_task and current_task.get('require_login'):
            full_prompt += " (Note: This task may require logging in. If credentials are unknown, fail gracefully.)"

        result = controller.run(full_prompt)
        history = result.history

        step_messages = _format_history_messages(history)

        evaluation_msg = _evaluate_result(history, current_task)

        success = history.is_successful()
        if "Failure:" in evaluation_msg:
            success = False
        elif "Success:" in evaluation_msg:
            success = True

        return jsonify({
            'success': success,
            'summary': history.final_result(),
            'steps': [{'step': n, 'content': c} for n, c in step_messages],
            'evaluation': evaluation_msg
        })

    except Exception as e:
        logger.exception("WebArena evaluation failed")
        return jsonify({'error': str(e)}), 500
