"""FTS5 lexical retriever contract (R1 item 1, 2026-08-18).

Offline doctrine: in-memory store shaped like the real notices table.
The load-bearing pin is TRIGGER CURRENCY: the index is external-content
with database-persistent triggers, so an upsert through the store's own
ON CONFLICT DO UPDATE path must be retrievable with no rebuild.
"""

from __future__ import annotations

import sqlite3

from tools.retrieval import fts


def _store(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE notices (notice_id TEXT PRIMARY KEY, title TEXT, "
        "description_prefix TEXT, agency TEXT)")
    conn.executemany(
        "INSERT INTO notices (notice_id, title, description_prefix, agency) "
        "VALUES (?,?,?,?)", rows)
    conn.commit()
    return conn


ROWS = [
    ("n1", "Data loss prevention platform renewal",
     "The agency requires enterprise data loss prevention licenses.", "GSA"),
    ("n2", "Steelhead trout habitat monitoring",
     "Innovasea acoustic tags for steelhead trout tracking.", "NOAA"),
    ("n3", "WAN optimization refresh",
     "Riverbed SteelHead appliance maintenance and support.", "DOI"),
]


def test_rebuild_receipt_and_bm25_ordering(tmp_path):
    conn = _store(ROWS)
    receipt = fts.rebuild(conn, receipt_path=tmp_path / "r.json")
    assert receipt["rows_indexed"] == 3
    assert receipt["rebuild_seconds"] >= 0
    assert receipt["triggers"] == [
        "notices_fts_ai", "notices_fts_ad", "notices_fts_au"]

    hits = fts.bm25_retrieve(conn, ["data loss prevention"], k=5)
    assert [h[0] for h in hits] == ["n1"]
    assert hits[0][1] > 0  # sign flipped: higher is better

    both = fts.bm25_retrieve(conn, ["SteelHead"], k=5)
    assert {h[0] for h in both} == {"n2", "n3"}


def test_upsert_currency_without_rebuild(tmp_path):
    conn = _store(ROWS)
    fts.rebuild(conn, receipt_path=tmp_path / "r.json")

    # New row through the store's own upsert shape: retrievable at once.
    conn.execute(
        "INSERT INTO notices (notice_id, title, description_prefix, agency) "
        "VALUES ('n4', 'Insider threat monitoring tool', "
        "'user activity monitoring platform', 'DOD') "
        "ON CONFLICT(notice_id) DO UPDATE SET title=excluded.title")
    conn.commit()
    assert [h[0] for h in fts.bm25_retrieve(conn, ["insider threat"], k=5)] == ["n4"]

    # Update through the same path: old text gone, new text found.
    conn.execute(
        "INSERT INTO notices (notice_id, title, description_prefix, agency) "
        "VALUES ('n4', 'Zero trust segmentation services', "
        "'microsegmentation design', 'DOD') "
        "ON CONFLICT(notice_id) DO UPDATE SET "
        "title=excluded.title, "
        "description_prefix=excluded.description_prefix")
    conn.commit()
    assert fts.bm25_retrieve(conn, ["insider threat"], k=5) == []
    assert [h[0] for h in fts.bm25_retrieve(conn, ["microsegmentation"], k=5)] == ["n4"]

    # Delete: gone from the index.
    conn.execute("DELETE FROM notices WHERE notice_id = 'n4'")
    conn.commit()
    assert fts.bm25_retrieve(conn, ["microsegmentation"], k=5) == []


def test_query_syntax_survives_hyphens_and_quotes():
    conn = _store([("m1", "OMB M-21-31 event logging maturity",
                    "M-21-31 compliance tooling", "OMB")])
    fts.ensure_fts(conn)
    conn.execute("INSERT INTO notices_fts(notices_fts) VALUES ('rebuild')")
    hits = fts.bm25_retrieve(conn, ['M-21-31', 'say "quoted"'], k=5)
    assert [h[0] for h in hits] == ["m1"]
    assert fts.bm25_retrieve(conn, [""], k=5) == []
