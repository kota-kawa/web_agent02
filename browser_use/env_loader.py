from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_ENV_CANDIDATES = [
	Path(__file__).resolve().parents[1] / 'secrets.env',
	Path(__file__).resolve().parent / 'secrets.env',
]


def load_secrets_env() -> None:
	"""Load secrets.env files for the browser agent with a legacy fallback."""

	loaded = False
	for env_path in _ENV_CANDIDATES:
		if load_dotenv(env_path, override=False):
			loaded = True
	if not loaded:
		load_dotenv()
