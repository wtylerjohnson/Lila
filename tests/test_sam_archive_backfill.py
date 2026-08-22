"""Gap recovery from the FY archive keeps per-day truth, and the floor holds.

INCIDENT 2026-07-29..2026-08-04: the nightly daily-extract export published a
header-only object for a week and the store froze at 2026-07-28. The recovery
path slices the weekly FY archive file (identical 47-column schema) by
DEPARTURE DAY and ingests each day under its own as_of. The rules pinned
here:

- a departure is bucketed on its ArchiveDate; an early-archived row whose
  stated ArchiveDate is elsewhere is recovered on its PostedDate;
- each recovered notice's first_seen/last_seen say the day it actually
  departed, never one lump fake day (tier B reads last_seen semantically);
- slices ingest with an explicit floor_bytes because their parent already
  passed the real floor; the DEFAULT floor is untouched and still refuses
  small raw files;
- a header-only parent can never seed slices.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import notice_store  # noqa: E402
from tools import sam_archive_backfill as ab  # noqa: E402

HEADER = ('"NoticeId","Title","Sol#","Department/Ind.Agency","Sub-Tier",'
          '"Office","PostedDate","Type","ArchiveDate","ResponseDeadLine",'
          '"NaicsCode","Active","Link","Description"')


def _row(nid: str, title: str, posted: str, archived: str,
         ntype: str = "Solicitation") -> str:
    return (f'"{nid}","{title}","SOL-{nid}","AGENCY X","SUB","OFF",'
            f'"{posted}","{ntype}","{archived}","","541512","No",'
            f'"https://sam.gov/workspace/contract/opp/{nid}/view",'
            f'"description of {title}"')


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    work = tmp_path / "sam_archive"
    monkeypatch.setenv("LILA_SAM_ARCHIVE_DIR", str(work))
    return work


def _fixture_archive(tmp_path) -> Path:
    """Six rows: three departing inside the window on their ArchiveDate, one
    early-archived row recoverable only via PostedDate, two out-of-window."""
    rows = [
        _row("a" * 32, "In-window departure one", "2026-07-10", "2026-07-29"),
        _row("b" * 32, "In-window departure two", "2026-07-15", "2026-07-31"),
        _row("c" * 32, "Same-day second departure", "2026-07-30", "2026-07-31"),
        # posted inside the gap, archived early with a future stamped date:
        # no daily ever showed it, so PostedDate is the recovery day
        _row("d" * 32, "Posted-in-gap future-archive", "2026-08-01", "2026-11-15"),
        _row("e" * 32, "Departed before the gap", "2026-06-01", "2026-07-20"),
        _row("f" * 32, "Departs after the window", "2026-07-01", "2026-09-09"),
    ]
    path = tmp_path / "FY2026_archived_opportunities.2026-08-02.csv"
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_slice_buckets_by_departure_day_with_posted_fallback(tmp_path, workdir):
    parent = _fixture_archive(tmp_path)
    slices = ab.slice_departures(parent, date(2026, 7, 29), date(2026, 8, 4),
                                 workdir / "slices")
    assert sorted(slices) == ["2026-07-29", "2026-07-31", "2026-08-01"]
    day_31 = slices["2026-07-31"].read_text(encoding="utf-8")
    assert "b" * 32 in day_31 and "c" * 32 in day_31
    assert "d" * 32 in slices["2026-08-01"].read_text(encoding="utf-8")
    for out in slices.values():
        text = out.read_text(encoding="utf-8")
        assert "e" * 32 not in text and "f" * 32 not in text
        # the original header rides every slice (csv-normalized quoting)
        assert text.lstrip('"').startswith("NoticeId")


def test_backfill_ingests_each_day_under_its_own_as_of(tmp_path, workdir):
    parent = _fixture_archive(tmp_path)
    results = ab.backfill_window(date(2026, 7, 29), date(2026, 8, 4),
                                 fys=[], local_files=[parent])
    assert [r["ingest_date"] for r in results] == [
        "2026-07-29", "2026-07-31", "2026-08-01"]   # oldest first
    conn = notice_store.connect()
    try:
        seen = {r["notice_id"]: (r["first_seen"], r["last_seen"], r["active"])
                for r in conn.execute(
                    "SELECT notice_id, first_seen, last_seen, active "
                    "FROM notices")}
        # per-day truth: the day it departed, not one lump ingest day
        assert seen["a" * 32][:2] == ("2026-07-29", "2026-07-29")
        assert seen["b" * 32][:2] == ("2026-07-31", "2026-07-31")
        assert seen["d" * 32][:2] == ("2026-08-01", "2026-08-01")
        assert all(v[2] == "No" for v in seen.values())  # archived state kept
        days = [r["ingest_date"] for r in conn.execute(
            "SELECT ingest_date FROM ingests ORDER BY ingest_date")]
        assert days == ["2026-07-29", "2026-07-31", "2026-08-01"]
        named = [r["extract_file"] for r in conn.execute(
            "SELECT extract_file FROM ingests")]
        assert all("slice_" in n for n in named)  # provenance in the series
    finally:
        conn.close()


def test_dry_run_slices_but_never_touches_the_store(tmp_path, workdir):
    parent = _fixture_archive(tmp_path)
    out = ab.backfill_window(date(2026, 7, 29), date(2026, 8, 4),
                             fys=[], local_files=[parent], dry_run=True)
    assert out == []
    assert not (Path(os.environ["LILA_NOTICE_STORE_DIR"]) / "notices.db").exists()


def test_default_floor_still_refuses_small_raw_extracts(tmp_path, monkeypatch):
    """The floor_bytes kwarg must not weaken the default gate: a small raw
    file with no override is refused exactly as before."""
    monkeypatch.setenv("LILA_MIN_EXTRACT_BYTES", "200")
    small = tmp_path / "opportunities_2026-08-04.csv"
    small.write_text(HEADER + "\n", encoding="utf-8")
    result = notice_store.ingest(str(small))
    assert result.get("error") == "extract_truncated"
    # and the explicit override admits the same bytes for the slice path
    ok = notice_store.ingest(str(small), as_of="2026-08-04", floor_bytes=0)
    assert "error" not in ok


def test_a_stub_parent_can_never_seed_slices(tmp_path, workdir, monkeypatch):
    monkeypatch.setenv("LILA_MIN_EXTRACT_BYTES", "200")
    stub = tmp_path / "FY2026_archived_opportunities.stub.csv"
    stub.write_text(HEADER + "\n", encoding="utf-8")
    with pytest.raises(ab.ArchiveRefused) as err:
        ab.backfill_window(date(2026, 7, 29), date(2026, 8, 4),
                           fys=[], local_files=[stub])
    assert "below the 200-byte floor" in str(err.value)


def test_fiscal_year_rolls_at_october():
    assert ab.fiscal_year_of(date(2026, 8, 4)) == 2026
    assert ab.fiscal_year_of(date(2026, 10, 1)) == 2027


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
