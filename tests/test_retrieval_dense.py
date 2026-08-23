"""Dense retriever contract (R1 item 2, 2026-08-18).

Offline doctrine: a deterministic fake encoder, tmp sidecars, no model
load. Pins: incremental embedding (a second pass embeds only new or
changed rows), model-id lock on the sidecar, cosine ranking with
max-over-queries, and the bge query-prefix convention.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from tools.retrieval import dense


def _fake_encoder(texts):
    """Deterministic 768-d unit vectors; near-duplicate texts collide."""
    out = []
    for text in texts:
        rng = np.random.default_rng(
            abs(hash(text.replace(dense.QUERY_PREFIX, ""))) % (2**32))
        v = rng.standard_normal(dense.DIM).astype(np.float32)
        out.append(v / np.linalg.norm(v))
    return np.vstack(out)


def _store(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE notices (notice_id TEXT PRIMARY KEY, "
                 "title TEXT, description_prefix TEXT)")
    conn.executemany("INSERT INTO notices VALUES (?,?,?)", rows)
    conn.commit()
    return conn


def test_incremental_embedding_and_receipt(tmp_path):
    store = _store([("a", "alpha title", "alpha body"),
                    ("b", "beta title", "beta body")])
    sidecar = dense.open_sidecar(tmp_path / "vec.db")
    r1 = dense.embed_missing(store, sidecar, encoder=_fake_encoder,
                             receipt_path=tmp_path / "r.json")
    assert r1["rows_embedded_this_pass"] == 2
    assert r1["sidecar_rows"] == 2
    assert r1["model_id"] == dense.MODEL_ID and r1["dim"] == dense.DIM

    # a morning adds one row: only it embeds
    store.execute("INSERT INTO notices VALUES ('c','gamma','gamma body')")
    r2 = dense.embed_missing(store, sidecar, encoder=_fake_encoder,
                             receipt_path=tmp_path / "r.json")
    assert r2["rows_embedded_this_pass"] == 1
    assert r2["sidecar_rows"] == 3

    # text drift re-embeds exactly that row
    store.execute("UPDATE notices SET description_prefix='beta CHANGED' "
                  "WHERE notice_id='b'")
    r3 = dense.embed_missing(store, sidecar, encoder=_fake_encoder,
                             receipt_path=tmp_path / "r.json")
    assert r3["rows_embedded_this_pass"] == 1


def test_cosine_ranking_max_over_queries(tmp_path):
    store = _store([("a", "alpha title", "alpha body"),
                    ("b", "beta title", "beta body"),
                    ("c", "gamma title", "gamma body")])
    sidecar = dense.open_sidecar(tmp_path / "vec.db")
    dense.embed_missing(store, sidecar, encoder=_fake_encoder,
                        receipt_path=tmp_path / "r.json")
    # a query whose text equals a document's text maxes cosine at ~1 on it
    hits = dense.dense_retrieve(["alpha title\nalpha body"], k=3,
                                sidecar=sidecar, encoder=_fake_encoder)
    assert hits[0][0] == "a"
    assert hits[0][1] == pytest.approx(1.0, abs=1e-5)
    # max over queries: adding a second query that matches c lifts c too
    hits = dense.dense_retrieve(
        ["alpha title\nalpha body", "gamma title\ngamma body"], k=2,
        sidecar=sidecar, encoder=_fake_encoder)
    assert {h[0] for h in hits} == {"a", "c"}


def test_sidecar_locks_the_model_id(tmp_path):
    sidecar = dense.open_sidecar(tmp_path / "vec.db")
    sidecar.execute("UPDATE meta SET v='some-other/model' WHERE k='model_id'")
    sidecar.commit()
    sidecar.close()
    with pytest.raises(RuntimeError, match="delete the sidecar"):
        dense.open_sidecar(tmp_path / "vec.db")


def test_doc_text_caps_description_at_2000_chars():
    text = dense.doc_text("t", "x" * 5000)
    assert len(text) == len("t\n") + 2000


def test_qbe_probe_ranks_and_family_separation(tmp_path):
    # R2 item 2: the probe queries with STORED vectors (doc-to-doc, no
    # instruction prefix) and separates amendment siblings from
    # cross-family discovery in the lead number.
    import sqlite3

    from tools.retrieval import qbe

    store = sqlite3.connect(":memory:")
    store.row_factory = sqlite3.Row
    store.execute("CREATE TABLE notices (notice_id TEXT PRIMARY KEY, "
                  "title TEXT, description_prefix TEXT, agency TEXT, "
                  "notice_type TEXT)")
    sidecar = dense.open_sidecar(tmp_path / "vec.db")
    rng = np.random.default_rng(7)
    base = rng.standard_normal(dense.DIM).astype(np.float32)
    rows = {}
    for fx in qbe.FIXTURES.values():
        for i, nid in enumerate(fx["family"]):
            # family members share a vector direction; families differ
            fam_seed = abs(hash(fx["query"])) % (2**32)
            fam = np.random.default_rng(fam_seed).standard_normal(
                dense.DIM).astype(np.float32)
            v = fam + 0.01 * i * base
            rows[nid] = v / np.linalg.norm(v)
            store.execute("INSERT OR IGNORE INTO notices VALUES (?,?,?,?,?)",
                          (nid, f"fixture {nid[:6]}", "", "AGENCY", "RFI"))
    sidecar.executemany(
        "INSERT INTO vectors (notice_id, text_sha, vec) VALUES (?,?,?)",
        [(nid, "x", v.tobytes()) for nid, v in rows.items()])
    sidecar.commit()
    store.commit()
    out = qbe.probe(k=5, sidecar=sidecar, store_conn=store)
    unno = out["results"]["UNNO"]
    # the sibling ranks first among neighbors; lead number excludes it
    assert unno["sibling_ranks"]["0f08be613b714a219397f8aa2d03a4a3"] == 2
    assert out["mean_cross_family_fixture_rank"] is not None
    assert len(out["cross_family_ranks_all"]) == 20
