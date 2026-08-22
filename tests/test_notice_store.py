"""Durable notice store: ingest, backfill, sampling log.

Offline: every test builds its own extract CSV in tmp_path. No network, no
real extract, no shared database.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import notice_store as ns  # noqa: E402

HEADER = ("NoticeId,Title,Type,Department/Ind.Agency,Sub-Tier,Office,"
          "PostedDate,ResponseDeadLine,NaicsCode,ClassificationCode,SetASide,"
          "Link,Description\n")


def _extract(tmp_path, name, rows):
    path = tmp_path / f"opportunities_{name}.csv"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(HEADER)
        for r in rows:
            fh.write(",".join('"%s"' % str(v).replace('"', "'") for v in r) + "\n")
    return str(path)


def _row(nid, title="A notice", ntype="Solicitation", desc="network monitoring"):
    return [nid, title, ntype, "DEPT OF X", "SUBTIER", "OFFICE",
            "2026-07-01", "2026-09-01", "541519", "DA10", "None",
            f"https://sam.gov/opp/{nid}/view", desc]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "store"))
    ns.load_guards.cache_clear() if hasattr(ns, "load_guards") else None
    return ns.connect(tmp_path / "store" / "notices.db")


# ---- ingest ---------------------------------------------------------------- #
def test_first_ingest_records_every_row_as_new(store, tmp_path):
    path = _extract(tmp_path, "2026-07-27", [_row("a"), _row("b")])
    out = ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    assert out["rows_in_file"] == 2 and out["new"] == 2 and out["updated"] == 0
    assert store.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 2


def test_second_day_adds_the_new_and_bumps_the_rest(store, tmp_path):
    d1 = _extract(tmp_path, "2026-07-27", [_row("a"), _row("b")])
    d2 = _extract(tmp_path, "2026-07-28", [_row("a"), _row("b"), _row("c")])
    ns.ingest(d1, conn=store, as_of="2026-07-27", log=lambda m: None)
    out = ns.ingest(d2, conn=store, as_of="2026-07-28", log=lambda m: None)
    assert out["new"] == 1 and out["updated"] == 2
    rows = {r["notice_id"]: r for r in store.execute("SELECT * FROM notices")}
    assert rows["a"]["first_seen"] == "2026-07-27"
    assert rows["a"]["last_seen"] == "2026-07-28"
    assert rows["a"]["seen_count"] == 2
    assert rows["c"]["first_seen"] == "2026-07-28"


def test_a_vanished_notice_is_kept_not_deleted(store, tmp_path):
    """THE ENTIRE POINT. sam.gov drops it; we do not."""
    d1 = _extract(tmp_path, "2026-07-27", [_row("a"), _row("gone")])
    d2 = _extract(tmp_path, "2026-07-28", [_row("a")])
    ns.ingest(d1, conn=store, as_of="2026-07-27", log=lambda m: None)
    out = ns.ingest(d2, conn=store, as_of="2026-07-28", log=lambda m: None)
    assert out["gone_since_prev"] == 1
    row = store.execute(
        "SELECT * FROM notices WHERE notice_id='gone'").fetchone()
    assert row is not None, "a dropped notice must survive in the store"
    assert row["last_seen"] == "2026-07-27"      # stops advancing, that is all


def test_re_ingesting_the_same_day_is_idempotent(store, tmp_path):
    path = _extract(tmp_path, "2026-07-27", [_row("a"), _row("b")])
    ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    out = ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    assert out["new"] == 0
    assert store.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 2


def test_a_rewritten_description_counts_as_a_revision(store, tmp_path):
    d1 = _extract(tmp_path, "2026-07-27", [_row("a", desc="original text")])
    d2 = _extract(tmp_path, "2026-07-28", [_row("a", desc="amended text")])
    ns.ingest(d1, conn=store, as_of="2026-07-27", log=lambda m: None)
    out = ns.ingest(d2, conn=store, as_of="2026-07-28", log=lambda m: None)
    assert out["revised"] == 1
    row = store.execute("SELECT * FROM notices WHERE notice_id='a'").fetchone()
    assert row["revisions"] == 1
    assert row["description_prefix"] == "amended text"


def test_rows_without_a_notice_id_are_skipped_not_stored(store, tmp_path):
    path = _extract(tmp_path, "2026-07-27", [_row(""), _row("a")])
    out = ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    assert out["rows_in_file"] == 1


# ---- description handling -------------------------------------------------- #
def test_description_is_capped_and_hashed_over_the_full_text(store, tmp_path):
    long = "x" * (ns.DESCRIPTION_PREFIX_CHARS + 500)
    path = _extract(tmp_path, "2026-07-27", [_row("a", desc=long)])
    ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    row = store.execute("SELECT * FROM notices WHERE notice_id='a'").fetchone()
    assert len(row["description_prefix"]) == ns.DESCRIPTION_PREFIX_CHARS
    assert row["description_len"] == len(long)     # the true length survives
    assert len(row["description_sha256"]) == 64    # hash covers the WHOLE text


def test_the_cap_clears_gsa_own_truncation_per_operator_ruling():
    """"We preserve data detail" (operator, 2026-07-30): GSA truncates the
    extract's Description column at ~32K (859 stored notices sat at exactly
    32,003 chars), so a cap above that stores the COMPLETE text GSA serves.
    The 2026-07-28 cap of 4,000 kept 53.1% of description bytes and cost
    10,570 notices their tails until the archive backfill restored them."""
    assert ns.DESCRIPTION_PREFIX_CHARS == 40_000
    assert ns.DESCRIPTION_PREFIX_CHARS > 32_003    # GSA's measured ceiling


# ---- the sampling log ------------------------------------------------------ #
def test_sampling_log_records_the_series(store, tmp_path):
    for day, rows in (("2026-07-27", [_row("a"), _row("b")]),
                      ("2026-07-28", [_row("a"), _row("c")])):
        ns.ingest(_extract(tmp_path, day, rows), conn=store, as_of=day,
                  log=lambda m: None)
    series = ns.sampling_log(store)
    assert [r["ingest_date"] for r in series] == ["2026-07-27", "2026-07-28"]
    assert series[1]["rows_new"] == 1
    assert series[1]["gone_since_prev"] == 1


def test_trend_break_is_flagged_but_the_day_is_still_kept(store, tmp_path):
    """A short extract looks exactly like a mass closure. We were fooled once
    already by the 07-27 file. The flag invites a look; it decides nothing."""
    for i, day in enumerate(("2026-07-20", "2026-07-21", "2026-07-22",
                             "2026-07-23")):
        rows = [_row(f"n{j}") for j in range(100)]
        ns.ingest(_extract(tmp_path, day, rows), conn=store, as_of=day,
                  log=lambda m: None)
    short = [_row(f"n{j}") for j in range(50)]        # half the file
    out = ns.ingest(_extract(tmp_path, "2026-07-24", short), conn=store,
                    as_of="2026-07-24", log=lambda m: None)
    assert out["suspect"] is True
    assert "short extract" in out["suspect_reason"]
    # and nothing was thrown away
    assert store.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 100


def test_early_days_are_not_judged_without_a_baseline(store, tmp_path):
    out = ns.ingest(_extract(tmp_path, "2026-07-27", [_row("a")]), conn=store,
                    as_of="2026-07-27", log=lambda m: None)
    assert out["suspect"] is False
    assert "insufficient history" in out["suspect_reason"]


# ---- freshness ------------------------------------------------------------- #
def test_last_ingest_is_how_a_reader_sees_staleness(store, tmp_path):
    assert ns.last_ingest(store) is None
    ns.ingest(_extract(tmp_path, "2026-07-27", [_row("a")]), conn=store,
              as_of="2026-07-27", log=lambda m: None)
    li = ns.last_ingest(store)
    assert li["ingest_date"] == "2026-07-27" and li["rows_in_file"] == 1


def test_missing_extract_degrades_and_never_raises(store, tmp_path):
    """A failed or absent pull must never fail anything downstream. The store
    keeps yesterday's rows and last_ingest stays where it was."""
    ns.ingest(_extract(tmp_path, "2026-07-27", [_row("a")]), conn=store,
              as_of="2026-07-27", log=lambda m: None)
    out = ns.ingest(str(tmp_path / "nope.csv"), conn=store,
                    as_of="2026-07-28", log=lambda m: None)
    assert out["error"] == "extract_not_readable"
    assert out["rows_in_file"] == 0
    assert store.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 1
    assert ns.last_ingest(store)["ingest_date"] == "2026-07-27"   # unchanged


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_a_double_run_does_not_corrupt_the_days_counts(store, tmp_path):
    """A day's new/gone counts are properties of the DAY. Running the morning
    job twice must not rewrite them to zero: the operator is collecting a
    seven-day series and a double-run would silently corrupt a point in it."""
    d1 = _extract(tmp_path, "2026-07-27", [_row("a"), _row("gone")])
    d2 = _extract(tmp_path, "2026-07-28", [_row("a"), _row("b")])
    ns.ingest(d1, conn=store, as_of="2026-07-27", log=lambda m: None)
    ns.ingest(d2, conn=store, as_of="2026-07-28", log=lambda m: None)
    second = ns.ingest(d2, conn=store, as_of="2026-07-28", log=lambda m: None)
    assert second["new"] == 0                     # true of THIS run
    series = {r["ingest_date"]: r for r in ns.sampling_log(store)}
    assert series["2026-07-28"]["rows_new"] == 1  # ... but the DAY keeps its 1
    assert series["2026-07-28"]["gone_since_prev"] == 1


# ---- the eight carried columns (2026-07-28) -------------------------------- #
_EXTRA = ("awardee", "award_number", "award_date", "award_dollars",
          "archive_type", "archive_date", "base_type", "sol_number")


def test_the_eight_carried_columns_round_trip(store, tmp_path):
    """Storage only: kept raw, exactly as the extract wrote them."""
    header = HEADER.rstrip("\n") + (",Awardee,AwardNumber,AwardDate,Award$,"
                                    "ArchiveType,ArchiveDate,BaseType\n")
    path = tmp_path / "opportunities_2026-07-27.csv"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write(",".join('"%s"' % v for v in (
            "a1", "A notice", "Solicitation", "DEPT OF X", "SUB", "OFFICE",
            "2026-07-01", "2026-09-01", "541519", "DA10", "None",
            "https://sam.gov/opp/a1/view", "network monitoring",
            "ACME CORP", "W123-26-C-0001", "2026-06-15", "$1,234,567",
            "auto15", "2026-10-01", "Sources Sought")) + "\n")
    ns.ingest(str(path), conn=store, as_of="2026-07-27", log=lambda m: None)
    row = store.execute("SELECT * FROM notices WHERE notice_id='a1'").fetchone()
    assert row["awardee"] == "ACME CORP"
    assert row["award_number"] == "W123-26-C-0001"
    assert row["award_dollars"] == "$1,234,567"   # raw, not parsed for us
    assert row["archive_type"] == "auto15"
    assert row["archive_date"] == "2026-10-01"
    assert row["base_type"] == "Sources Sought"   # differs from Type: amended


def test_absent_columns_store_empty_and_never_raise(store, tmp_path):
    """An extract without these columns still ingests."""
    ns.ingest(_extract(tmp_path, "2026-07-27", [_row("a")]), conn=store,
              as_of="2026-07-27", log=lambda m: None)
    row = store.execute("SELECT * FROM notices WHERE notice_id='a'").fetchone()
    for col in _EXTRA:
        assert row[col] == "", f"{col} should be empty, not missing"


def test_a_store_predating_these_columns_migrates_in_place(tmp_path):
    """CREATE TABLE IF NOT EXISTS does nothing to an existing table, so a
    store built before 2026-07-28 would keep the old shape and every insert
    naming a new column would fail. Additive only: nothing dropped, nothing
    retyped, existing rows untouched."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute(
        "CREATE TABLE notices (notice_id TEXT PRIMARY KEY, title TEXT, "
        "notice_type TEXT, agency TEXT, subtier TEXT, office TEXT, posted TEXT, "
        "deadline TEXT, naics TEXT, psc TEXT, set_aside TEXT, url TEXT, "
        "poc_name TEXT, poc_email TEXT, description_prefix TEXT, "
        "description_sha256 TEXT, description_len INTEGER, "
        "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, "
        "seen_count INTEGER NOT NULL DEFAULT 1, revisions INTEGER NOT NULL DEFAULT 0)")
    old.execute("INSERT INTO notices (notice_id, title, notice_type, first_seen, "
                "last_seen) VALUES ('keep','Old row','Sources Sought','2026-01-01','2026-01-01')")
    old.commit()
    old.close()

    conn = ns.connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(notices)")}
    assert set(_EXTRA) <= columns
    row = conn.execute("SELECT * FROM notices WHERE notice_id='keep'").fetchone()
    assert row["title"] == "Old row"          # untouched
    assert row["notice_type"] == "Sources Sought"
    assert row["awardee"] is None             # new column, no invented value


def test_migration_is_a_no_op_on_a_current_store(store):
    assert ns._migrate(store) == []


def test_a_truncated_extract_is_refused_not_ingested(store, tmp_path):
    """VERIFIED AT THE SOURCE 2026-07-29 03:30 GMT: the daily file was 685
    bytes, header only, zero data rows. Ingesting that would have recorded
    every stored notice as gone: a fabricated mass closure. A 100% collapse
    is not a trend break, it is a file that must never be read."""
    ns.ingest(_extract(tmp_path, "2026-07-27", [_row("a"), _row("b")]),
              conn=store, as_of="2026-07-27", log=lambda m: None)
    stub = tmp_path / "opportunities_2026-07-29.csv"
    stub.write_text(HEADER, encoding="utf-8")          # header only, like GSA's
    os.environ["LILA_MIN_EXTRACT_BYTES"] = "1000"      # a realistic floor
    out = ns.ingest(str(stub), conn=store, as_of="2026-07-29",
                    log=lambda m: None)
    assert out["error"] == "extract_truncated"
    assert out["gone_since_prev"] == 0, "must not report a mass closure"
    assert store.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 2
    assert ns.last_ingest(store)["ingest_date"] == "2026-07-27"   # unmoved


def test_the_production_floor_sits_between_a_stub_and_a_real_file(monkeypatch):
    monkeypatch.delenv("LILA_MIN_EXTRACT_BYTES", raising=False)
    assert 1_000 < ns.min_credible_extract_bytes() < 230_000_000


# ---- full fidelity (2026-07-30, "we preserve data detail") ----------------- #
FULL_HEADER = (
    '"NoticeId","Title","Sol#","Department/Ind.Agency","CGAC","Sub-Tier",'
    '"FPDS Code","Office","AAC Code","PostedDate","Type","BaseType",'
    '"ArchiveType","ArchiveDate","SetASideCode","SetASide","ResponseDeadLine",'
    '"NaicsCode","ClassificationCode","PopStreetAddress","PopCity","PopState",'
    '"PopZip","PopCountry","Active","AwardNumber","AwardDate","Award$",'
    '"Awardee","PrimaryContactTitle","PrimaryContactFullname",'
    '"PrimaryContactEmail","PrimaryContactPhone","PrimaryContactFax",'
    '"SecondaryContactTitle","SecondaryContactFullname",'
    '"SecondaryContactEmail","SecondaryContactPhone","SecondaryContactFax",'
    '"OrganizationType","State","City","ZipCode","CountryCode",'
    '"AdditionalInfoLink","Link","Description"\n')


def _full_row(nid, desc="network monitoring services"):
    vals = [nid, "A notice", "S-1", "DEPT OF X", "012", "SUBTIER", "FP01",
            "OFFICE", "AAC1", "2026-07-01", "Solicitation", "Solicitation",
            "auto15", "2026-10-01", "SBA", "Total Small Business",
            "2026-09-01", "541519", "DA10", "123 Main St", "Arlington", "VA",
            "22201", "USA", "Yes", "", "", "", "", "Contracting Officer",
            "Jane Doe", "jane@x.gov", "555-0100", "555-0199", "Deputy CO",
            "Jim Roe", "jim@x.gov", "555-0200", "555-0299", "FEDERAL AGENCY",
            "DC", "Washington", "20001", "USA",
            "https://x.gov/info", f"https://sam.gov/opp/{nid}/view", desc]
    return ",".join('"%s"' % str(v).replace('"', "'") for v in vals) + "\n"


def _full_extract(tmp_path, name, rows):
    path = tmp_path / f"opportunities_{name}.csv"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(FULL_HEADER)
        for r in rows:
            fh.write(r)
    return str(path)


def test_every_extract_column_survives_into_the_store(store, tmp_path):
    """The audit found 14 columns dropped at parse and 8 more dropped at the
    store. Place of performance is a sales-territory filter that cannot be
    reconstructed once dropped; nothing gets to vanish silently anymore."""
    path = _full_extract(tmp_path, "2026-07-27", [_full_row("f1")])
    ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    row = store.execute("SELECT * FROM notices WHERE notice_id='f1'").fetchone()
    assert row["pop_city"] == "Arlington"
    assert row["pop_state"] == "VA"
    assert row["set_aside_code"] == "SBA"
    assert row["active"] == "Yes"
    assert row["poc_title"] == "Contracting Officer"
    assert row["poc_fax"] == "555-0199"
    assert row["poc_secondary_title"] == "Deputy CO"
    assert row["organization_type"] == "FEDERAL AGENCY"
    assert row["office_state"] == "DC"
    assert row["office_city"] == "Washington"
    assert row["cgac"] == "012"
    assert row["fpds_code"] == "FP01"
    assert row["aac_code"] == "AAC1"
    assert row["info_link"] == "https://x.gov/info"


def test_an_old_shape_store_migrates_in_place(tmp_path, monkeypatch):
    """A store created before the full-fidelity block must come forward
    additively: no rewrite, no data loss, and inserts naming new columns
    must not fail."""
    import sqlite3 as sq

    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "old"))
    (tmp_path / "old").mkdir()
    old = sq.connect(str(tmp_path / "old" / "notices.db"))
    old.execute(
        "CREATE TABLE notices (notice_id TEXT PRIMARY KEY, title TEXT, "
        "notice_type TEXT, agency TEXT, subtier TEXT, office TEXT, "
        "posted TEXT, deadline TEXT, naics TEXT, psc TEXT, set_aside TEXT, "
        "url TEXT, poc_name TEXT, poc_email TEXT, description_prefix TEXT, "
        "description_sha256 TEXT, description_len INTEGER, "
        "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, "
        "seen_count INTEGER NOT NULL DEFAULT 1, "
        "revisions INTEGER NOT NULL DEFAULT 0)")
    old.execute(
        "INSERT INTO notices (notice_id, title, first_seen, last_seen) "
        "VALUES ('legacy', 'kept', '2026-07-01', '2026-07-01')")
    old.commit()
    old.close()

    conn = ns.connect(tmp_path / "old" / "notices.db")
    row = conn.execute(
        "SELECT * FROM notices WHERE notice_id='legacy'").fetchone()
    assert row["title"] == "kept"              # nothing lost
    assert row["pop_city"] is None             # new column exists, unset
    path = _full_extract(tmp_path, "2026-07-27", [_full_row("f2")])
    out = ns.ingest(path, conn=conn, as_of="2026-07-27", log=lambda m: None)
    assert out["new"] == 1                     # insert with new columns works
    conn.close()


def test_gz_archives_ingest_directly(store, tmp_path):
    """The compressed archive holds the ONLY copies of rotated days; being
    able to ingest them is what makes description backfill possible."""
    import gzip

    raw = _full_extract(tmp_path, "2026-07-26", [_full_row("g1")])
    gz = tmp_path / "opportunities_2026-07-26.csv.gz"
    with open(raw, "rb") as src, gzip.open(gz, "wb") as dst:
        dst.write(src.read())
    out = ns.ingest(str(gz), conn=store, as_of="2026-07-26",
                    log=lambda m: None)
    assert out["rows_in_file"] == 1 and out["new"] == 1


def test_backfill_stamps_the_day_from_a_gz_name(store, tmp_path):
    import gzip

    raw = _full_extract(tmp_path, "2026-07-25", [_full_row("g2")])
    gz = tmp_path / "opportunities_2026-07-25.csv.gz"
    with open(raw, "rb") as src, gzip.open(gz, "wb") as dst:
        dst.write(src.read())
    os.remove(raw)
    results = ns.backfill([str(gz)], conn=store, log=lambda m: None)
    assert results[0]["ingest_date"] == "2026-07-25"


def test_reingest_restores_a_truncated_description_without_fake_revisions(
        store, tmp_path, monkeypatch):
    """THE BACKFILL CONTRACT. A store that truncated under the old cap must
    heal by re-ingesting the archived day: the full text lands, and because
    the sha256 always covered the whole text, the revision counter must NOT
    move - same text, same hash, no fake amendment."""
    long = "d" * 6_000
    path = _full_extract(tmp_path, "2026-07-27", [_full_row("h1", desc=long)])
    monkeypatch.setattr(ns, "DESCRIPTION_PREFIX_CHARS", 4_000)  # the old cap
    ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    before = store.execute(
        "SELECT description_prefix, revisions FROM notices "
        "WHERE notice_id='h1'").fetchone()
    assert len(before["description_prefix"]) == 4_000

    monkeypatch.setattr(ns, "DESCRIPTION_PREFIX_CHARS", 40_000)
    ns.ingest(path, conn=store, as_of="2026-07-27", log=lambda m: None)
    after = store.execute(
        "SELECT description_prefix, revisions, description_len FROM notices "
        "WHERE notice_id='h1'").fetchone()
    assert len(after["description_prefix"]) == 6_000   # healed
    assert after["description_len"] == 6_000
    assert after["revisions"] == before["revisions"]   # no fake amendment
