"""Deep research sweep for evidence-pack expansion (2026-08-20).

Reads clients/<slug>/deep_frame.json (operator-ordered vocabulary plus
buyer-language statements), runs the guarded hybrid retrieval over the
notice store, screens every materialized forecast store with the same
terms, and emits NORMALIZED pack-shaped rows (L1_notice / L4_forecast)
with canonical source URLs and store-verified status fields, ready for
evidence_pack_v2 assembly. Zero LLM; the classification happens
downstream in evidence_route, never here.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = _ROOT / "data" / "state" / "retrieval"
K = 400


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _notice_row(r: dict, store_conn) -> Optional[dict]:
    rid = r.get("notice_id")
    db = store_conn.execute(
        "SELECT notice_id, title, notice_type, agency, subtier, office, "
        "posted, deadline, naics, psc, set_aside, url, active, "
        "description_prefix, poc_name, poc_email, poc_phone, "
        "poc_secondary_email FROM notices WHERE notice_id = ?",
        (rid,)).fetchone()
    if db is None:
        return None
    return {
        "lane": "L1_notice",
        "record_id": db["notice_id"],
        "title": db["title"],
        "notice_type": db["notice_type"],
        "agency": db["agency"],
        "sub_agency": db["subtier"],
        "office": db["office"],
        "posted_date": db["posted"],
        "response_deadline": db["deadline"],
        "naics": db["naics"],
        "psc": db["psc"],
        "set_aside": db["set_aside"],
        "status_active_flag": db["active"],
        "url": f"https://sam.gov/opp/{db['notice_id']}/view",
        "description": (db["description_prefix"] or "")[:1200],
        "contact_name": db["poc_name"],
        "contact_email": db["poc_email"],
        "contact_phone": db["poc_phone"],
        "contact_secondary_email": db["poc_secondary_email"],
        "relevance_matched": r.get("matched_term") or "",
        "match_route": r.get("match_route"),
        "rrf_score": r.get("rrf_score"),
        "in_code_boundary": r.get("in_code_boundary"),
        "matched_sentence": r.get("matched_sentence"),
        "retrieved_at": _now_iso(),
        "source": "deep_sweep.hybrid",
    }


def run_deep_sweep(slug: str, client_name: str, *,
                   k: int = K, root: Optional[Path] = None) -> tuple[Path, dict]:
    from tools.frame_rescreen import rescreen_forecasts
    from tools.notice_store import connect
    from tools.retrieval.hybrid import hybrid_retrieve

    base = Path(root) if root else _ROOT
    frame_spec = json.loads(
        (base / "clients" / slug / "deep_frame.json"
         ).read_text(encoding="utf-8"))
    frame = {
        "client_name": client_name,
        "as_ordered": {"tier1": [client_name],
                       "tier2": list(frame_spec.get("tier2") or []),
                       "tier3": []},
        "screen_routing": {
            "tier1_client_names": [client_name],
            "tier1_rival_names": [],
            "tier1_reseller_names": [],
        },
    }
    conn = connect()
    try:
        hy = hybrid_retrieve(
            frame, k=k,
            capability_statements=list(frame_spec.get("statements") or []),
            store_conn=conn)
        rows: list[dict] = []
        for r in hy["rows"]:
            normalized = _notice_row(r, conn)
            if normalized is not None:
                rows.append(normalized)

        forecast_keeps, forecast_receipt = rescreen_forecasts(frame)
        for f in forecast_keeps:
            rows.append({
                "lane": "L4_forecast",
                "record_id": f"forecast:{f.get('source')}:{f.get('source_id')}",
                "title": f.get("title"),
                "agency": f.get("agency"),
                "sub_agency": f.get("component"),
                "estimated_value_range": f.get("estimated_value_range"),
                "anticipated_solicitation": f.get("anticipated_solicitation"),
                "anticipated_award": f.get("anticipated_award"),
                "fiscal_year": f.get("fiscal_year"),
                "window_state": f.get("window_state"),
                "set_aside": f.get("set_aside"),
                "url": f.get("url"),
                "relevance_matched": f.get("matched_term"),
                "matched_sentence": f.get("matched_sentence"),
                "retrieved_at": _now_iso(),
                "source": f"deep_sweep.forecast:{f.get('source')}",
            })

        receipt = {
            "frame_terms": len(frame["as_ordered"]["tier2"]),
            "statements": len(frame_spec.get("statements") or []),
            "hybrid": {k2: v for k2, v in hy["receipt"].items()
                       if k2 != "rejected_ids"},
            "notices_normalized": sum(
                1 for r in rows if r["lane"] == "L1_notice"),
            "forecast_keeps": sum(
                1 for r in rows if r["lane"] == "L4_forecast"),
            "forecast_sources": forecast_receipt.get("sources"),
            "guard_rejections": hy["receipt"]["guard_rejected"],
            "at": _now_iso(),
        }
        payload = {"slug": slug, "records": rows, "receipt": receipt}
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"deep_sweep_{slug}.json"
        from tools.artifacts import atomic_write_json
        atomic_write_json(out, payload)
        return out, payload
    finally:
        conn.close()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("-k", type=int, default=K)
    args = ap.parse_args(argv)
    out, payload = run_deep_sweep(
        args.client, args.name or args.client, k=args.k)
    print(json.dumps(payload["receipt"], indent=1, default=str))
    print(f"WROTE {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
