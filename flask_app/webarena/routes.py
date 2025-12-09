from flask import render_template, request, jsonify
from . import webarena_bp
from .tasks import WEBARENA_TASKS
from flask_app.env_utils import _BROWSER_URL
from flask_app.formatting import _format_history_messages
import logging

logger = logging.getLogger(__name__)

@webarena_bp.route('/webarena')
def index():
    return render_template('webarena.html', tasks=WEBARENA_TASKS, browser_url=_BROWSER_URL)

def _evaluate_result(history, task):
    """
    Simple evaluation logic based on task type.
    """
    if not task:
        return "Custom task - no automated evaluation"

    final_result = history.final_result() or ""
    eval_type = task.get('evaluation_type')
    reference = task.get('reference')

    if not eval_type or not reference:
        return "No evaluation criteria defined"

    if eval_type == 'string_match':
        if reference.lower() in final_result.lower():
            return f"Success: Found '{reference}' in result"
        return f"Failure: '{reference}' not found in result"

    if eval_type == 'url_match':
        # Need to check the final URL. Agent history usually has 'urls' in state.
        # This is a bit tricky without direct access to the browser state object at the end,
        # but history messages might contain the URL or we assume the agent reports it.
        # For now, we check if the reference URL appears in the final result text as a proxy.
        if reference in final_result:
             return f"Success: URL '{reference}' found in result"
        return f"Failure: URL '{reference}' not found in result"

    return f"Unknown evaluation type: {eval_type}"

@webarena_bp.route('/webarena/run', methods=['POST'])
def run_task():
    data = request.json or {}
    task_id = data.get('task_id')
    custom_task = data.get('custom_task')

    intent = ""
    start_url = ""
    current_task = None

    if task_id is not None:
        current_task = next((t for t in WEBARENA_TASKS if t['id'] == str(task_id)), None)
        if current_task:
            intent = current_task['intent']
            start_url = current_task['start_url']
    elif custom_task:
        intent = custom_task.get('intent')
        start_url = custom_task.get('start_url')

    if not intent:
        return jsonify({'error': '有効なタスクではありません。'}), 400

    try:
        from flask_app.app import _get_agent_controller
        controller = _get_agent_controller()

        # Note: Model selection is handled by the frontend calling /model_settings
        # before calling this endpoint, so the controller should already be using the correct model.

        full_prompt = intent
        if start_url:
            full_prompt = f"まず {start_url} に移動してください。その後、以下の指示を実行してください: {intent}"

        if controller.is_running():
             return jsonify({'error': 'エージェントは既に実行中です。'}), 409

        result = controller.run(full_prompt)
        history = result.history

        step_messages = _format_history_messages(history)

        evaluation_msg = _evaluate_result(history, current_task)

        # Determine success based on evaluation if possible, otherwise rely on agent's self-report
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
