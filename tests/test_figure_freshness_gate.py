"""Per-figure freshness gate + arbitration policy enforcement (2026-07-16).

Doctrine: a figure rendered in a client artifact must prove WHEN it was
pulled. Stale (older than FRESHNESS_MAX_AGE_DAYS at render time),
UNKNOWN_FRESHNESS, and disputed-and-unresolved figures are hard gate events
riding the existing violation -> flag -> DRAFT chain. Policy text:
docs/VERIFICATION_POLICY.md. Motivating defect: the Insignary artifact dated
13 JUL 2026 headlining a figure retrievable only from a 02 JUN-era pull.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from agents.reports.facts import (
    UNKNOWN_FRESHNESS,
    CompetingValue,
    Fact,
    FactPack,
    SourceSystem,
)
from agents.reports.verification import (
    FRESHNESS_MAX_AGE_DAYS,
    FROZEN,
    POLICY_DECIDER,
    FigureResolution,
    append_resolution,
    arbitrate,
    cited_fact_ids,
    data_current_date,
    data_current_violations,
    figure_key,
    freshness_violations,
    is_resolved,
    load_resolutions,
)

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _fact(fid="F1", *, retrieved=None, freshness=None, disputed=False,
          competing=(), system=SourceSystem.USASPENDING,
          record="70SBUR22F00000113"):
    return Fact(
        id=fid, kind="incumbent_product",
        text="USCIS shows $174,537,375 in description-matched records.",
        source=("https://www.usaspending.gov/award/"
                "CONT_AWD_70SBUR22F00000113_7003"),
        source_system=system, source_record_id=record,
        retrieved_at=retrieved, freshness=freshness,
        disputed=disputed, competing_values=list(competing))


def _pack(*facts):
    return FactPack(client_name="Insignary", as_of=date(2026, 7, 13),
                    facts=list(facts))


# ── the staled fixture: the gate fires and names the adapter ────────────────

def test_stale_figure_hard_fails_with_repull_instruction():
    staled = NOW - timedelta(days=FRESHNESS_MAX_AGE_DAYS + 27)  # 02 JUN-era
    pack = _pack(_fact(retrieved=staled))
    v = freshness_violations(pack, {"F1"}, now=NOW)
    assert [x.rule for x in v] == ["FRESHNESS_STALE"]
    detail = v[0].detail
    assert "F1" in detail
    assert "USAspending adapter" in detail            # names the adapter
    assert "Command Center step 'searches'" in detail  # sanctioned path only
    assert "70SBUR22F00000113" in detail               # names the record
    assert str(FRESHNESS_MAX_AGE_DAYS) in detail       # limit from the constant


def test_threshold_is_a_boundary_not_a_literal():
    at_limit = NOW - timedelta(days=FRESHNESS_MAX_AGE_DAYS)
    past_limit = NOW - timedelta(days=FRESHNESS_MAX_AGE_DAYS, hours=1)
    assert freshness_violations(_pack(_fact(retrieved=at_limit)), {"F1"},
                                now=NOW) == []
    assert [x.rule for x in freshness_violations(
        _pack(_fact(retrieved=past_limit)), {"F1"}, now=NOW,
    )] == ["FRESHNESS_STALE"]


def test_unknown_freshness_hard_fails():
    pack = _pack(_fact(retrieved=None, freshness=UNKNOWN_FRESHNESS))
    v = freshness_violations(pack, {"F1"}, now=NOW)
    assert [x.rule for x in v] == ["FRESHNESS_UNKNOWN"]
    assert "UNKNOWN_FRESHNESS" in v[0].detail


def test_fresh_cited_figure_passes():
    pack = _pack(_fact(retrieved=NOW - timedelta(days=2)))
    assert freshness_violations(pack, {"F1"}, now=NOW) == []


def test_only_rendered_figures_gate():
    """An internal-only fact ages without blocking a release."""
    staled = _fact("F7", retrieved=NOW - timedelta(days=400))
    pack = _pack(_fact(retrieved=NOW - timedelta(days=1)), staled)
    assert freshness_violations(pack, {"F1"}, now=NOW) == []


def test_cited_fact_ids_reads_rendered_citations():
    html = "<p>$174,537,375 obligated [F10]; median [F2] and [F10] again</p>"
    assert cited_fact_ids(html) == {"F10", "F2"}


# ── the computed data-current line ──────────────────────────────────────────

def test_data_current_is_min_retrieved_across_rendered_figures():
    pack = _pack(
        _fact("F1", retrieved=datetime(2026, 7, 12, tzinfo=timezone.utc)),
        _fact("F2", retrieved=datetime(2026, 6, 2, tzinfo=timezone.utc)),
        _fact("F3", retrieved=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    assert data_current_date(pack, {"F1", "F2"}) == date(2026, 6, 2)
    assert data_current_date(pack, {"F1", "F3"}) == date(2026, 7, 1)


def test_data_current_refuses_a_dishonest_minimum():
    pack = _pack(
        _fact("F1", retrieved=datetime(2026, 7, 12, tzinfo=timezone.utc)),
        _fact("F2", retrieved=None, freshness=UNKNOWN_FRESHNESS))
    assert data_current_date(pack, {"F1", "F2"}) is None
    assert data_current_date(pack, set()) is None


def test_document_date_divergence_fails_before_render():
    """The Insignary shape: document dated 13 JUL, figure pulled 02 JUN."""
    pack = _pack(_fact(retrieved=datetime(2026, 6, 2, tzinfo=timezone.utc)))
    v = data_current_violations(pack, {"F1"}, date(2026, 7, 13))
    assert [x.rule for x in v] == ["DATA_CURRENT_DIVERGENT"]
    assert "2026-06-02" in v[0].detail and "2026-07-13" in v[0].detail
    fresh = _pack(_fact(retrieved=datetime(2026, 7, 12, tzinfo=timezone.utc)))
    assert data_current_violations(fresh, {"F1"}, date(2026, 7, 13)) == []


# ── dispute arbitration: the policy's three rules, mechanically ─────────────

_EXTRACT = CompetingValue(
    value=174537374.57, observed_by="extract",
    source_system=SourceSystem.USASPENDING,
    source_record_id="70SBUR22F00000113",
    source_url=("https://www.usaspending.gov/award/"
                "CONT_AWD_70SBUR22F00000113_7003"))
_SECONDARY = CompetingValue(
    value=122200000.0, observed_by="verify",
    source_url="https://washingtontechnology.com/contracts/2025/03/example")


def test_primary_record_wins_automatically_over_secondary():
    """Rule 1: extract vs verify disagreement resolves to the primary
    federal record; the decision is made by RULE, never by a model."""
    f = _fact(disputed=True, competing=[_EXTRACT, _SECONDARY])
    resolution = arbitrate(f, now=NOW)
    assert resolution is not None
    assert resolution.decider == POLICY_DECIDER
    assert "174537374.57" in resolution.decision
    assert resolution.figure == figure_key(f)
    assert len(resolution.values) == 2          # both values logged
    assert resolution.decided_at == NOW


def test_two_disagreeing_primaries_freeze_for_a_human():
    """Rule 2: primaries in conflict cannot auto-resolve."""
    other_primary = CompetingValue(
        value=122200000.0, observed_by="verify",
        source_system=SourceSystem.SAM, source_record_id="70SBUR22F00000113")
    f = _fact(disputed=True, competing=[_EXTRACT, other_primary])
    assert arbitrate(f, now=NOW) is None


def test_unreachable_primary_freezes_for_a_human():
    """Rule 2: a dispute with no reachable primary record cannot auto-resolve."""
    f = _fact(disputed=True, competing=[_SECONDARY])
    assert arbitrate(f, now=NOW) is None


def test_disputed_unresolved_blocks_like_stale():
    f = _fact(retrieved=NOW - timedelta(days=1),
              disputed=True, competing=[_EXTRACT, _SECONDARY])
    v = freshness_violations(_pack(f), {"F1"}, now=NOW)
    assert [x.rule for x in v] == ["FIGURE_DISPUTED"]
    assert "VERIFICATION_POLICY" in v[0].detail


# ── the append-only resolution log ──────────────────────────────────────────

def _resolution(f, decision, decider="operator:william"):
    return FigureResolution(
        figure=figure_key(f), figure_label=f.text[:120],
        values=[cv.model_dump(mode="json") for cv in f.competing_values],
        records_cited=["70SBUR22F00000113"],
        decision=decision, decider=decider, decided_at=NOW)


def test_resolution_log_round_trips_and_unblocks(tmp_path):
    f = _fact(retrieved=NOW - timedelta(days=1),
              disputed=True, competing=[_EXTRACT, _SECONDARY])
    log_dir = str(tmp_path)
    append_resolution("Insignary", _resolution(f, "174537374.57"),
                      fact=f, log_dir=log_dir)
    rows = load_resolutions("Insignary", log_dir=log_dir)
    assert len(rows) == 1
    assert is_resolved(f, rows)
    assert freshness_violations(_pack(f), {"F1"}, now=NOW,
                                resolutions=rows) == []


def test_frozen_resolution_keeps_the_figure_blocked(tmp_path):
    f = _fact(disputed=True, retrieved=NOW - timedelta(days=1),
              competing=[_EXTRACT, _SECONDARY])
    append_resolution("Insignary", _resolution(f, FROZEN),
                      fact=f, log_dir=str(tmp_path))
    rows = load_resolutions("Insignary", log_dir=str(tmp_path))
    assert not is_resolved(f, rows)
    assert [x.rule for x in freshness_violations(
        _pack(f), {"F1"}, now=NOW, resolutions=rows)] == ["FIGURE_DISPUTED"]


def test_log_is_append_only_and_latest_row_wins(tmp_path):
    f = _fact(disputed=True, retrieved=NOW - timedelta(days=1),
              competing=[_EXTRACT, _SECONDARY])
    path = append_resolution("Insignary", _resolution(f, FROZEN),
                             fact=f, log_dir=str(tmp_path))
    append_resolution("Insignary", _resolution(f, "174537374.57"),
                      fact=f, log_dir=str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        assert len(fh.readlines()) == 2       # superseding = a NEW row
    rows = load_resolutions("Insignary", log_dir=str(tmp_path))
    assert is_resolved(f, rows)               # latest decision governs


def test_no_model_resolves_a_figure_it_authored(tmp_path):
    """Rule 3: a disputant can never be the decider."""
    f = _fact(disputed=True, competing=[_EXTRACT, _SECONDARY])
    with pytest.raises(ValueError, match="no model resolves"):
        append_resolution("Insignary", _resolution(f, "x", decider="verify"),
                          fact=f, log_dir=str(tmp_path))
    with pytest.raises(ValueError, match="no model resolves"):
        append_resolution("Insignary", _resolution(f, "x", decider="extract"),
                          fact=f, log_dir=str(tmp_path))
    # the policy rule and a human are not disputants
    append_resolution("Insignary", _resolution(f, "x", decider=POLICY_DECIDER),
                      fact=f, log_dir=str(tmp_path))
    append_resolution("Insignary", _resolution(f, "x"),
                      fact=f, log_dir=str(tmp_path))


def test_naive_decision_timestamp_is_rejected(tmp_path):
    f = _fact(disputed=True, competing=[_EXTRACT, _SECONDARY])
    bad = _resolution(f, "x").model_copy(
        update={"decided_at": datetime(2026, 7, 13, 12, 0)})
    with pytest.raises(ValueError, match="timezone-aware"):
        append_resolution("Insignary", bad, fact=f, log_dir=str(tmp_path))


# ── the worked example the policy doc cites ─────────────────────────────────

def test_worked_example_insignary_odos_iii(tmp_path):
    """The motivating defect, end to end: the extract pass carries
    $174,537,375 obligated on 70SBUR22F00000113 from the USAspending award
    page; the verify pass carries $122.2M from Deltek-derived reporting.
    Rule 1 resolves to the primary record automatically, the resolution is
    logged append-only, and the gate opens."""
    f = _fact(retrieved=NOW - timedelta(days=1),
              disputed=True, competing=[_EXTRACT, _SECONDARY])

    # disputed and unresolved: the artifact cannot pass the release gate
    assert [x.rule for x in freshness_violations(
        _pack(f), {"F1"}, now=NOW,
        resolutions=load_resolutions("Insignary", log_dir=str(tmp_path)),
    )] == ["FIGURE_DISPUTED"]

    # the automatic rule: one primary record value, one secondary -> primary
    resolution = arbitrate(f, now=NOW)
    assert resolution is not None and resolution.decider == POLICY_DECIDER
    append_resolution("Insignary", resolution, fact=f, log_dir=str(tmp_path))

    rows = load_resolutions("Insignary", log_dir=str(tmp_path))
    assert is_resolved(f, rows)
    assert freshness_violations(_pack(f), {"F1"}, now=NOW,
                                resolutions=rows) == []
