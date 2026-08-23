"""Measure it or it didn't happen (R1 item 4, 2026-08-18).

Runs a client frame through the old keyword lane (frame_rescreen keeps)
and through hybrid_retrieve, and reports: candidate counts old vs new,
overlap, what hybrid found that keywords missed (top 25 new rows with
title, agency, matched sentence for hand adjudication), what hybrid
ranks below 200 that keywords kept, the named must-survive and must-die
fixtures, and a seeded 100-row random sample of everything the fused
pipeline rejected, for hand review.

Frames for Riverbed and Red Hat are DERIVED from their approved review
packets, mapping documented here one place: tier 1 = research entities
(products + competitors), tier 2 = capability and technology keywords,
tier 3 = empty (procurement lenses like agency and set-aside never enter
text matching, per the keyword doctrine). Capability statements ride the
capability profile's summary when one exists, else the packet keywords
joined as one statement.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = _ROOT / "data" / "state" / "retrieval"
BELOW_RANK = 200
NEW_ROWS_PRINTED = 25
REJECT_SAMPLE = 100
SAMPLE_SEED = 20260818

FIXTURES = {
    "riverbed": {
        "must_survive": {},
        "must_die": {
            "innovasea_trout": ["a13afa108c9d45b784a96c8801a8eb33"],
            "sel2731_substation": ["97ad88c8e78340488386bc9b6f020333"],
            "lab_middleware_set": [
                "5963aff308e54a9c8aa43a8b8c2ccee2",
                "6985e73dfecb447c9f098663e46094ec",
                "d85a9c6357394e37a427b50645e97dd5",
                "63a541e3bef742b18f5890867ba06687",
                "38b2dcdadc524da2ba547c335d9f5848",
                "d52c7d2c05e2474e8d0381f7cd05099e",
            ],
        },
    },
    "red_hat": {
        "must_survive": {
            "nasa_unno": ["6ea23246575a46f0bdcd254ba78dd780",
                          "0f08be613b714a219397f8aa2d03a4a3"],
            "abacus": ["87d63170a35f45acb11861b890a23556",
                       "1ac53954770b47ce8db9ad86c04d79df",
                       "74edd651aca84c8f9d751bd9b492b7ad"],
            "nih_rocky_linux": ["17c73aaadac6401e80548c504d6fc31b"],
            "iatdr_pair": ["7c0048186846424eaed435ecb78aa2ba",
                           "a72429c43b2c45669697061f813e8945",
                           "f58acf087ee34660b9e55e32f26bf80f"],
        },
        # Pinned from the operator's Jul 28 descriptions (R2 item 1):
        # Smartsheet at DoD; INDE FedRAMP AV at Interior NAICS 334310
        # (INDE = the Park Service code for Independence NHP, an AV kiosk
        # buy carrying Carahsoft and FedRAMP language); ServiceNow order
        # at DoD. All three are Carahsoft resale co-occurrence, never Red
        # Hat opportunity evidence.
        "must_die": {
            "carahsoft_cooccurrence": [
                "cd4847ed427a45c7897c372a61de794d",
                "05669f815f1f42b9822fd58dc488550e",
                "286e24b1c7f748b49c470ac7cc0c5eaa",
            ],
        },
    },
}

#: Witnessed CO-language terms from the operator's Jul 28 record: UNNO
#: matched "network management", IATDR matched "infrastructure
#: automation", both through CO frames. Operator facts publish by
#: construction.
CO_WITNESSED = {
    "riverbed": ["network management"],
    # The operator's Jul 28 record names both matches through the CO
    # frames of the Red Hat dry run: UNNO on network management, IATDR
    # on infrastructure automation. Both terms ride red_hat's CO frame.
    "red_hat": ["infrastructure automation", "network management"],
}

#: Harness-derived demand-language for a client with no taxonomy file,
#: disclosed as derived (never operator-approved vocabulary): the buyer
#: names the need, not the brand.
CO_DERIVED = {
    "red_hat": [
        "enterprise Linux", "Linux operating system",
        "open source software", "container platform", "containerization",
        "configuration management", "IT automation", "middleware",
        "virtualization platform", "operating system licenses",
    ],
}


def co_frame(slug: str, vendor_frame: dict) -> dict:
    """The CO (buyer-language) frame: same entities, demand-side tier 2.

    Source order: the client's capability taxonomy where one exists
    (buyer phrasings, minus exact brand terms, which stay tier 1), else
    the disclosed derived list; the operator-witnessed Jul 28 terms ride
    in either way.
    """
    tier1 = list(vendor_frame["as_ordered"]["tier1"])
    tier1_lower = {t.casefold() for t in tier1}
    terms: list[str] = []
    tax_path = _ROOT / "clients" / slug / "capability_taxonomy.json"
    source = "derived"
    if tax_path.exists():
        tax = json.loads(tax_path.read_text(encoding="utf-8"))
        for band in ("core", "adjacent"):
            for entry in tax.get(band) or []:
                term = str(entry.get("term") or "").strip()
                if term and term.casefold() not in tier1_lower:
                    terms.append(term)
        source = "capability_taxonomy.json"
    else:
        terms = list(CO_DERIVED.get(slug, []))
    for witnessed in CO_WITNESSED.get(slug, []):
        if witnessed.casefold() not in {t.casefold() for t in terms}:
            terms.append(witnessed)
    return {
        "client_name": vendor_frame["client_name"],
        "frame_variant": "co",
        "co_terms_source": source,
        "as_ordered": {"tier1": tier1, "tier2": terms, "tier3": []},
        "screen_routing": dict(vendor_frame["screen_routing"]),
    }


def frame_from_packet(slug: str, review_dir: Optional[Path] = None) -> dict:
    review_dir = Path(review_dir) if review_dir else _ROOT / "data" / "review"
    packet = json.loads(
        (review_dir / f"{slug}.review.json").read_text(encoding="utf-8"))
    strategy = packet.get("strategy") or {}
    products, rivals, resellers = [], [], []
    for ent in strategy.get("research_entities") or []:
        kind = str(ent.get("kind") or "")
        name = str(ent.get("name") or "").strip()
        if not name:
            continue
        if kind == "product":
            products.append(name)
        elif kind == "competitor":
            rivals.append(name)
        elif kind == "reseller":
            resellers.append(name)
    client = str(packet.get("client_name") or slug)
    entities_source = "packet"
    if not products and not rivals:
        # ENTITY BRIDGE, same seam the golden press discloses: a packet
        # approved before Phase 1 carries no research entities; the dev
        # research artifact supplies them when present.
        bridge = _ROOT / "data" / "state" / "golden_build" / (
            f"{slug}.research.raw.json")
        if bridge.exists():
            raw = json.loads(bridge.read_text(encoding="utf-8"))
            for ent in ((raw.get("strategy") or {})
                        .get("research_entities") or []):
                kind = str(ent.get("kind") or "")
                name = str(ent.get("name") or "").strip()
                if not name:
                    continue
                if kind == "product":
                    products.append(name)
                elif kind == "competitor":
                    rivals.append(name)
                elif kind == "reseller":
                    resellers.append(name)
            entities_source = "golden_build research artifact (bridge)"
    tier2 = []
    for kw in strategy.get("keywords") or []:
        if str(kw.get("category")) in ("capability", "technology"):
            term = str(kw.get("term") or "").strip()
            if term and term not in tier2:
                tier2.append(term)
    return {
        "client_name": client,
        "as_ordered": {"tier1": [client] + products + rivals,
                       "tier2": tier2, "tier3": []},
        "screen_routing": {
            "tier1_client_names": [client] + products,
            "tier1_rival_names": rivals,
            "tier1_reseller_names": resellers,
            "entities_source": entities_source,
        },
    }


def _entity_statements(slug: str) -> list[str]:
    """Grounded per-product prose: '<name>: <rationale>' from the packet
    entities (or the bridge artifact), so max-over-queries can catch a
    semantically on-point notice that shares no keyword with the frame
    (the IATDR class: infrastructure automation with zero product names).
    """
    sources = [
        _ROOT / "data" / "review" / f"{slug}.review.json",
        _ROOT / "data" / "state" / "golden_build" / (
            f"{slug}.research.raw.json"),
    ]
    out: list[str] = []
    for path in sources:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        strategy = payload.get("strategy") or {}
        for ent in strategy.get("research_entities") or []:
            if str(ent.get("kind")) != "product":
                continue
            name = str(ent.get("name") or "").strip()
            rationale = str(ent.get("rationale") or "").strip()
            if name and rationale:
                out.append(f"{name}: {rationale}"[:300])
        if out:
            break
    return out[:8]


def capability_statements(slug: str, frame: dict) -> list[str]:
    statements: list[str] = []
    profile_path = _ROOT / "clients" / slug / "profile.json"
    if profile_path.exists():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        summary = str(profile.get("capability_summary") or "").strip()
        if summary:
            statements.append(summary)
    if not statements:
        packet_path = _ROOT / "data" / "review" / f"{slug}.review.json"
        if packet_path.exists():
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            prose = str((packet.get("strategy") or {})
                        .get("pursuit_strategy") or "").strip()
            if prose:
                # real capability prose beats a comma soup of keywords
                statements.append(prose[:1200])
    statements += _entity_statements(slug)
    return statements or [", ".join(frame["as_ordered"]["tier2"][:16])]


def old_lane_keeps(frame: dict, store_conn) -> list[dict]:
    from tools.frame_rescreen import rescreen_store
    keeps, _receipt = rescreen_store(frame, conn=store_conn)
    return keeps


def run_measurement(slug: str, *, k: int = 500,
                    store_conn=None, sidecar=None,
                    encoder=None) -> dict:
    from tools.notice_store import connect
    from tools.retrieval.hybrid import hybrid_retrieve

    owned = store_conn is None
    if store_conn is None:
        store_conn = connect()
    try:
        vendor = frame_from_packet(slug)
        vendor["frame_variant"] = "vendor"
        frames = {"vendor": vendor, "co": co_frame(slug, vendor)}
        statements = capability_statements(slug, vendor)
        per_frame: dict[str, dict] = {}
        old_by_id: dict[str, dict] = {}
        union_rank: dict[str, int] = {}
        union_rows: dict[str, dict] = {}
        rejected_union: dict[str, dict] = {}
        for name, frame in frames.items():
            f_old = old_lane_keeps(frame, store_conn)
            f_hy = hybrid_retrieve(frame, k=k,
                                   capability_statements=statements,
                                   store_conn=store_conn, sidecar=sidecar,
                                   encoder=encoder)
            f_rank = {r["notice_id"]: i + 1
                      for i, r in enumerate(f_hy["rows"])}
            per_frame[name] = {
                "old": f_old, "old_ids": {r["notice_id"] for r in f_old},
                "rows": f_hy["rows"], "rank": f_rank,
                "receipt": f_hy["receipt"],
            }
            for r in f_old:
                old_by_id.setdefault(r["notice_id"], r)
            for r in f_hy["rows"]:
                nid = r["notice_id"]
                rank = f_rank[nid]
                if nid not in union_rank or rank < union_rank[nid]:
                    union_rank[nid] = rank
                    union_rows[nid] = dict(r, frame_variant=name)
            for rej in f_hy["receipt"]["rejected_ids"]:
                rejected_union.setdefault(rej["notice_id"],
                                          dict(rej, frame_variant=name))
        old = list(old_by_id.values())
        old_ids = set(old_by_id)
        rows = sorted(union_rows.values(),
                      key=lambda r: union_rank[r["notice_id"]])
        rank_of = union_rank
        hy_ids = set(rank_of)
        hy = {"rows": rows, "receipt": {
            "frames": {name: dict(pf["receipt"], rejected_ids="elided")
                       for name, pf in per_frame.items()},
            "rejected_ids": list(rejected_union.values()),
        }}

        new_rows = [r for r in rows if r["notice_id"] not in old_ids]
        kept_but_low = []
        for r in old:
            rank = rank_of.get(r["notice_id"])
            if rank is None or rank > BELOW_RANK:
                kept_but_low.append({
                    "notice_id": r["notice_id"], "title": r["title"],
                    "agency": r.get("agency"),
                    "matched_term": r.get("matched_term"),
                    "hybrid_rank": rank,
                })

        fixtures = FIXTURES.get(slug, {"must_survive": {}, "must_die": {}})
        survive = {}
        for name, ids in fixtures["must_survive"].items():
            survive[name] = {
                nid: {"union": rank_of.get(nid),
                      **{fname: pf["rank"].get(nid)
                         for fname, pf in per_frame.items()}}
                for nid in ids}
        die = {}
        rejected_ids = {r["notice_id"]: r
                        for r in hy["receipt"]["rejected_ids"]}
        for name, ids in fixtures["must_die"].items():
            die[name] = {}
            for nid in ids:
                if nid in rank_of:
                    die[name][nid] = f"ALIVE at rank {rank_of[nid]}"
                elif nid in rejected_ids:
                    die[name][nid] = ("guard-rejected: "
                                      + rejected_ids[nid]["reason"])
                else:
                    die[name][nid] = "dead (never fused into top pool)"

        pool = list(rejected_ids.values())
        rng = random.Random(SAMPLE_SEED)
        sample = pool if len(pool) <= REJECT_SAMPLE else rng.sample(
            pool, REJECT_SAMPLE)
        reject_sample = []
        for r in sample:
            db = store_conn.execute(
                "SELECT title, agency FROM notices WHERE notice_id=?",
                (r["notice_id"],)).fetchone()
            reject_sample.append({
                "notice_id": r["notice_id"],
                "reason": r["reason"],
                "detail": str(r["detail"])[:120],
                "title": (db[0] if db else None),
                "agency": (db[1] if db else None),
            })

        report = {
            "slug": slug,
            "k": k,
            "frames": {
                name: {"tier1": len(f["as_ordered"]["tier1"]),
                       "tier2": len(f["as_ordered"]["tier2"]),
                       "old_candidates": len(per_frame[name]["old"]),
                       "hybrid_candidates": len(per_frame[name]["rows"]),
                       "co_terms_source": f.get("co_terms_source")}
                for name, f in frames.items()},
            "capability_statements": len(statements),
            "old_candidates": len(old),
            "hybrid_candidates": len(rows),
            "overlap": len(old_ids & hy_ids),
            "new_in_hybrid": len(new_rows),
            "old_missing_or_below_rank": len(kept_but_low),
            "receipt": hy["receipt"] | {"rejected_ids": "elided"},
            "new_rows_top": [{
                "rank": rank_of[r["notice_id"]],
                "notice_id": r["notice_id"],
                "title": r["title"],
                "agency": r["agency"],
                "match_route": r["match_route"],
                "matched_term": r["matched_term"],
                "in_code_boundary": r["in_code_boundary"],
                "rrf_score": r["rrf_score"],
                "matched_sentence": r["matched_sentence"][:240],
            } for r in new_rows[:NEW_ROWS_PRINTED]],
            "old_kept_hybrid_low": kept_but_low[:40],
            "must_survive": survive,
            "must_die": die,
            "reject_sample": reject_sample,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"measure_{slug}.json"
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False),
                       encoding="utf-8")
        report["_path"] = str(out)
        return report
    finally:
        if owned:
            store_conn.close()


def _print_report(r: dict) -> None:
    print(f"== {r['slug']}  old={r['old_candidates']} "
          f"hybrid={r['hybrid_candidates']} overlap={r['overlap']} "
          f"new={r['new_in_hybrid']} "
          f"old_below_{BELOW_RANK}_or_gone={r['old_missing_or_below_rank']}")
    print(f"   receipt: {json.dumps(r['receipt'])}")
    print("   MUST SURVIVE:")
    for name, ids in r["must_survive"].items():
        print(f"     {name}: " + ", ".join(
            f"{nid[:8]}=rank {rank}" if rank else f"{nid[:8]}=MISSING"
            for nid, rank in ids.items()))
    print("   MUST DIE:")
    for name, ids in r["must_die"].items():
        for nid, verdict in ids.items():
            print(f"     {name} {nid[:8]}: {verdict}")
    print(f"   TOP NEW ROWS ({len(r['new_rows_top'])}):")
    for row in r["new_rows_top"]:
        print(f"     #{row['rank']:<4} [{row['match_route']}] "
              f"{str(row['agency'] or '')[:24]:<24} {row['title'][:66]}")
        print(f"        >> {row['matched_sentence'][:170]}")
    print(f"   artifact: {r['_path']}")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slugs", default="riverbed,red_hat")
    ap.add_argument("-k", type=int, default=500)
    args = ap.parse_args(argv)
    for slug in [s.strip() for s in args.slugs.split(",") if s.strip()]:
        report = run_measurement(slug, k=args.k)
        _print_report(report)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
