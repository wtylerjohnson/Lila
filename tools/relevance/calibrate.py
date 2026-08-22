"""Calibration harness: the engine versus the stored pipeline, advisory only.

Runs the relevance engine across stored sweeps and emits ONE human-review
CSV of disagreements: pipeline-included records scoring below threshold
(false-positive candidates) and record-level hits the pipeline missed
above threshold (false-negative candidates). Every row carries matched
spans verbatim, or their absence. Nothing is removed from any stored
artifact; the CSV is advisory until the human adjudicates (the
append-only seam in adjudicate.py).

    .venv/bin/python -m tools.relevance.calibrate [--client NAME ...]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from typing import Optional

from tools.relevance.engine import (
    RELEVANCE_THRESHOLD,
    score_record,
)
from tools.relevance.taxonomy import derived_taxonomy, load_taxonomy

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _spans_cell(verdict, limit: int = 4) -> str:
    if not verdict.spans:
        return "(no capability span)"
    return " || ".join(
        f"[{s.tier}] {s.context.strip()}" for s in verdict.spans[:limit])


def _pipeline_included(record: dict, triage: dict,
                       content_ids: set[str]) -> tuple[bool, str]:
    rid = str(record.get("source_id") or record.get("award_id") or "")
    verdict = (triage.get(rid) or {}).get("verdict") if rid else None
    if verdict in ("pursue", "monitor"):
        return True, f"triage:{verdict}"
    if rid and rid in content_ids:
        return True, "board-content"
    # capability-matched means CLIENT capability terms hit, never competitor
    # product tags (matched_products marks incumbent tooling, e.g. GitLab)
    if record.get("matched_terms") or (record.get("capability_score") or 0) >= 1:
        return True, "capability-matched"
    if verdict == "discard":
        return False, "triage:discard"
    return False, "untriaged"


def iter_records(sweep: dict):
    """Yield the canonical relevance-calibration record population."""
    results = sweep.get("results") or {}
    for n in results.get("sam.gov") or []:
        if isinstance(n, dict):
            yield "notice", n
    for lane in results.get("usaspending.gov") or []:
        if isinstance(lane, dict):
            for a in lane.get("awards") or []:
                if isinstance(a, dict):
                    yield "award", a
    for r in results.get("award_repulls") or []:
        if isinstance(r, dict):
            yield "repull", r
    bm = results.get("incumbent_buyer_map") or {}
    for b in bm.get("buyers") or []:
        for rec in (b.get("records") or []):
            if isinstance(rec, dict):
                yield "buyer_map", rec


# Compatibility aliases for callers/tests written before this population and
# scope seam became the public owner used by recurring press snapshots.
_iter_records = iter_records


def _content_ids(client: str) -> set[str]:
    from agents.reports.board_content import load_board_content
    content = load_board_content(
        client, allow_legacy_machine_scale=True)
    if content is None:
        return set()
    return {f.source_record_id for f in content.figures if f.source_record_id}


def resolve_sweep_scope(client: str, sweep: dict):
    """Scope precedence: explicit config, else the sweep's own declared
    gate scope (SWEEP-DERIVED), else None (UNSCOPED). Never guessed."""
    from tools.relevance.scope import EngagementScope, load_engagement_scope
    config = load_engagement_scope(client)
    if config is not None:
        return config, config.resolved_basis
    from tools.relevance.scope_diagnostic import sweep_scope_departments
    depts = sweep_scope_departments(sweep)
    if depts:
        return EngagementScope(departments=sorted(depts)), "SWEEP-DERIVED"
    return None, "UNSCOPED"


_sweep_scope = resolve_sweep_scope


def calibrate_client(client: str, sweep: dict) -> dict:
    taxonomy = load_taxonomy(client)
    derived = False
    if taxonomy is None:
        taxonomy = derived_taxonomy(client)
        derived = True
    if taxonomy is None:
        return {"client": client, "skipped": "no taxonomy or profile"}
    scope, scope_basis = resolve_sweep_scope(client, sweep)
    triage = (sweep.get("results") or {}).get("triage") or {}
    content_ids = _content_ids(client)
    rows, fp, fn, off_scope_rows = [], [], [], []
    seen_refs: set[str] = set()
    for kind, record in iter_records(sweep):
        ref = str(record.get("source_id") or record.get("award_id")
                  or record.get("url") or "")[:80]
        if not ref or ref in seen_refs:
            continue
        seen_refs.add(ref)
        verdict = score_record(record, taxonomy, engagement_scope=scope)
        included, status = _pipeline_included(record, triage, content_ids)
        title = str(record.get("title") or record.get("description")
                    or record.get("recipient") or "")[:90]
        row = {
            "client": client, "record_ref": ref, "kind": kind,
            "title": title,
            "pipeline_status": status, "engine_score": verdict.score,
            "relevant": verdict.relevant, "off_code": verdict.off_code,
            "core_terms": "|".join(verdict.core_terms),
            "adjacent_terms": "|".join(verdict.adjacent_terms),
            "matched_spans": _spans_cell(verdict),
            "taxonomy": f"v{taxonomy.version}"
                        + (" DERIVED" if derived else ""),
            "scope_basis": verdict.scope_basis
                           if scope is not None else "UNSCOPED",
        }
        if verdict.off_scope:
            # out-of-scope rows never enter FP/FN math: separate section,
            # would-be score visible, signal flag when strong
            row["disagreement"] = ("off_scope_signal"
                                   if verdict.off_scope_signal
                                   else "out_of_scope")
            row["would_be_score"] = verdict.score
            off_scope_rows.append(row)
            rows.append(row)
            continue
        if included and verdict.score < RELEVANCE_THRESHOLD:
            row["disagreement"] = "false_positive_candidate"
            fp.append(row)
        elif not included and verdict.relevant:
            row["disagreement"] = "false_negative_candidate"
            fn.append(row)
        else:
            continue
        rows.append(row)
    return {"client": client, "derived_taxonomy": derived,
            "records_scored": len(seen_refs),
            "scope_basis": scope_basis,
            "fp_candidates": fp, "fn_candidates": fn,
            "off_scope_rows": off_scope_rows, "rows": rows}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", action="append",
                    help="limit to these clients (default: every stored sweep)")
    ap.add_argument("--out", help="CSV path (default data/state/relevance/)")
    args = ap.parse_args(argv)

    all_rows, summaries = [], []
    for path in sorted(glob.glob(os.path.join(_ROOT, "data", "cleaned",
                                              "searches_*.json"))):
        with open(path, encoding="utf-8") as f:
            sweep = json.load(f)
        client = sweep.get("client") or os.path.basename(path)
        if args.client and client not in args.client:
            continue
        result = calibrate_client(client, sweep)
        if result.get("skipped"):
            print(f"[calibrate] {client}: SKIPPED ({result['skipped']})",
                  file=sys.stderr)
            continue
        summaries.append(result)
        all_rows.extend(result["rows"])
        print(f"[calibrate] {client}: {result['records_scored']} records · "
              f"scope {result['scope_basis']} · "
              f"{len(result['fp_candidates'])} FP · "
              f"{len(result['fn_candidates'])} FN · "
              f"{len(result['off_scope_rows'])} out-of-scope"
              + (" · DERIVED taxonomy" if result["derived_taxonomy"] else ""),
              file=sys.stderr)

    out = args.out or os.path.join(_ROOT, "data", "state", "relevance",
                                   "calibration.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fields = ["client", "record_ref", "kind", "title", "pipeline_status",
              "engine_score", "relevant", "off_code", "core_terms",
              "adjacent_terms", "matched_spans", "taxonomy", "scope_basis",
              "disagreement", "would_be_score"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"[calibrate] {len(all_rows)} disagreement rows -> {out} "
          f"(advisory; no stored artifact was changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
