import io
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from flask_app import app as flask_app_module


@pytest.fixture(autouse=True)
def reset_cdp_cleanup() -> None:
    cleanup = flask_app_module._consume_cdp_session_cleanup()
    if cleanup:
        cleanup()
    yield
    cleanup = flask_app_module._consume_cdp_session_cleanup()
    if cleanup:
        cleanup()


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200) -> None:
        super().__init__(payload)
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.close()


def _extract_request_info(request: Any) -> tuple[str, str]:
    if isinstance(request, str):
        return 'GET', request
    method = request.get_method()
    url = request.full_url
    return method, url


def test_resolve_cdp_url_prefers_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BROWSER_USE_CDP_URL', 'ws://explicit-host')

    result = flask_app_module._resolve_cdp_url()

    assert result == 'ws://explicit-host'
    assert flask_app_module._consume_cdp_session_cleanup() is None


def test_resolve_cdp_url_uses_webdriver_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('BROWSER_USE_CDP_URL', raising=False)

    monkeypatch.setattr(flask_app_module, '_probe_cdp_candidate', lambda candidate: None)

    seen: list[str] = []

    def fake_webdriver_probe(candidate: str) -> str | None:
        seen.append(candidate)
        if candidate == 'http://browser:4444':
            flask_app_module._replace_cdp_session_cleanup(lambda: None)
            return 'ws://via-webdriver'
        return None

    monkeypatch.setattr(flask_app_module, '_probe_cdp_via_webdriver', fake_webdriver_probe)

    result = flask_app_module._resolve_cdp_url()

    assert result == 'ws://via-webdriver'
    assert 'http://browser:4444' in seen
    cleanup = flask_app_module._consume_cdp_session_cleanup()
    assert cleanup is not None
    cleanup()


def test_probe_cdp_via_webdriver_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_urlopen(request: Any, timeout: float | int) -> FakeResponse:
        method, url = _extract_request_info(request)
        calls.append((method, url))

        if method == 'POST' and url.endswith('/session'):
            payload = json.dumps(
                {
                    'value': {
                        'sessionId': 'abc123',
                        'capabilities': {'se:cdp': ' ws://example.devtools '},
                    }
                }
            ).encode('utf-8')
            return FakeResponse(payload)

        if method == 'DELETE' and url.endswith('/session/abc123'):
            return FakeResponse(b'{}')

        raise AssertionError(f'unexpected request {method} {url}')

    monkeypatch.setattr(flask_app_module, 'urlopen', fake_urlopen)

    result = flask_app_module._probe_cdp_via_webdriver('http://selenium:4444')

    assert result == 'ws://example.devtools'
    assert calls[0][0] == 'POST'

    cleanup = flask_app_module._consume_cdp_session_cleanup()
    assert callable(cleanup)
    assert len(calls) == 1

    cleanup()

    assert len(calls) == 2
    assert calls[1][0] == 'DELETE'
    assert calls[1][1].endswith('/session/abc123')

    cleanup()

    assert len(calls) == 2
