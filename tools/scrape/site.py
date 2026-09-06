"""Website / public-data scraper (Step 1b).

Bounded crawl of a client's site using only stdlib HTML parsing (no bs4) + httpx.
After identity bind, this is the primary picture of what the company sells:
homepage plus marketing sections (products, customers, compare, partners),
seeded even when the homepage is a JS shell with no links.

Kept dependency-light and polite (page cap, same-domain only).
"""

from __future__ import annotations

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
_SEED_PATHS = (
    "/about",
    "/about-us",
    "/company",
    "/products",
    "/solutions",
    "/platform",
    "/capabilities",
    "/industries",
    "/use-cases",
    "/customers",
    "/case-studies",
    "/partners",
    "/ecosystem",
    "/resources",
    "/compare",
    "/alternatives",
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

    queue: list[str] = [root]
    for path in _SEED_PATHS:
        seeded = root + path
        if seeded not in queue:
            queue.append(seeded)
    seen: set[str] = set()

    while queue and len(bundle.pages) < cap:
        url = queue.pop(0).split("#")[0].rstrip("/") or root
        if url in seen or not _same_domain(url, domain):
            continue
        seen.add(url)
        html = fetch(url)
        if not html:
            continue
        text, links = _extract(html)
        if text.strip():
            bundle.pages.append(ScrapedPage(url=url, text=text))
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
