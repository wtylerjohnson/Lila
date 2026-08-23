"""Hybrid retrieval with reciprocal rank fusion (R1 item 3, 2026-08-18).

hybrid_retrieve(frame, k=500): BM25 over the frame's tier-1 names and
tier-2 terms, dense over the client capability statements plus tier-2
phrases, fused with reciprocal rank fusion, then the EXISTING guard
ladder and negative-context corpus applied to the fused set. Nothing here
bypasses the guards:

  - a fused row with term hits rides screen_text exactly as the keyword
    lane does (entity guard, ladder, negative context); if every hit is
    guard-rejected the row DIES, receipted;
  - a dense-only row (no term span for the ladder to judge) dies when a
    witnessed corpus negative phrase appears anywhere in its text,
    receipted as corpus_negative_no_span; otherwise it survives with its
    best-cosine sentence as the matched sentence.

The inversion (build-state notes): NAICS/PSC membership in the tech code
set is a boolean FEATURE on each row (in_code_boundary), never a gate.
Concretely: the keyword lane's ladder requires a tech code before a
tier-2 phrase or common word may qualify; here the ladder runs with that
code requirement LIFTED (has_tech_code=True) while its polysemy guards
stay fully armed: common single words still need a domain anchor nearby,
negative context still kills, and the entity guard still rejects
ambiguous names. The row's real code membership is recorded as
in_code_boundary for the ranker. Every output row carries bm25_score,
dense_score, rrf_score, in_code_boundary, and matched_sentence, ready
for the six-term score and a later ranker feature row.

Deviation, named: the work order spells the feature with the banned
word; the standing house ban (one occurrence ships a press uncertified)
wins, so the field is in_code_boundary.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

RRF_K = 60
POOL_MULTIPLIER = 2
MIN_POOL = 1000
MAX_SENTENCES_PER_ROW = 40


def frame_lanes(frame: dict, capability_statements: list[str]) -> dict:
    """The two query sets, derived from the frame one place."""
    from tools.frame_rescreen import frame_vocabulary

    vocab = frame_vocabulary(frame)
    tier2 = [t for t in vocab["capability_terms"]
             if vocab["tier_by_term"].get(t.casefold()) == 2]
    bm25_terms = (list(vocab["entities"]["product"])
                  + list(vocab["entities"]["competitor"]) + tier2)
    dense_queries = [s for s in capability_statements if str(s).strip()]
    dense_queries += tier2
    return {"vocab": vocab, "bm25_terms": bm25_terms,
            "dense_queries": dense_queries, "tier2": tier2}


def rrf_fuse(ranked_lists: list[list[tuple[str, float]]],
             rrf_k: int = RRF_K) -> dict[str, dict]:
    """Reciprocal rank fusion; keeps each lane's score and rank."""
    fused: dict[str, dict] = {}
    names = ("bm25", "dense")
    for name, ranking in zip(names, ranked_lists):
        for rank, (notice_id, score) in enumerate(ranking, start=1):
            row = fused.setdefault(notice_id, {
                "bm25_score": None, "dense_score": None,
                "bm25_rank": None, "dense_rank": None, "rrf_score": 0.0})
            row[f"{name}_score"] = score
            row[f"{name}_rank"] = rank
            row["rrf_score"] += 1.0 / (rrf_k + rank)
    return fused


def _best_dense_sentence(text: str, query_vecs, encoder) -> Optional[str]:
    import numpy as np
    import re

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)
                 if len(s.strip()) >= 25][:MAX_SENTENCES_PER_ROW]
    if not sentences:
        return None
    vecs = np.asarray(encoder(sentences), dtype=np.float32)
    vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
    scores = (vecs @ query_vecs.T).max(axis=1)
    return sentences[int(scores.argmax())]


def hybrid_retrieve(frame: dict, k: int = 500, *,
                    capability_statements: Optional[list[str]] = None,
                    store_conn=None, sidecar=None,
                    encoder: Optional[Callable] = None,
                    ) -> dict:
    """Fused, guarded candidate set with per-row lane features."""
    import numpy as np

    from types import SimpleNamespace

    from agents.golden_press.notice_join import (
        SOLICITABLE_MAX_RANK, dedupe_by_title_agency, leverage_rank)
    from agents.golden_press.sam_lanes import (
        _corpus_negatives, _pattern, aliases, build_matchers)
    from agents.golden_press.term_tiers import load_guards
    from tools.frame_rescreen import _has_tech_code, screen_text
    from tools.retrieval import dense as dense_mod
    from tools.retrieval import fts

    owned_store = store_conn is None
    if store_conn is None:
        from tools.notice_store import connect
        store_conn = connect()
    owned_sidecar = sidecar is None
    if sidecar is None:
        sidecar = dense_mod.open_sidecar()
    encoder = encoder or dense_mod.default_encoder()
    try:
        lanes = frame_lanes(frame, capability_statements or [])
        pool = max(POOL_MULTIPLIER * k, MIN_POOL)
        fts.ensure_fts(store_conn)
        bm25 = fts.bm25_retrieve(store_conn, lanes["bm25_terms"], k=pool)
        dense_hits = dense_mod.dense_retrieve(
            lanes["dense_queries"], k=pool, sidecar=sidecar, encoder=encoder)
        fused = rrf_fuse([bm25, dense_hits])

        vocab = lanes["vocab"]
        guards = load_guards()
        negatives = _corpus_negatives()
        # Resellers ride the matchers so the shared entity guard's
        # reseller-without-vendor-or-product rule can fire on the fused
        # set (frame_vocabulary itself carries product/competitor only).
        entities = dict(vocab["entities"])
        resellers = list((frame.get("screen_routing") or {})
                         .get("tier1_reseller_names") or [])
        if resellers:
            entities["reseller"] = resellers
        matchers = build_matchers(entities, vocab["vendor"])
        vendor_pattern = _pattern(aliases(vocab["vendor"]))
        corpus_phrases = [p for p in guards.get("negative", []) if p]

        q = np.asarray(
            encoder([dense_mod.QUERY_PREFIX + t
                     for t in lanes["dense_queries"]]), dtype=np.float32)
        q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)

        receipt: dict[str, Any] = {
            "bm25_pool": len(bm25), "dense_pool": len(dense_hits),
            "fused": len(fused), "k": k,
            "guard_rejected": {"entity_or_ladder": 0,
                               "corpus_negative_no_span": 0},
            "award_history_excluded": 0,
            "rejected_ids": [],
        }
        term_rows: list[dict] = []
        dense_rows: list[dict] = []
        ordered = sorted(fused.items(), key=lambda x: -x[1]["rrf_score"])
        for notice_id, lane_scores in ordered:
            db_row = store_conn.execute(
                "SELECT notice_id, title, agency, subtier, office, "
                "notice_type, posted, deadline, naics, psc, set_aside, "
                "description_prefix FROM notices WHERE notice_id = ?",
                (notice_id,)).fetchone()
            if db_row is None:
                continue
            title = str(db_row["title"] or "")
            text = title + "\n" + str(db_row["description_prefix"] or "")
            screen_receipt = {"entity_rejections": {}, "ladder_rejections": {},
                              "purview_measurement": {}}
            has_code = _has_tech_code(db_row["naics"], db_row["psc"])
            # The inversion: the ladder runs with the code requirement
            # lifted (True); anchors, negative context, and the entity
            # guard stay armed. has_code itself becomes the feature.
            hits = screen_text(text, len(title), vocab, matchers,
                               vendor_pattern, guards, negatives,
                               True, screen_receipt)
            qualifying = [h for h in hits if h["tier"] in (1, 2)]
            if qualifying:
                best = qualifying[0]
                matched_sentence = best["sentence"]
                matched_term = best["term"]
                matched_tier = best["tier"]
                route = "term"
            else:
                if (screen_receipt["entity_rejections"]
                        or screen_receipt["ladder_rejections"]):
                    receipt["guard_rejected"]["entity_or_ladder"] += 1
                    receipt["rejected_ids"].append(
                        {"notice_id": notice_id,
                         "reason": "entity_or_ladder",
                         "detail": {**screen_receipt["entity_rejections"],
                                    **screen_receipt["ladder_rejections"]}})
                    continue
                low = text.casefold()
                blocked = next((p for p in corpus_phrases if p in low), None)
                if blocked:
                    receipt["guard_rejected"]["corpus_negative_no_span"] += 1
                    receipt["rejected_ids"].append(
                        {"notice_id": notice_id,
                         "reason": "corpus_negative_no_span",
                         "detail": blocked})
                    continue
                matched_sentence = _best_dense_sentence(text, q, encoder) \
                    or ("TITLE: " + title)
                matched_term = None
                matched_tier = None
                route = "dense_only"
            rank = leverage_rank(db_row["notice_type"])
            if rank is not None and rank > SOLICITABLE_MAX_RANK:
                # Award-history rows are evidence, never candidates,
                # exactly as the keyword lane's solicitable ordering rules.
                receipt["award_history_excluded"] += 1
                continue
            out_row = ({
                "notice_id": notice_id,
                "title": title,
                "agency": db_row["agency"],
                "subtier": db_row["subtier"],
                "notice_type": db_row["notice_type"],
                "posted": db_row["posted"],
                "deadline": db_row["deadline"],
                "naics": db_row["naics"],
                "psc": db_row["psc"],
                "set_aside": db_row["set_aside"],
                "bm25_score": lane_scores["bm25_score"],
                "dense_score": lane_scores["dense_score"],
                "bm25_rank": lane_scores["bm25_rank"],
                "dense_rank": lane_scores["dense_rank"],
                "rrf_score": round(lane_scores["rrf_score"], 6),
                # The inversion: code membership is a FEATURE, never a gate.
                "in_code_boundary": bool(has_code),
                "matched_sentence": matched_sentence,
                "matched_term": matched_term,
                "matched_tier": matched_tier,
                "match_route": route,
                "notice_leverage_rank": rank,
            })
            (term_rows if route == "term" else dense_rows).append(out_row)
            # Term rows keep their seats: rows the ladder qualified are
            # never crowded out of the cap by unranked dense noise;
            # dense-only rows fill the remainder (measured on the first
            # Red Hat run, where genuine bm25 keeps fell out of k=500).
            if len(term_rows) >= k:
                break
        rows = term_rows[:k]
        rows += dense_rows[:max(0, k - len(rows))]
        rows.sort(key=lambda r: -r["rrf_score"])
        # Family dedupe, the house owner: amendment reposts collapse to
        # one row per (title, agency) family exactly as the keyword lane.
        # lane must read L1_notice: the house dedupe passes every other
        # lane through untouched (measured: 0 dropped with a foreign lane
        # while triplet titles sat in the top 25).
        shimmed = [SimpleNamespace(
            lane="L1_notice", title=r["title"], agency=r["agency"],
            notice_leverage_rank=r["notice_leverage_rank"],
            response_deadline=r["deadline"], record_id=r["notice_id"],
            keep=r) for r in rows]
        deduped, dropped = dedupe_by_title_agency(shimmed)
        receipt["family_deduped_away"] = len(dropped)
        rows = [s.keep for s in deduped]
        receipt["returned"] = len(rows)
        receipt["term_route_rows"] = sum(
            1 for r in rows if r["match_route"] == "term")
        receipt["dense_only_rows"] = sum(
            1 for r in rows if r["match_route"] == "dense_only")
        receipt["in_code_boundary_rows"] = sum(
            1 for r in rows if r["in_code_boundary"])
        return {"rows": rows, "receipt": receipt}
    finally:
        if owned_store:
            store_conn.close()
        if owned_sidecar:
            sidecar.close()
