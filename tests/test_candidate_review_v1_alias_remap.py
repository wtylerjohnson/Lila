"""Alias remap tests (handoff: candidate-review-v1-alias-remap-handoff.md).

The composer resolves evidence references against the canonical universe only
and silently drops what does not resolve.  These tests prove the remap keeps
alias-referencing content ALIVE through composition, and that the driver runs
the remap instead of refusing when an alias exists.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import run_candidate_review as driver
import run_candidate_review_watch as watch_cli
from agents.candidate_review_v1.alias_remap import (
    remap_accepted_evidence,
    remap_all,
    remap_evidence_ids,
)
from agents.candidate_review_v1.calendar_engine import EventSeed, build_calendar
from agents.candidate_review_v1.candidate_engine import build_candidate_inventory
from agents.candidate_review_v1.composer import compose_candidate_review_document
from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    CandidateReviewDocument,
    CoverageState,
    DateKind,
    DatePrecision,
    DateStatus,
    DateValue,
    EventKind,
    EventLifecycleStatus,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceBoundClaim,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    NoticeRole,
    NoticeStatus,
    SourceIdentity,
    SourceTier,
    TickerItem,
)
from agents.candidate_review_v1.event_research import (
    EventQueryAttempt,
    EventResearchFrame,
    EventSourceClass,
    build_event_query_manifest,
)

NOW = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
HASH = "a" * 64
_WIRING_PATH = Path(__file__).parent / "test_candidate_review_v1_wiring.py"
_AS_OF = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def _binding(client_id: str = "testco") -> ArtifactBinding:
    return ArtifactBinding(
        client_id=client_id,
        client_name={"testco": "TestCo", "riverbed": "Riverbed"}[client_id],
        run_id="run-1",
        scope_designator="all-federal",
        scope_sha256=HASH,
        profile_sha256="b" * 64,
        evidence_snapshot_sha256="c" * 64,
    )


def _evidence(
    evidence_id: str,
    record_id: str,
    *,
    client_id: str = "testco",
    run_id: str = "run-1",
    scope_hash: str = HASH,
    retrieved_at: datetime | None = None,
    kind: EvidenceKind = EvidenceKind.ORGANIZER_EVENT,
    excerpt: str = (
        "Example Organizer presents the Federal Data Summit on 2026-09-01 "
        "at Denver, CO."
    ),
    url: str = "https://events.example.org/data-summit",
    assertions: tuple[EvidenceAssertion, ...] = (
        EvidenceAssertion.OFFICIAL_EVENT,
    ),
) -> EvidenceRecord:
    notice = kind == EvidenceKind.NOTICE
    return EvidenceRecord(
        evidence_id=evidence_id,
        client_id=client_id,
        run_id=run_id,
        scope_sha256=scope_hash,
        source_identity=SourceIdentity(
            source_system=("sam.gov" if notice else "event-organizer"),
            record_id=record_id,
        ),
        source_tier=SourceTier.NOTICE if notice else SourceTier.PROGRAM,
        source_kind=kind,
        source_name="Example Organizer",
        source_url=url,
        retrieved_at=retrieved_at or NOW - timedelta(hours=2),
        title=f"Official record {record_id}",
        excerpt=excerpt,
        official_source=True,
        primary_source=True,
        supports=(EvidenceUse.TIMING, EvidenceUse.EVENT),
        notice_status=NoticeStatus.ACTIVE if notice else None,
        notice_role=NoticeRole.END_USER_REQUIREMENT if notice else None,
        verified_at=(retrieved_at or NOW) - timedelta(hours=1) if notice else None,
        assertion_spans=tuple(
            EvidenceAssertionSpan(assertion=assertion, quote=excerpt)
            for assertion in assertions
        ),
    )


def _claim(
    evidence_ids: tuple[str, ...],
    *,
    block_id: str = "B-1",
    client_id: str = "testco",
) -> EvidenceBoundClaim:
    return EvidenceBoundClaim(
        block_id=block_id,
        client_id=client_id,
        heading="Pattern in the official record",
        records_show="Official records show one reviewable buying signal.",
        may_suggest="The evidence may suggest a capability-aligned path.",
        validate_next="Validate requirements, timing, and access next.",
        evidence_ids=evidence_ids,
    )


def _timing(evidence_id: str, *, start: date = date(2026, 9, 1)) -> DateValue:
    return DateValue(
        date_id=f"{evidence_id}:event-start",
        label="Event date",
        kind=DateKind.EVENT_START,
        status=DateStatus.CONFIRMED,
        precision=DatePrecision.DAY,
        source_text=start.isoformat(),
        sort_date=start,
        start=start,
        evidence_ids=(evidence_id,),
    )


def _manifest(binding: ArtifactBinding | None = None):
    frame = EventResearchFrame(
        binding=binding or _binding(),
        revision_sha256="d" * 64,
        capabilities_missions=("federal data operations",),
        trusted_organizer_domains=("events.example.org",),
    )
    return build_event_query_manifest(frame, NOW)


def _query(manifest):
    return next(
        item for item in manifest.queries
        if item.required
        and item.source_class == EventSourceClass.OFFICIAL_ORGANIZER
    )


def _attempts(manifest, *, leads_by_query=None):
    leads_by_query = leads_by_query or {}
    rows = []
    for query in manifest.queries:
        if not query.required:
            rows.append(EventQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.SCOPE_EXCLUDED,
            ))
        else:
            lead_ids = leads_by_query.get(query.query_id, ())
            rows.append(EventQueryAttempt(
                query_id=query.query_id,
                state=CoverageState.RETURNED,
                attempted_at=NOW,
                records_returned=len(lead_ids),
                lead_ids=lead_ids,
                public_detail=(
                    "Discovery completed with no leads."
                    if not lead_ids
                    else f"Discovery returned {len(lead_ids)} lead(s)."
                ),
            ))
    return tuple(rows)


def _event_seed(evidence: EvidenceRecord, manifest) -> EventSeed:
    return EventSeed(
        external_event_id="DATA-SUMMIT-2026",
        title="Federal Data Summit",
        kind=EventKind.CONFERENCE,
        timing=_timing(evidence.evidence_id),
        organizer="Example Organizer",
        location="Denver, CO",
        audience="Federal program and acquisition leaders",
        relevance="The program covers the approved capability frame.",
        validate_next="Confirm the current agenda and registration status.",
        evidence_ids=(evidence.evidence_id,),
        lifecycle_status=EventLifecycleStatus.SCHEDULED,
        last_checked_at=NOW - timedelta(minutes=30),
        query_ids=(_query(manifest).query_id,),
        discovery_lead_ids=("LEAD-1",),
    )


# --- unit behavior -----------------------------------------------------------


def test_empty_alias_map_returns_identical_object():
    # 2026-07-23 handoff sec 4 step 1: empty alias is identity, no rebuild.
    claim = _claim(("E-1",))
    assert remap_evidence_ids(claim, {}) is claim
    seeds = (claim, _claim(("E-2",), block_id="B-2"))
    assert remap_all(seeds, {}) == seeds
    assert remap_all(seeds, {})[0] is seeds[0]


def test_single_alias_rewrites_nested_model_and_keeps_date_id():
    # 2026-07-23 handoff sec 3.1: nested timing.evidence_ids must remap;
    # sec 4 bite 4: date_id keeps its alias-prefixed namespace verbatim.
    seed = _event_seed(_evidence("E-2", "DATA-SUMMIT-2026"), _manifest())
    remapped = remap_evidence_ids(seed, {"E-2": "E-1"})
    assert remapped.evidence_ids == ("E-1",)
    assert remapped.timing.evidence_ids == ("E-1",)
    assert remapped.timing.date_id == "E-2:event-start"


def test_collapsing_aliases_dedupe_order_preserving_and_validate():
    # 2026-07-23 handoff sec 4 bite 1: two aliases onto one canonical id must
    # dedupe or the _unique validator raises (the headline regression).
    claim = _claim(("E-2", "E-3", "E-9"))
    remapped = remap_evidence_ids(claim, {"E-2": "E-1", "E-3": "E-1"})
    assert remapped.evidence_ids == ("E-1", "E-9")


def test_ids_absent_from_the_map_are_untouched():
    # 2026-07-23 handoff sec 1 rule: an id not in the map is already canonical.
    claim = _claim(("E-7", "E-8"))
    remapped = remap_evidence_ids(claim, {"E-2": "E-1"})
    assert remapped.evidence_ids == ("E-7", "E-8")
    assert remapped == claim


def test_ticker_source_evidence_stays_inside_evidence_ids():
    # 2026-07-23 handoff sec 4 bite 3: both fields ride the same table, so the
    # contracts.py traceability validator must keep holding after the remap.
    ticker = TickerItem(
        ticker_id="T-1",
        label="A new official record appeared for review.",
        source_evidence_id="E-2",
        evidence_ids=("E-2", "E-9"),
    )
    remapped = remap_evidence_ids(ticker, {"E-2": "E-1"})
    assert remapped.source_evidence_id == "E-1"
    assert remapped.source_evidence_id in remapped.evidence_ids
    assert remapped.evidence_ids == ("E-1", "E-9")


def test_remap_accepted_evidence_swaps_records_and_dedupes():
    # 2026-07-23 handoff sec 3.7: accepted vehicle evidence holds RECORDS;
    # aliased records are replaced by the canonical record object and deduped.
    first = _evidence("E-1", "VEHICLE-1")
    duplicate = _evidence("E-2", "VEHICLE-1")
    other = _evidence("E-9", "VEHICLE-9")
    canonical_by_id = {"E-1": first, "E-9": other}
    remapped = remap_accepted_evidence(
        {"Q-1": (duplicate, first, other)},
        {"E-2": "E-1"},
        canonical_by_id,
    )
    assert remapped == {"Q-1": (first, other)}
    assert remapped["Q-1"][0] is first
    with pytest.raises(LookupError):
        remap_accepted_evidence(
            {"Q-1": (duplicate,)}, {"E-2": "E-1"}, {"E-9": other})


# --- the integration test that matters ---------------------------------------


def test_alias_referencing_content_survives_composition():
    # 2026-07-23 handoff sec 2: without the remap the composer silently drops
    # alias-referencing claims and events (hollow report); with the remap the
    # same content is PRESENT in the composed document.
    binding = _binding()
    manifest = _manifest(binding)
    first = _evidence("E-1", "DATA-SUMMIT-2026")
    duplicate = _evidence("E-2", "DATA-SUMMIT-2026")
    build = build_candidate_inventory(
        binding=binding, as_of=NOW, evidence=(first, duplicate), seeds=())
    alias = build.evidence_alias_map
    assert alias == {"E-2": "E-1"}

    claim = _claim(("E-2",))
    seed = _event_seed(duplicate, manifest)
    remapped_claim = remap_evidence_ids(claim, alias)
    remapped_seed = remap_evidence_ids(seed, alias)

    attempts = _attempts(
        manifest, leads_by_query={_query(manifest).query_id: ("LEAD-1",)})
    calendar = build_calendar(
        binding=binding, as_of=NOW, candidates=build.candidates,
        evidence=build.evidence, event_seeds=(remapped_seed,),
        manifest=manifest, attempts=attempts,
        trusted_organizer_domains=manifest.trusted_organizer_domains,
        vehicle_signals=(), maximum_items=32)

    composed = compose_candidate_review_document(
        binding=binding, document_id="crv1-testco-run-1", as_of=NOW,
        generated_at=NOW, candidate_build=build, evidence=(),
        calendar_events=calendar.events_for_document,
        pattern_claims=(remapped_claim,),
        coverage=calendar.coverage_receipt.rows)
    document = composed.document
    assert [item.block_id for item in document.pattern_claims] == ["B-1"]
    assert document.pattern_claims[0].evidence_ids == ("E-1",)
    assert any(
        event.title == "Federal Data Summit"
        for event in document.calendar_events
    )

    # The exact same content WITHOUT the remap is silently absent.
    hollow = compose_candidate_review_document(
        binding=binding, document_id="crv1-testco-run-1", as_of=NOW,
        generated_at=NOW, candidate_build=build, evidence=(),
        calendar_events=calendar.events_for_document,
        pattern_claims=(claim,),
        coverage=calendar.coverage_receipt.rows)
    assert hollow.document.pattern_claims == ()


# --- driver behavior ----------------------------------------------------------


def _wiring():
    spec = importlib.util.spec_from_file_location("_crv1_wiring", _WIRING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_generation(tmp_path, monkeypatch):
    registry = _wiring()._approved_world(tmp_path, monkeypatch)
    watch_cli.run_watch_generation(
        "riverbed", root=tmp_path, registry_path=registry)
    return tmp_path / "data" / "state" / "candidate_review_v1"


def test_driver_remaps_instead_of_raising(tmp_path, monkeypatch):
    # 2026-07-23 handoff sec 5.8: with an alias present and remappable content
    # in play the driver must compose (guard replaced by the remap), and the
    # composed document must carry the content on the CANONICAL id.
    state_root = _make_generation(tmp_path, monkeypatch)

    import agents.candidate_review_v1.authoring as authoring
    import agents.candidate_review_v1.verification as verification

    real_verify = verification.verify_research
    real_author = authoring.author_candidate_review

    def _verify(**kwargs):
        result = real_verify(**kwargs)
        binding = kwargs["binding"]
        as_of = kwargs["as_of"]
        pair = tuple(
            _evidence(
                evidence_id,
                "ALIAS-RECORD-1",
                client_id=binding.client_id,
                run_id=binding.run_id,
                scope_hash=binding.scope_sha256,
                retrieved_at=as_of - timedelta(hours=2),
            )
            for evidence_id in ("E-ALIAS-1", "E-ALIAS-2")
        )
        return replace(result, evidence=(*result.evidence, *pair))

    def _author(**kwargs):
        result = real_author(**kwargs)
        claim = _claim(
            ("E-ALIAS-2",), block_id="B-ALIAS",
            client_id=kwargs["binding"].client_id)
        return replace(
            result, pattern_claims=(*result.pattern_claims, claim))

    monkeypatch.setattr(verification, "verify_research", _verify)
    monkeypatch.setattr(authoring, "author_candidate_review", _author)

    qa_path = driver.run_candidate_review(
        "riverbed", root=tmp_path, state_root=state_root,
        certified_at=_AS_OF)
    assert qa_path.is_file()
    document = CandidateReviewDocument.model_validate_json(
        (state_root / "riverbed" / "riverbed.candidate_review.document.json")
        .read_text(encoding="utf-8"))
    claims = {item.block_id: item for item in document.pattern_claims}
    assert "B-ALIAS" in claims
    assert claims["B-ALIAS"].evidence_ids == ("E-ALIAS-1",)
