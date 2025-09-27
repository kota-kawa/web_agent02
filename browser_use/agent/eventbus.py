"""Utilities for creating uniquely named EventBus instances.

This module centralises the logic for generating safe EventBus identifiers
so we can reuse it from different agent entry points and test it in isolation.
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

from bubus import EventBus
from uuid_extensions import uuid7str


class EventBusFactory:
    """Factory that creates EventBus instances with safe identifiers."""

    _ACTIVE_NAMES: ClassVar[set[str]] = set()
    _PREFIX: ClassVar[str] = 'Agent_'

    @classmethod
    def clear_active_names(cls) -> None:
        """Clear the registry of active EventBus names (primarily for tests)."""

        cls._ACTIVE_NAMES.clear()

    @classmethod
    def release(cls, name: str | None) -> None:
        """Release a previously reserved EventBus *name* if present."""

        if name:
            cls._ACTIVE_NAMES.discard(name)

    @classmethod
    def sanitize(cls, name: str) -> str:
        """Return a valid identifier derived from *name*.

        The sanitizer preserves alphanumeric characters and underscores, swaps
        other characters for underscores, collapses duplicate underscores, and
        ensures the resulting value always starts with the ``Agent_`` prefix.
        If sanitisation produces an empty identifier it retries with a UUID
        derived suffix so the function never returns an invalid identifier.
        """

        candidate = name

        while True:
            sanitized = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in candidate)
            sanitized = re.sub(r'_+', '_', sanitized).strip('_')

            if not sanitized:
                candidate = f'{cls._PREFIX}{uuid7str()}'
                continue

            if not sanitized.startswith(cls._PREFIX):
                sanitized = f'{cls._PREFIX}{sanitized}'

            if sanitized in {cls._PREFIX, cls._PREFIX.rstrip('_')} or not sanitized.isidentifier():
                candidate = f'{cls._PREFIX}{uuid7str()}'
                continue

            return sanitized

    @classmethod
    def _ensure_unique(cls, name: str) -> str:
        """Return a version of *name* that is not already reserved."""

        if name not in cls._ACTIVE_NAMES:
            return name

        for _ in range(5):
            random_suffix = uuid7str().replace('-', '')[:6]
            candidate = cls.sanitize(f'{name}_{random_suffix}')
            if candidate not in cls._ACTIVE_NAMES:
                return candidate

        return cls.sanitize(f'{cls._PREFIX}{uuid7str()}')

    @classmethod
    def _generate_candidate(cls, agent_id: str, *, force_random: bool) -> str:
        """Create an initial EventBus name candidate for *agent_id*."""

        if not force_random:
            suffix_source = ''.join(ch for ch in str(agent_id) if ch.isalnum())
            suffix = suffix_source[-8:] if suffix_source else ''
        else:
            suffix = ''

        if not suffix:
            suffix = uuid7str().replace('-', '')[-8:]

        return f'{cls._PREFIX}{suffix}'

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        force_random: bool = False,
        logger: logging.Logger | None = None,
    ) -> tuple[EventBus, str]:
        """Instantiate an EventBus with a safe, unique identifier."""

        log = logger or logging.getLogger(__name__)

        attempts: list[tuple[str, str]] = []
        preferred_name_raw = cls._generate_candidate(agent_id, force_random=force_random)
        attempts.append(("preferred", preferred_name_raw))

        bus: EventBus | None = None
        created_name = ''
        last_error: Exception | None = None
        idx = 0

        while idx < len(attempts):
            label, raw_name = attempts[idx]
            idx += 1

            sanitized = cls.sanitize(raw_name)
            unique_candidate = cls._ensure_unique(sanitized)
            final_candidate = cls.sanitize(unique_candidate)

            try:
                if not final_candidate.isidentifier():
                    raise ValueError(
                        f'Invalid EventBus identifier produced after sanitization: {final_candidate!r}'
                    )

                bus = EventBus(name=final_candidate)
                created_name = final_candidate
                break
            except Exception as exc:  # pragma: no cover - defensive logging paths
                last_error = exc
                log_message = 'Failed to create EventBus with %s name %s (raw=%s, %s)'

                if label == 'preferred':
                    log.warning(
                        log_message + '. Trying fallback name.',
                        label,
                        final_candidate,
                        raw_name,
                        exc,
                        exc_info=isinstance(exc, AssertionError),
                    )
                    fallback_candidate = cls._generate_candidate(agent_id, force_random=True)
                    attempts.append(('fallback', fallback_candidate))
                elif label == 'fallback':
                    log.error(
                        log_message + '. Using emergency name.',
                        label,
                        final_candidate,
                        raw_name,
                        exc,
                        exc_info=True,
                    )
                    emergency_candidate = f'{cls._PREFIX}{uuid7str()}'
                    attempts.append(('emergency', emergency_candidate))
                else:
                    log.error(
                        log_message + '. No more candidates available.',
                        label,
                        final_candidate,
                        raw_name,
                        exc,
                        exc_info=True,
                    )

        if bus is None:
            if last_error is not None:  # pragma: no cover - defensive fallback logging
                log.error(
                    'Creating named EventBus failed (%s). Falling back to anonymous EventBus().',
                    last_error,
                    exc_info=True,
                )
            bus = EventBus()
            created_name = bus.name

        cls._ACTIVE_NAMES.add(created_name)
        return bus, created_name
