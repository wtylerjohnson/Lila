"""SBA SubNet census: fetch receipts, deterministic parse, measurement only.

Operator order 2026-08-05: MEASURE the subcontracting channel before any
join. This module owns the listing parser, the pagination-exhaustion rule,
and the fail-closed row floor. It builds no lane, no ingest, no report
field; the census artifact and the measurement doc are the entire product.

Source shape (verified 2026-08-05): Drupal view at
legacy.sba.gov/federal-contracting/contracting-guide/prime-subcontracting/
subcontracting-opportunities?state=All&page=N. Ten rows per page. The view
CLAMPS: any page number past the real end re-serves the final page, so
exhaustion is content-based (first page whose row titles equal the prior
page's), never trust the pager widget (it advertised 9 pages when 2
existed). www.sba.gov redirects to legacy.sba.gov and drops the query
string, returning 404 for any paged fetch; fetch the legacy host directly.
"""

from __future__ import annotations

import html as html_mod
import re
from typing import Optional

LISTING_URL = ("https://legacy.sba.gov/federal-contracting/contracting-guide/"
               "prime-subcontracting/subcontracting-opportunities")

# FAIL-CLOSED FLOOR (extract size-floor posture): a fetch yielding fewer
# rows than one real page is a broken fetch, not a small market; refuse to
# promote it over an existing census.
MIN_CREDIBLE_ROWS = 10

_TR_SPLIT = re.compile(r"<tr[\s>]", re.I)
_TITLE = re.compile(r'<a href="(/opportunity/[^"]+)"[^>]*>([^<]+)</a>')
_BUSINESS = re.compile(r'subnet_business_name">([^<]+)<', re.I)
_TD = re.compile(
    r'headers="view-{col}-table-column"[^>]*>(.*?)</td>', re.S)


def _td(block: str, col: str) -> Optional[str]:
    m = re.search(_TD.pattern.format(col=re.escape(col)), block, re.S | re.I)
    if not m:
        return None
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
    return " ".join(text.split()) or None


def parse_listing_page(page_html: str) -> list[dict]:
    """Every opportunity row from the listing's TABLE rendering, verbatim.

    The page also carries a card rendering, but only the table carries the
    NAICS and point-of-contact columns (verified 2026-08-05)."""
    rows = []
    for block in _TR_SPLIT.split(page_html)[1:]:
        m = _TITLE.search(block)
        if not m:
            continue                      # the header row has no title link
        body = re.search(_TD.pattern.format(col="body"), block, re.S | re.I)
        desc = None
        if body:
            paragraph = re.search(r"<p>(.*?)(?:</td>|$)", body.group(1), re.S)
            if paragraph:
                desc = " ".join(html_mod.unescape(re.sub(
                    r"<[^>]+>", " ", paragraph.group(1))).split())[:800] or None
        rows.append({
            "title": html_mod.unescape(m.group(2)).strip(),
            "source_url": "https://legacy.sba.gov" + m.group(1),
            "posting_organization": html_mod.unescape(
                (_BUSINESS.search(block) or [None, ""])[1]).strip() or None,
            "description": desc,
            "closing_date": _td(block, "field-subnet-closing-timestamp"),
            "performance_start": _td(block, "field-subnet-start-date"),
            "place_of_performance": _td(block, "field-subnet-place-performance"),
            "naics": _td(block, "field-subnet-naics"),
            "point_of_contact": _td(block, "nothing"),
        })
    return rows


def page_signature(page_html: str) -> tuple[str, ...]:
    """Row-title signature used by the clamp-exhaustion rule."""
    return tuple(r["title"] for r in parse_listing_page(page_html))


def exhausted(previous_html: str, current_html: str) -> bool:
    """True when the view clamped: the current page re-serves the prior
    page's rows (or is empty). Content equality, never the pager widget."""
    cur = page_signature(current_html)
    return not cur or cur == page_signature(previous_html)


def guard_row_floor(rows: list[dict],
                    floor: int = MIN_CREDIBLE_ROWS) -> list[dict]:
    """Fail closed below the floor: a broken fetch never becomes a census."""
    if len(rows) < floor:
        raise ValueError(
            f"subnet census refused: {len(rows)} rows is below the "
            f"{floor}-row credibility floor; a broken fetch never replaces "
            "a census")
    return rows


# ---- classification rule (stated, checkable) ------------------------------- #
FEDERAL_PRIME_RULE = (
    "A row is FEDERAL-PRIME subcontracting when its description or title "
    "names a federal agency, base, federal program, or a prime flowing down "
    "a federal contract (Job Corps centers count: ETR operates them under "
    "Department of Labor contracts). A row is STATE/LOCAL/AUTHORITY when "
    "the buyer named is a city, county, state agency, transit or utility "
    "authority, or a public school district with no federal contract "
    "reference. The strings that decided each row are printed beside it.")

_FEDERAL_MARKERS = re.compile(
    r"\b(federal|USACE|army|navy|air force|USAF|DoD|GSA|VA\b|NASA|DHS|DOE\b"
    r"|department of [a-z]+|job corps|AFB\b|naval|fort [a-z]+|IDIQ"
    r"|prime contract|8\(a\)|FAR\b)\b", re.I)
_STATE_LOCAL_MARKERS = re.compile(
    r"\b(city of|county|school district|transit|authority|municipal"
    r"|state of|dept\.? of transportation|DOT district|water district"
    r"|public schools|university)\b", re.I)


def classify_row(row: dict) -> dict:
    """{side, matched} for the federal-vs-state split, receipts printed."""
    text = f"{row.get('title') or ''} {row.get('description') or ''} " \
           f"{row.get('posting_organization') or ''}"
    fed = _FEDERAL_MARKERS.search(text)
    loc = _STATE_LOCAL_MARKERS.search(text)
    if fed and not loc:
        return {"side": "federal_prime", "matched": fed.group(0)}
    if loc and not fed:
        return {"side": "state_local_authority", "matched": loc.group(0)}
    if fed and loc:
        return {"side": "federal_prime",
                "matched": f"{fed.group(0)} (also {loc.group(0)})"}
    return {"side": "state_local_authority",
            "matched": "(no federal marker found)"}
