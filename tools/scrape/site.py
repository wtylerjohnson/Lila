"""Website / public-data scraper (Step 1b).

Bounded crawl of a client's site using only stdlib HTML parsing (no bs4) + httpx.
Fetches the homepage plus a few high-signal internal pages (about, services,
capabilities, past performance, contracts), strips scripts/styles to visible text,
and returns a ScrapeBundle that carries every source URL for traceability.

Kept dependency-light and polite (small page cap, same-domain only).
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

# Internal-link slugs worth following beyond the homepage.
_PRIORITY_SLUGS = (
    "about",
    "service",
    "capabilit",
    "past-performance",
    "past_performance",
    "contract",
    "solution",
    "industries",
    "what-we-do",
)
_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class ScrapedPage(BaseModel):
    url: str
    text: str


class ScrapeBundle(BaseModel):
    root_url: str
    pages: list[ScrapedPage] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list, description="every URL fetched (traceability)")

    def combined_text(self, max_chars: int = 40_000) -> str:
        chunks = [f"# {p.url}\n{p.text}" for p in self.pages]
        return "\n\n".join(chunks)[:max_chars]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            text = data.strip()
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return " ".join(self._parts)


def _fetch(url: str, timeout: float = 20.0) -> Optional[str]:
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "LILA-intake-scraper/1.0"})
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return None
        return resp.text
    except httpx.HTTPError:
        return None


def _extract(html: str) -> tuple[str, list[str]]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text(), parser.links


def check_url(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Verify a web lead's URL is real: fetch it, confirm it's not a 404/empty page.

    Returns (reachable, snippet). Used to ground Claude-sourced web leads before they
    enter a deliverable — the system's verify-before-trust rule applied to web results.
    """
    html = _fetch(url, timeout=timeout)
    if html is None:
        return False, ""
    text, _ = _extract(html)
    low = text.lower()[:400]
    if not text.strip() or "page not found" in low or "404 not found" in low:
        return False, text[:200]
    return True, text[:300]


def scrape_site(root_url: str, max_pages: int = 5) -> ScrapeBundle:
    """Crawl up to `max_pages` same-domain pages, prioritizing capability content."""
    root = root_url.rstrip("/")
    domain = urlparse(root).netloc
    bundle = ScrapeBundle(root_url=root)

    home_html = _fetch(root)
    if home_html is None:
        return bundle  # site unreachable — caller proceeds on form data alone
    home_text, links = _extract(home_html)
    bundle.pages.append(ScrapedPage(url=root, text=home_text))
    bundle.sources.append(root)

    # Rank internal links by priority slug, dedup, same-domain only.
    candidates: list[str] = []
    seen = {root}
    for href in links:
        absolute = urljoin(root + "/", href).split("#")[0].rstrip("/")
        if urlparse(absolute).netloc != domain or absolute in seen:
            continue
        if any(slug in absolute.lower() for slug in _PRIORITY_SLUGS):
            seen.add(absolute)
            candidates.append(absolute)

    for url in candidates[: max_pages - 1]:
        html = _fetch(url)
        if not html:
            continue
        text, _ = _extract(html)
        if text:
            bundle.pages.append(ScrapedPage(url=url, text=text))
            bundle.sources.append(url)

    return bundle
