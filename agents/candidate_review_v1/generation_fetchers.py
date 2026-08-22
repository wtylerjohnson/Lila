"""Generation-bound replay fetchers (the 24h notice trap fix).

A LIVE watch run fetches authoritative records DURING collection, when
``as_of`` is fresh, and persists them into the digest-bound generation as two
extra artifacts.  The document press then replays those persisted fetches
instead of touching the network, so every ``verified_at`` sits inside the
same-run 24h current-notice window and a press is byte-reproducible.

Pure and offline: this module reads a loaded generation object only.
"""

from __future__ import annotations

from typing import Callable, Optional

from agents.candidate_review_v1.verification import FetchedPage, FetchedRecord

VEHICLE_RECORDS_ARTIFACT = "vehicle-notice-records"
EVENT_PAGES_ARTIFACT = "event-page-records"
GENERATION_FETCH_SCHEMA_VERSION = "candidate_review_v1.generation_fetch.v1"


def vehicle_records_payload(
    records: dict[str, FetchedRecord],
) -> dict[str, object]:
    """Artifact payload for collection-time vehicle fetches, by canonical key."""

    return {
        "schema_version": GENERATION_FETCH_SCHEMA_VERSION,
        "records": {
            canonical_key: record.model_dump(mode="json")
            for canonical_key, record in sorted(records.items())
        },
    }


def event_pages_payload(pages: dict[str, FetchedPage]) -> dict[str, object]:
    """Artifact payload for collection-time event page fetches, by lead id."""

    return {
        "schema_version": GENERATION_FETCH_SCHEMA_VERSION,
        "pages": {
            lead_id: page.model_dump(mode="json")
            for lead_id, page in sorted(pages.items())
        },
    }


def generation_bound_fetchers(
    generation,
) -> tuple[Optional[Callable], Optional[Callable]]:
    """Build ``(fetch_event, fetch_vehicle)`` replaying persisted fetches.

    A generation without the artifacts returns ``(None, None)`` so the
    zero-network default path is byte-identical to before.  A persisted
    payload that fails validation raises: a corrupt fetch store must never
    silently degrade into a thin document.
    """

    artifacts = getattr(generation, "artifacts", {}) or {}

    fetch_event = None
    event_payload = artifacts.get(EVENT_PAGES_ARTIFACT)
    if isinstance(event_payload, dict) and event_payload.get("pages"):
        pages = {
            lead_id: FetchedPage.model_validate(data)
            for lead_id, data in event_payload["pages"].items()
        }

        def fetch_event(lead):  # noqa: F811 - deliberate closure name
            return pages.get(lead.lead_id)

    fetch_vehicle = None
    vehicle_payload = artifacts.get(VEHICLE_RECORDS_ARTIFACT)
    if isinstance(vehicle_payload, dict) and vehicle_payload.get("records"):
        records = {
            canonical_key: FetchedRecord.model_validate(data)
            for canonical_key, data in vehicle_payload["records"].items()
        }

        def fetch_vehicle(lead):  # noqa: F811 - deliberate closure name
            return records.get(lead.source_identity.canonical_key)

    return fetch_event, fetch_vehicle
