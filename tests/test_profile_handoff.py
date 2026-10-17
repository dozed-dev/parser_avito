import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import BrowserType

from get_cookies import get_profile_cookies


@pytest.fixture
def browser_site(monkeypatch):
    executable = os.getenv(
        "AVITO_CHROMIUM_PATH",
        "/ms-playwright/chromium-1169/chrome-linux/chrome",
    )
    if not Path(executable).is_file():
        pytest.skip("Requires the Chromium bundled in the Playwright container")

    launch = BrowserType.launch_persistent_context

    def install(api_status=439):
        def handle(route):
            path = urlsplit(route.request.url).path
            if path == "/":
                route.fulfill(status=200, content_type="text/html", body="<title>Search</title>")
            elif path == "/verify":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={"set-cookie": "session_ready=1; Path=/; Secure; HttpOnly"},
                    body='{"verified": true}',
                )
            elif path == "/web/1/js/items":
                if "session_ready=1" in (route.request.header_value("cookie") or ""):
                    route.fulfill(status=200, content_type="application/json", body='{"catalog":{"items":[]}}')
                else:
                    route.fulfill(
                        status=api_status,
                        content_type="text/html",
                        body="""<!doctype html><script>
                            document.addEventListener('DOMContentLoaded', async () => {
                                await fetch('/verify', {method: 'POST'});
                                location.reload();
                            });
                        </script>""",
                    )
            else:
                route.abort()

        def launch_with_site(self, *args, **kwargs):
            context = launch(self, *args, **kwargs)
            context.route("**/*", handle)
            return context

        monkeypatch.setattr(BrowserType, "launch_persistent_context", launch_with_site)

    return install


def test_handoff_exports_session_only_after_browser_finishes_challenge(tmp_path, browser_site):
    browser_site()

    provider = get_profile_cookies(profile_path=str(tmp_path), headless=True)

    assert provider.get() == {"session_ready": "1"}


def test_handoff_does_not_export_an_api_blocked_session(tmp_path, browser_site):
    browser_site(api_status=403)

    with pytest.raises(RuntimeError, match="HTTP 403"):
        get_profile_cookies(profile_path=str(tmp_path), headless=True)
