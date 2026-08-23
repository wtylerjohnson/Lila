"""Frame re-screen doctrine tests (work order A1, 2026-08-17).

Offline doctrine: in-memory sqlite store, temp forecast dir, no network,
no LLM, explicit frozen dates (frozen-clock doctrine 2026-08-03: every
comparison date is passed, never sampled from the wall clock).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from tools import frame_rescreen as fr

TODAY = date(2026, 8, 17)

_COLS = ("notice_id", "title", "notice_type", "agency", "subtier", "office",
         "posted", "deadline", "naics", "psc", "set_aside", "url",
         "description_prefix", "last_seen",
         # A4 (2026-08-18): the store has carried the extract's POC columns
         # since 2026-07-28, and every kept notice now stores them.
         "poc_title", "poc_name", "poc_email", "poc_phone",
         "poc_secondary_name", "poc_secondary_email", "poc_secondary_phone")


def _store(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE notices (" + ", ".join(f"{c} TEXT" for c in _COLS) + ")")
    conn.executemany(
        "INSERT INTO notices VALUES (" + ",".join("?" * len(_COLS)) + ")",
        rows)
    return conn


def _row(notice_id, title, desc, *, ntype="Solicitation", naics="541519",
         psc="", deadline="2026-09-01", agency="GSA"):
    return (notice_id, title, ntype, agency, "", "", "2026-08-01", deadline,
            naics, psc, "", "", desc, "2026-08-17",
            "Contract Specialist", "Pat Fixture", "pat.fixture@agency.gov",
            "555-0100", "Sam Backup", "sam.backup@agency.gov", "555-0101")


@pytest.fixture()
def frame():
    return fr.load_frame("varonis")


def _screen(frame, rows):
    keeps, receipt = fr.rescreen_store(
        frame, conn=_store(rows), today=TODAY)
    return keeps, receipt


def test_tier1_name_alone_qualifies_even_off_code(frame):
    # A distinctive brand name is tier 1 on the operator ladder: it needs no
    # tech code and no anchor (term_tiers doctrine), so a Varonis award in an
    # off-code lane still enters the evidence set.
    keeps, _ = _screen(frame, [
        _row("t1", "Software renewal", "Renewal of Varonis DatAdvantage "
             "licenses for the enterprise.", ntype="Award Notice",
             naics="999999", deadline="")])
    assert len(keeps) == 1
    assert keeps[0]["best_tier"] == 1
    assert keeps[0]["leverage_rank"] == 4


def test_rank4_award_is_evidence_never_a_printed_opportunity(frame):
    keeps, _ = _screen(frame, [
        _row("t2", "Varonis renewal", "Varonis DatAdvantage renewal.",
             ntype="Award Notice", deadline="")])
    assert keeps and fr.sort_solicitable(keeps, TODAY) == []


def test_tier2_phrase_requires_tech_code(frame):
    # "DLP" on a non-tech record is the projector-lamp collision; the ladder
    # kills it by requiring a tech NAICS/PSC for tier 2/3 language.
    keeps, _ = _screen(frame, [
        _row("t3", "DLP projector lamps",
             "Replacement DLP projector lamps for the auditorium.",
             naics="339999"),
        _row("t4", "Data protection tools",
             "The agency requires data loss prevention licenses.",
             naics="541519")])
    ids = {k["notice_id"] for k in keeps}
    assert ids == {"t4"}


def test_tier3_only_match_never_keeps(frame):
    # Operator keep rule: tier 3 is context, never sufficient alone.
    keeps, receipt = _screen(frame, [
        _row("t5", "Zero Trust Architecture Support",
             "Zero trust engineering services for the enterprise.")])
    assert keeps == []
    assert receipt["tier3_only_dropped"] == 1


def test_bare_purview_idiom_is_measured_never_kept(frame):
    keeps, receipt = _screen(frame, [
        _row("t6", "Oversight matters",
             "These functions fall under the purview of the Office of the "
             "CIO for information technology.")])
    assert keeps == []
    assert receipt["purview_measurement"].get("idiom") == 1
    assert receipt["purview_measurement"]["samples"][0]["notice_id"] == "t6"


def test_matched_sentence_is_the_receipt(frame):
    keeps, _ = _screen(frame, [
        _row("t7", "Enterprise services",
             "First sentence about staffing. The contractor shall deploy "
             "sensitivity labels across the tenant. Final sentence.")])
    assert len(keeps) == 1
    s = keeps[0]["matched_sentence"]
    assert s == ("The contractor shall deploy sensitivity labels across "
                 "the tenant.")


def test_title_match_is_labeled(frame):
    keeps, _ = _screen(frame, [
        _row("t8", "Insider threat monitoring support", "Details follow.")])
    assert keeps[0]["matched_sentence"].startswith("TITLE: ")


def test_leverage_orders_live_windows_then_rank(frame):
    keeps, _ = _screen(frame, [
        _row("t9", "Data loss prevention services", "data loss prevention.",
             ntype="Solicitation", deadline="2026-09-01"),
        _row("t10", "Insider threat program sources sought",
             "insider threat program market research.",
             ntype="Sources Sought", deadline="2026-09-15"),
        _row("t11", "Expired data loss prevention", "data loss prevention.",
             ntype="Sources Sought", deadline="2026-01-01")])
    ordered = [k["notice_id"] for k in fr.sort_solicitable(keeps, TODAY)]
    # live first; within live, rank 1 Sources Sought beats rank 3
    # Solicitation; the dead window sorts last regardless of its rank.
    assert ordered == ["t10", "t9", "t11"]


def test_family_dedupe_collapses_same_title_agency(frame):
    keeps, receipt = _screen(frame, [
        _row("t12", "Data loss prevention BPA", "data loss prevention.",
             ntype="Solicitation"),
        _row("t13", "Data loss prevention BPA", "data loss prevention.",
             ntype="Sources Sought")])
    assert len(keeps) == 1
    assert receipt["deduped_away"] == 1
    # the survivor is the most shapeable member (leverage rank 1)
    assert keeps[0]["leverage_rank"] == 1


def test_forecast_screen_keep_rule_and_labels(frame, tmp_path):
    store = {
        "F1": {"title": "Insider threat detection tools", "description": "",
               "agency": "DHS", "component": "CISA", "naics_code": "541519",
               "psc": "", "source_id": "F1", "url": "https://example.gov/f1",
               "anticipated_solicitation": "2026-10-01"},
        "F2": {"title": "Zero trust advisory", "description": "",
               "agency": "DHS", "component": "", "naics_code": "541519",
               "psc": "", "source_id": "F2", "url": "https://example.gov/f2",
               "anticipated_solicitation": "2026-09-01"},
        "F3": {"title": "Janitorial services", "description": "",
               "agency": "DHS", "component": "", "naics_code": "561720",
               "psc": "", "source_id": "F3", "url": "https://example.gov/f3",
               "anticipated_solicitation": "2026-09-15"},
    }
    (tmp_path / "test_source.json").write_text(json.dumps(store),
                                               encoding="utf-8")
    keeps, receipt = fr.rescreen_forecasts(frame, store_dir=str(tmp_path),
                                           today=TODAY)
    assert [k["source_id"] for k in keeps] == ["F1"]
    assert keeps[0]["evidence_tier"].startswith("PROGRAM")
    assert keeps[0]["window_state"] == "live"
    assert receipt["sources"]["test_source"] == {"records": 3, "kept": 1}
    assert receipt["tier3_only_dropped"] == 1
    assert receipt["press_impact_live_window_keeps"] == 1


def test_decay_reclassifies_past_windows_and_prices_press_impact(
        frame, tmp_path):
    # Item 2 (operator review, 2026-08-18): a forecast row is an agency's
    # promise dated once. Past-window rows are reclassified evidence, never
    # dropped and never candidate opportunities; unstated windows are their
    # own class. Item 3: press impact = live-window keeps, the number that
    # changes the deliverable.
    store = {
        "P1": {"title": "Insider threat monitoring recompete",
               "description": "", "agency": "GSA", "component": "",
               "naics_code": "541519", "psc": "", "source_id": "P1",
               "url": "https://example.gov/p1",
               "anticipated_solicitation": "2026-01-15"},
        "P2": {"title": "Insider threat analytics platform",
               "description": "", "agency": "GSA", "component": "",
               "naics_code": "541519", "psc": "", "source_id": "P2",
               "url": "https://example.gov/p2",
               "anticipated_solicitation": "2026-10"},
        "P3": {"title": "Insider threat advisory services",
               "description": "", "agency": "GSA", "component": "",
               "naics_code": "541519", "psc": "", "source_id": "P3",
               "url": "https://example.gov/p3",
               "anticipated_solicitation": None},
    }
    (tmp_path / "decay_source.json").write_text(json.dumps(store),
                                                encoding="utf-8")
    keeps, receipt = fr.rescreen_forecasts(frame, store_dir=str(tmp_path),
                                           today=TODAY)
    by_id = {k["source_id"]: k for k in keeps}
    assert by_id["P1"]["window_state"] == "stated_past"
    assert by_id["P1"]["decay_note"] == fr.DECAY_NOTE
    assert by_id["P2"]["window_state"] == "live"        # yyyy-mm stamp
    assert by_id["P3"]["window_state"] == "unstated"
    # ordering: live first, unstated after fy_only, stated-past last
    assert [k["source_id"] for k in keeps] == ["P2", "P3", "P1"]
    assert receipt["kept"] == 3
    assert receipt["press_impact_live_window_keeps"] == 1
    assert receipt["kept_window_unstated"] == 1
    assert receipt["kept_stated_past"] == 1


def test_fy_only_splits_unstated_and_sorts_by_fiscal_year(frame, tmp_path):
    # Operator follow-up (2026-08-18): unstated cannot sit as a flat
    # majority bucket. A row with a stated fiscal year is fy_only, dated
    # to that federal FY's end (September 30) and sorted by it; a wholly
    # past FY is stated-past evidence; only a row with nothing stays
    # unstated and sorts after fy_only.
    def _row(sid, fy):
        return {"title": "Insider threat monitoring line",
                "description": "", "agency": "GSA", "component": "",
                "naics_code": "541519", "psc": "", "source_id": sid,
                "url": f"https://example.gov/{sid}",
                "anticipated_solicitation": None,
                "fiscal_year": fy}
    store = {
        "F27": _row("F27", "2027"),
        "F26": _row("F26", "2026"),     # FY26 ends 2026-09-30: still ahead
        "F25": _row("F25", "2025"),     # wholly past FY
        "FNONE": _row("FNONE", None),
        "FJUNK": _row("FJUNK", "TBD"),
    }
    (tmp_path / "fy_source.json").write_text(json.dumps(store),
                                             encoding="utf-8")
    keeps, receipt = fr.rescreen_forecasts(frame, store_dir=str(tmp_path),
                                           today=TODAY)
    by_id = {k["source_id"]: k for k in keeps}
    assert by_id["F26"]["window_state"] == "fy_only"
    assert by_id["F26"]["fy"] == 2026
    assert by_id["F27"]["window_state"] == "fy_only"
    assert by_id["F25"]["window_state"] == "stated_past"
    assert by_id["F25"]["decay_note"] == fr.DECAY_NOTE
    assert by_id["FNONE"]["window_state"] == "unstated"
    assert by_id["FJUNK"]["window_state"] == "unstated"
    # fy_only sorts by fiscal year, then unstated, then stated-past
    assert [k["source_id"] for k in keeps] == [
        "F26", "F27", "FJUNK", "FNONE", "F25"]  # unstated ties break by id
    assert receipt["kept_fy_only"] == 2
    assert receipt["kept_window_unstated"] == 2
    assert receipt["kept_stated_past"] == 1
    assert receipt["press_impact_live_window_keeps"] == 0


def test_artifact_and_vocabulary_shape(frame):
    vocab = fr.frame_vocabulary(frame)
    # The operator's tier map: every tier 2 term resolves to 2, tier 3 to 3,
    # and entity names never ride the capability list (the SteelHead lesson).
    assert vocab["tier_by_term"]["data loss prevention"] == 2
    assert vocab["tier_by_term"]["zero trust"] == 3
    assert "Microsoft Purview" in vocab[
        "capability_terms_stripped_to_entity_route"]
    assert "Microsoft Purview" not in vocab["capability_terms"]
    assert vocab["tier_by_term"]["varonis"] == 1


def test_kept_notice_stores_its_points_of_contact(frame):
    # A4 (operator order, 2026-08-18): POCs render on the lead and are
    # stored with every kept notice, straight from the extract columns.
    keeps, _ = _screen(frame, [_row(
        "n-poc", "Insider threat monitoring tool", "insider threat program")])
    assert keeps, "fixture row must be kept for the POC assertion to bind"
    poc = keeps[0]["poc"]
    assert poc["primary_name"] == "Pat Fixture"
    assert poc["primary_email"] == "pat.fixture@agency.gov"
    assert poc["primary_phone"] == "555-0100"
    assert poc["secondary_email"] == "sam.backup@agency.gov"
