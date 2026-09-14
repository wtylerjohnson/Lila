"""Joining pack notice records against the durable store. Offline, no fetch."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.notice_join import (  # noqa: E402
    LEVERAGE_RANK, dedupe_by_title_agency, enrich_from_store, is_solicitable,
    leverage_rank,
)
from agents.golden_press.records import GoldenRecord  # noqa: E402
from tools import notice_store as ns  # noqa: E402


def _rec(rid, **kw):
    base = dict(record_id=rid, lane="L1_notice", title="A notice",
                agency="DEPT OF X")
    base.update(kw)
    return GoldenRecord(**base)


@pytest.fixture()
def store(tmp_path):
    conn = ns.connect(tmp_path / "notices.db")
    rows = [("n1", "Sources Sought"), ("n2", "Award Notice"),
            ("n3", "Special Notice"), ("n4", "Combined Synopsis/Solicitation")]
    with conn:
        for nid, ntype in rows:
            conn.execute(
                "INSERT INTO notices (notice_id, title, notice_type, agency, "
                "office, deadline, set_aside, url, description_prefix, "
                "description_sha256, description_len, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, f"title {nid}", ntype, "DEPT OF X", "OFFICE-9",
                 "2026-09-01", "SDVOSB", f"https://sam.gov/opp/{nid}/view",
                 "", "", 0, "2026-07-27", "2026-07-28"))
    return conn


# ---- the ladder ------------------------------------------------------------ #
@pytest.mark.parametrize("ntype,rank", [
    ("Sources Sought", 1), ("Special Notice", 2), ("Presolicitation", 2),
    ("Combined Synopsis/Solicitation", 3), ("Solicitation", 3),
    ("Award Notice", 4), ("Justification", 4),
])
def test_leverage_rank_is_deterministic(ntype, rank):
    assert leverage_rank(ntype) == rank


def test_an_unknown_type_is_unranked_never_guessed():
    """An invented rank would sort an unrecognised type into a band on no
    evidence."""
    assert leverage_rank("Some New Notice Type") is None
    assert leverage_rank(None) is None
    assert leverage_rank("") is None


def test_award_notice_is_never_solicitable():
    """Rank 4 is history. The work is placed."""
    r = _rec("x")
    r.notice_leverage_rank = 4
    assert is_solicitable(r) is False
    for rank in (1, 2, 3):
        r.notice_leverage_rank = rank
        assert is_solicitable(r) is True


def test_unranked_is_not_solicitable_either():
    assert is_solicitable(_rec("x")) is False


# ---- the join -------------------------------------------------------------- #
def test_join_fills_type_rank_and_carried_fields(store):
    recs = [_rec("n1")]
    receipt = enrich_from_store(recs, conn=store)
    assert receipt["matched"] == 1 and receipt["unmatched"] == 0
    r = recs[0]
    assert r.notice_type == "Sources Sought"
    assert r.notice_leverage_rank == 1
    assert r.response_deadline == "2026-09-01"
    assert r.set_aside == "SDVOSB"
    assert r.office == "OFFICE-9"
    assert r.url == "https://sam.gov/opp/n1/view"


def test_a_miss_is_reported_with_a_reason(store):
    recs = [_rec("n1"), _rec("ghost")]
    receipt = enrich_from_store(recs, conn=store)
    assert receipt["matched"] == 1 and receipt["unmatched"] == 1
    assert "absent from the store" in receipt["misses"][0]["reason"]
    assert recs[1].notice_type is None          # untouched, not defaulted


def test_the_pack_own_values_are_never_overwritten(store):
    """The pack's deadline came from the sweep. A later snapshot does not get
    to rewrite it."""
    recs = [_rec("n1", response_deadline="2026-08-01", set_aside="8(a)")]
    enrich_from_store(recs, conn=store)
    assert recs[0].response_deadline == "2026-08-01"
    assert recs[0].set_aside == "8(a)"
    assert recs[0].notice_type == "Sources Sought"   # still gains the new field


def test_non_l1_records_are_left_alone(store):
    award = GoldenRecord(record_id="CONT1", lane="L2_entity_award", title="t")
    receipt = enrich_from_store([award], conn=store)
    assert receipt["l1_records"] == 0
    assert award.notice_type is None


def test_an_unreadable_store_changes_nothing(tmp_path, monkeypatch):
    """MIGRATION IS A NO-OP. Nothing here can fail a press."""
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "absent"))
    recs = [_rec("n1")]
    receipt = enrich_from_store(recs)
    assert recs[0].notice_type is None
    assert recs[0].notice_leverage_rank is None
    assert receipt["matched"] == 0


# ---- dedupe ---------------------------------------------------------------- #
def test_dedupe_collapses_one_notice_posted_many_times():
    """Undeduped, a single BAA reads as six opportunities."""
    recs = [_rec(f"id{i}", title="Broad Agency Announcement",
                 agency="DEPT OF X") for i in range(6)]
    from agents.golden_press.notice_bridge import source_transport
    for i, record in enumerate(recs):
        record.source_fields["notice_source_v1"] = source_transport(dict(source_id=record.record_id,
            solicitation="BAA-1", agency="DEPT OF X", office="Office", posted_date=f"2026-08-0{i+1}"))
    kept, dropped = dedupe_by_title_agency(recs)
    assert kept[0].record_id == "id5"
    assert len(kept) == 1 and len(dropped) == 5


def test_dedupe_keeps_latest_even_when_older_member_is_more_actionable():
    a = _rec("a", title="BAA", agency="X"); a.notice_leverage_rank = 4
    b = _rec("b", title="BAA", agency="X"); b.notice_leverage_rank = 1
    from agents.golden_press.notice_bridge import source_transport
    for record, posted in ((a, "2026-08-02"), (b, "2026-08-01")):
        record.source_fields["notice_source_v1"] = source_transport(dict(source_id=record.record_id,
            solicitation="BAA-1", agency="X", office="Office", posted_date=posted))
    kept, dropped = dedupe_by_title_agency([a, b])
    assert kept[0].record_id == "a", "current source chronology overrides actionability"


def test_dedupe_does_not_merge_different_agencies():
    a = _rec("a", title="Same Title", agency="ARMY")
    b = _rec("b", title="Same Title", agency="NAVY")
    kept, _ = dedupe_by_title_agency([a, b])
    assert len(kept) == 2


def test_dedupe_never_touches_award_or_forecast_lanes():
    awards = [GoldenRecord(record_id=f"c{i}", lane="L2_entity_award",
                           title="Same", agency="X") for i in range(3)]
    kept, dropped = dedupe_by_title_agency(awards)
    assert len(kept) == 3 and dropped == []


def test_every_ranked_type_in_the_table_is_reachable():
    assert set(LEVERAGE_RANK.values()) == {1, 2, 3, 4}
