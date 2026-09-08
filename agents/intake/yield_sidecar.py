"""Intake yield receipts against the durable notice store.

THE TITLES ARE THE RECEIPT; THE COUNT IS ONLY THE INDEX. This sidecar adds
one honesty layer the sweep receipt already assumes: an empty or missing
store is named as empty, never as a vocabulary of zeros. A true zero on a
populated store still has no verdict field.
"""

from __future__ import annotations

from typing import Optional


def notice_store_census(*, conn=None) -> dict:
    """How many notice rows the durable store actually holds.

    Distinguishes 'store empty' from 'terms matched zero'. Never a
    vocabulary verdict. Soft-fails to an unreadable status.
    """
    owned = conn is None
    try:
        if conn is None:
            from tools.notice_store import connect as _connect
            conn = _connect()
        row = conn.execute("SELECT COUNT(*) AS n FROM notices").fetchone()
        count = int(row["n"] if hasattr(row, "keys") else row[0])
        if count <= 0:
            return {
                "status": "empty",
                "row_count": 0,
                "note": "notice store has zero rows; counts are not a market zero",
            }
        return {
            "status": "ready",
            "row_count": count,
            "note": f"notice store holds {count} rows",
        }
    except Exception as exc:  # noqa: BLE001 - a receipt never sinks intake
        return {
            "status": "unreadable",
            "row_count": 0,
            "note": f"notice store unreadable ({type(exc).__name__}); "
                    "counts are not a market zero",
        }
    finally:
        if owned and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def intake_yield_receipt(
    phrases: list[str],
    *,
    conn=None,
    sample_cap: int = 3,
    client_name: str = "",
) -> dict:
    """Yield sidecar for the review packet.

    ``store.status`` is ready | empty | unreadable. Term rows are emitted
    only when the store is ready. An empty store returns terms=[] and a
    loud note, never a list of {term, count: 0}.
    """
    from tools.query_terms import term_yield

    store = notice_store_census(conn=conn)
    payload = {
        "schema_version": "intake_yield.v1",
        "client_name": client_name,
        "store": store,
        "terms": [],
        "note": store["note"],
    }
    if store["status"] != "ready":
        return payload
    owned = conn is None
    try:
        if conn is None:
            from tools.notice_store import connect as _connect
            conn = _connect()
        payload["terms"] = term_yield(
            phrases, conn=conn, sample_cap=sample_cap)
        payload["note"] = (
            f"{store['row_count']} stored notices screened; "
            "titles sit beside each count; no health verdict"
        )
        return payload
    finally:
        if owned and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
