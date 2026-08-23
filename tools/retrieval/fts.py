"""FTS5 lexical retriever over the notice store (R1 item 1, 2026-08-18).

External-content virtual table over title plus description_prefix on the
notices table. The three triggers are DATABASE-PERSISTENT objects: created
once by rebuild, they keep the index current on every later writer,
including the morning LaunchAgent ingest, with zero ingest-code change.
The store's upsert is a proper ON CONFLICT(notice_id) DO UPDATE (verified
2026-08-18), so AFTER INSERT / AFTER UPDATE / AFTER DELETE cover every
mutation and rowids stay stable; there is no INSERT OR REPLACE
delete-trigger gap here.

Zero LLM, zero network. bm25_retrieve is retrieval only: candidates it
returns still ride the guard ladder downstream; a BM25 score is never a
keep decision.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FTS_TABLE = "notices_fts"
_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = _ROOT / "data" / "state" / "retrieval" / "fts_receipt.json"

_CREATE = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    title, description_prefix,
    content='notices', content_rowid='rowid',
    tokenize='porter unicode61'
)
"""

_TRIGGERS = (
    f"""CREATE TRIGGER IF NOT EXISTS notices_fts_ai
        AFTER INSERT ON notices BEGIN
        INSERT INTO {FTS_TABLE}(rowid, title, description_prefix)
        VALUES (new.rowid, new.title, new.description_prefix);
        END""",
    f"""CREATE TRIGGER IF NOT EXISTS notices_fts_ad
        AFTER DELETE ON notices BEGIN
        INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, description_prefix)
        VALUES ('delete', old.rowid, old.title, old.description_prefix);
        END""",
    f"""CREATE TRIGGER IF NOT EXISTS notices_fts_au
        AFTER UPDATE ON notices BEGIN
        INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, description_prefix)
        VALUES ('delete', old.rowid, old.title, old.description_prefix);
        INSERT INTO {FTS_TABLE}(rowid, title, description_prefix)
        VALUES (new.rowid, new.title, new.description_prefix);
        END""",
)


def ensure_fts(conn) -> None:
    """Create the virtual table and its currency triggers if absent."""
    conn.execute(_CREATE)
    for trigger in _TRIGGERS:
        conn.execute(trigger)
    conn.commit()


def index_bytes(conn) -> Optional[int]:
    """Size of the FTS index shadow tables, when dbstat is available."""
    try:
        row = conn.execute(
            "SELECT SUM(pgsize) FROM dbstat WHERE name LIKE ?",
            (FTS_TABLE + "%",)).fetchone()
        return int(row[0]) if row and row[0] else None
    except Exception:  # noqa: BLE001 - dbstat is an optional vtab
        return None


def rebuild(conn, *, receipt_path: Optional[Path] = None) -> dict:
    """(Re)build the index from the content table; receipted."""
    ensure_fts(conn)
    started = time.monotonic()
    conn.execute(
        f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES ('rebuild')")
    conn.commit()
    elapsed = time.monotonic() - started
    rows = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
    receipt = {
        "table": FTS_TABLE,
        "rows_indexed": rows,
        "rebuild_seconds": round(elapsed, 2),
        "index_bytes": index_bytes(conn),
        "tokenizer": "porter unicode61",
        "content": "external (notices.title, notices.description_prefix)",
        "triggers": ["notices_fts_ai", "notices_fts_ad", "notices_fts_au"],
        "rebuilt_at": datetime.now(timezone.utc).isoformat(),
    }
    path = Path(receipt_path) if receipt_path else RECEIPT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    return receipt


def _fts_query(terms: list[str]) -> str:
    """OR of quoted phrases. Quoting keeps hyphenated terms (M-21-31)
    and multiword phrases intact under fts5 query syntax."""
    quoted = []
    for term in terms:
        text = str(term or "").strip().replace('"', '""')
        if text:
            quoted.append(f'"{text}"')
    return " OR ".join(quoted)


def bm25_retrieve(conn, terms: list[str], k: int = 500) -> list[tuple[str, float]]:
    """Top-k (notice_id, score) by BM25. Higher score = better match
    (SQLite's bm25() is smaller-is-better; the sign is flipped here)."""
    query = _fts_query(terms)
    if not query:
        return []
    rows = conn.execute(
        f"SELECT n.notice_id, -bm25({FTS_TABLE}) AS score "
        f"FROM {FTS_TABLE} JOIN notices n ON n.rowid = {FTS_TABLE}.rowid "
        f"WHERE {FTS_TABLE} MATCH ? ORDER BY bm25({FTS_TABLE}) LIMIT ?",
        (query, int(k))).fetchall()
    return [(str(r[0]), float(r[1])) for r in rows]


def main(argv: Optional[list] = None) -> int:
    import argparse

    from tools.notice_store import connect

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--query", default=None,
                    help="comma-separated terms for a smoke retrieval")
    ap.add_argument("-k", type=int, default=10)
    args = ap.parse_args(argv)
    conn = connect()
    try:
        if args.rebuild:
            receipt = rebuild(conn)
            print(json.dumps(receipt, indent=1))
        if args.query:
            terms = [t.strip() for t in args.query.split(",") if t.strip()]
            for notice_id, score in bm25_retrieve(conn, terms, k=args.k):
                print(f"{score:9.3f}  {notice_id}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
