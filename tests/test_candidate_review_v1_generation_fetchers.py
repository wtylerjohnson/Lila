"""Generation-bound replay fetchers (2026-07-24, the 24h notice trap fix).

Collection-time fetches persist into the generation; the press replays them
with zero network. Corrupt stores raise; absent stores keep the zero-network
default byte-identical.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.candidate_review_v1.contracts import NoticeRole, NoticeStatus
from agents.candidate_review_v1.generation_fetchers import (
    EVENT_PAGES_ARTIFACT,
    VEHICLE_RECORDS_ARTIFACT,
    event_pages_payload,
    generation_bound_fetchers,
    vehicle_records_payload,
)
from agents.candidate_review_v1.verification import FetchedPage, FetchedRecord

_NOW = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)


def _record() -> FetchedRecord:
    return FetchedRecord(
        lead_id="lead-1", final_url="https://sam.gov/opp/N1/view",
        fetched_at=_NOW - timedelta(minutes=30), title="Notice",
        text="USSOCOM seeks services. Responses are due August 3, 2026.",
        record_sha256="a1" * 32, verified_at=_NOW - timedelta(minutes=20),
        notice_status=NoticeStatus.ACTIVE,
        notice_role=NoticeRole.END_USER_REQUIREMENT,
        issuing_office="USSOCOM", solicitation_number="RFI1")


def _page() -> FetchedPage:
    return FetchedPage(
        lead_id="lead-e", final_url="https://www.afcea.org/technet",
        fetched_at=_NOW - timedelta(minutes=25), title="TechNet",
        text="Organizer: AFCEA. Location: Baltimore, MD. Opens December 1, 2026.",
        record_sha256="b2" * 32)


def test_round_trip_replays_persisted_fetches_by_key():
    record, page = _record(), _page()
    generation = SimpleNamespace(artifacts={
        VEHICLE_RECORDS_ARTIFACT: vehicle_records_payload({"sam.gov\x00N1": record}),
        EVENT_PAGES_ARTIFACT: event_pages_payload({"lead-e": page}),
    })
    fetch_event, fetch_vehicle = generation_bound_fetchers(generation)
    got = fetch_vehicle(SimpleNamespace(
        source_identity=SimpleNamespace(canonical_key="sam.gov\x00N1")))
    assert got == record
    assert fetch_vehicle(SimpleNamespace(
        source_identity=SimpleNamespace(canonical_key="other"))) is None
    assert fetch_event(SimpleNamespace(lead_id="lead-e")) == page
    assert fetch_event(SimpleNamespace(lead_id="missing")) is None


def test_absent_artifacts_keep_the_zero_network_default():
    assert generation_bound_fetchers(SimpleNamespace(artifacts={})) == (None, None)
    assert generation_bound_fetchers(SimpleNamespace(artifacts=None)) == (None, None)


def test_corrupt_store_raises_instead_of_degrading():
    generation = SimpleNamespace(artifacts={
        VEHICLE_RECORDS_ARTIFACT: {"schema_version": "x",
                                   "records": {"k": {"nope": 1}}},
    })
    with pytest.raises(Exception):
        generation_bound_fetchers(generation)
