"""Durable notice store: what sam.gov showed us, kept after sam.gov drops it.

WHY THIS EXISTS. The daily Contract Opportunities extract is a snapshot of
what is ACTIVE, and download_extract() keeps only the newest two days. A
notice posted and closed between two looks was never seen and never will be.
Today the notice history is whatever happened to be on disk when a press ran.
This store is the memory: the morning job feeds it, and client lanes read it
instead of the raw extract, at which point the two-day retention stops
mattering.

SQLITE, NOT JSONL. Measured on the 07-28 extract: an ingest touches 78,553
rows of which ~1,729 are new and the rest need only a last_seen bump. JSONL
cannot update in place, so recording ~1,750 real changes would mean rewriting
the whole file, which is 2.5 GB a morning at twelve months. SQLite updates
only changed pages and indexes the questions we actually ask ("every RFI and
sources sought under these NAICS in the last twelve months") instead of
scanning 710K rows. It is still one file, no service, no daemon.

DESCRIPTION: COMPLETE TEXT PLUS A SHA256. The 2026-07-28 store kept a
4,000-character prefix (53.1% of description bytes; 10,570 notices truncated
irreversibly). The operator's "we preserve data detail" ruling (2026-07-30)
raised the cap to 40,000, above GSA's own ~32K truncation of the extract
column, so the stored text IS the complete text GSA serves and the days that
had already been truncated were restored from the compressed archives. The
hash still detects an amendment that rewrote a description, and still guards
the one case left: text longer than the cap, which today only GSA's side
can produce.

FAILURE IS NOT ALLOWED TO PROPAGATE. The merge runs in one transaction. A
failed ingest rolls back and leaves yesterday's store intact and readable,
and every reader can see last_ingest, so a stale store is visible rather than
silent. Nothing here can fail a press.

NOT WIRED YET: recompete history. Disappearance is recorded, but it is not a
signal anyone consumes until the sampling series says what the real closure
rate is. See sampling_log().
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

_MISSING = object()

# Positions derived from _FIELDS, never hardcoded. A literal index here broke
# once already: inserting three contact columns ahead of the hash moved
# description_sha256 from 15 to 18, and the revision check silently began
# comparing a stored hash against a secondary contact name, reporting 66,411
# spurious revisions on a backfill that should report zero.
def _field_index(name: str) -> int:
    return _FIELDS.index(name)

_DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "state" / "notice_store"

# Operator-set 2026-07-28 at 4,000; raised to 40,000 on the operator's
# "we preserve data detail" ruling (2026-07-30). GSA itself truncates the
# extract's Description at ~32K (859 stored notices sit at exactly
# description_len=32,003), so at 40,000 the "prefix" IS the complete text
# GSA serves and nothing is dropped on our side of the wire. The name keeps
# its honesty for the one case that remains: if GSA ever serves more than
# 40,000 chars, we keep a prefix and the sha256 says so.
DESCRIPTION_PREFIX_CHARS = 40_000

# A real daily extract is ~230 MB. 50 MB is far below any credible file and
# far above a header-only stub, so it separates "GSA is mid-rewrite" from
# "quiet federal day" without ever rejecting a real one.
def min_credible_extract_bytes() -> int:
    """The floor below which an extract is refused rather than ingested.

    Overridable so hermetic tests, which build 200-byte fixtures, can run at
    all. Production never sets it: the default is what protects the store.
    """
    try:
        return int(os.environ.get("LILA_MIN_EXTRACT_BYTES", "50000000"))
    except ValueError:
        return 50_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    notice_id           TEXT PRIMARY KEY,
    title               TEXT,
    notice_type         TEXT,
    agency              TEXT,
    subtier             TEXT,
    office              TEXT,
    posted              TEXT,
    deadline            TEXT,
    naics               TEXT,
    psc                 TEXT,
    set_aside           TEXT,
    url                 TEXT,
    poc_name            TEXT,
    poc_email           TEXT,
    poc_phone           TEXT,
    poc_secondary_email TEXT,
    poc_secondary_name  TEXT,
    description_prefix  TEXT,
    description_sha256  TEXT,
    description_len     INTEGER,
    -- Columns the daily extract has always carried and the store discarded
    -- until 2026-07-28. STORAGE ONLY: nothing derives from these yet, they
    -- feed no churn logic, no leverage rank, no posture. Kept raw, exactly as
    -- the extract wrote them, so a later reader parses them rather than
    -- inheriting a parse we guessed at today.
    awardee             TEXT,
    award_number        TEXT,
    award_date          TEXT,
    award_dollars       TEXT,
    archive_type        TEXT,
    archive_date        TEXT,
    base_type           TEXT,
    sol_number          TEXT,
    -- Full-fidelity columns (2026-07-30, "we preserve data detail"): every
    -- remaining extract column, kept raw like the award block above. Place
    -- of performance is a sales-territory filter that could never be
    -- reconstructed once dropped; the rest cost bytes today so a backfill
    -- is never needed tomorrow.
    set_aside_code      TEXT,
    active              TEXT,
    poc_title           TEXT,
    poc_fax             TEXT,
    poc_secondary_title TEXT,
    poc_secondary_phone TEXT,
    poc_secondary_fax   TEXT,
    pop_street          TEXT,
    pop_city            TEXT,
    pop_state           TEXT,
    pop_zip             TEXT,
    pop_country         TEXT,
    cgac                TEXT,
    fpds_code           TEXT,
    aac_code            TEXT,
    organization_type   TEXT,
    office_state        TEXT,
    office_city         TEXT,
    office_zip          TEXT,
    office_country      TEXT,
    info_link           TEXT,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    seen_count          INTEGER NOT NULL DEFAULT 1,
    revisions           INTEGER NOT NULL DEFAULT 0
);
"""

INDEX_SCHEMA = """
-- the questions this store exists to answer
CREATE INDEX IF NOT EXISTS ix_notices_type_posted  ON notices(notice_type, posted);
CREATE INDEX IF NOT EXISTS ix_notices_naics_posted ON notices(naics, posted);
CREATE INDEX IF NOT EXISTS ix_notices_last_seen    ON notices(last_seen);
CREATE INDEX IF NOT EXISTS ix_notices_agency       ON notices(agency);

CREATE TABLE IF NOT EXISTS ingests (
    ingest_date   TEXT PRIMARY KEY,
    extract_file  TEXT,
    rows_in_file  INTEGER,
    rows_new      INTEGER,
    rows_updated  INTEGER,
    rows_revised  INTEGER,
    gone_since_prev INTEGER,
    prev_date     TEXT,
    seconds       REAL,
    suspect       INTEGER NOT NULL DEFAULT 0,
    suspect_reason TEXT,
    finished_at   TEXT
);
"""

_FIELDS = ("notice_id", "title", "notice_type", "agency", "subtier", "office",
           "posted", "deadline", "naics", "psc", "set_aside", "url",
           "poc_name", "poc_email", "poc_phone", "poc_secondary_email",
           "poc_secondary_name", "description_prefix",
           "description_sha256", "description_len",
           "awardee", "award_number", "award_date", "award_dollars",
           "archive_type", "archive_date", "base_type", "sol_number",
           # full-fidelity block (2026-07-30); appended so earlier positions
           # never move (see _field_index's war story above)
           "set_aside_code", "active", "poc_title", "poc_fax",
           "poc_secondary_title", "poc_secondary_phone", "poc_secondary_fax",
           "pop_street", "pop_city", "pop_state", "pop_zip", "pop_country",
           "cgac", "fpds_code", "aac_code", "organization_type",
           "office_state", "office_city", "office_zip", "office_country",
           "info_link")

# The full-fidelity columns share one extract-key-equals-store-key shape, so
# they ride shape_row and the upsert as a block rather than 21 hand-typed
# lines that can drift out of order.
_FULL_FIDELITY = _FIELDS[_FIELDS.index("set_aside_code"):]


def store_dir() -> Path:
    return Path(os.environ.get("LILA_NOTICE_STORE_DIR", str(_DEFAULT_DIR)))


def db_path() -> Path:
    return store_dir() / "notices.db"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # ORDER MATTERS. Tables, then the additive migration, then indexes. An
    # index names columns, so creating indexes before a store predating those
    # columns has been migrated fails on the column that is not there yet.
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.executescript(INDEX_SCHEMA)
    return conn


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add columns a store predating them does not have. ADDITIVE ONLY.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    a store built before 2026-07-28 would keep the old shape and every insert
    naming a new column would fail. This brings it forward in place: no
    rewrite, no data loss, and a no-op on a current store. Nothing is ever
    dropped or retyped here, because a migration that can lose a column is a
    migration that can lose the only copy of a vanished notice.
    """
    have = {r["name"] for r in conn.execute("PRAGMA table_info(notices)")}
    added = []
    for column in ("awardee", "award_number", "award_date", "award_dollars",
                   "archive_type", "archive_date", "base_type", "sol_number",
                   "poc_phone", "poc_secondary_email", "poc_secondary_name",
                   *_FULL_FIDELITY):
        if column not in have:
            conn.execute(f"ALTER TABLE notices ADD COLUMN {column} TEXT")
            added.append(column)
    if added:
        conn.commit()
    return added


def _log(msg: str) -> None:
    print(f"[notice-store] {msg}", file=sys.stderr, flush=True)


def _open_extract(path: str):
    """A text handle for a raw or archived extract.

    The archive keeps rotated days as .csv.gz, and those are the ONLY copies
    of their days that exist anywhere. Being able to ingest them directly is
    what made the 2026-07-30 full-description backfill possible at all.
    """
    if str(path).endswith(".gz"):
        import gzip
        return gzip.open(path, "rt", encoding="utf-8", errors="replace",
                         newline="")
    return open(path, encoding="utf-8", errors="replace", newline="")


def _credible_size(path: str) -> tuple[int, int]:
    """(size, floor) with the floor scaled for compressed archives.

    Gzip lands a real extract near 25% of raw (measured: 238 MB -> 61 MB),
    so a fifth of the raw floor still towers over any header-only stub while
    never refusing a genuine archived day.
    """
    size = os.path.getsize(path)
    floor = min_credible_extract_bytes()
    if str(path).endswith(".gz"):
        floor //= 5
    return size, floor


def shape_row(row: dict) -> tuple:
    """One extract row in store shape. Description is capped and hashed."""
    description = str(row.get("description") or "")
    digest = (hashlib.sha256(description.encode("utf-8", "replace")).hexdigest()
              if description else "")
    return (
        str(row.get("notice_id") or ""),
        str(row.get("title") or ""),
        str(row.get("type") or ""),
        str(row.get("agency") or ""),
        str(row.get("subtier") or ""),
        str(row.get("office") or ""),
        str(row.get("posted") or ""),
        str(row.get("deadline") or ""),
        str(row.get("naics") or ""),
        str(row.get("psc") or ""),
        str(row.get("set_aside") or ""),
        str(row.get("url") or ""),
        str(row.get("poc_name") or ""),
        str(row.get("poc_email") or ""),
        str(row.get("poc_phone") or ""),
        str(row.get("poc_secondary_email") or ""),
        str(row.get("poc_secondary_name") or ""),
        description[:DESCRIPTION_PREFIX_CHARS],
        digest,
        len(description),
        str(row.get("awardee") or ""),
        str(row.get("award_number") or ""),
        str(row.get("award_date") or ""),
        str(row.get("award_dollars") or ""),
        str(row.get("archive_type") or ""),
        str(row.get("archive_date") or ""),
        str(row.get("base_type") or ""),
        str(row.get("solicitation") or ""),
    ) + tuple(str(row.get(field) or "") for field in _FULL_FIDELITY)


def ingest(extract_path: str, *, conn: Optional[sqlite3.Connection] = None,
           as_of: Optional[str] = None, floor_bytes: Optional[int] = None,
           log=_log) -> dict:
    """Fold one daily extract into the store. Idempotent for a given day.

    Staging first, then ONE transaction. A crash anywhere leaves the previous
    store exactly as it was; there is no half-ingested state.

    floor_bytes exists for exactly one caller: sam_archive_backfill's per-day
    slices, which are small BY CONSTRUCTION because they were cut from a
    parent FY archive that already passed the real floor. A raw daily extract
    must never be ingested with a lowered floor; the default (None) keeps the
    one-owner floor exactly as it is.
    """
    from tools.api.sam_extract import iter_rows

    day = as_of or date.today().isoformat()
    started = time.monotonic()
    # A TRUNCATED EXTRACT IS REFUSED, NOT FLAGGED. Verified at the source on
    # 2026-07-29 03:30 GMT: ContractOpportunitiesFullCSV.csv was 685 bytes,
    # one header row and ZERO data rows. GSA republishes it during the day, so
    # the morning job can and will catch it mid-rewrite.
    #
    # _suspect() would have flagged that and ingested it anyway, and the
    # ingest would then have recorded 78,577 notices as gone_since_prev: a
    # fabricated mass closure written into the store and into the sampling
    # series the operator is collecting. A 100% row collapse is not a trend
    # break to note, it is a file that must never be read. Yesterday's store
    # is always the better artifact.
    if os.path.exists(extract_path):
        size, floor = _credible_size(extract_path)
        if floor_bytes is not None:
            floor = floor_bytes
        if size < floor:
            log(f"{day}: REFUSED, extract is {size:,} bytes which is below the "
                f"{floor:,}-byte floor (a header-only or "
                f"mid-rewrite file). Store left untouched.")
            return {"ingest_date": day, "extract": os.path.basename(extract_path),
                    "rows_in_file": 0, "new": 0, "updated": 0, "revised": 0,
                    "gone_since_prev": 0, "prev_date": None, "store_total": None,
                    "store_total_before": None, "seconds": 0.0, "suspect": True,
                    "suspect_reason": (f"extract only {size:,} bytes; refused "
                                       f"below the {floor:,} floor"),
                    "error": "extract_truncated"}
    if not os.path.exists(extract_path):
        # A missing or unreadable extract means the pull did not land. That
        # is a fact to report, never an exception to propagate: the store
        # keeps yesterday's rows and every reader sees an unchanged
        # last_ingest. Nothing downstream fails because a download did.
        log(f"{day}: extract not readable at {extract_path}; store unchanged")
        return {"ingest_date": day, "extract": os.path.basename(extract_path),
                "rows_in_file": 0, "new": 0, "updated": 0, "revised": 0,
                "gone_since_prev": 0, "prev_date": None, "store_total": None,
                "store_total_before": None, "seconds": 0.0, "suspect": False,
                "suspect_reason": "extract not readable; no ingest attempted",
                "error": "extract_not_readable"}
    owned = conn is None
    conn = conn or connect()
    try:
        prev = conn.execute(
            "SELECT ingest_date FROM ingests WHERE ingest_date < ? "
            "ORDER BY ingest_date DESC LIMIT 1", (day,)).fetchone()
        prev_date = prev["ingest_date"] if prev else None
        before_total = conn.execute(
            "SELECT COUNT(*) c FROM notices").fetchone()["c"]

        # ---- merge in ONE upsert pass ----------------------------------- #
        # The first cut of this staged every row into a temp table and then
        # ran six correlated subqueries per row against it, unindexed. That
        # is O(rows x rows) and it took 4,800 SECONDS on 78,553 notices,
        # which is unusable for a job that has to finish before the operator
        # reads a report. The design always said upsert; this is the design.
        # One statement per row, straight onto the primary key.
        #
        # New / revised counts come from a cheap pre-read of (id, hash) for
        # rows already stored: 78K short strings, a few MB, and it removes
        # the need to ask the database what changed after the fact.
        known = {r["notice_id"]: r["description_sha256"] for r in conn.execute(
            "SELECT notice_id, description_sha256 FROM notices")}
        upsert = (
            f"INSERT INTO notices ({','.join(_FIELDS)}, first_seen, last_seen, "
            f"seen_count, revisions) VALUES ({','.join('?' * len(_FIELDS))},?,?,1,0) "
            f"ON CONFLICT(notice_id) DO UPDATE SET "
            f"  last_seen = excluded.last_seen, "
            f"  seen_count = notices.seen_count + 1, "
            f"  revisions = notices.revisions + "
            f"    (notices.description_sha256 <> excluded.description_sha256), "
            f"  title = excluded.title, "
            f"  notice_type = excluded.notice_type, "
            f"  deadline = excluded.deadline, "
            f"  description_prefix = excluded.description_prefix, "
            f"  description_sha256 = excluded.description_sha256, "
            f"  description_len = excluded.description_len, "
            f"  awardee = excluded.awardee, "
            f"  award_number = excluded.award_number, "
            f"  award_date = excluded.award_date, "
            f"  award_dollars = excluded.award_dollars, "
            f"  archive_type = excluded.archive_type, "
            f"  archive_date = excluded.archive_date, "
            f"  base_type = excluded.base_type, "
            f"  sol_number = excluded.sol_number, "
            f"  poc_name = excluded.poc_name, "
            f"  poc_email = excluded.poc_email, "
            f"  poc_phone = excluded.poc_phone, "
            f"  poc_secondary_email = excluded.poc_secondary_email, "
            f"  poc_secondary_name = excluded.poc_secondary_name, "
            # amendments legitimately change set-aside, place of performance,
            # contacts, and the active flag; the full-fidelity block follows
            # the day's file like the other mutable fields
            + ", ".join(f"{c} = excluded.{c}" for c in _FULL_FIDELITY))
        _ID, _SHA = _field_index("notice_id"), _field_index("description_sha256")
        rows_in_file = new = revised = 0
        batch: list[tuple] = []
        with conn:                       # one transaction; a crash rolls back
            with _open_extract(extract_path) as fh:
                for row in iter_rows(fh):
                    shaped = shape_row(row)
                    if not shaped[_ID]:
                        continue                  # no notice id, no identity
                    rows_in_file += 1
                    prior = known.get(shaped[_ID], _MISSING)
                    if prior is _MISSING:
                        new += 1
                    elif prior != shaped[_SHA]:
                        revised += 1
                    batch.append(shaped + (day, day))
                    if len(batch) >= 5000:
                        conn.executemany(upsert, batch)
                        batch.clear()
            if batch:
                conn.executemany(upsert, batch)
            updated = rows_in_file - new
            # DEDUPED BY (title, agency). The raw count overstates: the 24
            # ids that vanished on 2026-07-28 were about 10 distinct
            # requirements, because agencies post the same buy several times
            # (Office Furniture x4, Oil/Coolant Storage x4, BOAST Tubing x4).
            # Counting ids would report a churn rate 2.4x the real one.
            gone = gone_ids = 0
            if prev_date:
                gone_ids = conn.execute(
                    "SELECT COUNT(*) c FROM notices WHERE last_seen = ?",
                    (prev_date,)).fetchone()["c"]
                gone = conn.execute(
                    "SELECT COUNT(*) c FROM (SELECT DISTINCT "
                    "  LOWER(TRIM(title)), LOWER(TRIM(COALESCE(agency,''))) "
                    "  FROM notices WHERE last_seen = ?)",
                    (prev_date,)).fetchone()["c"]

            # THE INGEST RECORD COMMITS WITH THE ROWS, NOT AFTER THEM.
            # These were two transactions. A death in the gap left today's
            # notices in the store under yesterday's ingest date, so
            # last_ingest would UNDERSTATE freshness: the press would read
            # current data and stamp it stale. A freshness marker that can
            # disagree with the data it describes is worse than none, so the
            # rows and the claim about the rows are now one atomic unit.
            elapsed = round(time.monotonic() - started, 1)
            suspect, reason = _suspect(conn, day, rows_in_file)
            conn.execute(
                "INSERT INTO ingests (ingest_date, extract_file, "
                "rows_in_file, rows_new, rows_updated, rows_revised, "
                "gone_since_prev, prev_date, seconds, suspect, suspect_reason, "
                "finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                # A day's new/gone counts are properties of the DAY, fixed
                # the first time we look. A second run must not rewrite them
                # to zero: the operator is collecting a seven-day series and
                # a double-run would silently corrupt a point in it.
                "ON CONFLICT(ingest_date) DO UPDATE SET "
                "  extract_file = excluded.extract_file, "
                "  rows_in_file = excluded.rows_in_file, "
                "  rows_new = MAX(ingests.rows_new, excluded.rows_new), "
                "  rows_updated = excluded.rows_updated, "
                "  rows_revised = MAX(ingests.rows_revised, excluded.rows_revised), "
                "  gone_since_prev = MAX(ingests.gone_since_prev, "
                "                        excluded.gone_since_prev), "
                "  seconds = excluded.seconds, "
                "  suspect = excluded.suspect, "
                "  suspect_reason = excluded.suspect_reason, "
                "  finished_at = excluded.finished_at",
                (day, os.path.basename(extract_path), rows_in_file, new,
                 updated, revised, gone, prev_date, elapsed,
                 int(suspect), reason,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        total = conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"]
        result = {"ingest_date": day, "extract": os.path.basename(extract_path),
                  "rows_in_file": rows_in_file, "new": new, "updated": updated,
                  "revised": revised, "gone_since_prev": gone,
                  "gone_ids_before_dedupe": gone_ids,
                  "prev_date": prev_date, "store_total": total,
                  "store_total_before": before_total, "seconds": elapsed,
                  "suspect": suspect, "suspect_reason": reason}
        log(f"{day}: {rows_in_file:,} rows in file, {new:,} new, "
            f"{updated:,} refreshed, {revised:,} revised, {gone:,} gone since "
            f"{prev_date or 'n/a'}; store now {total:,} ({elapsed}s)"
            + (f"  SUSPECT: {reason}" if suspect else ""))
        return result
    finally:
        if owned:
            conn.close()


def _suspect(conn: sqlite3.Connection, day: str, rows_in_file: int) -> tuple:
    """Flag a day whose row count breaks trend, WITHOUT discarding it.

    A short or partial extract looks exactly like a mass closure event, and
    we have already been fooled once: the 07-27 file held 76,848 rows between
    a 80,579 and a 78,553, and a 5,191-notice "closure" was read off that dip.
    The rule is deliberately dumb and the data is always kept; a flag invites
    a human look, it does not decide anything.
    """
    history = [r["rows_in_file"] for r in conn.execute(
        "SELECT rows_in_file FROM ingests WHERE ingest_date < ? "
        "ORDER BY ingest_date DESC LIMIT 7", (day,)).fetchall()]
    if len(history) < 3:
        return False, "insufficient history to judge trend"
    ordered = sorted(history)
    median = ordered[len(ordered) // 2]
    if not median:
        return False, "no baseline"
    drift = (rows_in_file - median) / median
    if abs(drift) > 0.03:
        return True, (f"row count {rows_in_file:,} deviates {drift*100:+.1f}% "
                      f"from the {len(history)}-day median {median:,}; treat as "
                      f"a possible short extract, not a real event")
    return False, f"{drift*100:+.1f}% vs median {median:,}"


def sampling_log(conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """The daily series: row counts, new, gone, and any suspect flag.

    Seven consecutive days INCLUDING A WEEKEND before closure counts mean
    anything. The two windows measured on 2026-07-28 were never comparable:
    07-24 to 07-27 spans a weekend and 07-27 to 07-28 does not, and both
    involve the 07-27 file whose count is the one that looks wrong.
    """
    owned = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ingests ORDER BY ingest_date").fetchall()]
    finally:
        if owned:
            conn.close()


def last_ingest(conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """What a reader checks to know whether the store is fresh."""
    owned = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT * FROM ingests ORDER BY ingest_date DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        if owned:
            conn.close()


def backfill(paths: Iterable[str], *, conn: Optional[sqlite3.Connection] = None,
             log=_log) -> list[dict]:
    """Ingest older extracts oldest-first so first_seen means what it says.

    LIMITATION, stated because it never goes away: first_seen is "first seen
    BY US", not the posting date, which the row carries separately. Extracts
    that already rotated off disk cannot be backfilled at all.
    """
    owned = conn is None
    conn = conn or connect()
    try:
        out = []
        for path in sorted(paths):
            stamp = (os.path.basename(path)
                     .replace("opportunities_", "")
                     .replace(".csv.gz", "").replace(".csv", ""))
            out.append(ingest(path, conn=conn, as_of=stamp, log=log))
        return out
    finally:
        if owned:
            conn.close()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Ingest sam.gov extracts.")
    ap.add_argument("--extract", default=None,
                    help="path to one extract; default is the newest on disk")
    ap.add_argument("--backfill", action="store_true",
                    help="ingest every extract on disk, oldest first")
    ap.add_argument("--from-archive", action="store_true",
                    help="backfill from the compressed archive too (the only "
                         "copies of rotated days), oldest first, then any "
                         "raw extracts on disk")
    ap.add_argument("--log", action="store_true", help="print the daily series")
    args = ap.parse_args(argv)

    if args.log:
        rows = sampling_log()
        if not rows:
            print("no ingests recorded yet")
            return 0
        print(f"{'DATE':12s} {'ROWS':>8s} {'NEW':>7s} {'GONE':>6s} "
              f"{'REVISED':>8s} {'SUSPECT':>7s}  NOTE")
        for r in rows:
            print(f"{r['ingest_date']:12s} {r['rows_in_file']:8,d} "
                  f"{r['rows_new']:7,d} {r['gone_since_prev']:6,d} "
                  f"{r['rows_revised']:8,d} {'YES' if r['suspect'] else '-':>7s}"
                  f"  {r['suspect_reason'] or ''}")
        need = 7 - len(rows)
        print(f"\n{len(rows)} of 7 days recorded"
              + (f"; {need} more (including a weekend) before closure counts "
                 f"are a signal" if need > 0 else
                 "; the series is long enough to judge closure rate"))
        return 0

    from tools.api.sam_extract import _archive_dir, _cache_dir, _latest_path
    if args.backfill or args.from_archive:
        paths = [str(p) for p in sorted(_cache_dir().glob("opportunities_*.csv"))]
        if args.from_archive:
            paths = [str(p) for p in sorted(
                _archive_dir().glob("opportunities_*.csv.gz"))] + paths
        if not paths:
            _log("no extracts on disk to backfill")
            return 0
        backfill(paths)
        return 0

    target = args.extract or _latest_path()
    if not target:
        _log("no extract on disk; nothing to ingest (the pull runs first)")
        return 0
    ingest(str(target))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
