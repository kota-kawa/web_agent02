from flask import render_template, request, jsonify
from . import webarena_bp
import logging
import json
import os
import re
from flask_app.env_utils import _BROWSER_URL
from flask_app.formatting import _format_history_messages
from flask_app.webarena.evaluation import WebArenaEvaluator

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
DEFAULT_ENV_URLS = {
    "shopping": os.getenv("WEBARENA_SHOPPING_URL", "http://shopping:80"),
    "shopping_admin": os.getenv("WEBARENA_SHOPPING_ADMIN_URL", "http://shopping_admin:80"),
    "gitlab": os.getenv("WEBARENA_GITLAB_URL", "http://gitlab:8023"),
    "reddit": os.getenv("WEBARENA_REDDIT_URL", "http://forum:80"),
    "map": os.getenv("WEBARENA_MAP_URL", "http://map:3000"),
    "wikipedia": os.getenv("WEBARENA_WIKIPEDIA_URL", "https://en.wikipedia.org/wiki")
}

@webarena_bp.route('/webarena')
def index():
    return render_template('webarena.html', browser_url=_BROWSER_URL, env_urls=DEFAULT_ENV_URLS)

@webarena_bp.route('/webarena/tasks')
def get_tasks():
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
    start_url = task.get('start_url', '')
    env_urls = DEFAULT_ENV_URLS.copy()
    if env_urls_override:
        env_urls.update(env_urls_override)

    replacements = {
        "__SHOPPING__": env_urls.get("shopping"),
        "__SHOPPING_ADMIN__": env_urls.get("shopping_admin"),
        "__GITLAB__": env_urls.get("gitlab"),
        "__REDDIT__": env_urls.get("reddit"),
        "__MAP__": env_urls.get("map"),
        "__WIKIPEDIA__": env_urls.get("wikipedia"),
    }

    for placeholder, base_url in replacements.items():
        if base_url and placeholder in start_url:
            start_url = start_url.replace(placeholder, base_url)

    return start_url

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

        full_prompt = intent
        if start_url:
            full_prompt = f"Navigate to {start_url} first. Then, {intent}"

        if controller.is_running():
             return jsonify({'error': 'エージェントは既に実行中です。'}), 409

        if current_task and current_task.get('require_login'):
            full_prompt += " (Note: This task may require logging in. If credentials are unknown, fail gracefully.)"

        result = controller.run(full_prompt)
        history = result.history

        step_messages = _format_history_messages(history)

        # Evaluation
        evaluator = WebArenaEvaluator(controller)
        evaluation_msg = evaluator.evaluate(current_task, history, history.final_result() or "")

        success = history.is_successful()
        if "Failure" in evaluation_msg:
            success = False
        elif "Success" in evaluation_msg and not success:
             # Check if all criteria in evaluation were successes
             # If evaluation says success but agent self-report says fail, we trust evaluation (ground truth)
             pass

        return jsonify({
            'success': success,
            'summary': history.final_result(),
            'steps': [{'step': n, 'content': c} for n, c in step_messages],
            'evaluation': evaluation_msg
        })

    except Exception as e:
        logger.exception("WebArena evaluation failed")
        return jsonify({'error': str(e)}), 500
