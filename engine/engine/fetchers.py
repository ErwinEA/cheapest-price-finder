"""Page fetchers. Adapters depend on the small `Fetcher` interface so tests and
the offline demo can swap in `FixtureFetcher` without touching the network."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .errors import ConfigError, NetworkError


@dataclass
class FetchResponse:
    status: int
    text: str
    url: str = ""


class Fetcher(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        render_js: bool = False,
        timeout: float = 20.0,
    ) -> FetchResponse: ...


class DefaultFetcher:
    """`requests` for static pages, Playwright (headless Chromium) when render_js."""

    def get(self, url, *, headers=None, cookies=None, render_js=False, timeout=20.0):
        if render_js:
            return self._playwright_get(url, headers or {}, cookies or {}, timeout)
        return self._requests_get(url, headers, cookies, timeout)

    @staticmethod
    def _requests_get(url, headers, cookies, timeout):
        import requests

        try:
            r = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
        except requests.RequestException as exc:
            raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
        return FetchResponse(r.status_code, r.text, r.url)

    @staticmethod
    def _playwright_get(url, headers, cookies, timeout):
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConfigError(
                "Playwright is required for this vendor: "
                "pip install playwright && playwright install chromium"
            ) from exc

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        user_agent=headers.get("User-Agent"),
                        extra_http_headers={
                            k: v for k, v in headers.items() if k.lower() != "user-agent"
                        },
                    )
                    if cookies:
                        host = url.split("/")[2]
                        context.add_cookies(
                            [{"name": k, "value": str(v), "domain": host, "path": "/"}
                             for k, v in cookies.items()]
                        )
                    page = context.new_page()
                    response = page.goto(url, wait_until="domcontentloaded",
                                         timeout=timeout * 1000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=timeout * 500)
                    except PlaywrightError:
                        pass  # keep whatever rendered so far
                    status = response.status if response else 200
                    return FetchResponse(status, page.content(), page.url)
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise NetworkError(f"Playwright: {exc}") from exc


class FixtureFetcher:
    """Serves canned HTML. `pages` maps a URL substring to html, or to (status, html)."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.requested: list[str] = []

    @classmethod
    def from_dir(cls, directory: str | Path) -> "FixtureFetcher":
        """Loads <vendor>.html files; a file named 'amazon.html' answers any amazon URL."""
        pages = {p.stem: p.read_text(encoding="utf-8") for p in Path(directory).glob("*.html")}
        return cls(pages)

    def get(self, url, **kwargs):
        self.requested.append(url)
        for key, value in self.pages.items():
            if key in url:
                if isinstance(value, tuple):
                    status, text = value
                elif isinstance(value, Exception):
                    raise value
                else:
                    status, text = 200, value
                return FetchResponse(status, text, url)
        return FetchResponse(404, "", url)
