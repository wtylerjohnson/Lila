"""Recover a daily-extract gap from sam.gov's FY archive file. Zero LLM.

WHY THIS EXISTS (incident 2026-07-29..2026-08-04). The nightly
ContractOpportunitiesFullCSV.csv export published a header-only 685-byte
object for a week straight. The floor refused every empty day (correctly), so
the notice store froze at 2026-07-28 while ~6.4% of active notices vanish
every three days. Diagnosis on 2026-08-04 established that the URL, naming,
and location did NOT change: the Data Services tree, the FSD knowledge base,
data.gov, and sam.gov's own mediated download endpoint all still name that
key, no date-stamped daily snapshot exists anywhere on falextracts, and prior
S3 object versions are not anonymously readable. The missed DAYS are gone.

THE DEPARTURES ARE NOT. Contract Opportunities/Archived Data/
FY{n}_archived_opportunities.csv is rebuilt weekly (observed Sun 2026-08-02
14:51 UTC) with the IDENTICAL 47-column schema as the daily extract, complete
Description text included. Every notice that left the active universe during
the gap is in there with its final state. This tool downloads that file
(floor-validated like any extract), slices out the rows whose departure falls
inside the gap window, and folds each day's slice into the notice store under
its own as_of day.

PER-DAY INGEST IS THE POINT, NOT A FLOURISH. The store's consumers read
last_seen semantically: tier B keeps an undated industry day only while the
notice's last_seen equals the store's newest (industry_days.collect). One
lump ingest under a single fake day would stamp a week of departures as
"present on that day" and make archived rooms read as live ones. Bucketing by
departure day keeps first_seen and last_seen meaning what they say, and the
ingests series records one honestly-small, clearly-named slice per day.

WHAT THIS CANNOT RECOVER, stated so nobody re-derives it: notices posted
during the gap that are STILL ACTIVE (the first healed daily pull delivers
them whole), and departures after the newest FY build (re-run after the next
Sunday build). A row archived early with a future-fiscal-year ArchiveDate
lives only in that future FY file; pass --fy more than once to sweep those.
"""

from __future__ import annotations

import csv
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import httpx

from tools.api.sam_extract import _norm_header
from tools.notice_store import connect, ingest, last_ingest, min_credible_extract_bytes

ARCHIVE_URL_TEMPLATE = os.environ.get(
    "LILA_SAM_ARCHIVE_URL_TEMPLATE",
    "https://falextracts.s3.amazonaws.com/Contract%20Opportunities/"
    "Archived%20Data/FY{fy}_archived_opportunities.csv",
)
_DEFAULT_WORKDIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "sam_archive"


class ArchiveRefused(RuntimeError):
    """The FY archive payload is not credible (header-only stub or partial).
    Raised INSTEAD of slicing, so a broken parent can never seed slices."""


def _workdir() -> Path:
    return Path(os.environ.get("LILA_SAM_ARCHIVE_DIR", str(_DEFAULT_WORKDIR)))


def _log(msg: str) -> None:
    print(f"[sam-archive] {msg}", file=sys.stderr, flush=True)


def fiscal_year_of(day: date) -> int:
    """Federal fiscal year: Oct 1 rolls into the next FY."""
    return day.year + (1 if day.month >= 10 else 0)


def fetch_fy_archive(fy: int, *, url: Optional[str] = None) -> Path:
    """Stream one FY archive file down, floor-validated, never re-fetched
    for the same upstream build (the Last-Modified date is in the name)."""
    src = url or ARCHIVE_URL_TEMPLATE.format(fy=fy)
    stamp = date.today().isoformat()
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True,
                          transport=httpx.HTTPTransport(
                              local_address="0.0.0.0")) as c:
            head = c.head(src)
            head.raise_for_status()
            lm = head.headers.get("last-modified", "")
            if lm:
                from email.utils import parsedate_to_datetime
                stamp = parsedate_to_datetime(lm).date().isoformat()
    except Exception:  # noqa: BLE001 - the GET below is the real gate
        pass
    _workdir().mkdir(parents=True, exist_ok=True)
    target = _workdir() / f"FY{fy}_archived_opportunities.{stamp}.csv"
    floor = min_credible_extract_bytes()
    if target.exists() and target.stat().st_size >= floor:
        _log(f"reusing {target.name} ({target.stat().st_size:,} bytes)")
        return target
    tmp = target.with_suffix(".part")
    _log(f"downloading FY{fy} archive (large file) ...")
    t0 = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(30.0, read=600.0),
                      transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                      follow_redirects=True) as c:
        with c.stream("GET", src) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
    size = tmp.stat().st_size
    if size < floor:
        tmp.unlink(missing_ok=True)
        raise ArchiveRefused(
            f"FY{fy} archive payload is {size:,} bytes, below the "
            f"{floor:,}-byte floor; refusing to slice it")
    tmp.rename(target)
    _log(f"{size/1e6:.0f} MB in {time.monotonic()-t0:.0f}s -> {target.name}")
    return target


def slice_departures(path: Path, start: date, end: date,
                     out_dir: Path) -> dict[str, Path]:
    """Write one raw-row CSV per departure day inside [start, end].

    A row is admitted when its ArchiveDate falls in the window (it departed
    then), or - for early-archived rows whose stated ArchiveDate is elsewhere -
    when its PostedDate falls in the window (it entered during the gap and is
    already inactive, so no daily ever showed it). The bucket day is the
    departure day when in-window, else the posted day. Rows pass through the
    csv module content-unchanged under the original header (quoting is
    normalized, values are not), so ingest parses exactly what GSA wrote.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    lo, hi = start.isoformat(), end.isoformat()
    writers: dict[str, tuple] = {}
    kept = scanned = 0
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            raise ArchiveRefused(f"{path.name} has no header row")
        norm = [_norm_header(h) for h in header]
        try:
            i_arch = norm.index("archivedate")
            i_posted = norm.index("posteddate")
        except ValueError as exc:
            raise ArchiveRefused(
                f"{path.name} is missing ArchiveDate/PostedDate: {exc}")
        for row in reader:
            scanned += 1
            if not row:
                continue
            arch = (row[i_arch].strip()[:10] if i_arch < len(row) else "")
            posted = (row[i_posted].strip()[:10] if i_posted < len(row) else "")
            if lo <= arch <= hi:
                day = arch
            elif lo <= posted <= hi:
                day = posted
            else:
                continue
            if day not in writers:
                out = out_dir / f"{path.stem}.slice_{day}.csv"
                handle = open(out, "w", encoding="utf-8", newline="")
                w = csv.writer(handle)
                w.writerow(header)
                writers[day] = (out, handle, w)
            writers[day][2].writerow(row)
            kept += 1
    for _, handle, _w in writers.values():
        handle.close()
    _log(f"{path.name}: scanned {scanned:,} archived rows, "
         f"kept {kept:,} departing {lo}..{hi} across {len(writers)} day(s)")
    return {day: out for day, (out, _h, _w) in sorted(writers.items())}


def backfill_window(start: date, end: date, *, fys: Iterable[int],
                    local_files: Iterable[Path] = (),
                    dry_run: bool = False) -> list[dict]:
    """Fetch (or reuse) FY archives, slice the window, ingest oldest-first.

    Slice files are far below the daily-extract floor BY CONSTRUCTION, so
    their ingest passes floor_bytes=0: the credibility question was answered
    on the floor-validated parent, and a slice of a credible parent is not a
    header-only stub. Raw daily extracts never take this path.
    """
    parents = [Path(p) for p in local_files]
    floor = min_credible_extract_bytes()
    for parent in parents:
        size = parent.stat().st_size
        if size < floor:
            raise ArchiveRefused(
                f"{parent.name} is {size:,} bytes, below the {floor:,}-byte "
                f"floor; a stub parent can never seed slices")
    for fy in fys:
        parents.append(fetch_fy_archive(fy))
    slices: dict[str, Path] = {}
    for parent in parents:
        for day, path in slice_departures(
                parent, start, end, _workdir() / "slices").items():
            if day in slices:
                _log(f"{day}: slices from two parents; keeping both rows "
                     f"via sequential ingest")
                merged = slices[day].read_text(encoding="utf-8")
                extra = path.read_text(encoding="utf-8").splitlines(True)[1:]
                with open(slices[day], "a", encoding="utf-8", newline="") as f:
                    f.writelines(extra)
                del merged
            else:
                slices[day] = path
    if not slices:
        _log("nothing to recover in the window; the store is unchanged")
        return []
    if dry_run:
        for day, path in sorted(slices.items()):
            rows = max(0, sum(1 for _ in open(path, encoding="utf-8")) - 1)
            _log(f"DRY RUN {day}: {rows:,} rows in {path.name}")
        return []
    results = []
    conn = connect()
    try:
        for day, path in sorted(slices.items()):  # oldest first: first_seen
            results.append(ingest(str(path), conn=conn, as_of=day,
                                  floor_bytes=0))
            # A slice day's gone_since_prev measures store rows the SLICE did
            # not refresh, never closures: the 07-29 recovery row recorded
            # 31,927 "gone" against a 1,264-row slice, and the trend rule
            # cannot flag the earliest rows (insufficient history). The tool
            # owns the honesty of the series rows it creates, so every slice
            # day is stamped suspect with the reason spelled out.
            conn.execute(
                "UPDATE ingests SET suspect = 1, suspect_reason = ? "
                "WHERE ingest_date = ?",
                (f"gap-recovery slice ({path.name}); gone_since_prev "
                 f"measures store rows the slice did not refresh, "
                 f"never closures", day))
            conn.commit()
    finally:
        conn.close()
    return results


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Backfill notice-store gap days from FY archive files.")
    ap.add_argument("--window", default=None,
                    help="START:END inclusive ISO dates; default is the day "
                         "after the last ingest through today")
    ap.add_argument("--fy", type=int, action="append", default=None,
                    help="fiscal year archive(s) to sweep; default derives "
                         "from the window end; repeatable")
    ap.add_argument("--file", action="append", default=[],
                    help="already-downloaded FY archive CSV; skips fetching")
    ap.add_argument("--dry-run", action="store_true",
                    help="slice and report; do not touch the store")
    args = ap.parse_args(argv)

    if args.window:
        lo, _, hi = args.window.partition(":")
        start, end = date.fromisoformat(lo), date.fromisoformat(hi or lo)
    else:
        last = last_ingest()
        if not last:
            _log("store has no ingests; a gap window cannot be derived. "
                 "Pass --window START:END explicitly.")
            return 2
        start = date.fromisoformat(last["ingest_date"][:10])
        start = date.fromordinal(start.toordinal() + 1)
        end = date.today()
    if start > end:
        _log(f"window {start}..{end} is empty; nothing to do")
        return 0
    fys = args.fy or [fiscal_year_of(end)]
    results = backfill_window(start, end, fys=fys,
                              local_files=[Path(p) for p in args.file],
                              dry_run=args.dry_run)
    for r in results:
        _log(f"{r['ingest_date']}: {r['rows_in_file']:,} rows, "
             f"{r['new']:,} new, {r['revised']:,} revised "
             f"(store now {r['store_total']:,})")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
