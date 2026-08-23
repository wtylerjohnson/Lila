"""Query by example over the dense sidecar (R2 item 2, 2026-08-18).

The question this answers: does dense retrieval work when the query is a
NOTICE rather than a frame? Each fixture notice's own stored passage
vector queries the full sidecar by doc-to-doc cosine (no instruction
prefix: this is symmetric similarity, not asymmetric search). Reported:
the rank of every other fixture in each fixture's neighbor list, with
same-family amendment siblings separated from cross-family finds (a
sibling at rank 1 is a sanity check, not a discovery), the top-k
neighbors per query for hand adjudication, and one number to lead with:
the mean cross-family rank of the other fixtures when each fixture is
the query.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = _ROOT / "data" / "state" / "retrieval" / "qbe_probe.json"
NEIGHBORS_PRINTED = 25

#: The four fixtures plus Rocky, keyed by family so amendment siblings
#: count as sanity checks rather than discoveries.
FIXTURES = {
    "UNNO": {"query": "6ea23246575a46f0bdcd254ba78dd780",
             "family": ["6ea23246575a46f0bdcd254ba78dd780",
                        "0f08be613b714a219397f8aa2d03a4a3"]},
    "IATDR": {"query": "7c0048186846424eaed435ecb78aa2ba",
              "family": ["7c0048186846424eaed435ecb78aa2ba",
                         "a72429c43b2c45669697061f813e8945",
                         "f58acf087ee34660b9e55e32f26bf80f"]},
    "Abacus": {"query": "87d63170a35f45acb11861b890a23556",
               "family": ["87d63170a35f45acb11861b890a23556",
                          "1ac53954770b47ce8db9ad86c04d79df",
                          "74edd651aca84c8f9d751bd9b492b7ad"]},
    "DTRA DevOps": {"query": "445e92e49f7f4b418ab3c5b4b341317e",
                    "family": ["445e92e49f7f4b418ab3c5b4b341317e"]},
    "Rocky Linux": {"query": "17c73aaadac6401e80548c504d6fc31b",
                    "family": ["17c73aaadac6401e80548c504d6fc31b"]},
}


def probe(k: int = 200, *, sidecar=None, store_conn=None) -> dict:
    import numpy as np

    from tools.retrieval import dense as dense_mod

    owned_sidecar = sidecar is None
    if sidecar is None:
        sidecar = dense_mod.open_sidecar()
    owned_store = store_conn is None
    if store_conn is None:
        from tools.notice_store import connect
        store_conn = connect()
    try:
        ids, matrix = dense_mod._load_matrix(sidecar)
        pos = {nid: i for i, nid in enumerate(ids)}
        results: dict = {}
        lead_ranks: list[int] = []
        for name, fx in FIXTURES.items():
            qid = fx["query"]
            if qid not in pos:
                results[name] = {"error": f"{qid} not in sidecar"}
                continue
            qvec = matrix[pos[qid]]
            scores = matrix @ qvec
            order = scores.argsort()[::-1]
            rank_of = {}
            for rank, idx in enumerate(order, start=1):
                rank_of[ids[idx]] = rank
            family = set(fx["family"])
            fixture_ranks = {}
            for other_name, other in FIXTURES.items():
                if other_name == name:
                    continue
                oid = other["query"]
                entry = {"rank": rank_of.get(oid)}
                fixture_ranks[other_name] = entry
                if oid in rank_of and oid not in family:
                    lead_ranks.append(rank_of[oid])
            sibling_ranks = {
                sid: rank_of.get(sid)
                for sid in fx["family"] if sid != qid}
            neighbors = []
            for rank, idx in enumerate(order[:k + 1], start=1):
                nid = ids[idx]
                if nid == qid:
                    continue
                if len(neighbors) >= k:
                    break
                neighbors.append((nid, float(scores[idx]), rank))
            top = []
            for nid, score, rank in neighbors[:NEIGHBORS_PRINTED]:
                row = store_conn.execute(
                    "SELECT title, agency, notice_type FROM notices "
                    "WHERE notice_id = ?", (nid,)).fetchone()
                top.append({
                    "rank": rank, "notice_id": nid,
                    "cosine": round(score, 4),
                    "family_sibling": nid in family,
                    "title": (row[0] if row else None),
                    "agency": (row[1] if row else None),
                    "notice_type": (row[2] if row else None),
                })
            results[name] = {
                "query_id": qid,
                "fixture_ranks": fixture_ranks,
                "sibling_ranks": sibling_ranks,
                "top_neighbors": top,
            }
        cross = [r for r in lead_ranks if r is not None]
        payload = {
            "corpus_rows": len(ids),
            "mean_cross_family_fixture_rank": (
                round(sum(cross) / len(cross), 1) if cross else None),
            "cross_family_ranks_all": sorted(cross),
            "cross_family_in_top_50": sum(1 for r in cross if r <= 50),
            "results": results,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=1,
                                       ensure_ascii=False),
                            encoding="utf-8")
        return payload
    finally:
        if owned_sidecar:
            sidecar.close()
        if owned_store:
            store_conn.close()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-k", type=int, default=200)
    args = ap.parse_args(argv)
    payload = probe(k=args.k)
    print(f"corpus={payload['corpus_rows']:,} "
          f"MEAN CROSS-FAMILY FIXTURE RANK="
          f"{payload['mean_cross_family_fixture_rank']} "
          f"(in top 50: {payload['cross_family_in_top_50']} of "
          f"{len(payload['cross_family_ranks_all'])})")
    print("cross-family ranks:", payload["cross_family_ranks_all"])
    for name, res in payload["results"].items():
        if "error" in res:
            print(f"== {name}: {res['error']}")
            continue
        print(f"== {name} (query {res['query_id'][:8]})")
        print("   fixture ranks:", {k: v["rank"]
                                    for k, v in res["fixture_ranks"].items()})
        print("   sibling ranks:", {k[:8]: v
                                    for k, v in res["sibling_ranks"].items()})
        for n in res["top_neighbors"]:
            tag = "SIBLING" if n["family_sibling"] else n["notice_type"] or ""
            print(f"   #{n['rank']:<4} {n['cosine']:.3f} "
                  f"{str(n['agency'] or '')[:22]:<22} "
                  f"{str(n['title'] or '')[:60]} {tag}")
    print(f"artifact: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
