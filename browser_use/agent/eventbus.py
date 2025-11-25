"""Utility helpers for creating and tracking named :class:`~bubus.EventBus` objects."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import ClassVar

from bubus import EventBus
from uuid_extensions import uuid7str


class EventBusFactory:
	"""Factory that produces uniquely named :class:`EventBus` instances."""

	_ACTIVE_NAMES: ClassVar[set[str]] = set()
	_PREFIX: ClassVar[str] = 'Agent'
	_SANITIZE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r'[^0-9A-Za-z_]')
	_COLLAPSE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r'_+')

	@classmethod
	def clear_active_names(cls) -> None:
		"""Reset the internal registry of reserved EventBus identifiers."""

		cls._ACTIVE_NAMES.clear()

	@classmethod
	def release(cls, name: str | None) -> None:
		"""Release a previously-reserved EventBus *name* if provided."""

		if name:
			cls._ACTIVE_NAMES.discard(name)

	# -- name generation -------------------------------------------------
	@classmethod
	def _random_name(cls) -> str:
		"""Return a random, identifier-safe EventBus name."""

		suffix = uuid7str().replace('-', '')
		return f'{cls._PREFIX}_{suffix}'

	@classmethod
	def _candidate_from_agent(cls, agent_id: str, *, force_random: bool) -> str:
		"""Return a raw candidate derived from *agent_id* or a random fallback."""

		if force_random:
			return cls._random_name()

		# Take the most identifying characters from the agent id to build a stable suffix.
		alnum = ''.join(ch for ch in str(agent_id) if ch.isalnum())
		if not alnum:
			return cls._random_name()

		suffix = alnum[-12:]
		return f'{cls._PREFIX}_{suffix}'

	@classmethod
	def sanitize(cls, raw_name: str) -> str:
		"""Normalise *raw_name* into a safe, identifier-valid EventBus name."""

		candidate = unicodedata.normalize('NFKC', raw_name or '')
		sanitized = cls._SANITIZE_PATTERN.sub('_', candidate)
		sanitized = cls._COLLAPSE_PATTERN.sub('_', sanitized).strip('_')

		if not sanitized:
			return cls._random_name()

		if not sanitized.startswith(cls._PREFIX):
			sanitized = f'{cls._PREFIX}_{sanitized}'

		# Trim overlong identifiers to keep them readable while remaining valid.
		if len(sanitized) > 64:
			sanitized = sanitized[:64].rstrip('_')

		if sanitized in {cls._PREFIX, f'{cls._PREFIX}_'}:
			return cls._random_name()

		if not sanitized.isidentifier():
			return cls._random_name()

		return sanitized

	@classmethod
	def _ensure_unique(cls, sanitized: str) -> str:
		"""Return a unique name based on *sanitized*, adding random suffixes if needed."""

		if sanitized not in cls._ACTIVE_NAMES:
			return sanitized

		# Try a handful of deterministic collisions before falling back to a fresh random name.
		for _ in range(5):
			random_suffix = uuid7str().replace('-', '')[:6]
			candidate = cls.sanitize(f'{sanitized}_{random_suffix}')
			if candidate not in cls._ACTIVE_NAMES:
				return candidate

		return cls._random_name()

	# -- EventBus creation -----------------------------------------------
	@classmethod
	def create(
		cls,
		*,
		agent_id: str,
		force_random: bool = False,
		logger: logging.Logger | None = None,
	) -> tuple[EventBus, str]:
		"""Instantiate an :class:`EventBus` with a unique, sanitised identifier."""

		log = logger or logging.getLogger(__name__)
		attempts: list[tuple[str, str]] = [('preferred', cls._candidate_from_agent(agent_id, force_random=force_random))]

		while attempts:
			label, raw_name = attempts.pop(0)
			sanitized = cls.sanitize(raw_name)
			unique_name = cls._ensure_unique(sanitized)
			unique_name = cls.sanitize(unique_name)

			try:
				bus = EventBus(name=unique_name)
			except AssertionError as exc:  # pragma: no cover - defensive logging path
				log.warning(
					'Failed to create EventBus name=%s (source=%s, raw=%s): %s',
					unique_name,
					label,
					raw_name,
					exc,
				)
				if label == 'preferred':
					attempts.append(('fallback', cls._candidate_from_agent(agent_id, force_random=True)))
				elif label == 'fallback':
					attempts.append(('emergency', cls._random_name()))
				continue
			except Exception:  # pragma: no cover - defensive logging path
				log.exception(
					'Unexpected error while creating EventBus name=%s (source=%s, raw=%s).',
					unique_name,
					label,
					raw_name,
				)
				attempts.append(('emergency', cls._random_name()))
				continue

			cls._ACTIVE_NAMES.add(bus.name)
			return bus, bus.name

		log.error('Exhausted candidate names for EventBus; falling back to anonymous EventBus().')
		fallback_bus = EventBus()
		return fallback_bus, fallback_bus.name
