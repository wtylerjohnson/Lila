"""Tier C · curated industry-conference map (EVENTS_LANE, 2026-07-27).

The fan-out that catches AUSA, AFCEA, NDIA and peers: the events a client
should be AT, which never appear as SAM notices because nobody procures a
conference.

DESIGN, forced by live evidence (measured 2026-07-27):
  - URLs are DISCOVERED, never recalled. Eight hand-written conference URLs
    were tested and five 404'd: a static URL map rots yearly because event
    pages move (ausa.org -> meetings.ausa.org/annual/2026/). The seed roster
    therefore carries the ORGANISATION and its affinities, not a URL.
  - The model PROPOSES a current event page; a real fetch DISPOSES. Nothing
    ships on a model's say-so: the page must return 200 and print its own
    dates, and the verbatim span that produced those dates rides on the
    record.
  - Sites that block us (403/406) or render only via JavaScript are SKIPPED
    AND REPORTED, never approximated. Verified live: ACT-IAC returns 406 and
    NDIA serves an empty body to a non-browser client.
  - Re-verification is quarterly; every roster row carries verified_at.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from agents.golden_press.events import EventRecord, extract_event_range

ROSTER_PATH = "data/reference/event_conferences.json"
RE_VERIFY_DAYS = 92                      # quarterly, per the frozen scope

# Seed roster: organisation, the event's own name, and what it is ABOUT.
# Affinities are matched against the client's pack, never assumed. No URLs
# here on purpose: they are discovered per cycle and verified before use.
CONFERENCE_SEEDS: tuple[dict, ...] = (
    {"key": "ausa_annual", "org": "AUSA",
     "name": "AUSA Annual Meeting & Exposition",
     "agencies": ("Department of the Army", "Department of Defense"),
     "topics": ("army", "defense", "c5isr", "network modernization")},
    {"key": "afcea_augusta", "org": "AFCEA",
     "name": "AFCEA TechNet Augusta",
     "agencies": ("Department of the Army", "Department of Defense",
                  "Defense Information Systems Agency"),
     "topics": ("signal", "cyber", "network", "c5isr", "defense")},
    {"key": "afcea_west", "org": "AFCEA / USNI",
     "name": "AFCEA WEST",
     "agencies": ("Department of the Navy", "Department of Defense",
                  "U.S. Coast Guard"),
     "topics": ("navy", "maritime", "cyber", "network", "defense")},
    {"key": "sea_air_space", "org": "Navy League",
     "name": "Sea-Air-Space Exposition",
     "agencies": ("Department of the Navy", "U.S. Coast Guard",
                  "Department of Defense"),
     "topics": ("navy", "maritime", "defense")},
    {"key": "afa_asc", "org": "Air & Space Forces Association",
     "name": "AFA Air, Space & Cyber Conference",
     "agencies": ("Department of the Air Force", "Department of Defense"),
     "topics": ("air force", "space", "cyber", "defense")},
    {"key": "actiac_elc", "org": "ACT-IAC",
     "name": "ACT-IAC Imagine Nation ELC",
     "agencies": (),                      # government-wide civilian IT
     "topics": ("federal it", "modernization", "civilian", "cloud")},
    {"key": "ndia_events", "org": "NDIA",
     "name": "NDIA conference program",
     "agencies": ("Department of Defense",),
     "topics": ("defense", "industrial base", "acquisition")},
    {"key": "dodiis", "org": "DIA",
     "name": "DoDIIS Worldwide Conference",
     "agencies": ("Department of Defense", "Defense Intelligence Agency"),
     "topics": ("intelligence", "cyber", "cloud", "defense")},
    {"key": "rsa_conference", "org": "RSAC",
     "name": "RSA Conference",
     "agencies": (),
     "topics": ("cyber", "security", "zero trust")},
    {"key": "hhs_himss", "org": "HIMSS",
     "name": "HIMSS Global Health Conference",
     "agencies": ("Department of Health and Human Services",
                  "Department of Veterans Affairs", "Defense Health Agency"),
     "topics": ("health it", "ehr", "health")},
)

_DISCOVERY_SYSTEM = """\
You locate OFFICIAL event pages for federal-market industry conferences.
For each conference named, return ONLY the official event URL on the
organisation's own domain, one per line, formatted exactly as:

<key> <url>

Rules: the URL must be the organisation's own domain, must be the page for
the NEXT occurrence, and must be one you actually saw in search results.
If you cannot find an official page for a conference, write "<key> NONE".
Never invent a URL. Output no other prose.
"""


def _log(msg: str) -> None:
    print(f"     [events:tierC] {msg}", file=sys.stderr, flush=True)


def _visible_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def discover_urls(seeds: Iterable[dict], *, engine: Any = None) -> dict:
    """Model PROPOSES official URLs. Nothing here is trusted yet."""
    seeds = list(seeds)
    if engine is None:
        from agents.decisions.engine import research_engine
        engine = research_engine()
    ask = "; ".join(f"{s['key']}: {s['name']} ({s['org']})" for s in seeds)
    findings, citations = engine.web_research(
        system_prompt=_DISCOVERY_SYSTEM,
        query=f"Official event pages for the next occurrence of: {ask}",
        max_uses=8)
    proposed: dict[str, str] = {}
    for line in str(findings or "").splitlines():
        m = re.match(r"\s*([a-z0-9_]+)\s+(https?://\S+)", line.strip(), re.I)
        if m and any(s["key"] == m.group(1) for s in seeds):
            proposed[m.group(1)] = m.group(2).rstrip(").,")
    # citations are a second source of candidate URLs for keys the model
    # answered NONE for; they still face the same verification.
    _log(f"discovery proposed {len(proposed)} url(s) from "
         f"{len(citations)} citation(s)")
    return proposed


def verify_conference(
    seed: dict,
    url: str,
    *,
    fetch: Optional[Callable[[str], tuple[int, str]]] = None,
    today: Optional[date] = None,
) -> tuple[Optional[dict], Optional[dict]]:
    """Fetch DISPOSES: (verified_row, skip_report). A page that will not load
    or does not print its own dates yields no row."""
    today = today or date.today()
    if fetch is None:
        import httpx
        from tools.api.sam_gov import SAM_HEADERS

        client = httpx.Client(timeout=30.0, headers=SAM_HEADERS,
                              follow_redirects=True,
                              transport=httpx.HTTPTransport(
                                  local_address="0.0.0.0"))

        def fetch(u: str) -> tuple[int, str]:  # noqa: F811
            r = client.get(u)
            return r.status_code, r.text

    try:
        status, body = fetch(url)
    except Exception as exc:  # noqa: BLE001
        return None, {"key": seed["key"], "name": seed["name"], "url": url,
                      "reason": f"{type(exc).__name__}"}
    if status != 200:
        return None, {"key": seed["key"], "name": seed["name"], "url": url,
                      "reason": f"HTTP {status} (site blocks programmatic "
                                f"verification)"}
    text = _visible_text(body)
    if len(text) < 400:
        return None, {"key": seed["key"], "name": seed["name"], "url": url,
                      "reason": "page renders via JavaScript; no verifiable "
                                "text"}
    # Take the next FUTURE occurrence: these pages carry last year's recap
    # alongside next year's dates.
    start, end, span = extract_event_range(text, not_before=today)
    if not start:
        return None, {"key": seed["key"], "name": seed["name"], "url": url,
                      "reason": "page prints no explicit future date range"}
    location = location_from_span(span)
    return {
        "key": seed["key"], "name": seed["name"], "org": seed["org"],
        "url": url, "event_start": start, "event_end": end,
        "date_span": span, "location": location,
        "agencies": list(seed.get("agencies") or ()),
        "topics": list(seed.get("topics") or ()),
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "http_status": status,
    }, None


def location_from_span(span: Optional[str]) -> Optional[str]:
    """A "City, ST" the span actually prints, or None.

    The first regex accepted any capitalized run before a comma and any
    capitalized word after it, so month names and slogans qualified as
    states: the live roster held "Logos Give to AFA Air, Space",
    "Join us at WEST, February", and "With assistance from the U.S. Army
    Cyber Center of Excellence, August" as LOCATIONS, ready to render into
    a client report. Now only the City, TWO-LETTER-STATE shape qualifies;
    a page that prints no such shape gets None, and None renders as
    nothing - garbage never beats absence.
    """
    found = re.search(
        r"\b([A-Z][a-z]+(?:[ .-][A-Z][a-z]+){0,3},\s*[A-Z]{2})\b",
        span or "")
    return found.group(1).strip() if found else None


def _roster_file(root: str) -> str:
    """LILA_EVENT_ROSTER_DIR wins so hermetic tests never read the real
    roster (and never chase its URLs onto the live network); otherwise the
    repo-root reference path."""
    env = os.environ.get("LILA_EVENT_ROSTER_DIR")
    if env:
        return os.path.join(env, os.path.basename(ROSTER_PATH))
    return os.path.join(root, ROSTER_PATH)


def load_roster(root: str = ".") -> dict:
    path = _roster_file(root)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle) or {}
    except (OSError, ValueError):
        return {}


def save_roster(roster: dict, root: str = ".") -> str:
    path = _roster_file(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(roster, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def stale_keys(roster: dict, *, today: Optional[date] = None) -> list[str]:
    """Rows past the quarterly re-verification window, or already finished."""
    today = today or date.today()
    stale = []
    for key, row in (roster.get("conferences") or {}).items():
        verified = str(row.get("verified_at") or "")[:10]
        try:
            age = (today - date.fromisoformat(verified)).days
        except ValueError:
            stale.append(key)
            continue
        ended = str(row.get("event_end") or row.get("event_start") or "")[:10]
        if age > RE_VERIFY_DAYS or (ended and ended < today.isoformat()):
            stale.append(key)
    return stale


def match_to_pack(row: dict, frame: dict) -> tuple[Optional[str], dict]:
    """A conference ships only when it connects to THIS client's book: an
    agency they are funded in (named with its pack record) or a capability
    the pack actually carries. Otherwise it is an industry calendar, which
    is exactly what the client did not ask for."""
    from agents.golden_press.events import agency_match

    agency_hit = next(
        (display for key, display in frame["agencies"].items()
         if any(agency_match(conf_agency, display)
                or agency_match(display, conf_agency)
                for conf_agency in row.get("agencies") or ())), None)
    hay = " ".join(str(t) for t in (row.get("topics") or [])).casefold()
    term_hits = [t for t in frame["terms"]
                 if any(word in hay for word in t.casefold().split()
                        if len(word) > 4)]
    if not (agency_hit or term_hits):
        return None, {}
    basis: dict[str, Any] = {"strength": "conference"}
    # Lead with the conference's OWN audience so each line is distinct and
    # precise: five DoD-corridor conferences reading identically tells the
    # operator nothing and trips the repeated-prose check.
    audience = next(iter(row.get("agencies") or ()), None)
    parts = []
    # The host org keeps sibling conferences distinguishable: AUSA and AFCEA
    # TechNet Augusta share an Army audience, WEST and Sea-Air-Space a Navy
    # one, and identical rationales would be both useless and a repeated-
    # prose violation.
    org = str(row.get("org") or "").strip()
    if audience:
        parts.append(f"{org} · {audience} audience" if org
                     else f"{audience} audience")
    elif org:
        parts.append(f"{org} program")
    if agency_hit:
        evidence = (frame.get("agency_evidence") or {}).get(agency_hit.casefold())
        if evidence is not None and getattr(evidence, "record_id", None):
            dollars = evidence.obligated_dollars or 0
            money = f"${dollars:,.0f}" if dollars else "a cited award"
            # "corridor" is a banned invented term (contract L7). This note
            # never reached an artifact until the v1.2 unbound-feature
            # fallback let conference events render, and then it failed the
            # press. events.py already says "buying account"; match it.
            parts.append(f"your {agency_hit} buying account cites "
                         f"{evidence.record_id} at {money}")
            basis["cited_record"] = {"record_id": evidence.record_id,
                                     "url": evidence.url}
        else:
            parts.append(f"{agency_hit} appears in this pack")
        basis["agency"] = agency_hit
    if term_hits:
        parts.append(f"themes match {term_hits[0]}")
        basis["capability_terms"] = term_hits[:3]
    return " · ".join(parts[:3]), basis


def _conference_in_scope(row: dict, scope: Any) -> bool:
    """The operator's agency universe gates conferences too (2026-07-30).

    `match_to_pack`'s term path could ship an Army conference into a
    civilian-scoped engagement on the word "network" - the same pull-time
    leakage class the industry-day lane closed. A conference naming NO
    agencies is government-wide and passes; a conference whose every named
    agency is out of scope does not.
    """
    if scope is None:
        return True
    agencies = [a for a in (row.get("agencies") or ()) if str(a).strip()]
    if not agencies:
        return True
    from tools.relevance.scope import in_scope

    return any(in_scope({"agency": str(a)}, scope)[0] for a in agencies)


def harvest_tier_c(
    pack: Any,
    *,
    roster: Optional[dict] = None,
    root: str = ".",
    today: Optional[date] = None,
    scope: Any = None,
) -> tuple[list[EventRecord], dict]:
    """CANDIDATE conference rows bound to this client's pack.

    Candidates, not verified events (2026-07-30): the roster's stored
    verification is quarterly, but the lane's law is LIVE verification per
    press, so rows leave here with url_verified=False and the caller runs
    them through the same verify_urls as tiers A and B. The stored
    http_status stays on the roster for the re-verification schedule; it is
    not press-time proof.
    """
    from agents.golden_press.events import pack_frame

    today = today or date.today()
    roster = roster if roster is not None else load_roster(root)
    frame = pack_frame(pack)
    rows = (roster.get("conferences") or {})
    out: list[EventRecord] = []
    stats = {"roster_rows": len(rows), "client_relevant": 0, "kept": 0,
             "out_of_scope": 0, "already_ended": 0}
    for key, row in sorted(rows.items()):
        ended = str(row.get("event_end") or row.get("event_start") or "")
        if ended and ended < today.isoformat():
            stats["already_ended"] += 1
            continue
        if not _conference_in_scope(row, scope):
            stats["out_of_scope"] += 1
            continue
        relevance, basis = match_to_pack(row, frame)
        if relevance is None:
            continue
        stats["client_relevant"] += 1
        out.append(EventRecord(
            event_id=f"conf:{key}",
            name=row.get("name") or key,
            event_type="industry conference",
            source_system="curated_conference",
            url=row.get("url") or "",
            url_verified=False,
            url_status=None,
            host=row.get("org") or "",
            location=row.get("location"),
            event_start=row.get("event_start"),
            event_end=row.get("event_end"),
            event_date_span=row.get("date_span"),
            relevance=relevance,
            relevance_basis=basis,
            retrieved_at=row.get("verified_at"),
        ))
        stats["kept"] += 1
    out.sort(key=lambda e: (e.event_start or "9999", e.name))
    return out, stats
