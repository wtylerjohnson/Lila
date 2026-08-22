"""Link integrity: verify EVERY anchor, degrade rather than block.

Doctrine (operator directive 2026-07-27):
  - Every anchor in a pressed report is verified live. Not sampled.
  - NOTHING BLOCKS. A dead link never suppresses a row and never stops a
    press: the record is real, only the link failed. The row degrades to
    plain text with its identifier fully preserved.
  - Redirects are not failures. They are followed and the destination is
    recorded so the anchor can be rewritten to where the content actually
    lives.
  - Canonical URL forms are preferred over search-derived ones, because a
    canonical form survives archiving and a search URL does not.

This module OBSERVES and REPORTS. It renders nothing and mutates no
document; the degrade rendering is a separate, operator-approved step.
"""

from __future__ import annotations

import re
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

# Anchor classes. Order matters: first match wins, most specific first.
ANCHOR_CLASSES: tuple[tuple[str, str], ...] = (
    ("usaspending_award", r"usaspending\.gov/award/"),
    ("sam_notice", r"sam\.gov/opp/"),
    ("sam_workspace", r"sam\.gov/workspace/"),
    ("sam_search", r"sam\.gov/search"),
    ("apfs_forecast", r"apfs-cloud\.dhs\.gov/"),
    ("naics_reference", r"census\.gov/naics"),
    ("vehicle_reference", r"(sewp\.nasa\.gov|gsa\.gov/(buy-through-us|technology))"),
    ("in_page", r"^#"),
)
# Everything else is an outbound site link; sub-classified by intent when the
# caller supplies the maps the press used.
DEFAULT_CLASS = "external_site"

# Canonical-form owners. A class listed here CAN be rebuilt from pack
# identity, so a non-canonical instance is a defect worth reporting.
CANONICAL_CLASSES = {"usaspending_award", "sam_notice", "naics_reference"}
# Classes whose URLs are inherently search-derived or discovered: they cannot
# be rebuilt from pack identity and do not survive archiving well.
SEARCH_DERIVED_CLASSES = {"sam_search", "external_site", "vehicle_reference"}

GLOBAL_CONCURRENCY = 8
PER_DOMAIN_CONCURRENCY = 2
REQUEST_TIMEOUT = 20.0

_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0 Safari/537.36"),
       "Accept": "text/html,application/xhtml+xml,*/*"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(href: str) -> str:
    for name, pattern in ANCHOR_CLASSES:
        if re.search(pattern, href, re.I):
            return name
    return DEFAULT_CLASS


def extract_anchors(html: str) -> list[dict]:
    """Every anchor with its href and visible text, in document order."""
    out: list[dict] = []
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.S | re.I):
        attrs, inner = m.group(1), m.group(2)
        href = re.search(r'href="([^"]*)"', attrs)
        if not href:
            continue
        text = re.sub(r"<[^>]+>", "", inner)
        text = re.sub(r"\s+", " ", text).strip()
        out.append({"href": href.group(1), "text": text,
                    "klass": classify(href.group(1)),
                    "start": m.start(), "end": m.end()})
    return out


def canonical_expectation(anchor: dict, pack: Any = None) -> Optional[str]:
    """The canonical URL this anchor SHOULD carry, when one is derivable."""
    href, klass = anchor["href"], anchor["klass"]
    if klass == "usaspending_award":
        gid = re.search(r"usaspending\.gov/award/([A-Z0-9_\-]+)", href, re.I)
        if gid:
            from agents.reports.links import build_usaspending_award_link
            try:
                return build_usaspending_award_link(gid.group(1).rstrip("/")).url
            except ValueError:
                return None
    if klass in ("sam_notice", "sam_workspace"):
        guid = re.search(r"([0-9a-f]{32})", href, re.I)
        if guid:
            from agents.reports.links import build_sam_notice_link
            try:
                return build_sam_notice_link(guid.group(1).lower()).url
            except ValueError:
                return None
    if klass == "apfs_forecast":
        # VERIFIED 2026-07-27 against apfs-cloud.dhs.gov: /forecast/<id>
        # returns 404 for every id tested, /record/<id>/public-print/
        # returns 200. A delivered Red Hat report carried 51 anchors in the
        # dead form. The hand-made golden reference used the working form,
        # so this is a producer defect, not a site change.
        rid = re.search(r"/(?:forecast|record)/(\d+)", href)
        if rid:
            return f"https://apfs-cloud.dhs.gov/record/{rid.group(1)}/public-print/"
    return None


def verify_anchors(
    anchors: Iterable[dict],
    *,
    fetch: Optional[Callable[[str], tuple[int, str]]] = None,
    global_concurrency: int = GLOBAL_CONCURRENCY,
    per_domain_concurrency: int = PER_DOMAIN_CONCURRENCY,
    timeout: float = REQUEST_TIMEOUT,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[dict]:
    """Verify every DISTINCT url once, concurrently, capped per domain.

    Returns one result row per distinct url: url, status, checked_at,
    redirect target, and ok. In-page anchors are resolved structurally by
    the caller, never fetched.
    """
    rows = [a for a in anchors if not a["href"].startswith("#")]
    distinct: dict[str, list[dict]] = defaultdict(list)
    for a in rows:
        distinct[a["href"]].append(a)

    client = None
    if fetch is None:
        import httpx
        client = httpx.Client(
            timeout=timeout, headers=_UA, follow_redirects=True,
            transport=httpx.HTTPTransport(retries=0, local_address="0.0.0.0"))

        def fetch(url: str) -> tuple[int, str]:  # noqa: F811
            # HEAD is unreliable across federal hosts; GET and drop the body.
            r = client.get(url)
            return r.status_code, str(r.url)

    domain_locks: dict[str, threading.Semaphore] = defaultdict(
        lambda: threading.Semaphore(per_domain_concurrency))
    results: list[dict] = []
    lock = threading.Lock()
    done = 0
    total = len(distinct)

    def check(url: str) -> dict:
        host = (urlsplit(url).hostname or "").lower()
        sem = domain_locks[host]
        sem.acquire()
        try:
            status, final = fetch(url)
            redirect = final if final and final != url else None
            return {"url": url, "status": status, "checked_at": _now(),
                    "redirect_target": redirect, "ok": 200 <= status < 400,
                    "error": None}
        except Exception as exc:  # noqa: BLE001 - a dead link is data
            return {"url": url, "status": None, "checked_at": _now(),
                    "redirect_target": None, "ok": False,
                    "error": f"{type(exc).__name__}"}
        finally:
            sem.release()

    try:
        with ThreadPoolExecutor(max_workers=global_concurrency) as pool:
            futures = {pool.submit(check, url): url for url in distinct}
            for fut in as_completed(futures):
                row = fut.result()
                row["klass"] = distinct[row["url"]][0]["klass"]
                row["occurrences"] = len(distinct[row["url"]])
                with lock:
                    results.append(row)
                    done += 1
                    if progress:
                        progress(done, total)
    finally:
        if client is not None:
            client.close()
    return results


def health_report(anchors: list[dict], results: list[dict],
                  pack: Any = None) -> dict:
    """The press-time link health object. Counts by class and by outcome."""
    by_url = {r["url"]: r for r in results}
    in_page = [a for a in anchors if a["href"].startswith("#")]
    per_class: dict[str, Counter] = defaultdict(Counter)
    degraded: list[dict] = []
    non_canonical: list[dict] = []

    for a in anchors:
        klass = a["klass"]
        if a["href"].startswith("#"):
            per_class[klass]["in_page"] += 1
            continue
        r = by_url.get(a["href"])
        if r is None:
            per_class[klass]["unchecked"] += 1
            continue
        if r["ok"] and r["redirect_target"]:
            per_class[klass]["redirected"] += 1
        elif r["ok"]:
            per_class[klass]["live"] += 1
        else:
            per_class[klass]["degraded"] += 1
            degraded.append({"href": a["href"], "text": a["text"][:80],
                             "klass": klass, "status": r["status"],
                             "error": r["error"]})
        expected = canonical_expectation(a, pack)
        if expected and expected != a["href"]:
            non_canonical.append({"href": a["href"], "expected": expected,
                                  "klass": klass})

    totals = Counter()
    for klass, counts in per_class.items():
        totals.update(counts)
    checked = sum(1 for a in anchors if not a["href"].startswith("#"))
    degraded_share = (totals["degraded"] / checked) if checked else 0.0
    return {
        "generated_at": _now(),
        "totals": {
            "anchors": len(anchors),
            "distinct_urls": len(results),
            "in_page": len(in_page),
            "live": totals["live"],
            "redirected": totals["redirected"],
            "degraded": totals["degraded"],
            "degraded_share": round(degraded_share, 4),
        },
        "by_class": {k: dict(v) for k, v in sorted(per_class.items())},
        "degraded_anchors": degraded[:60],
        "non_canonical": non_canonical[:60],
        "search_derived_classes": sorted(
            {a["klass"] for a in anchors
             if a["klass"] in SEARCH_DERIVED_CLASSES}),
    }
