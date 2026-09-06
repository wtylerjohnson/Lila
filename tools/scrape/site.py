"""Website / public-data scraper (Step 1b).

Bounded crawl of a client's site using only stdlib HTML parsing (no bs4) + httpx.
After identity bind, this is the primary picture of what the company sells:
homepage plus marketing sections (products, customers, compare, partners),
seeded even when the homepage is a JS shell with no links.

Kept dependency-light and polite (page cap, same-domain only).
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

# Documented mastery budget. 5 pages was enough for a homepage glance and
# too little for product + customer + compare hubs.
SITE_MASTERY_MAX_PAGES = 24

# Internal-link slugs worth following beyond the homepage.
_PRIORITY_SLUGS = (
    "about",
    "company",
    "product",
    "platform",
    "solution",
    "capabilit",
    "service",
    "industr",
    "use-case",
    "usecase",
    "customer",
    "case-stud",
    "casestudy",
    "partner",
    "ecosystem",
    "resource",
    "blog",
    "compare",
    "alternativ",
    "versus",
    "/vs",
    "why-",
    "competitor",
    "hardware",
    "switching",
    "cloudvision",
    "/eos",
    "past-performance",
    "past_performance",
    "contract",
)

# Seeded even when nav extraction fails (JS homepage, empty footer).
# Locale prefixes first: arista.com hubs live under /en/, not the bare path.
_HUB_PATHS = (
    "/products",
    "/solutions",
    "/customers",
    "/case-studies",
    "/partners",
    "/compare",
    "/alternatives",
    "/about",
    "/company",
    "/industries",
    "/products/eos",
    "/products/cloudvision",
)
_SEED_PATHS = tuple(
    f"{prefix}{path}"
    for prefix in ("/en", "")
    for path in _HUB_PATHS
) + (
    "/about-us",
    "/platform",
    "/capabilities",
    "/use-cases",
    "/ecosystem",
    "/resources",
)

# Optional JS render budget for interstitial / SPA shells.
_JS_RENDER_CAP = 8
_UNRENDERED = re.compile(
    r"\b(error loading|load error|loading error|browser error|"
    r"enable javascript|enable js|access denied|just a moment|"
    r"attention required|cloudflare|captcha|page not found|"
    r"error code|failed to (?:load|fetch|open)|timeout|"
    r"502 bad gateway|503 service|404 not found)\b",
    re.I,
)

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class ScrapedPage(BaseModel):
    url: str
    text: str


class ScrapeBundle(BaseModel):
    root_url: str
    pages: list[ScrapedPage] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list, description="every URL fetched (traceability)")
    render_failures: list[str] = Field(
        default_factory=list,
        description="fetched URLs that were empty, interstitial, or failed to render",
    )

    def usable_pages(self) -> list[ScrapedPage]:
        return [p for p in self.pages if p.text.strip() and not is_unrendered_text(p.text)]

    def combined_text(self, max_chars: int = 40_000) -> str:
        chunks = [f"# {p.url}\n{p.text}" for p in self.usable_pages()]
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


def is_unrendered_text(text: str) -> bool:
    """True for empty pages, load-errors, and JS/captcha interstitials."""
    raw = str(text or "")
    if not raw.strip():
        return True
    if raw.strip().casefold() in {"citation", "cite", "source", "url"}:
        return True
    return bool(_UNRENDERED.search(raw))


def _js_render_enabled() -> bool:
    flag = (os.environ.get("LILA_INTAKE_JS_RENDER") or "on").strip().casefold()
    return flag not in {"0", "off", "false", "no"}


def _fetch_rendered(url: str, timeout: float = 25.0) -> Optional[str]:
    """Best-effort Playwright render. Missing browser is not a crash."""
    if not _js_render_enabled():
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as player:
            browser = player.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001 - SPA may never idle
                pass
            html = page.content()
            browser.close()
            return html
    except Exception:  # noqa: BLE001 - render is optional
        return None


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


def _same_domain(url: str, domain: str) -> bool:
    host = urlparse(url).netloc
    return bool(host) and (host == domain or host.endswith("." + domain))


def _priority_url(url: str) -> bool:
    low = url.casefold()
    return any(slug in low for slug in _PRIORITY_SLUGS)


def scrape_site(
    root_url: str,
    max_pages: int = SITE_MASTERY_MAX_PAGES,
    *,
    fetcher=None,
) -> ScrapeBundle:
    """Crawl up to `max_pages` same-domain pages, prioritizing mastery sections.

    Seeds product / customer / compare hubs even when the homepage has no
    extractable nav. Follows in-domain priority links from each fetched page
    (nav, footer, product hubs). Same-domain only.
    """
    root = root_url.rstrip("/")
    domain = urlparse(root).netloc
    bundle = ScrapeBundle(root_url=root)
    fetch = fetcher or _fetch
    cap = max(1, int(max_pages or SITE_MASTERY_MAX_PAGES))
    js_left = 0 if fetcher is not None else _JS_RENDER_CAP

    queue: list[str] = [root]
    for path in _SEED_PATHS:
        seeded = root + path
        if seeded not in queue:
            queue.append(seeded)
    seen: set[str] = set()
    empty_hits = 0
    saw_html = False

    while queue and len(bundle.pages) < cap:
        url = queue.pop(0).split("#")[0].rstrip("/") or root
        if url in seen or not _same_domain(url, domain):
            continue
        seen.add(url)
        html = fetch(url)
        if not html:
            bundle.render_failures.append(url)
            empty_hits += 1
            # Dead host: do not walk every seed path.
            if empty_hits >= 8 and not saw_html:
                break
            continue
        saw_html = True
        empty_hits = 0
        text, links = _extract(html)
        if is_unrendered_text(text) and js_left > 0 and _priority_url(url):
            rendered = _fetch_rendered(url)
            js_left -= 1
            if rendered:
                html = rendered
                text, links = _extract(html)
        if text.strip() and not is_unrendered_text(text):
            bundle.pages.append(ScrapedPage(url=url, text=text))
            bundle.sources.append(url)
        else:
            bundle.render_failures.append(url)
            if url not in bundle.sources:
                bundle.sources.append(url)
        for href in links:
            absolute = urljoin(url + "/", href).split("#")[0].rstrip("/")
            if (
                absolute
                and absolute not in seen
                and absolute not in queue
                and _same_domain(absolute, domain)
                and _priority_url(absolute)
            ):
                queue.append(absolute)

    return bundle
