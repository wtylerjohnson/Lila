"""Scoped award re-pull (2026-07-17): cited records land in the stored sweep.

Doctrine: the tool pulls exactly the records the board's figure registry
cites, refuses partial pulls, writes through the canonical atomic artifact
path, and never touches the sweep's generated_at. AGENCY_DOC rows are
secondary evidence with an honest source note and release-time claim check,
never attested.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from agents.reports.board_content import (  # noqa: E402
    BoardFigure,
    SignalBoardContent,
    reconcile_figures,
)
from tools.api import award_repull as rp  # noqa: E402

NOW = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)

_GID = "CONT_AWD_HR001124C0488_9700_-NONE-_-NONE-"
_PAYLOAD = {
    "piid": "HR001124C0488",
    "description": "Autonomous software assurance research",
    "type_description": "DELIVERY ORDER",
    "recipient": {"recipient_name": "GALOIS, INC."},
    "total_obligation": 3862451.0,
    "base_and_all_options": 4446007.0,
    "period_of_performance": {
        "start_date": "2024-10-07", "end_date": "2026-06-07",
        "potential_end_date": "2026-09-07 00:00:00"},
    "latest_transaction_contract_data": {
        "naics": "541715",
        "naics_description": "Research and Development",
        "product_or_service_code": "AC13",
        "product_or_service_description": "R&D Defense System",
        "type_set_aside": "SBA",
        "type_set_aside_description": "TOTAL SMALL BUSINESS SET-ASIDE",
        "extent_competed": "A",
        "extent_competed_description": "FULL AND OPEN COMPETITION",
        "multiple_or_single_award_description": "MULTIPLE AWARD",
        "solicitation_identifier": "HR0011-24-R-0001",
    },
    "place_of_performance": {
        "city_name": "ARLINGTON", "state_code": "VA",
        "country_name": "UNITED STATES", "zip5": "22203",
    },
    "parent_award": {
        "piid": "N0017821D9411",
        "generated_unique_award_id": "CONT_IDV_N0017821D9411_9700",
        "type_of_idc_description": "INDEFINITE DELIVERY / INDEFINITE QUANTITY",
        "multiple_or_single_aw_desc": "MULTIPLE AWARD",
    },
}


def _content():
    return SignalBoardContent(client_name="Testco", figures=[
        BoardFigure(text="$3.86M", raw=3862451.0,
                    source_system="usaspending",
                    source_record_id="HR001124C0488",
                    generated_internal_id=_GID),
        BoardFigure(text="$4.45M", raw=4446007.0,
                    source_system="usaspending",
                    source_record_id="HR001124C0488",
                    generated_internal_id=_GID),
        BoardFigure(text="$20B", raw=20000000000.0,
                    source_system="agency_doc",
                    source_record_id="sewpvi-ceiling",
                    source_url=(
                        "https://www.nasa.gov/news-release/"
                        "nasa-awards-solutions-for-federal-enterprise-"
                        "procurement-contracts/")),
    ])


def test_normalize_award_row_is_flat_and_provenanced():
    row = rp.normalize_award(_GID, _PAYLOAD, retrieved_at=NOW.isoformat())
    assert row["award_id"] == "HR001124C0488"
    assert row["amount"] == 3862451.0
    assert row["potential_ceiling"] == 4446007.0
    assert row["end_date"] == "2026-06-07"
    assert row["potential_end_date"] == "2026-09-07"   # date-only, no time
    assert row["naics"] == "541715"
    assert row["psc"] == "AC13"
    assert row["set_aside"] == "TOTAL SMALL BUSINESS SET-ASIDE"
    assert row["competition"] == "FULL AND OPEN COMPETITION"
    assert row["place_of_performance"] == {
        "city_name": "ARLINGTON", "state_code": "VA",
        "country_name": "UNITED STATES", "zip5": "22203",
    }
    assert row["parent_award_id"] == "N0017821D9411"
    assert row["parent_generated_id"] == "CONT_IDV_N0017821D9411_9700"
    assert row["parent_vehicle_type"] == (
        "INDEFINITE DELIVERY / INDEFINITE QUANTITY"
    )
    assert row["source_system"] == "usaspending"
    assert row["retrieved_at"] == NOW.isoformat()


def test_board_gids_dedupe_and_skip_agency_docs():
    assert rp.board_award_gids(_content()) == [_GID]


def test_agency_doc_row_is_secondary_with_honest_note():
    rows = rp.agency_doc_rows(_content(), retrieved_at=NOW.isoformat())
    assert len(rows) == 1
    row = rows[0]
    assert row["source_record_id"] == "sewpvi-ceiling"
    assert row["value"] == 20000000000.0
    assert row["source_system"] == "agency_doc"
    assert "claim visibility is checked" in row["note"]


def test_repulled_rows_back_the_figures_record_scoped(monkeypatch, tmp_path):
    import agents.reports.board_content as bc
    monkeypatch.setattr(bc, "load_board_content", lambda _c: _content())
    pulled = rp.repull_board_awards("Testco", fetch=lambda _u: _PAYLOAD, now=NOW)
    assert not pulled["errors"]

    sweep_path = tmp_path / "searches_testco.json"
    sweep_path.write_text(json.dumps(
        {"client": "Testco", "generated_at": "2026-07-14T00:33:38+00:00",
         "results": {"sam.gov": []}}), encoding="utf-8")
    rp.merge_into_sweep(str(sweep_path), pulled)

    sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    assert sweep["generated_at"] == "2026-07-14T00:33:38+00:00"  # untouched
    assert len(sweep["results"]["award_repulls"]) == 1
    assert len(sweep["results"]["agency_docs"]) == 1
    assert (tmp_path / ".history" / "searches_testco.json").is_dir()

    rec = reconcile_figures(_content(), sweep)
    assert rec.clean, rec
    assert set(rec.backed) == {"$3.86M", "$4.45M", "$20B"}
    assert rec.attested == []   # real data, zero attestation


def test_partial_pull_refuses_to_write(monkeypatch, capsys):
    import agents.reports.board_content as bc
    monkeypatch.setattr(bc, "load_board_content", lambda _c: _content())

    def _flaky(url):
        raise TimeoutError("upstream")

    pulled = rp.repull_board_awards("Testco", fetch=_flaky, now=NOW)
    assert pulled["errors"] and not pulled["award_repulls"]


def test_merge_is_idempotent(monkeypatch, tmp_path):
    import agents.reports.board_content as bc
    monkeypatch.setattr(bc, "load_board_content", lambda _c: _content())
    pulled = rp.repull_board_awards("Testco", fetch=lambda _u: _PAYLOAD, now=NOW)
    sweep_path = tmp_path / "searches_testco.json"
    sweep_path.write_text(json.dumps(
        {"client": "Testco", "results": {}}), encoding="utf-8")
    rp.merge_into_sweep(str(sweep_path), pulled)
    once = sweep_path.read_text(encoding="utf-8")
    rp.merge_into_sweep(str(sweep_path), pulled)
    assert sweep_path.read_text(encoding="utf-8") == once
