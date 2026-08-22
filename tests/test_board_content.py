"""Operator-approved Signal Board content + figure reconciliation (2026-07-16).

Doctrine: content is COPY, never evidence. Every rendered dollar figure
lives in the content's figure registry; every registry row reconciles
against the stored sweep ON ITS OWN RECORD or carries an explicit operator
attestation. Unbacked and unattested figures fail loudly with the exact
disagreement: a data discrepancy for the human, never a softened render.
"""
import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from test_assessment_document import _searches  # noqa: E402

from agents.reports.board_content import (  # noqa: E402
    BoardFigure,
    SignalBoardContent,
    figure_provenance_rows,
    load_board_content,
    reconcile_figures,
    reconciliation_error,
)

_SWEEP = {"client": "Testco", "generated_at": "2026-07-14T00:33:38+00:00",
          "results": {"usaspending.gov": [{
              "awards": [{"award_id": "70SBUR22F00000113",
                          "recipient": "DV UNITED LLC",
                          "amount": 174537374.57,
                          "url": ("https://www.usaspending.gov/award/"
                                  "CONT_AWD_70SBUR22F00000113_7003")},
                         {"award_id": "1333BJ26F00282001",
                          "recipient": "THUNDERCAT TECHNOLOGY, LLC",
                          "amount": 1372724.0,
                          "url": ("https://www.usaspending.gov/award/"
                                  "CONT_AWD_1333BJ26F00282001_1344")}]}]}}


def _content(figures) -> SignalBoardContent:
    return SignalBoardContent(client_name="Testco", figures=figures)


def test_machine_marquee_requires_an_explicit_obligation_basis():
    with pytest.raises(
            ValueError, match="recognized obligated-value basis"):
        SignalBoardContent(
            client_name="Testco",
            composition_mode="machine",
            scale_label="Combined obligated contract scale",
            scale={"rows": [], "total": "$1.00M"},
        )

    with pytest.raises(ValueError, match="nonzero machine scale requires"):
        SignalBoardContent(
            client_name="Testco",
            composition_mode="machine",
            scale_label="Competitor-held obligated history",
            scale_total="$1.00M",
            scale={
                "rows": [],
                "total": "$1.00M",
                "basis": "competitor_obligated_to_date",
            },
        )

    with pytest.raises(ValueError, match="components must bind"):
        SignalBoardContent(
            client_name="Testco",
            composition_mode="machine",
            scale_label="Competitor-held obligated history",
            scale_total="$1.00M",
            scale={
                "rows": [{"label": "Route", "amount": "$1.00M"}],
                "total": "$1.00M",
                "basis": "competitor_obligated_to_date",
            },
        )


def test_machine_marquee_components_reconcile_at_display_precision():
    content = SignalBoardContent(
        client_name="Testco",
        composition_mode="machine",
        scale_label="Competitor-held obligated history",
        scale_total="$736.89K",
        scale={
            "rows": [
                {"label": "Route A", "amount": "$548.07K",
                 "amount_basis": "obligated_to_date"},
                {"label": "Route B", "amount": "$188.83K",
                 "amount_basis": "obligated_to_date"},
            ],
            "total": "$736.89K",
            "basis": "competitor_obligated_to_date",
        },
    )
    assert content.scale["total"] == "$736.89K"

    with pytest.raises(ValueError, match="do not reconcile"):
        SignalBoardContent(
            client_name="Testco",
            composition_mode="machine",
            scale_label="Competitor-held obligated history",
            scale_total="$800K",
            scale={
                "rows": [
                    {"label": "Route A", "amount": "$548.07K",
                     "amount_basis": "obligated_to_date"},
                    {"label": "Route B", "amount": "$188.83K",
                     "amount_basis": "obligated_to_date"},
                ],
                "total": "$800K",
                "basis": "competitor_obligated_to_date",
            },
        )


def test_machine_marquee_allows_empty_zero_scale():
    content = SignalBoardContent(
        client_name="Testco",
        composition_mode="machine",
        scale_label="Client-footprint obligated history",
        scale_total="$0",
        scale={
            "rows": [],
            "total": "$0",
            "basis": "client_obligated_to_date",
        },
    )
    assert content.scale["total"] == "$0"


def test_legacy_machine_scale_is_readable_only_for_named_migration():
    legacy = {
        "client_name": "Testco",
        "composition_mode": "machine",
        "scale_total": "$3.00M",
        "scale": {
            "rows": [{"label": "Mixed legacy route", "amount": "$3.00M"}],
            "total": "$3.00M",
        },
    }
    with pytest.raises(ValueError, match="recognized obligated-value basis"):
        SignalBoardContent.model_validate(legacy)

    migrated = SignalBoardContent.model_validate(
        legacy,
        context={"allow_legacy_machine_scale": True},
    )
    assert migrated.scale_total == "$3.00M"


def test_backed_figure_reconciles_on_its_own_record():
    rec = reconcile_figures(_content([BoardFigure(
        text="$174.54M", raw=174537374.57,
        source_record_id="70SBUR22F00000113")]), _SWEEP)
    assert rec.backed == ["$174.54M"]
    assert rec.clean
    assert reconciliation_error(rec) is None


def test_figure_cannot_borrow_a_raw_from_another_record():
    """$1.37M exists in the sweep, but not on the ODOS record; attribution
    integrity is part of backing."""
    rec = reconcile_figures(_content([BoardFigure(
        text="$1.37M", raw=1372724.0,
        source_record_id="70SBUR22F00000113")]), _SWEEP)
    assert rec.backed == []
    assert len(rec.unbacked) == 1
    assert "not stated by its own stored record" in rec.unbacked[0]


def test_unbacked_figure_fails_loudly_with_the_disagreement():
    rec = reconcile_figures(_content([BoardFigure(
        text="$71.23M", raw=71230136.71,
        source_record_id="1333BJ25F00280001")]), _SWEEP)
    assert not rec.clean
    err = reconciliation_error(rec)
    assert "data discrepancy for the human" in err
    assert "$71.23M" in err and "1333BJ25F00280001" in err


def test_attested_figure_passes_as_analyst_decision():
    rec = reconcile_figures(_content([BoardFigure(
        text="$71.23M", raw=71230136.71,
        source_record_id="1333BJ25F00280001",
        analyst_attested=True)]), _SWEEP)
    assert rec.attested == ["$71.23M"]
    assert rec.clean


def test_derived_total_backed_when_every_component_is():
    sweep = json.loads(json.dumps(_SWEEP))
    sweep["results"]["usaspending.gov"][0]["awards"].append(
        {"award_id": "X1", "amount": 1000000.0})
    rec = reconcile_figures(_content([BoardFigure(
        text="$1.17M", raw=1174537374.57 / 1000.0,  # nonsense guard
    )]), sweep)
    assert not rec.clean  # no components, no record: honest failure
    rec2 = reconcile_figures(_content([BoardFigure(
        text="$175.54M", raw=175537374.57,
        component_raws=[174537374.57, 1000000.0])]), sweep)
    assert rec2.backed == ["$175.54M"]


def test_display_precision_must_state_the_raw():
    rec = reconcile_figures(_content([BoardFigure(
        text="$999.99M", raw=174537374.57,
        source_record_id="70SBUR22F00000113")]), _SWEEP)
    assert not rec.clean
    assert "display does not state raw" in rec.unbacked[0]


def test_unregistered_rendered_figure_is_loud():
    content = SignalBoardContent(
        client_name="Testco",
        competitors=[{"money": "$5.55M", "title": "X"}],
        figures=[])
    rec = reconcile_figures(content, _SWEEP)
    assert rec.unregistered == ["$5.55M"]
    assert "UNREGISTERED" in reconciliation_error(rec)


def test_insignary_content_artifact_loads_and_registers_every_figure():
    """The committed content artifact is schema-valid and self-consistent:
    every rendered figure is registered (backing is the golden test's and
    the runner's gate, not this one)."""
    content = load_board_content("Insignary")
    assert content is not None
    assert content.client_name == "Insignary"
    rec = reconcile_figures(content, {"results": {}})
    assert rec.unregistered == []
    # 12 rendered display figures + the two USPTO component orders that
    # back the $71.23M sum (registry-only; they never render)
    assert len(content.figures) == 14


def test_provenance_rows_hold_data_current_closed_without_retrieval_times():
    """Mechanism: any figure without a retrieval time means no honest
    minimum exists and the computed line stays closed."""
    content = SignalBoardContent(client_name="Testco", figures=[
        BoardFigure(text="$1.00M", raw=1000000.0,
                    retrieved_at="2026-07-17T00:00:00+00:00"),
        BoardFigure(text="$2.00M", raw=2000000.0)])   # no retrieval time
    rows = figure_provenance_rows(content)
    from agents.reports.signal_board import _figure_retrieval_dates
    assert _figure_retrieval_dates(rows) is None  # no honest minimum -> no line


def test_insignary_figures_all_carry_record_retrieval_times():
    """Golden data closure (2026-07-17): every live figure is record-backed
    with a real retrieval moment; zero attestations anywhere."""
    content = load_board_content("Insignary")
    rows = figure_provenance_rows(content)
    assert len(rows) == 14
    assert all(r["retrieved_at"] is not None for r in rows)
    assert not any(f.analyst_attested for f in content.figures)


def test_content_overlay_reaches_the_render(monkeypatch):
    from agents.reports.document import build_document
    from agents.reports import signal_board as sb
    doc = build_document("Testco", searches=_searches(2), qualify=None,
                         as_of=date(2026, 7, 15))
    content = SignalBoardContent(
        client_name="Testco",
        hero_context="Screened primary federal records for Testco.",
        chips=[{"label": "BINARY SCA", "tone": "hot"}],
        scale_total="$1.00M", scale_counts="1 corridor · not pipeline",
        scale={"rows": [{"label": "R", "url": "https://www.usaspending.gov/award/A",
                         "naics": "541512", "amount": "$1.00M"}],
               "total": "$1.00M"},
        signal_cards=[{"label": f"C{i}", "value": str(i), "sub": "s",
                       "detail": "d"} for i in range(4)],
        competitors=[{"label": "L", "title": "T", "url": "https://x.gov",
                      "money": "$1.00M", "small": "s", "wedge": "w"}],
        pocs=[{"opp": "O", "opp_url": "https://sam.gov/opp/aa/view",
               "name": "N", "role": "R", "deadline": "D", "signal": "S"}],
        sequence="Lead with the evidenced route.",
        figures=[BoardFigure(text="$1.00M", raw=1000000.0,
                             retrieved_at="2026-07-14T00:00:00+00:00")])
    model = sb.build_model(doc, report_date="15 JUL 2026", content=content)
    assert model["chips"][0]["label"] == "BINARY SCA"
    assert model["signal_cards"][0]["label"] == "C0"   # content beats derived
    assert model["pocs"] and model["sequence"].startswith("Lead")
    html = sb.render_signal_board(model)
    assert "BINARY SCA" in html
    assert "data current 14 JUL 2026" in html   # registry drives the line
    assert "$1.00M" in html


def test_fresh_repull_overrides_stale_buyer_map_money():
    """Pinned (defect round 2): the composed figure and the old buyer-map
    row say $5M; the fresh re-pull for the same GID/PIID says $7M.
    Reconciliation fails with a derivable figure-drift reason naming both
    values; the stale buyer-map occurrence cannot back the figure. A
    figure agreeing with its re-pulled row still backs."""
    sweep = {
        "client": "Testco",
        "results": {
            "incumbent_buyer_map": {"retrieved_at": "2026-07-18", "buyers": [{
                "buyer": "ICE", "agency": "DHS",
                "records": [{"kind": "award", "recipient": "VENDOR",
                             "amount": 5000000.0,
                             "award_id": "70CDCR25F0001",
                             "generated_internal_id": "CONT_AWD_X_7012"}]}]},
            "award_repulls": [{
                "kind": "award_repull",
                "generated_id": "CONT_AWD_X_7012",
                "award_id": "70CDCR25F0001",
                "amount": 7000000.0,
                "retrieved_at": "2026-07-19T09:05:00+00:00"}],
        }}
    stale = BoardFigure(text="$5M", raw=5000000.0,
                        source_record_id="70CDCR25F0001",
                        generated_internal_id="CONT_AWD_X_7012")
    rec = reconcile_figures(_content([stale]), sweep)
    assert rec.backed == []
    assert len(rec.unbacked) == 1
    assert "figure drift" in rec.unbacked[0]
    assert "7000000.0" in rec.unbacked[0]
    assert "5000000.0" in rec.unbacked[0]
    assert "CONT_AWD_X_7012" in rec.unbacked[0]

    fresh = BoardFigure(text="$7M", raw=7000000.0,
                        source_record_id="70CDCR25F0001",
                        generated_internal_id="CONT_AWD_X_7012")
    rec = reconcile_figures(_content([fresh]), sweep)
    assert rec.backed == ["$7M"]
    assert rec.clean


def test_derived_sum_lane_never_rescues_an_authority_governed_figure():
    """Review-round pin: a figure whose GID/PIID the re-pull lane governs
    is rescued by the derived-sum pass ONLY when a component states that
    row's CURRENT value; a whole-blob match on the stale occurrence the
    authority overruled can never rescue it."""
    sweep = {
        "client": "Testco",
        "results": {
            "incumbent_buyer_map": {"retrieved_at": "2026-07-18", "buyers": [{
                "buyer": "ICE", "agency": "DHS",
                "records": [{"kind": "award", "recipient": "VENDOR",
                             "amount": 5000000.0,
                             "award_id": "70CDCR25F0001",
                             "generated_internal_id": "CONT_AWD_X_7012"}]}]},
            "award_repulls": [{
                "kind": "award_repull",
                "generated_id": "CONT_AWD_X_7012",
                "award_id": "70CDCR25F0001",
                "amount": 7000000.0,
                "retrieved_at": "2026-07-19T09:05:00+00:00"}],
        }}
    stale_with_components = BoardFigure(
        text="$5M", raw=5000000.0,
        source_record_id="70CDCR25F0001",
        generated_internal_id="CONT_AWD_X_7012",
        component_raws=[5000000.0])
    rec = reconcile_figures(_content([stale_with_components]), sweep)
    assert rec.backed == []
    assert len(rec.unbacked) == 1
    assert "figure drift" in rec.unbacked[0]
    # an identity-free derived total still backs through its components
    free_total = BoardFigure(text="$5M", raw=5000000.0,
                             component_raws=[5000000.0])
    rec = reconcile_figures(_content([free_total]), sweep)
    assert rec.backed == ["$5M"]
    # a governed derived total whose components INCLUDE the row's fresh
    # value stays backed (the established identity-on-a-sum shape)
    honest_total = BoardFigure(
        text="$12M", raw=12000000.0,
        source_record_id="70CDCR25F0001",
        generated_internal_id="CONT_AWD_X_7012",
        component_raws=[5000000.0, 7000000.0])
    rec = reconcile_figures(_content([honest_total]), sweep)
    assert rec.backed == ["$12M"]
    assert rec.clean


def test_ambiguous_piid_asserts_no_repull_authority():
    """Review-round pin: an award_id shared by two re-pulled rows (PIIDs
    are only per-agency unique) asserts no authority; a gid-less figure
    citing it falls back to record-scoped backing instead of being
    judged against the wrong row."""
    sweep = {
        "client": "Testco",
        "results": {
            "incumbent_buyer_map": {"retrieved_at": "2026-07-18", "buyers": [{
                "buyer": "ICE", "agency": "DHS",
                "records": [{"kind": "award", "recipient": "VENDOR",
                             "amount": 7000000.0,
                             "award_id": "SHARED-PIID",
                             "generated_internal_id": "CONT_AWD_A_1"}]}]},
            "award_repulls": [
                {"kind": "award_repull", "generated_id": "CONT_AWD_A_1",
                 "award_id": "SHARED-PIID", "amount": 7000000.0,
                 "retrieved_at": "2026-07-19T09:05:00+00:00"},
                {"kind": "award_repull", "generated_id": "CONT_AWD_B_2",
                 "award_id": "SHARED-PIID", "amount": 90000000.0,
                 "retrieved_at": "2026-07-19T09:05:00+00:00"},
            ],
        }}
    legacy = BoardFigure(text="$7M", raw=7000000.0,
                         source_record_id="SHARED-PIID")
    rec = reconcile_figures(_content([legacy]), sweep)
    assert rec.backed == ["$7M"]
    # a unique GID keeps its full authority under the same collision
    fresh = BoardFigure(text="$90M", raw=90000000.0,
                        source_record_id="SHARED-PIID",
                        generated_internal_id="CONT_AWD_B_2")
    rec = reconcile_figures(_content([fresh]), sweep)
    assert rec.backed == ["$90M"]
