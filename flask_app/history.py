from __future__ import annotations

import queue
import threading
from contextlib import suppress
from datetime import datetime
from itertools import count
from typing import Any

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


def _initial_history() -> list[dict[str, str | int]]:
	return [
		_make_message(
			'assistant',
			'ブラウザ操作エージェントへようこそ。GeminiのAPIキー（環境変数 GOOGLE_API_KEY または GEMINI_API_KEY）とCDP URLを設定すると、左側のチャットから自然言語でChromeを操作できます。',
		)
	]


class MessageBroadcaster:
	"""Simple pub/sub helper for Server-Sent Events."""

	def __init__(self) -> None:
		self._listeners: list[queue.SimpleQueue[dict[str, Any]]] = []
		self._lock = threading.Lock()

	def subscribe(self) -> queue.SimpleQueue[dict[str, Any]]:
		listener: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
		with self._lock:
			self._listeners.append(listener)
		return listener

	def unsubscribe(self, listener: queue.SimpleQueue[dict[str, Any]]) -> None:
		with self._lock:
			with suppress(ValueError):
				self._listeners.remove(listener)

	def publish(self, event: dict[str, Any]) -> None:
		with self._lock:
			listeners = list(self._listeners)
		for listener in listeners:
			listener.put(event)

	def publish_message(self, message: dict[str, Any]) -> None:
		self.publish({'type': 'message', 'payload': message})

	def publish_update(self, message: dict[str, Any]) -> None:
		self.publish({'type': 'update', 'payload': message})

	def publish_reset(self) -> None:
		self.publish({'type': 'reset'})


_history_lock = threading.Lock()
_history: list[dict[str, str | int]] = _initial_history()
_broadcaster = MessageBroadcaster()


def _copy_history() -> list[dict[str, str | int]]:
	with _history_lock:
		return [dict(message) for message in _history]


def _append_history_message(role: str, content: str) -> dict[str, str | int]:
	message = _make_message(role, content)
	with _history_lock:
		_history.append(message)
		stored = dict(message)
	_broadcaster.publish_message(stored)
	return stored


def _update_history_message(message_id: int, new_content: str) -> dict[str, str | int] | None:
	with _history_lock:
		for entry in _history:
			if entry['id'] == message_id:
				entry['content'] = new_content
				updated = dict(entry)
				break
		else:
			return None
	_broadcaster.publish_update(updated)
	return updated


def _reset_history() -> list[dict[str, str | int]]:
	global _history, _message_sequence
	with _history_lock:
		_message_sequence = count()
		_history = _initial_history()
		snapshot = [dict(message) for message in _history]
	_broadcaster.publish_reset()
	return snapshot
