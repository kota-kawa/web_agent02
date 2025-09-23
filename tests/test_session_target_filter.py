from browser_use.browser.session import BrowserSession


def make_session() -> BrowserSession:
    return BrowserSession()


def test_is_valid_target_excludes_oopif_without_iframe_support():
    session = make_session()
    oopif_target = {
        'targetId': 'oopif',
        'type': 'page',
        'subtype': 'iframe',
        'url': 'https://example.com',
    }

    assert not session._is_valid_target(
        oopif_target,
        include_http=True,
        include_iframes=False,
        include_pages=True,
    )


def test_is_valid_target_includes_oopif_when_iframes_requested():
    session = make_session()
    oopif_target = {
        'targetId': 'oopif',
        'type': 'page',
        'subtype': 'iframe',
        'url': 'https://example.com',
    }

    assert session._is_valid_target(
        oopif_target,
        include_http=True,
        include_iframes=True,
        include_pages=True,
    )


def test_is_valid_target_keeps_regular_page_handling():
    session = make_session()
    page_target = {
        'targetId': 'page',
        'type': 'page',
        'url': 'https://example.com',
    }

    assert session._is_valid_target(
        page_target,
        include_http=True,
        include_iframes=False,
        include_pages=True,
    )
