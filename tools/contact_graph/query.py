"""Query interface for downstream use.

Given any of agency / office / NAICS / keywords, return ranked ContactProfiles
with sighting counts and graded channels — e.g.

    Jane Smith, Contracting Officer, 14 notices in NAICS 541512 at
    COMMERCE.NOAA.ACQ — email grade A (observed 2026-06-12), phone grade B
    (observed 2025-05-01)

Grades are recomputed from the observations at call time (default now = today),
so an answer is never served from a stale grade. Ranking favors reachability then
activity then recency: best channel grade, then sighting count, then last seen.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence, Union

from tools.contact_graph.resolve import derive_profiles
from tools.contact_graph.schemas import ContactProfile
from tools.contact_graph.store import ContactGraphStore

_GRADE_SCORE = {"A": 3, "B": 2, "C": 1}

Keywords = Union[str, Sequence[str], None]


def _terms(keywords: Keywords) -> list[str]:
    if not keywords:
        return []
    if isinstance(keywords, str):
        keywords = [keywords]
    return [k.strip().lower() for k in keywords if k and k.strip()]


def _blob(p: ContactProfile) -> str:
    parts = [p.person_name, p.agency, *p.offices, *p.titles, *p.naics, *p.role_types]
    return " ".join(x for x in parts if x).lower()


def _best_grade_score(p: ContactProfile) -> int:
    return max((_GRADE_SCORE.get(c.grade, 0) for c in p.channels), default=0)


def _matches(
    p: ContactProfile,
    agency: Optional[str],
    office: Optional[str],
    naics: Optional[str],
    terms: list[str],
) -> bool:
    if agency and agency.strip().lower() not in (p.agency or "").lower():
        return False
    if office and not any(office.strip().lower() in (o or "").lower() for o in p.offices):
        return False
    if naics and naics.strip() not in p.naics:
        return False
    if terms and not any(t in _blob(p) for t in terms):  # keywords are OR (recall)
        return False
    return True


def query(
    store: ContactGraphStore,
    *,
    agency: Optional[str] = None,
    office: Optional[str] = None,
    naics: Optional[str] = None,
    keywords: Keywords = None,
    now: Optional[date] = None,
    limit: Optional[int] = None,
) -> list[ContactProfile]:
    """Ranked profiles matching the given filters. Empty filters return all."""
    now = now or date.today()
    profiles, _ = derive_profiles(store.read_observations(), now=now)
    terms = _terms(keywords)
    hits = [p for p in profiles if _matches(p, agency, office, naics, terms)]
    hits.sort(
        key=lambda p: (_best_grade_score(p), p.sighting_count, p.last_observed or date.min),
        reverse=True,
    )
    return hits[:limit] if limit else hits


def format_profile(p: ContactProfile) -> str:
    """One-line, human-readable summary (spec item 6 example shape)."""
    role = (p.titles[0] if p.titles else None) or (p.role_types[0] if p.role_types else None)
    head = p.person_name + (f", {role}" if role else "")

    middle = f"{p.sighting_count} notice" + ("s" if p.sighting_count != 1 else "")
    if p.naics:
        middle += f" in NAICS {p.naics[0]}"
    if p.offices:
        middle += f" at {p.offices[0]}"

    channels = ", ".join(
        f"{c.kind} grade {c.grade} (observed {c.last_observed})" for c in p.channels
    ) or "no reachable channel"

    line = f"{head}, {middle} — {channels}"
    if p.rotation and p.rotation.likely_successor:
        line += (
            f" [rotation: last seen {p.rotation.last_seen}, "
            f"possible successor {p.rotation.likely_successor}]"
        )
    return line
