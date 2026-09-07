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
import time
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
    "casestud",
    "darktrace",
    "testimonial",
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
    "/products/product-testimonials",
    "/partners",
    "/compare",
    "/alternatives",
    "/about",
    "/company",
    "/industries",
    "/products/eos",
    "/products/cloudvision",
    "/company/competitor-comparisons",
    "/ndr-darktrace-comparison",
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

# Optional JS render budget for interstitial / SPA / WAF shells.
# Env: LILA_INTAKE_JS_RENDER=on|off
#      LILA_INTAKE_JS_STEALTH=on|off
#      LILA_INTAKE_JS_WAIT_MS=20000
#      LILA_INTAKE_JS_MIN_CHARS=80
#      LILA_INTAKE_JS_RENDER_CAP=8
_JS_RENDER_CAP = 8
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.6478.127 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}
_UNRENDERED = re.compile(
    r"\b(error loading|load error|loading error|browser error|"
    r"enable javascript|enable js|access denied|just a moment|"
    r"attention required|cloudflare|captcha|page not found|"
    r"error code|failed to (?:load|fetch|open)|timeout|"
    r"502 bad gateway|503 service|404 not found|"
    r"checking your browser|verify you are human|"
    r"pardon our interruption|bot detection)\b",
    re.I,
)
_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_ROBOTS_SITEMAP = re.compile(r"(?im)^sitemap:\s*(\S+)")

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


def _env_on(name: str, default: str = "on") -> bool:
    flag = (os.environ.get(name) or default).strip().casefold()
    return flag not in {"0", "off", "false", "no"}


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        return max(lo, min(hi, int(raw))) if raw else default
    except ValueError:
        return default


def _thin_floor() -> int:
    return _env_int("LILA_INTAKE_JS_MIN_CHARS", 80, lo=20, hi=400)


def _js_wait_ms() -> int:
    return _env_int("LILA_INTAKE_JS_WAIT_MS", 20000, lo=2000, hi=60000)


def _js_render_cap() -> int:
    return _env_int("LILA_INTAKE_JS_RENDER_CAP", _JS_RENDER_CAP, lo=0, hi=24)


def js_render_config() -> dict:
    """Documented render flags. Tests and operators read the same map."""
    return {
        "LILA_INTAKE_JS_RENDER": _env_on("LILA_INTAKE_JS_RENDER"),
        "LILA_INTAKE_JS_STEALTH": _env_on("LILA_INTAKE_JS_STEALTH"),
        "LILA_INTAKE_JS_WAIT_MS": _js_wait_ms(),
        "LILA_INTAKE_JS_MIN_CHARS": _thin_floor(),
        "LILA_INTAKE_JS_RENDER_CAP": _js_render_cap(),
    }


def is_unrendered_text(text: str) -> bool:
    """True for empty, thin WAF/SPA shells, load-errors, and interstitials."""
    raw = str(text or "").strip()
    if not raw:
        return True
    if raw.casefold() in {"citation", "cite", "source", "url"}:
        return True
    if _UNRENDERED.search(raw):
        return True
    if len(raw) < _thin_floor():
        return True
    return False


def _js_render_enabled() -> bool:
    return _env_on("LILA_INTAKE_JS_RENDER")


def _wait_for_settled_content(page, *, min_chars: int, wait_ms: int) -> None:
    """Wait for readable body copy to appear and stop changing.

    networkidle hangs on analytics-heavy SPAs and is not the success signal.
    """
    deadline = time.time() + (wait_ms / 1000.0)
    last = -1
    stable = 0
    while time.time() < deadline:
        try:
            text = page.inner_text("body") or ""
        except Exception:  # noqa: BLE001 - body may not exist yet
            text = ""
        n = len(text.strip())
        if n >= min_chars and n == last:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
        last = n
        try:
            page.wait_for_timeout(700)
        except Exception:  # noqa: BLE001
            return


def _fetch_rendered(url: str, timeout: float = 45.0) -> Optional[str]:
    """Playwright render with a realistic browser fingerprint.

    Missing Playwright or a WAF-empty body is not a crash. Caller decides
    whether the extracted text is usable.
    """
    if not _js_render_enabled():
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    stealth = _env_on("LILA_INTAKE_JS_STEALTH")
    args = ["--disable-dev-shm-usage", "--no-sandbox"]
    if stealth:
        args.append("--disable-blink-features=AutomationControlled")
    try:
        with sync_playwright() as player:
            browser = player.chromium.launch(headless=True, args=args)
            context = browser.new_context(
                user_agent=_BROWSER_UA,
                locale="en-US",
                viewport={"width": 1365, "height": 900},
                extra_http_headers={
                    k: v for k, v in _BROWSER_HEADERS.items()
                    if k.casefold() != "user-agent"
                },
            )
            if stealth:
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined});"
                )
            page = context.new_page()
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(timeout * 1000),
            )
            _wait_for_settled_content(
                page, min_chars=_thin_floor(), wait_ms=_js_wait_ms())
            html = page.content()
            context.close()
            browser.close()
            return html
    except Exception:  # noqa: BLE001 - render is optional
        return None


def _fetch(url: str, timeout: float = 20.0) -> Optional[str]:
    try:
        resp = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers=_BROWSER_HEADERS,
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if (
            "html" not in ctype
            and "text" not in ctype
            and "xml" not in ctype
        ):
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


def _customer_proof_url(url: str) -> bool:
    low = (url or "").casefold()
    return any(
        slug in low
        for slug in (
            "customer", "case-stud", "casestud", "testimonial",
            "proof-point", "proof_point",
        )
    )


def _news_like_url(url: str) -> bool:
    low = (url or "").casefold()
    return bool(re.search(
        r"(?:^|/)(?:news|press(?:-release)?|blog)(?:/|$|\?|#)", low))


def _compare_hub_url(url: str) -> bool:
    """Compare / vs / Darktrace NDR paths. Must not lose to case-study flood."""
    low = (url or "").casefold()
    return bool(re.search(
        r"compare|alternativ|versus|/vs|competitor|darktrace", low))


def _queue_tier(url: str) -> int:
    """Compare hubs first, then customers, then products; news/blog last."""
    if _compare_hub_url(url):
        return 0
    if _customer_proof_url(url):
        return 1
    if _news_like_url(url):
        return 4
    low = (url or "").casefold()
    if any(s in low for s in ("product", "solution")):
        return 2
    return 3


def _locs_from_xml(xml: str, domain: str) -> tuple[list[str], list[str]]:
    pages: list[str] = []
    children: list[str] = []
    for raw in _SITEMAP_LOC.findall(xml or ""):
        url = raw.strip().rstrip("/")
        if not url or not _same_domain(url, domain):
            continue
        if url.casefold().endswith(".xml"):
            children.append(url)
        elif _priority_url(url):
            pages.append(url)
    return pages, children


def discover_official_hubs(root_url: str, fetcher=None) -> list[str]:
    """Priority official-domain URLs from sitemap.xml, robots.txt, and seeds."""
    root = root_url.rstrip("/")
    domain = urlparse(root).netloc
    fetch = fetcher or _fetch
    found: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        key = url.rstrip("/")
        if not key or key in seen or not _same_domain(key, domain):
            return
        if key != root and not _priority_url(key):
            return
        seen.add(key)
        found.append(key)

    sitemap_urls = [
        root + "/sitemap.xml",
        root + "/sitemap_index.xml",
        root + "/en/sitemap.xml",
    ]
    robots = fetch(root + "/robots.txt")
    if robots:
        sitemap_urls[0:0] = _ROBOTS_SITEMAP.findall(robots)
    child_cap = 4
    for sm_url in list(dict.fromkeys(sitemap_urls)):
        xml = fetch(sm_url)
        if not xml:
            continue
        pages, children = _locs_from_xml(xml, domain)
        for page in pages:
            _add(page)
        for child in children[:child_cap]:
            nested = fetch(child)
            if not nested:
                continue
            nested_pages, _ = _locs_from_xml(nested, domain)
            for page in nested_pages:
                _add(page)
        if len(found) >= 40:
            break
    return found[:40]


def official_hub_hints(root_url: str, scrape=None) -> list[str]:
    """Seed + discovered official URLs for site-anchored probes."""
    root = (root_url or "").rstrip("/")
    out: list[str] = []
    if root:
        out.append(root)
        for path in _SEED_PATHS:
            out.append(root + path)
    if scrape is not None:
        for url in list(getattr(scrape, "sources", None) or []):
            out.append(url)
        for url in list(getattr(scrape, "render_failures", None) or []):
            out.append(url)
        for page in list(getattr(scrape, "pages", None) or []):
            if getattr(page, "url", None):
                out.append(page.url)
    seen: set[str] = set()
    hubs: list[str] = []
    for url in out:
        key = (url or "").split("#")[0].rstrip("/")
        if not key or key in seen:
            continue
        if key != root and not _priority_url(key):
            continue
        seen.add(key)
        hubs.append(key)
    return hubs[:24]


def scrape_site(
    root_url: str,
    max_pages: int = SITE_MASTERY_MAX_PAGES,
    *,
    fetcher=None,
    renderer=None,
) -> ScrapeBundle:
    """Crawl up to `max_pages` same-domain pages, prioritizing mastery sections.

    Fallback chain per URL: static HTML if usable, else JS render (realistic
    UA + settled-content wait), else record a render_failure. Sitemap.xml and
    HTML nav/footer links seed product / customer / compare hubs. Same-domain
    only. Inject ``renderer`` in tests; live uses Playwright when enabled.
    """
    root = root_url.rstrip("/")
    domain = urlparse(root).netloc
    bundle = ScrapeBundle(root_url=root)
    fetch = fetcher or _fetch
    if renderer is not None:
        render = renderer
    elif fetcher is None:
        render = _fetch_rendered
    else:
        render = None
    cap = max(1, int(max_pages or SITE_MASTERY_MAX_PAGES))
    js_left = 0 if render is None else _js_render_cap()

    queue: list[str] = [root]
    for url in discover_official_hubs(root, fetch):
        if url not in queue:
            queue.append(url)
    for path in _SEED_PATHS:
        seeded = root + path
        if seeded not in queue:
            queue.append(seeded)
    if len(queue) > 1:
        queue[1:] = sorted(queue[1:], key=_queue_tier)
    seen: set[str] = set()
    empty_hits = 0
    saw_html = False

    while queue and len(bundle.pages) < cap:
        url = queue.pop(0).split("#")[0].rstrip("/") or root
        if url in seen or not _same_domain(url, domain):
            continue
        seen.add(url)
        html = fetch(url)
        text, links = "", []
        if html:
            saw_html = True
            empty_hits = 0
            text, links = _extract(html)
        else:
            empty_hits += 1
            if empty_hits >= 8 and not saw_html and render is None:
                bundle.render_failures.append(url)
                break
        if (
            render is not None
            and js_left > 0
            and (not html or is_unrendered_text(text))
            and (_priority_url(url) or url == root)
        ):
            rendered = render(url)
            js_left -= 1
            if rendered:
                html = rendered
                text, links = _extract(html)
                saw_html = True
                empty_hits = 0
        if text.strip() and not is_unrendered_text(text):
            bundle.pages.append(ScrapedPage(url=url, text=text))
            bundle.sources.append(url)
        else:
            bundle.render_failures.append(url)
            if html and url not in bundle.sources:
                bundle.sources.append(url)
            if empty_hits >= 8 and not saw_html:
                break
            if not html:
                continue
        for href in links:
            absolute = urljoin(url + "/", href).split("#")[0].rstrip("/")
            if (
                absolute
                and absolute not in seen
                and absolute not in queue
                and _same_domain(absolute, domain)
                and _priority_url(absolute)
            ):
                if _compare_hub_url(absolute):
                    queue.insert(0, absolute)
                elif _customer_proof_url(absolute):
                    idx = 0
                    while idx < len(queue) and _compare_hub_url(queue[idx]):
                        idx += 1
                    queue.insert(idx, absolute)
                else:
                    queue.append(absolute)

    return bundle
