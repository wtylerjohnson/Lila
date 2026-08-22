"""Read the client's own product pages. Stop guessing what they sell.

THE ROOT CAUSE THIS ENDS (operator, 2026-08-07: "why are we not ingesting
the stated client's product page").

`clients/<slug>/profile.json` carries `capability_terms.core`, and those
terms are HAND-AUTHORED. For apexanalytix someone typed eight marketing
phrases. Measured against 330,641 federal notices, all eight matched ZERO,
while the company's real product surface, published on its own site, runs to
fifty-plus named capabilities including unclaimed property recovery, sales
and use tax recovery, escheatment avoidance, supplier diversity, insurance
coverage monitoring and multi-tier supplier mapping. None of those was in
the frame, so none was ever searched.

Everything else built to compensate (synonym widening, corpus mining, the
phrase tiers) was treating a symptom. The source of truth for what a company
sells is the company's own product pages.

WHAT THIS DOES. Crawls the client's product and solutions pages through the
EXISTING `tools.scrape.site` seam, extracts the named capability surface,
measures every candidate against the local notice store, and proposes the
ones with federal demand.

IT PROPOSES, IT DOES NOT DECIDE. Same law as term_discovery: the capability
frame is the ENGAGEMENT boundary and stays operator-owned. This writes a
review artifact with real yield attached; the operator moves what belongs
into the profile.

ZERO MODEL CALLS. Extraction is structural, from headings and product-link
text, not an LLM summarising a page. A model reading a marketing site would
reproduce marketing language, which is precisely the input that failed.
"""

from __future__ import annotations

import re
from typing import Any, Optional

CAPABILITY_INGEST_VERSION = "capability_ingest.v1.2026-08-07"

# Where a product surface actually lives on a vendor site.
PRODUCT_SLUGS = ("/solutions", "/products", "/platform", "/capabilities",
                 "/services", "/what-we-do", "/use-cases")

# A capability name is a short noun phrase. These are the shapes that are
# navigation furniture rather than a product.
_NOT_A_CAPABILITY = re.compile(
    r"\b(learn more|read more|get started|contact|careers|privacy|terms|"
    r"copyright|disclaimer|blog|resources|about|login|sign in|subscribe|"
    r"webinar|guide|ebook|whitepaper|customer story|press|news|events|"
    r"view all|see all|home|search|menu|cookie)\b", re.I)

# Words that mark real capability language in this domain family. A candidate
# needs one: it is what separates "Supplier Onboarding" from "Ready to roar".
_CAPABILITY_MARKERS = (
    "management", "monitoring", "audit", "recovery", "validation", "risk",
    "compliance", "onboarding", "prevention", "detection", "intelligence",
    "analytics", "automation", "optimization", "optimisation", "screening",
    "verification", "enrichment", "mapping", "scoring", "tracking",
    "reporting", "diligence", "resolution", "discovery", "financing",
    "payment", "invoice", "supplier", "vendor", "data", "security",
    "governance", "sourcing", "procurement", "performance", "visibility",
)

_WS = re.compile(r"\s+")


def _clean(value: Any) -> str:
    return _WS.sub(" ", str(value or "")).strip()


def _looks_like_capability(text: str) -> bool:
    words = _clean(text).split()
    if not (2 <= len(words) <= 7):
        return False
    if _NOT_A_CAPABILITY.search(text):
        return False
    low = text.casefold()
    if not any(marker in low for marker in _CAPABILITY_MARKERS):
        return False
    # a sentence, not a name
    return not text.rstrip().endswith((".", "?", "!"))


def extract_capabilities(html: str) -> list:
    """Named capabilities from one product page. Structural, no model.

    Reads heading text and the anchor text of links that point at product
    or solution URLs, which is where a vendor names what it sells.
    """
    found: list = []
    seen: set = set()

    def _add(text: str) -> None:
        name = _clean(re.sub(r"<[^>]+>", " ", text))
        key = name.casefold()
        if not key or key in seen or not _looks_like_capability(name):
            return
        seen.add(key)
        found.append(name)

    for match in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>", html or "",
                             re.S | re.I):
        _add(match.group(1))
    for match in re.finditer(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>',
                             html or "", re.S | re.I):
        href, label = match.group(1), match.group(2)
        if any(slug in href.casefold() for slug in PRODUCT_SLUGS):
            _add(label)
    return found


def ingest(root_url: str, *, max_pages: int = 40,
           fetcher: Optional[Any] = None) -> dict:
    """Crawl the product surface and return every named capability.

    `fetcher` is injectable so tests never touch the network. The default
    rides the repo's existing scrape seam, which already handles timeouts,
    same-domain limits and text extraction.
    """
    from urllib.parse import urljoin, urlparse

    from tools.scrape.site import _extract, _fetch

    fetch = fetcher or _fetch
    root = _clean(root_url).rstrip("/")
    domain = urlparse(root).netloc
    receipt = {
        "version": CAPABILITY_INGEST_VERSION, "root_url": root,
        "pages_fetched": 0, "pages_failed": 0, "model_calls": 0,
        "credits_spent": 0, "urls": [],
    }
    if not root:
        receipt["why"] = "no site url on the client profile"
        return {"capabilities": [], "receipt": receipt}

    queue, seen = [root], {root}
    for slug in PRODUCT_SLUGS:
        candidate = root + slug
        if candidate not in seen:
            seen.add(candidate)
            queue.append(candidate)

    capabilities: list = []
    names: set = set()
    while queue and receipt["pages_fetched"] < max_pages:
        url = queue.pop(0)
        html = fetch(url)
        if not html:
            receipt["pages_failed"] += 1
            continue
        receipt["pages_fetched"] += 1
        receipt["urls"].append(url)
        for name in extract_capabilities(html):
            if name.casefold() not in names:
                names.add(name.casefold())
                capabilities.append({"name": name, "found_on": url})
        # follow product links only, same domain
        try:
            _text, links = _extract(html)
        except Exception:                                 # noqa: BLE001
            links = []
        for href in links:
            absolute = urljoin(url + "/", href).split("#")[0].rstrip("/")
            if urlparse(absolute).netloc != domain or absolute in seen:
                continue
            if any(slug in absolute.casefold() for slug in PRODUCT_SLUGS):
                seen.add(absolute)
                queue.append(absolute)
    receipt["capabilities_found"] = len(capabilities)
    return {"capabilities": capabilities, "receipt": receipt}


def measure(capabilities: Any, conn, *, existing: Any = ()) -> dict:
    """Federal demand for every extracted capability. Local store only.

    A capability with no federal demand is still recorded: it says the
    company sells something the government is not currently buying, which is
    a finding rather than an omission.
    """
    have = {_clean(t).casefold() for t in (existing or ())}
    rows: list = []
    for entry in (capabilities or []):
        name = _clean(entry.get("name") if isinstance(entry, dict) else entry)
        if not name:
            continue
        low = name.casefold()
        in_frame = any(low in h or h in low for h in have)
        hit = conn.execute(
            "SELECT COUNT(*) n FROM notices WHERE title LIKE ? OR "
            "description_prefix LIKE ?", (f"%{name}%", f"%{name}%")).fetchone()
        count = int(hit["n"] if hasattr(hit, "keys") else hit[0])
        rows.append({
            "capability": name,
            "found_on": (entry.get("found_on") if isinstance(entry, dict)
                         else ""),
            "in_frame": in_frame,
            "federal_notices": count,
        })
    rows.sort(key=lambda r: (-r["federal_notices"], r["capability"]))
    proposed = [r for r in rows
                if not r["in_frame"] and r["federal_notices"] > 0]
    return {
        "rows": rows,
        "proposed": proposed,
        "receipt": {
            "capabilities_measured": len(rows),
            "already_in_frame": sum(1 for r in rows if r["in_frame"]),
            "with_federal_demand": sum(1 for r in rows
                                       if r["federal_notices"] > 0),
            "proposed_for_the_frame": len(proposed),
            "no_current_federal_demand": sum(1 for r in rows
                                             if not r["federal_notices"]),
        },
    }


def render_review(measured: dict) -> str:
    receipt = measured.get("receipt") or {}
    lines = [
        "CAPABILITY INGESTION",
        f"  {receipt.get('capabilities_measured', 0)} capability names read "
        f"from the client's own product pages",
        f"  {receipt.get('already_in_frame', 0)} already in the approved "
        f"frame · {receipt.get('proposed_for_the_frame', 0)} proposed · "
        f"{receipt.get('no_current_federal_demand', 0)} with no current "
        f"federal demand",
        "",
        "  PROPOSED (the client sells it and the government is buying it):",
    ]
    for row in (measured.get("proposed") or [])[:30]:
        lines.append(f"    {row['federal_notices']:6,d}  {row['capability']}")
    return "\n".join(lines)
