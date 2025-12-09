from browser_use.agent.service import Agent
from browser_use.browser.views import PLACEHOLDER_4PX_SCREENSHOT, BrowserStateSummary
from browser_use.dom.views import SerializedDOMState


def _make_state(
	url: str = 'https://example.com',
	selector_map: dict | None = None,
	screenshot: str | None = 'data:image/png;base64,abc',
	is_pdf: bool = False,
	browser_errors: list[str] | None = None,
) -> BrowserStateSummary:
	return BrowserStateSummary(
		dom_state=SerializedDOMState(_root=None, selector_map=selector_map or {}),
		url=url,
		title='',
		tabs=[],
		screenshot=screenshot,
		browser_errors=browser_errors or [],
		is_pdf_viewer=is_pdf,
	)


def test_retry_when_dom_is_empty_on_http_page():
	state = _make_state(selector_map={}, screenshot='real')
	assert Agent._should_retry_browser_state(state) is True


def test_no_retry_when_dom_has_elements():
	state = _make_state(selector_map={1: object()})
	assert Agent._should_retry_browser_state(state) is False


def test_no_retry_for_placeholder_screenshot():
	state = _make_state(selector_map={}, screenshot=PLACEHOLDER_4PX_SCREENSHOT)
	assert Agent._should_retry_browser_state(state) is False


def test_no_retry_for_non_http_urls():
	state = _make_state(url='about:blank', selector_map={})
	assert Agent._should_retry_browser_state(state) is False


def test_no_retry_for_pdf_or_browser_errors():
	pdf_state = _make_state(url='https://example.com/file.pdf', selector_map={}, is_pdf=True)
	error_state = _make_state(selector_map={}, browser_errors=['oops'])

	assert Agent._should_retry_browser_state(pdf_state) is False
	assert Agent._should_retry_browser_state(error_state) is False
