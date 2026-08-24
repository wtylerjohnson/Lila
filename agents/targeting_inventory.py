"""Read-only, Flask-free projection of the current Targeting inventory.

The Control Room and the external-release boundary must bind the same target
rows.  This module owns that deterministic join.  It may read governed local
artifacts, the contact graph, and the operator outreach list; it never mutates
any of them and it has no dependency on the web application.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from agents.review import (
    canonical_client_name,
    sweep_artifact_path,
    targeting_play_key,
)
from tools.contact_graph import ContactGraphStore, derive_profiles
from tools.contact_graph.names import normalize_name
from tools.contact_graph.outreach import OutreachList, _entry_id
from tools.slug import client_slug


LogoDomainResolver = Callable[[Optional[str], Optional[str]], Optional[str]]


def target_bucket(entry: dict, prime_names: set[str]) -> str:
    """Classify one reasoned target into the Control Room's three rails."""

    blob = " ".join(
        str(entry.get(key) or "")
        for key in ("title", "agency", "org", "company", "source_note")
    ).lower()
    if any(prime for prime in prime_names if prime and prime in blob):
        return "prime"
    return {
        "pursuit": "solicitation",
        "watchlist": "candidate",
        "manual": "candidate",
    }.get(entry.get("reason_kind"), "candidate")


def prime_names_from_searches(searches: Optional[dict]) -> set[str]:
    """Return the normalized teaming-prime names carried by a sweep."""

    if not isinstance(searches, dict):
        return set()
    results = searches.get("results") or {}
    if not isinstance(results, dict):
        return set()
    subawards = results.get("subawards") or {}
    if not isinstance(subawards, dict):
        return set()
    names: set[str] = set()
    for prime in subawards.get("primes") or []:
        if not isinstance(prime, dict):
            continue
        name = str(prime.get("name") or "").strip().lower()
        if name:
            names.add(name.split(",")[0][:40])
    return names


def _profile_row(
    profile: Any,
    today: date,
    logo_domain: Optional[LogoDomainResolver],
) -> dict:
    office_path = profile.offices[0] if profile.offices else None
    return {
        "person_name": profile.person_name,
        "normalized_name": profile.normalized_name,
        "agency": profile.agency,
        "offices": profile.offices,
        "naics": profile.naics,
        "title": profile.titles[0] if profile.titles else None,
        "seat": profile.role_types[0] if profile.role_types else None,
        "sighting_count": profile.sighting_count,
        "first_observed": (
            profile.first_observed.isoformat()
            if profile.first_observed else None
        ),
        "last_observed": (
            profile.last_observed.isoformat()
            if profile.last_observed else None
        ),
        "days_since": (
            (today - profile.last_observed).days
            if profile.last_observed else None
        ),
        "channels": [
            {
                "kind": channel.kind,
                "value": channel.value,
                "grade": channel.grade,
                "last_observed": (
                    channel.last_observed.isoformat()
                    if channel.last_observed else None
                ),
                "source_url": channel.source_url,
                "notice_id": channel.notice_id,
            }
            for channel in profile.channels
        ],
        "rotation": (
            profile.rotation.model_dump(mode="json")
            if profile.rotation else None
        ),
        "logo_domain": (
            logo_domain(profile.agency, office_path)
            if logo_domain is not None else None
        ),
    }


def project_target_inventory(
    *,
    slug: str,
    document: Any,
    profiles: list[Any],
    observations: list[Any],
    outreach_entries: list[dict],
    prime_names: set[str],
    today: date,
    logo_domain: Optional[LogoDomainResolver] = None,
) -> dict[str, list[dict]]:
    """Join current research and contact state into exact UI target rows.

    The return shape deliberately includes ``pursuit_pocs`` for the Control
    Room, while the release boundary consumes only ``targets``.  Input rows
    are copied before annotations are added.
    """

    notice_reason: dict[str, dict] = {}
    if document is not None:
        for pursuit in document.board.pursuits:
            reason = {
                "kind": "pursuit",
                "rank": pursuit.rank,
                "label": f"POC on pursuit #{pursuit.rank} · {pursuit.title}",
            }
            notice_reason[pursuit.source_id] = reason
            for amendment in pursuit.amendments:
                source_id = amendment.get("source_id")
                if source_id:
                    notice_reason[source_id] = reason
        for entry in document.watchlist.entries:
            if entry.id and entry.id not in notice_reason:
                notice_reason[entry.id] = {
                    "kind": "watchlist",
                    "rank": 90,
                    "label": (
                        "sighted on watchlist item · "
                        f"{entry.title or entry.id}"
                    ),
                }

    person_reason: dict[tuple[str, str], dict] = {}
    for observation in observations:
        reason = notice_reason.get(observation.notice_id)
        if not reason:
            continue
        key = (
            normalize_name(observation.person_name),
            (observation.agency or "").lower(),
        )
        current = person_reason.get(key)
        if current is None or reason["rank"] < current["rank"]:
            person_reason[key] = reason

    targets: list[dict] = []
    outreach_ids: set[str] = set()
    for entry in outreach_entries:
        reason = (
            {"kind": "manual", "rank": 50,
             "label": "added for this client"}
            if entry.get("client") == slug else None
        )
        observed_reason = person_reason.get((
            entry.get("normalized_name"),
            (entry.get("agency") or "").lower(),
        ))
        if (observed_reason is not None
                and (reason is None
                     or observed_reason["rank"] < reason["rank"])):
            reason = observed_reason
        if reason is None:
            continue
        outreach_ids.add(entry["id"])
        row = {
            **entry,
            "reason": reason["label"],
            "reason_kind": reason["kind"],
            "reason_rank": reason["rank"],
        }
        row["bucket"] = target_bucket(row, prime_names)
        row["play_id"] = targeting_play_key(row.get("reason") or "")
        targets.append(row)

    pursuit_pocs: list[dict] = []
    for profile in profiles:
        reason = person_reason.get((
            profile.normalized_name,
            (profile.agency or "").lower(),
        ))
        if (not reason
                or _entry_id(profile.normalized_name, profile.agency)
                in outreach_ids):
            continue
        row = _profile_row(profile, today, logo_domain)
        row.update(
            reason=reason["label"],
            reason_kind=reason["kind"],
            reason_rank=reason["rank"],
        )
        row["bucket"] = target_bucket(row, prime_names)
        pursuit_pocs.append(row)

    targets.sort(key=lambda row: (
        row["reason_rank"], -(row.get("sighting_count") or 0)))
    pursuit_pocs.sort(key=lambda row: (
        row["reason_rank"], -(row.get("sighting_count") or 0)))
    return {"targets": targets, "pursuit_pocs": pursuit_pocs}


def _read_json_object(path: Path) -> Optional[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _agency_domain_resolver(root: Path) -> LogoDomainResolver:
    payload = _read_json_object(
        root / "data" / "reference" / "agency_domains.json") or {}
    agencies = payload.get("agencies") or {}
    offices = payload.get("offices") or {}

    def resolve(agency: Optional[str], office_path: Optional[str]) -> Optional[str]:
        haystack = (office_path or "").upper()
        for name, domain in offices.items():
            if str(name).upper() in haystack:
                return str(domain)
        domain = agencies.get((agency or "").strip().upper())
        return str(domain) if domain else None

    return resolve


def load_target_inventory(
    client_name: str,
    root: Path,
    *,
    today: Optional[date] = None,
) -> list[dict]:
    """Read and derive the actual current target rows for a release gate.

    This is intentionally a live loader rather than a saved target payload.
    Calling it twice re-reads the sweep, review inputs, contact observations,
    merge decisions, and outreach list both times.
    """

    root = Path(root).resolve()
    review_dir = root / "data" / "review"
    slug = client_slug(client_name)
    exact_client = canonical_client_name(slug, str(review_dir))
    current_date = today or date.today()

    searches: Optional[dict] = None
    searches_path = Path(sweep_artifact_path(
        exact_client, review_dir=str(review_dir)))
    if searches_path.is_file():
        searches = _read_json_object(searches_path)

    document = None
    if isinstance(searches, dict) and searches.get("results"):
        from agents.reports.document import build_document

        qualify = _read_json_object(review_dir / f"{slug}.qualify.json")
        document_client = searches.get("client") or slug
        document = build_document(
            document_client,
            searches=searches,
            qualify=qualify,
            _log_unresolved_entities=False,
        )

    graph_root = Path(os.environ.get(
        "LILA_CONTACT_GRAPH_DIR",
        root / "data" / "state" / "contact_graph",
    ))
    store = ContactGraphStore(root=graph_root)
    observations = store.read_observations()
    profiles, _review = derive_profiles(
        observations,
        now=current_date,
        decisions=store.read_decisions(),
    )
    outreach_entries = OutreachList(store=store).render(
        recover_corrupt=False)
    projection = project_target_inventory(
        slug=slug,
        document=document,
        profiles=profiles,
        observations=observations,
        outreach_entries=outreach_entries,
        prime_names=prime_names_from_searches(searches),
        today=current_date,
        logo_domain=_agency_domain_resolver(root),
    )
    return projection["targets"]


__all__ = [
    "load_target_inventory",
    "prime_names_from_searches",
    "project_target_inventory",
    "target_bucket",
]
