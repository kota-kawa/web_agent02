"""Runtime patch to sanitize EventBus identifiers.

The bubus ``EventBus`` recently tightened validation for the ``name``
parameter and now rejects any value that is not a valid Python identifier.
Historically parts of the codebase — and downstream extensions — relied on
passing human readable identifiers that could include hyphens or other
characters.  When follow-up tasks attempted to reuse the previous
convention, the stricter validation raised ``AssertionError`` and the agent
could no longer start.

To shield the rest of the application (and any user customisations) from
that breaking change we normalise every explicit EventBus name before the
constructor performs its validation.  The normalisation mirrors the logic
used by :mod:`browser_use.agent.eventbus` but is intentionally lightweight so
it can run during package import without pulling heavy dependencies.
"""
from __future__ import annotations

import keyword
import re
import unicodedata
from functools import wraps
from typing import Any, Callable

from bubus.service import EventBus
from uuid_extensions import uuid7str

_IDENTIFIER_CHARS = re.compile(r"[^0-9A-Za-z_]")
_RANDOM_PREFIX = "EventBus"


def _generate_fallback_identifier() -> str:
	"""Return a guaranteed valid identifier for emergency fallbacks."""

	token = uuid7str().replace('-', '')[:16]
	return f'{_RANDOM_PREFIX}_{token}'


def _normalise_identifier(candidate: str) -> str:
	"""Return a safe identifier derived from *candidate*.

	The helper preserves ASCII letters, numbers and underscores, replaces
	everything else with underscores, collapses duplicate underscores and
	trims leading/trailing underscores.  If the result would be empty, start
	with a digit, or collide with a Python keyword we generate a fresh
	fallback identifier instead.
	"""

	normalised = unicodedata.normalize('NFKC', candidate)
	sanitized = _IDENTIFIER_CHARS.sub('_', normalised)
	sanitized = re.sub(r'_+', '_', sanitized).strip('_')

	if not sanitized:
		return _generate_fallback_identifier()

	if sanitized[0].isdigit() or keyword.iskeyword(sanitized) or not sanitized.isidentifier():
		return _generate_fallback_identifier()

	return sanitized


def _maybe_sanitise(name: Any) -> Any:
	"""Sanitise ``name`` if it looks like an EventBus identifier."""

	if name is None:
		return None

	try:
		text = str(name)
	except Exception:
		return name

	return _normalise_identifier(text)


def ensure_eventbus_name_sanitizer() -> None:
	"""Patch :class:`~bubus.service.EventBus` to sanitise explicit names."""

	original_init: Callable[..., Any] = EventBus.__init__
	if getattr(original_init, '__browser_use_name_patch__', False):
		return

	@wraps(original_init)
	def _safe_eventbus_init(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
		if args:
			arg_list = list(args)
			arg_list[0] = _maybe_sanitise(arg_list[0])
			args = tuple(arg_list)
		elif 'name' in kwargs:
			kwargs['name'] = _maybe_sanitise(kwargs['name'])

		return original_init(self, *args, **kwargs)

	_safe_eventbus_init.__browser_use_name_patch__ = True  # type: ignore[attr-defined]
	EventBus.__init__ = _safe_eventbus_init  # type: ignore[assignment]


# Apply the patch immediately so every EventBus honours the sanitiser.
ensure_eventbus_name_sanitizer()

