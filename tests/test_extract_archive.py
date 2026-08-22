"""Rotated extracts are archived, never deleted.

A rotated file is the ONLY copy of that day that will ever exist: the S3
object is overwritten nightly, so sam.gov cannot serve last Tuesday. The
2026-07-24 extract rotated out before the notice store existed and its ids
can never enter it. That loss is not repeatable.
"""

from __future__ import annotations

import gzip
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.api import sam_extract as se  # noqa: E402


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    cache, archive = tmp_path / "cache", tmp_path / "archive"
    cache.mkdir()
    monkeypatch.setenv("LILA_SAM_EXTRACT_DIR", str(cache))
    monkeypatch.setenv("LILA_SAM_EXTRACT_ARCHIVE_DIR", str(archive))
    # These fixtures are tiny by design; the credibility floor (2026-07-30)
    # would otherwise hide them from _latest_path.
    monkeypatch.setenv("LILA_MIN_EXTRACT_BYTES", "10")
    return cache, archive


def _csv(cache, day, body="NoticeId,Title\nabc,A notice\n"):
    p = cache / f"opportunities_{day}.csv"
    p.write_text(body, encoding="utf-8")
    return p


def test_archiving_compresses_and_removes_the_csv(dirs):
    cache, archive = dirs
    src = _csv(cache, "2026-07-24")
    out = se.archive_extract(src)
    assert out.exists() and out.name == "opportunities_2026-07-24.csv.gz"
    assert not src.exists(), "the CSV goes only after the archive is written"


def test_the_archived_bytes_round_trip_exactly(dirs):
    cache, archive = dirs
    body = "NoticeId,Title\n" + "".join(f"n{i},Notice {i}\n" for i in range(500))
    src = _csv(cache, "2026-07-24", body)
    out = se.archive_extract(src)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert fh.read() == body


def test_archiving_is_idempotent(dirs):
    cache, archive = dirs
    src = _csv(cache, "2026-07-24")
    first = se.archive_extract(src)
    _csv(cache, "2026-07-24")                      # the day comes back somehow
    second = se.archive_extract(cache / "opportunities_2026-07-24.csv")
    assert first == second
    assert len(list(archive.glob("*.gz"))) == 1


def test_a_failed_archive_leaves_the_csv_alone(dirs):
    """Costing disk is acceptable. Costing the day is not. The source is
    unreadable here, which is the real shape of a mid-archive failure."""
    cache, archive = dirs
    src = _csv(cache, "2026-07-24")
    os.chmod(src, 0o000)
    try:
        with pytest.raises(OSError):
            se.archive_extract(src)
        assert src.exists(), "the source CSV must survive a failed archive"
    finally:
        os.chmod(src, 0o644)


def test_no_partial_file_is_left_behind_on_failure(dirs):
    cache, archive = dirs
    src = _csv(cache, "2026-07-24")
    os.chmod(src, 0o000)
    try:
        with pytest.raises(OSError):
            se.archive_extract(src)
        assert list(archive.glob("*.part")) == []
        assert list(archive.glob("*.gz")) == []
        assert src.exists()
    finally:
        os.chmod(src, 0o644)


def test_a_short_read_is_caught_by_byte_count(dirs, monkeypatch):
    """gzip writes a header even for empty input, so the output being
    non-empty proves nothing about what was copied. The byte count against
    the source size is what licenses deleting the source."""
    cache, archive = dirs
    src = _csv(cache, "2026-07-24", "NoticeId,Title\n" + "x" * 5000 + "\n")
    real_open = open

    def short_open(file, mode="r", *a, **k):
        fh = real_open(file, mode, *a, **k)
        if "b" in mode and str(file) == str(src):
            class _Short:
                def read(self, n=-1):
                    data = fh.read(n)
                    return data[:10] if data else b""   # loses bytes silently
                def __enter__(self): return self
                def __exit__(self, *x): fh.close(); return False
            return _Short()
        return fh

    monkeypatch.setattr("builtins.open", short_open)
    with pytest.raises(OSError, match="copied"):
        se.archive_extract(src)
    monkeypatch.undo()
    assert src.exists(), "a short read must never cost the source"
    assert list(archive.glob("*")) == []


def test_rotation_keeps_exactly_two_days_uncompressed(dirs, monkeypatch):
    """THE WORKING SET IS UNCHANGED. This is additive retention only."""
    cache, archive = dirs
    for day in ("2026-07-22", "2026-07-23", "2026-07-24", "2026-07-25"):
        _csv(cache, day)
    for old in sorted(cache.glob("opportunities_*.csv"))[:-2]:
        se.archive_extract(old)
    live = sorted(p.name for p in cache.glob("opportunities_*.csv"))
    assert live == ["opportunities_2026-07-24.csv", "opportunities_2026-07-25.csv"]
    archived = sorted(p.name for p in archive.glob("*.gz"))
    assert archived == ["opportunities_2026-07-22.csv.gz",
                        "opportunities_2026-07-23.csv.gz"]


def test_a_below_floor_file_is_discarded_not_archived(dirs):
    """THE ARCHIVE ADMITS NO POISON (2026-08-04). During the July-August
    outage, rotation gzipped a 685-byte header-only file into the permanent
    archive, where it claimed to be the only copy of a day that was never
    captured. Poison is discarded at the admission owner."""
    cache, archive = dirs
    src = _csv(cache, "2026-07-31", "NoticeId\n")   # 9 bytes, floor is 10
    out = se.archive_extract(src)
    assert out is None
    assert not src.exists(), "poison is discarded, not kept"
    assert list(archive.glob("*.gz")) == [], "and never enters the archive"


def test_latest_path_still_sees_only_the_working_set(dirs):
    cache, archive = dirs
    _csv(cache, "2026-07-27")
    _csv(cache, "2026-07-28")
    assert se._latest_path().name == "opportunities_2026-07-28.csv"


def test_the_archive_lives_outside_the_disposable_cache(dirs):
    cache, archive = dirs
    assert se._archive_dir() != se._cache_dir()


# ---- metered exposure ------------------------------------------------------ #
def test_the_attachment_stage_has_a_hard_call_cap():
    """Wall clock cannot bound SPEND: a fast network drains more quota in 120
    seconds than a slow one. The pool is 20/day and permanent."""
    import run_searches as rs
    assert isinstance(rs._SAM_ATTACHMENT_CALL_HARD_CAP, int)
    assert rs._SAM_ATTACHMENT_CALL_HARD_CAP <= 4


def test_one_press_fits_twice_inside_the_permanent_pool():
    """The whole point of the caps. If this fails, one press can drain a day
    and two presses collide."""
    import run_searches as rs
    from tools.api.sam_gov import SamGovSource
    from tools.api.contract_awards import MAX_SYNC_PAGES
    worst = (0                                    # extract path
             + SamGovSource.MAX_PASSES            # live fallback
             + rs._SAM_ATTACHMENT_CALL_HARD_CAP   # attachment enrichment
             + MAX_SYNC_PAGES                     # contract awards
             + 1)                                 # federal hierarchy
    assert worst <= 10, f"one press worst case is {worst}"
    assert worst * 2 <= 20, f"two presses would cost {worst * 2} of a 20 pool"


def test_nothing_proposes_a_sam_role():
    """The pool is permanent. Proposing a role sends the operator after
    something that does not exist."""
    import glob
    import os as _os
    bad = []
    for path in glob.glob("tools/**/*.py", recursive=True) + \
                glob.glob("agents/**/*.py", recursive=True) + \
                glob.glob("ui/*.py") + glob.glob("run_*.py"):
        text = open(path, encoding="utf-8", errors="replace").read()
        if "request a role" in text or "1,000/day with one" in text:
            bad.append(path)
    assert bad == [], f"these still propose a sam.gov role: {bad}"
