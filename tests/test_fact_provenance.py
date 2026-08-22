"""Per-figure provenance on the verified-fact model (2026-07-16).

Doctrine: freshness is a property of the FIGURE, not the document. Every
fact carries source_system, source_record_id, and retrieved_at (UTC);
retrieval time recovers from run metadata the artifacts already carry
(sweep `generated_at`, bucket-level `retrieved_at`); unrecoverable means
UNKNOWN_FRESHNESS, never a guess. The motivating defect: a document dated
13 JUL 2026 rendering a figure pulled 02 JUN 2026 was invisible as a risk.
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from agents.reports.facts import (  # noqa: E402
    UNKNOWN_FRESHNESS,
    CompetingValue,
    Fact,
    FactPack,
    SourceSystem,
    _parse_retrieved,
    build_fact_pack,
)
from test_assessment_document import _searches  # noqa: E402


def _pack(searches=None, **kw) -> FactPack:
    return build_fact_pack("Testco", searches=searches or _searches(3),
                           qualify_report={}, as_of=date(2026, 7, 15),
                           profile=None, **kw)


# ── schema: additive, legacy-compatible ─────────────────────────────────────

def test_legacy_fact_construction_still_validates():
    f = Fact(id="F1", kind="market", text="x", source="https://sam.gov/a")
    assert f.source_system is None
    assert f.retrieved_at is None
    assert f.freshness is None
    assert f.disputed is False
    assert f.competing_values == []


def test_verification_tier_is_a_read_alias_of_tier():
    f = Fact(id="F1", kind="opportunity", text="x",
             source="https://sam.gov/a", tier="notice")
    assert f.verification_tier == "notice"
    assert "verification_tier" not in f.model_dump()  # alias, never a field


def test_source_system_enum_members():
    assert {s.name for s in SourceSystem} == {
        "USASPENDING", "SAM", "APFS", "AGENCY_DOC", "ANALYST"}


def test_composer_context_carries_no_provenance_vocabulary():
    """Provenance is gate machinery: the composer payload keeps its
    pre-provenance shape so retrieval language can never leak into copy."""
    pack = _pack()
    assert pack.facts, "fixture must build facts"
    for row in pack.context()["facts"]:
        assert set(row) == {"id", "kind", "text", "value", "source", "tier"}


# ── derivation: run metadata -> per-fact retrieval times ─────────────────────

def test_facts_inherit_sweep_generated_at_as_utc():
    pack = _pack()
    stamped = [f for f in pack.facts if f.retrieved_at is not None]
    assert stamped, "sweep carries generated_at; facts must inherit it"
    expected = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    assert all(f.retrieved_at == expected for f in stamped)
    assert all(f.freshness is None for f in stamped)


def test_sweep_without_generated_at_marks_unknown_freshness():
    searches = _searches(3)
    searches.pop("generated_at")
    pack = _pack(searches=searches)
    assert pack.facts
    assert all(f.retrieved_at is None for f in pack.facts)
    assert all(f.freshness == UNKNOWN_FRESHNESS for f in pack.facts)


def test_source_system_derives_from_the_fact_citation():
    pack = _pack()
    by_system = {f.id: f.source_system for f in pack.facts}
    assert by_system, "fixture builds facts"
    market = [f for f in pack.facts if f.kind == "market"]
    assert market and all(
        f.source_system == SourceSystem.USASPENDING for f in market)
    monitors = [f for f in pack.facts if (f.value or {}).get("verdict") == "monitor"]
    assert monitors and all(
        f.source_system == SourceSystem.SAM for f in monitors)


def test_monitor_fact_carries_notice_record_id():
    pack = _pack()
    monitors = [f for f in pack.facts if (f.value or {}).get("verdict") == "monitor"]
    assert monitors
    assert monitors[0].source_record_id == "M1"


def test_bucket_level_retrieved_at_beats_the_sweep_default():
    """The incumbent buyer map already stamps its own retrieval time; a
    closer stamp always wins over the run-level fallback."""
    from agents.reports.facts import _Counter, _buyer_map_facts, _stamp_provenance
    results = {"incumbent_buyer_map": {
        "retrieved_at": "2026-07-02",
        "population_label": "description-matched records",
        "buyers": [{
            "buyer": "USCIS", "total": 174537374.57,
            "products": ["DevSecOps"],
            "records": [{
                "recipient": "DV UNITED LLC", "amount": 174537374.57,
                "award_id": "70SBUR22F00000113",
                "description": "ODOS III",
                "url": ("https://www.usaspending.gov/award/"
                        "CONT_AWD_70SBUR22F00000113_7003"),
            }],
        }],
    }}
    facts = _buyer_map_facts(results, None, _Counter())
    assert facts
    _stamp_provenance(facts, datetime(2026, 7, 15, tzinfo=timezone.utc))
    f = facts[0]
    assert f.retrieved_at == datetime(2026, 7, 2, tzinfo=timezone.utc)
    assert f.source_system == SourceSystem.USASPENDING
    assert f.source_record_id == "70SBUR22F00000113"


def test_parse_retrieved_handles_aware_naive_date_and_garbage():
    aware = _parse_retrieved("2026-07-14T00:33:38+00:00")
    assert aware == datetime(2026, 7, 14, 0, 33, 38, tzinfo=timezone.utc)
    naive = _parse_retrieved("2026-07-10T18:29:55")
    assert naive == datetime(2026, 7, 10, 18, 29, 55, tzinfo=timezone.utc)
    day = _parse_retrieved("2026-07-13")
    assert day == datetime(2026, 7, 13, tzinfo=timezone.utc)
    assert _parse_retrieved("not a time") is None
    assert _parse_retrieved(None) is None


# ── dispute structure: what the verify pass writes into ─────────────────────

def test_internal_sidecar_carries_the_provenance_trail():
    """Provenance renders NOWHERE in client artifacts; the INTERNAL sidecar
    is where the per-figure trail lives (docs/VERIFICATION_POLICY.md)."""
    from agents.reports.internal_file import build_internal_md
    from agents.reports.remediation import QAReport

    pack = _pack()
    unknown = Fact(id=f"F{len(pack.facts) + 1}", kind="context", text="x",
                   source="https://example.gov", freshness="UNKNOWN_FRESHNESS",
                   disputed=True)
    pack.facts.append(unknown)
    md = build_internal_md("Testco", pack, QAReport(), None)
    assert "## Figure provenance (per-figure freshness)" in md
    assert "retrieved 2026-07-06T09:00:00+00:00" in md   # sweep-recovered
    assert "UNKNOWN_FRESHNESS" in md
    assert "DISPUTED (unresolved blocks release)" in md


def test_dispute_structure_round_trips():
    f = Fact(
        id="F10", kind="incumbent_product",
        text="USCIS shows $174,537,375 in description-matched records.",
        source=("https://www.usaspending.gov/award/"
                "CONT_AWD_70SBUR22F00000113_7003"),
        source_system=SourceSystem.USASPENDING,
        source_record_id="70SBUR22F00000113",
        retrieved_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        disputed=True,
        competing_values=[
            CompetingValue(
                value=174537374.57, observed_by="extract",
                source_system=SourceSystem.USASPENDING,
                source_record_id="70SBUR22F00000113",
                source_url=("https://www.usaspending.gov/award/"
                            "CONT_AWD_70SBUR22F00000113_7003")),
            CompetingValue(
                value=122200000.0, observed_by="verify",
                source_url=("https://washingtontechnology.com/"
                            "contracts/2025/03/example")),
        ],
    )
    again = Fact(**f.model_dump())
    assert again.disputed is True
    assert len(again.competing_values) == 2
    assert again.competing_values[0].observed_by == "extract"
    assert again.competing_values[1].source_system is None  # secondary source
