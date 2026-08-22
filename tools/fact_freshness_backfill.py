"""Freshness backfill audit for existing stored fact substrates (2026-07-16).

Facts are never persisted as Fact objects: build_fact_pack rebuilds them each
press from the sweep and qualify artifacts. The migration for existing stored
data is therefore the DERIVATION (facts.py::_stamp_provenance recovers
retrieved_at from run metadata the artifacts already carry) plus this audit,
which rebuilds every client's pack offline and counts what recovered vs what
is UNKNOWN_FRESHNESS.

This tool WRITES NOTHING, deliberately. Sweep artifact bytes are hash-bound
into operator approvals (assess approval fingerprints, workstation sweep
bindings, compose-cache keys); rewriting them to embed provenance would
invalidate operator decisions. Recovery-at-load gives the same answer without
touching operator-owned bytes.

Offline, zero LLM spend; sanctioned for direct bash use.

    .venv/bin/python -m tools.fact_freshness_backfill [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SWEEP_RE = re.compile(r"^searches_(?P<stem>.+)\.json$")


def _audit_pack(client: str, searches: dict) -> dict:
    from agents.reports.facts import UNKNOWN_FRESHNESS, build_fact_pack
    pack = build_fact_pack(client, searches=searches)
    times = [f.retrieved_at for f in pack.facts if f.retrieved_at is not None]
    by_system: dict[str, int] = {}
    for f in pack.facts:
        key = f.source_system.value if f.source_system else "unmapped"
        by_system[key] = by_system.get(key, 0) + 1
    return {
        "facts": len(pack.facts),
        "backfilled": len(times),
        "unknown_freshness": sum(
            1 for f in pack.facts if f.freshness == UNKNOWN_FRESHNESS),
        "by_source_system": dict(sorted(by_system.items())),
        "sweep_generated_at": searches.get("generated_at"),
        "oldest_retrieved_at": min(times).isoformat() if times else None,
        "newest_retrieved_at": max(times).isoformat() if times else None,
    }


def _audit_horizon_bank(path: str) -> Optional[dict]:
    """Secondary table: Horizon fact-bank rows have their own v2 retrieval
    machinery; count legacy rows without a bank retrieval time, change nothing."""
    try:
        with open(path, encoding="utf-8") as f:
            bank = (json.load(f) or {}).get("fact_bank") or []
    except (OSError, ValueError):
        return None
    if not isinstance(bank, list):
        return None
    return {
        "rows": len(bank),
        "with_retrieved_at": sum(
            1 for r in bank if isinstance(r, dict) and r.get("retrieved_at")),
    }


def audit(cleaned_dir: Optional[str] = None,
          review_dir: Optional[str] = None) -> dict:
    """Rebuild each stored sweep's FactPack through the provenance derivation
    and report recovered vs UNKNOWN_FRESHNESS counts. Read-only."""
    cleaned = cleaned_dir or os.path.join(_ROOT, "data", "cleaned")
    review = review_dir or os.path.join(_ROOT, "data", "review")
    report: dict = {"sweeps": {}, "horizon_banks": {}, "errors": {}}
    for name in sorted(os.listdir(cleaned) if os.path.isdir(cleaned) else []):
        m = _SWEEP_RE.match(name)
        if not m:
            continue
        path = os.path.join(cleaned, name)
        try:
            with open(path, encoding="utf-8") as f:
                searches = json.load(f)
            client = searches.get("client") or m.group("stem")
            report["sweeps"][name] = _audit_pack(client, searches)
        except Exception as e:  # noqa: BLE001 — audit every artifact it can, name the rest
            report["errors"][name] = f"{type(e).__name__}: {e}"
    for name in sorted(os.listdir(review) if os.path.isdir(review) else []):
        if not name.endswith(".horizon.json"):
            continue
        row = _audit_horizon_bank(os.path.join(review, name))
        if row is not None:
            report["horizon_banks"][name] = row
    totals = {
        "facts": sum(s["facts"] for s in report["sweeps"].values()),
        "backfilled": sum(s["backfilled"] for s in report["sweeps"].values()),
        "unknown_freshness": sum(
            s["unknown_freshness"] for s in report["sweeps"].values()),
    }
    report["totals"] = totals
    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true",
                    help="emit the raw JSON report only")
    args = ap.parse_args(argv)
    # entity-resolution isolation (parity pattern): pack rebuilds must not
    # grow the operator's unresolved.log; the audit is observational
    prior = os.environ.get("LILA_ENTITIES_DIR")
    with tempfile.TemporaryDirectory(prefix="lila-freshness-audit-") as tmp:
        os.environ["LILA_ENTITIES_DIR"] = tmp
        try:
            report = audit()
        finally:
            if prior is None:
                os.environ.pop("LILA_ENTITIES_DIR", None)
            else:
                os.environ["LILA_ENTITIES_DIR"] = prior
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    t = report["totals"]
    print(f"facts across stored sweeps: {t['facts']} · "
          f"backfilled retrieved_at: {t['backfilled']} · "
          f"UNKNOWN_FRESHNESS: {t['unknown_freshness']}")
    for name, s in report["sweeps"].items():
        print(f"  {name}: {s['backfilled']}/{s['facts']} backfilled "
              f"(sweep generated_at {s['sweep_generated_at'] or 'ABSENT'}; "
              f"oldest figure {s['oldest_retrieved_at'] or 'n/a'})")
    for name, s in report["horizon_banks"].items():
        print(f"  {name}: {s['with_retrieved_at']}/{s['rows']} horizon rows "
              f"carry a bank retrieval time")
    for name, err in report["errors"].items():
        print(f"  ERROR {name}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
