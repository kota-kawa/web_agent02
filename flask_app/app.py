from __future__ import annotations

import os
from datetime import datetime
from itertools import count
from flask import Flask, jsonify, render_template, request
from flask.typing import ResponseReturnValue

app = Flask(__name__)

_message_sequence = count()


def _utc_timestamp() -> str:
    """Return a simple ISO 8601 timestamp in UTC."""
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def _make_message(role: str, content: str) -> dict[str, str | int]:
    return {
        'id': next(_message_sequence),
        'role': role,
        'content': content,
        'timestamp': _utc_timestamp(),
    }


_history: list[dict[str, str | int]] = [
    _make_message(
        'assistant',
        'ブラウザ操作用のデモUIへようこそ。左側のチャット欄から指示を送信できます。',
    )
]

_BROWSER_URL = os.environ.get(
    'EMBED_BROWSER_URL',
    'http://127.0.0.1:7900/?autoconnect=1&resize=scale',
)


def _demo_updates(prompt: str) -> list[str]:
    """Create placeholder status updates for the demo assistant."""
    return [
        'LLM: 受け取った指示を解析しています…',
        'LLM: ブラウザ操作の計画を立てています…',
        f'LLM: これはデモ応答です。実際の処理は「{prompt}」に合わせて実装できます。',
    ]


@app.route('/')
def index() -> str:
    return render_template('index.html', browser_url=_BROWSER_URL)


@app.get('/api/history')
def history() -> ResponseReturnValue:
    return jsonify({'messages': _history}), 200


@app.post('/api/chat')
def chat() -> ResponseReturnValue:
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get('prompt') or '').strip()

    if not prompt:
        return jsonify({'error': 'プロンプトを入力してください。'}), 400

    _history.append(_make_message('user', prompt))

    for update in _demo_updates(prompt):
        _history.append(_make_message('assistant', update))

    return jsonify({'messages': _history}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
