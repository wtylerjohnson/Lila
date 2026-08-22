"""Deterministic seed-author lane tests (Chunk 6, Pass D default path).

The default (no-adapter) path must turn verified evidence into real,
evidence-backed candidate seeds with NO model calls -- the fix for sparse
reports.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from agents.candidate_review_v1.authoring import (
    SeedAuthorLaneNotImplemented,
    author_candidate_review,
)
from agents.candidate_review_v1.candidate_engine import build_candidate_inventory
from agents.candidate_review_v1.contracts import (
    ArtifactBinding,
    EvidenceAssertion,
    EvidenceAssertionSpan,
    EvidenceKind,
    EvidenceRecord,
    EvidenceUse,
    SourceIdentity,
    SourceTier,
)
from agents.candidate_review_v1.verification import AnchorOffer

_NOW = datetime(2026, 7, 23, 18, 0, tzinfo=timezone.utc)
_H = "a" * 64
_BINDING = ArtifactBinding(
    client_id="riverbed", client_name="Riverbed", run_id="run-1",
    scope_designator="all_federal", scope_sha256=_H,
    profile_sha256="b" * 64, evidence_snapshot_sha256="c" * 64)


def _award(index: int, agency: str, program: str, end_text: str, amount: str,
           *, with_span: bool = True) -> EvidenceRecord:
    rid = f"AWARD-{index}"
    end_q = f"The period of performance ends {end_text}."
    excerpt = (f"{agency} awarded {program} to an incumbent. {end_q} "
               f"Obligations to date total {amount} across the ordering period.")
    spans = ((EvidenceAssertionSpan(
        assertion=EvidenceAssertion.AWARD_PERIOD_END, quote=end_q),) if with_span else ())
    return EvidenceRecord(
        evidence_id=f"E-A{index}", client_id="riverbed", run_id="run-1", scope_sha256=_H,
        source_identity=SourceIdentity(source_system="usaspending.gov", record_id=rid),
        source_tier=SourceTier.PROGRAM, source_kind=EvidenceKind.AWARD,
        source_name="USAspending.gov", source_url=f"https://www.usaspending.gov/award/{rid}",
        retrieved_at=_NOW - timedelta(hours=2), title=f"{agency} - {program}", excerpt=excerpt,
        official_source=True, primary_source=True,
        supports=(EvidenceUse.BUYER, EvidenceUse.FUNDING), effective_date=date(2025, 10, 1),
        assertion_spans=spans)


def _offer(record: EvidenceRecord) -> AnchorOffer:
    return AnchorOffer(evidence_id=record.evidence_id, can_anchor=True,
                       confirms_open_notice=False, notice_window_open=False)


def _evidence():
    return (
        _award(1, "Department of the Navy", "Network Observability Services", "September 30, 2027", "$42.6M"),
        _award(2, "Department of Homeland Security", "Enterprise Monitoring", "March 31, 2027", "$18.2M"),
    )


def test_default_path_authors_evidence_backed_corridor_seeds_without_a_model():
    evidence = _evidence()
    result = author_candidate_review(
        binding=_BINDING, as_of=_NOW, evidence=evidence,
        anchor_offers=tuple(_offer(e) for e in evidence))
    assert result.provider_calls == 0 and result.cache_hit is False
    assert len(result.seeds) == 2
    for seed in result.seeds:
        assert seed.corridor_id and seed.corridor_id.startswith("corridor:")
        assert len(seed.dates) == 1 and seed.dates[0].kind.value == "award_end_research_clock"
        assert seed.dates[0].status.value == "research_clock"
        assert len(seed.money) == 1 and seed.money[0].basis.value == "obligated_to_date"


def test_seeds_become_valid_candidates_with_calendar_clocks_and_money():
    evidence = _evidence()
    result = author_candidate_review(
        binding=_BINDING, as_of=_NOW, evidence=evidence,
        anchor_offers=tuple(_offer(e) for e in evidence))
    build = build_candidate_inventory(
        binding=_BINDING, as_of=_NOW, evidence=evidence, seeds=result.seeds)
    assert len(build.candidates) == 2
    assert all(c.kind.value == "research_corridor" for c in build.candidates)
    assert all(len(c.dates) == 1 and len(c.money) == 1 for c in build.candidates)


def test_award_without_a_period_end_span_yields_a_candidate_but_no_clock():
    record = _award(9, "NASA", "Ground Data Services", "July 1, 2028", "$5M", with_span=False)
    result = author_candidate_review(
        binding=_BINDING, as_of=_NOW, evidence=(record,), anchor_offers=(_offer(record),))
    assert len(result.seeds) == 1 and result.seeds[0].dates == ()
    build = build_candidate_inventory(
        binding=_BINDING, as_of=_NOW, evidence=(record,), seeds=result.seeds)
    assert len(build.candidates) == 1


def test_authoring_is_deterministic():
    evidence = _evidence()
    offers = tuple(_offer(e) for e in evidence)
    a = author_candidate_review(binding=_BINDING, as_of=_NOW, evidence=evidence, anchor_offers=offers)
    b = author_candidate_review(binding=_BINDING, as_of=_NOW, evidence=evidence, anchor_offers=offers)
    assert [s.model_dump(mode="json") for s in a.seeds] == [s.model_dump(mode="json") for s in b.seeds]


def test_empty_evidence_authors_nothing():
    result = author_candidate_review(binding=_BINDING, as_of=_NOW, evidence=(), anchor_offers=())
    assert result.seeds == ()


def test_adapter_raises_until_max_plan_pass_lands():
    class _Adapter:
        model = "claude-opus-4-8"

        def call(self, request):
            return None

    with pytest.raises(SeedAuthorLaneNotImplemented):
        author_candidate_review(binding=_BINDING, as_of=_NOW, evidence=(), anchor_offers=(),
                                adapter=_Adapter())
