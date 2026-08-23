"""Hybrid fusion and the inversion (R1 item 3, 2026-08-18).

Offline doctrine: in-memory store, fake encoder, real guard reference
files (screen_guards.json and the witnessed polysemy corpus are tracked
repo data). Pins: RRF math, guard application on the fused set (the
witnessed 'substation' negative kills both a term row and a dense-only
row), the inversion (an off-code tier-2 phrase match SURVIVES with
in_code_boundary False while anchors still guard common words), and the
per-row feature contract.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from tools.retrieval import dense, fts, hybrid

FRAME = {
    "client_name": "TestClient",
    "as_ordered": {
        "tier1": ["Xylotech"],
        "tier2": ["data loss prevention", "network monitoring"],
        "tier3": ["zero trust"],
    },
    "screen_routing": {
        "tier1_client_names": ["Xylotech"],
        "tier1_rival_names": ["Rivalsoft"],
    },
}

ROWS = [
    # term row, on-code: qualifies, in_code_boundary True
    ("t-on", "Enterprise data loss prevention licenses",
     "The agency requires data loss prevention software.", "541519", ""),
    # term row, OFF-code: the old lane killed this on the code gate; the
    # inversion keeps it with in_code_boundary False
    ("t-off", "Data loss prevention services procurement",
     "Scope includes data loss prevention for the enterprise.",
     "339999", ""),
    # witnessed negative context: 'substation' near 'network monitoring'
    ("t-sub", "Network monitoring for substation automation",
     "Protective relays and network monitoring for substation "
     "protection and automation systems.", "541519", ""),
    # dense-only survivor: no frame term, no corpus negative
    ("d-ok", "Unified observability platform support",
     "Application performance analytics and telemetry pipeline "
     "operations for enterprise systems.", "541519", ""),
    # dense-only with a witnessed corpus negative anywhere in text
    ("d-sub", "Electrical equipment refresh",
     "Replacement equipment for the substation protection and "
     "automation program.", "238210", ""),
]


def _fake_encoder(texts):
    out = []
    for text in texts:
        rng = np.random.default_rng(
            abs(hash(text.replace(dense.QUERY_PREFIX, ""))) % (2**32))
        v = rng.standard_normal(dense.DIM).astype(np.float32)
        out.append(v / np.linalg.norm(v))
    return np.vstack(out)


@pytest.fixture()
def rig(tmp_path):
    store = sqlite3.connect(":memory:")
    store.row_factory = sqlite3.Row
    store.execute(
        "CREATE TABLE notices (notice_id TEXT PRIMARY KEY, title TEXT, "
        "description_prefix TEXT, naics TEXT, psc TEXT, agency TEXT, "
        "subtier TEXT, office TEXT, notice_type TEXT, posted TEXT, "
        "deadline TEXT, set_aside TEXT)")
    store.executemany(
        "INSERT INTO notices (notice_id, title, description_prefix, naics, "
        "psc) VALUES (?,?,?,?,?)", ROWS)
    store.commit()
    fts.rebuild(store, receipt_path=tmp_path / "fts.json")
    sidecar = dense.open_sidecar(tmp_path / "vec.db")
    dense.embed_missing(store, sidecar, encoder=_fake_encoder,
                        receipt_path=tmp_path / "dense.json")
    return store, sidecar


def test_rrf_math_is_reciprocal_rank_with_both_lanes():
    fused = hybrid.rrf_fuse([[("a", 9.0), ("b", 8.0)], [("b", 0.9)]])
    assert fused["a"]["rrf_score"] == pytest.approx(1 / 61)
    assert fused["b"]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["b"]["bm25_rank"] == 2 and fused["b"]["dense_rank"] == 1


def test_fused_set_rides_the_guards_and_the_inversion(rig):
    store, sidecar = rig
    out = hybrid.hybrid_retrieve(
        FRAME, k=10,
        capability_statements=["Unified observability platform support\n"
                               "Application performance analytics and "
                               "telemetry pipeline operations for "
                               "enterprise systems."],
        store_conn=store, sidecar=sidecar, encoder=_fake_encoder)
    rows = {r["notice_id"]: r for r in out["rows"]}
    rejected = {r["notice_id"]: r for r in out["receipt"]["rejected_ids"]}

    # on-code term row: kept, feature True, receipt sentence present
    assert rows["t-on"]["match_route"] == "term"
    assert rows["t-on"]["in_code_boundary"] is True
    assert rows["t-on"]["matched_term"] == "data loss prevention"
    assert "data loss prevention" in rows["t-on"]["matched_sentence"].casefold()

    # THE INVERSION: off-code phrase match survives, feature False
    assert rows["t-off"]["match_route"] == "term"
    assert rows["t-off"]["in_code_boundary"] is False

    # witnessed negative context still kills a term row on the fused set
    assert "t-sub" not in rows
    assert rejected["t-sub"]["reason"] == "entity_or_ladder"

    # dense-only survivor carries a best-cosine sentence and null term
    assert rows["d-ok"]["match_route"] == "dense_only"
    assert rows["d-ok"]["matched_term"] is None
    assert rows["d-ok"]["matched_sentence"]

    # dense-only row with a witnessed corpus phrase anywhere dies
    assert "d-sub" not in rows
    assert rejected["d-sub"]["reason"] == "corpus_negative_no_span"
    # both phrases on the SEL2731 corpus entry are witnessed kills
    assert str(rejected["d-sub"]["detail"]) in (
        "substation", "protection and automation")

    # receipt accounting holds
    r = out["receipt"]
    assert r["returned"] == len(out["rows"])
    assert r["guard_rejected"]["entity_or_ladder"] >= 1
    assert r["guard_rejected"]["corpus_negative_no_span"] >= 1
    # rows are ordered by fused score
    rrf = [row["rrf_score"] for row in out["rows"]]
    assert rrf == sorted(rrf, reverse=True)


def test_lane_queries_derive_from_frame_and_statements():
    lanes = hybrid.frame_lanes(FRAME, ["capability statement one"])
    assert "Xylotech" in lanes["bm25_terms"]
    assert "Rivalsoft" in lanes["bm25_terms"]
    assert "data loss prevention" in lanes["bm25_terms"]
    assert "zero trust" not in lanes["bm25_terms"]  # tier 3 never retrieves
    assert lanes["dense_queries"][0] == "capability statement one"
    assert "network monitoring" in lanes["dense_queries"]


def test_measure_frame_mapping_from_packet(tmp_path):
    # R1 item 4 harness: tier 1 = research entities, tier 2 = capability
    # and technology keywords; procurement lenses never enter the frame.
    import json as _json
    from tools.retrieval import measure
    packet = {
        "client_name": "Riverbed",
        "strategy": {
            "research_entities": [
                {"kind": "product", "name": "SteelHead"},
                {"kind": "competitor", "name": "Dynatrace"},
                {"kind": "reseller", "name": "Carahsoft"},
            ],
            "keywords": [
                {"term": "network performance monitoring",
                 "category": "capability"},
                {"term": "packet capture", "category": "technology"},
                {"term": "Department of Defense", "category": "agency"},
            ],
        },
    }
    (tmp_path / "riverbed.review.json").write_text(_json.dumps(packet))
    frame = measure.frame_from_packet("riverbed", review_dir=tmp_path)
    assert frame["screen_routing"]["tier1_client_names"] == [
        "Riverbed", "SteelHead"]
    assert frame["screen_routing"]["tier1_rival_names"] == ["Dynatrace"]
    # resellers arm the reseller guard, never the rival or product lists;
    # lenses never become tier 2
    assert frame["screen_routing"]["tier1_reseller_names"] == ["Carahsoft"]
    assert "Carahsoft" not in frame["as_ordered"]["tier1"]
    assert frame["screen_routing"]["entities_source"] == "packet"
    assert frame["as_ordered"]["tier2"] == [
        "network performance monitoring", "packet capture"]


def test_desktop_delivery_carries_the_internal_banner(tmp_path):
    # Operator protocol (2026-08-19): research reports deliver to
    # ~/Desktop/<Client>/ like gate-clean files, but wear a loud INTERNAL
    # banner and never the client template.
    import json as _json
    from tools.retrieval import deliver
    artifact = tmp_path / "measure_x.json"
    artifact.write_text(_json.dumps({
        "book": {"awards": 2, "total": 1000.0,
                 "by_agency": {"DOJ": {"FBI": 1000.0}}},
        "receipts": {"vendor": {"returned": 1, "term_route_rows": 1,
                                "dense_only_rows": 0,
                                "guard_rejected": {"entity_or_ladder": 3},
                                "award_history_excluded": 2,
                                "family_deduped_away": 1}},
        "union_rows": [{"rank": 1, "match_route": "term",
                        "variant": "vendor", "agency": "DOJ",
                        "title": "Language services", "deadline": "2026-09-01",
                        "matched_sentence": "translation and interpretation"}],
    }))
    out = deliver.deliver(artifact, "TestCo", desktop_dir=tmp_path / "desk")
    text = out.read_text(encoding="utf-8")
    assert "INTERNAL RESEARCH REPORT" in text
    assert "not a certified client deliverable" in text
    assert "Language services" in text
    assert "TestCo_Federal_Research_Depth_" in out.name
    assert "—" not in text  # the em dash ban holds in delivered files
