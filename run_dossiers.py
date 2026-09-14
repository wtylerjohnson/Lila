#!/usr/bin/env python3
"""Deadline-aware depth: pursuit dossiers for the pursue-grade shortlist.

    python3 run_dossiers.py --client "Recorded Future" [--budget 8]

Requires a searched artifact with Anthropic triage verdicts. Spends AT MOST
--budget metered SAM calls (LILA_DEEP_BUDGET, default 8) fetching full notice
text, most urgent deadlines first, reusing verified description custody;
then writes one combined dossier artifact the control room surfaces.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()


def _source_depth_record(notice_id: str, source_url: str, depth: dict) -> dict:
    """Persist acquisition, lifecycle and migration custody beside dossier analysis."""
    return {
        "notice_id": notice_id,
        "source_url": source_url,
        "retrieved_at": depth.get("retrieved_at"),
        "description": depth.get("description"),
        "attachments": depth.get("attachments"),
        "withdrawn_attachments": depth.get("withdrawn_attachments") or [],
        "depth_schema": depth.get("depth_schema"),
        "description_capture": depth.get("description_capture"),
        "resources_capture": depth.get("resources_capture"),
        "migration": depth.get("migration"),
        "description_checked": bool(depth.get("description_checked")),
        "resources_checked": bool(depth.get("resources_checked")),
        "resources_schema": depth.get("resources_schema"),
        "attachment_inventory_count": depth.get(
            "attachment_inventory_count"),
        "attachment_inventory_hash": depth.get(
            "attachment_inventory_hash"),
        "errors": depth.get("errors") or [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--budget", type=int,
                    default=int(os.environ.get("LILA_DEEP_BUDGET", "8")))
    args = ap.parse_args()

    from agents.review import load_approved
    strategy = load_approved(args.client)
    strategy_summary = getattr(strategy, "pursuit_strategy", "") or ""

    slug = "".join(c if c.isalnum() else "_" for c in args.client).strip("_").lower()
    from agents.review import sweep_artifact_path
    path = sweep_artifact_path(args.client)  # L19: gate-designated artifact, loud on miss
    if not os.path.exists(path):
        print("[dossier] no search artifact — run the search first.", file=sys.stderr)
        return 1
    with open(path, encoding="utf-8") as f:
        out = json.load(f)
    results = out.get("results") or {}
    sam = results.get("sam.gov")
    verdicts = results.get("triage") or {}
    if not isinstance(sam, list) or not sam:
        print("[dossier] artifact has no notices.", file=sys.stderr)
        return 1
    if not isinstance(verdicts, dict) or verdicts.get("error"):
        print("[dossier] no triage verdicts — run the Anthropic screen first.",
              file=sys.stderr)
        return 1

    from agents.decisions.dossier import (
        compose_dossier, render_dossier_markdown, select_targets,
    )
    from tools.api.sam_notice_detail import fetch_notice_depth
    import tools.api.sam_quota as sam_quota

    targets = select_targets(sam, verdicts, args.budget)
    n_pursue = sum(1 for v in verdicts.values()
                   if isinstance(v, dict) and v.get("verdict") == "pursue")
    if not targets:
        print("[dossier] no pursue-grade notices to deepen.", file=sys.stderr)
        return 1
    print(f"[dossier] {n_pursue} pursue-grade; deepening {len(targets)} "
          f"(budget {args.budget}, most urgent deadlines first)", file=sys.stderr)

    existing_dossiers = results.get("dossiers")
    existing_dossiers = (existing_dossiers
                         if isinstance(existing_dossiers, dict) else {})
    retained_depth: dict[str, dict] = {}
    for key in ("depth_records", "records"):
        for prior in existing_dossiers.get(key) or []:
            if not isinstance(prior, dict):
                continue
            prior_id = str(prior.get("id") or "").strip()
            source_depth = prior.get("source_depth")
            if prior_id and isinstance(source_depth, dict):
                retained_depth.setdefault(
                    prior_id, {"id": prior_id, "source_depth": source_depth})

    sections, index, records = [], [], []
    for i, n in enumerate(targets, 1):
        nid = n.get("source_id") or n.get("id")
        try:
            raw = (n.get("raw_payload")
                   if isinstance(n.get("raw_payload"), dict) else {})
            current_source_time = (
                raw.get("modifiedDate") or raw.get("modified")
                or raw.get("lastUpdatedDate") or n.get("posted_date")
                or raw.get("postedDate") or raw.get("posted"))
            depth = fetch_notice_depth(
                nid, min_retrieved_at=current_source_time)
            src = "cache (free)" if depth.get("from_cache") else "live fetch (1 metered call)"
            print(f"[dossier] {i}/{len(targets)} {nid}: depth via {src}"
                  + (f" · {len(depth.get('attachments') or [])} attachments"
                     if depth.get("attachments") else ""), file=sys.stderr)
            source_depth = _source_depth_record(
                nid, n.get("api_url") or n.get("url"), depth)
            # Persisted even if optional dossier synthesis fails below. One
            # successful metered source fetch must never be discarded because
            # an analysis model is unavailable.
            retained_depth[str(nid)] = {
                "id": nid, "source_depth": source_depth}
            d = compose_dossier(args.client, strategy_summary, n, depth)
            sections.append(render_dossier_markdown(d, n))
            # Preserve the authoritative depth beside the synthesized dossier.
            # The dossier is analysis; source_depth is the audit trail future
            # bid/no-bid and ledger adapters can verify without another model
            # call or another metered SAM description fetch.
            record = json.loads(d.model_dump_json())
            record["source_depth"] = source_depth
            records.append(record)
            index.append({"id": nid, "title": d.title, "fit": d.fit_verdict,
                          "deadline": n.get("response_deadline") or n.get("deadline"),
                          "next_action": d.next_action})
        except Exception as e:  # noqa: BLE001 — one notice never sinks the rest
            print(f"[dossier] {nid} FAILED: {e}", file=sys.stderr)
            index.append({"id": nid, "title": n.get("title"), "error": str(e)})

    header = [
        f"# Pursuit Dossiers — {args.client}",
        f"*Deadline-aware depth on the pursue-grade shortlist · {date.today().isoformat()} · "
        f"{len(sections)} of {n_pursue} pursue-grade deepened (budget {args.budget})*",
        "",
        f"_{sam_quota.summary()}_",
        "", "---", "",
    ]
    review_dir = os.path.join(os.path.dirname(__file__), "data", "review")
    os.makedirs(review_dir, exist_ok=True)
    md_path = os.path.join(review_dir, f"{slug}.dossiers.md")
    with open(md_path, "w") as f:
        f.write("\n".join(header) + "\n\n---\n\n".join(sections))

    depth_records = [retained_depth[key] for key in sorted(retained_depth)]
    out["results"]["dossiers"] = {"built": index, "records": records,
                                  "depth_records": depth_records,
                                  "artifact": md_path,
                                  "as_of": date.today().isoformat()}
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    from tools.artifacts import atomic_write_json
    atomic_write_json(path, out)
    # Creation-free (2026-07-12): refresh an existing strict run only; the
    # cutover pointer is operator-owned (tools/assess_refresh.py).
    from tools.assess_refresh import refresh_current_assess_run_if_active
    print("[assess-ledger] after depth: "
          + refresh_current_assess_run_if_active(args.client, sweep_path=path),
          file=sys.stderr)
    ok = [x for x in index if not x.get("error")]
    print(f"[RESULT] {len(ok)} PURSUIT DOSSIERS BUILT "
          f"({sum(1 for x in ok if x.get('fit') == 'strong_fit')} strong fit)",
          file=sys.stderr)
    print(f"[done] -> {md_path}", file=sys.stderr)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
