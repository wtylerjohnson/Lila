"""Materialize agency forecast stores outside a sweep (A8, 2026-08-18).

The sweep already upserts forecast pulls into the global change store; this
runner is the diagnostic lane for wiring days: pull the named adapters with
the client frame's vocabulary, upsert their records into
data/state/forecast_store/<source>.json, and write the coverage ledger row,
so the stores exist before the next press. Zero LLM, zero SAM quota; bash
is the sanctioned lane. A blocked adapter (va_fco) prints its receipted
provenance and records ok=False; it never minted a clean zero. The HHS
census rides the adapter itself (single ingest path since 2026-08-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Optional

from tools.api.base import REGISTRY, SourceQuery


def frame_terms(client_slug: str) -> list[str]:
    """Tier 1 names plus tier 2 buyer language from the client frame (the
    A1 frame is the screen; forecasts honor the same gate vocabulary)."""
    from tools.frame_rescreen import frame_vocabulary, load_frame
    frame = load_frame(client_slug)
    vocab = frame_vocabulary(frame)
    terms = (list(vocab["entities"]["product"])
             + list(vocab["entities"]["competitor"]))
    for term in vocab["capability_terms"]:
        tier = vocab["tier_by_term"].get(term.casefold())
        if tier == 2 and term not in terms:
            terms.append(term)
    return terms


def refresh(sources: list[str], *, client_slug: str = "varonis",
            terms: Optional[list[str]] = None) -> dict:
    from tools.api.forecasts import record_pull, store_upsert

    terms = terms if terms is not None else frame_terms(client_slug)
    query = SourceQuery(keywords=terms)
    out: dict[str, dict] = {}
    for name in sources:
        try:
            adapter = REGISTRY.get(name)
        except KeyError:
            out[name] = {"status": "unknown-source"}
            continue
        row: dict = {}
        try:
            try:
                records = adapter.forecasts(query)
            except TypeError:
                # Workbook/PDF census adapters take no query: their whole
                # publication is the pull and matching happens downstream.
                records = adapter.forecasts()
        except Exception as exc:  # noqa: BLE001 - one source never sinks another
            records = []
            row["error"] = f"{type(exc).__name__}: {exc}"
        provenance = getattr(adapter, "last_provenance", None)
        if records:
            _, events = store_upsert(name, records)
            row.update({"status": "stored", "fetched": len(records),
                        "events": {}, })
            for ev in events:
                kind = getattr(ev, "kind", None) or (
                    ev.get("kind") if isinstance(ev, dict) else "event")
                row["events"][kind] = row["events"].get(kind, 0) + 1
            record_pull(name, "frame-refresh", len(records), ok=True)
        else:
            status = "blocked" if (provenance or {}).get(
                "status") == "failed" else "empty"
            row.update({"status": status, "fetched": 0})
            if provenance:
                row["provenance"] = {
                    k: provenance.get(k)
                    for k in ("mode", "receipts", "limitations", "measured",
                              "form_reachable_anonymously", "source_url")
                    if provenance.get(k) is not None}
            record_pull(name, "frame-refresh", 0, ok=False,
                        note=(provenance or {}).get("mode")
                        or row.get("error") or "no records")
        out[name] = row
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sources", default="hhs_sbcx,dhs_apfs,va_fco")
    ap.add_argument("--client", default="varonis")
    args = ap.parse_args(argv)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    result = refresh(sources, client_slug=args.client)
    for name, row in result.items():
        print(f"{name}: {json.dumps(row, default=str)[:400]}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
