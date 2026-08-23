"""Dense retriever over the notice store (R1 item 2, 2026-08-18).

Embeds title plus the first 2,000 characters of description for every
notice with a local sentence-transformer and stores vectors in a sidecar
SQLite keyed by notice_id, incremental on ingest: a morning adds only new
or changed rows (change = text sha drift). dense_retrieve(query_texts, k)
ranks by cosine (max over the query set). Retrieval only: candidates
still ride the guard ladder downstream.

Model: BAAI/bge-base-en-v1.5, 768 dimensions, chosen from the bge-base /
e5-base class per the work order; the id and dimension are pinned in the
sidecar meta and verified against the loaded model at embed time. bge
convention: queries carry the instruction prefix, passages do not.
Device: MPS when available, else CPU. The suite never loads the model
(offline doctrine: tests inject a fake encoder).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

MODEL_ID = "BAAI/bge-base-en-v1.5"
DIM = 768
QUERY_PREFIX = ("Represent this sentence for searching relevant passages: ")
DESCRIPTION_CHARS = 2000
_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_PATH = _ROOT / "data" / "state" / "retrieval" / "dense_vectors.db"
RECEIPT_PATH = _ROOT / "data" / "state" / "retrieval" / "dense_receipt.json"

_matrix_cache: dict = {}


def doc_text(title, description_prefix) -> str:
    return (str(title or "") + "\n"
            + str(description_prefix or "")[:DESCRIPTION_CHARS]).strip()


def _text_sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def open_sidecar(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path) if path else SIDECAR_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE IF NOT EXISTS vectors ("
                 "notice_id TEXT PRIMARY KEY, text_sha TEXT, vec BLOB)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    row = conn.execute("SELECT v FROM meta WHERE k='model_id'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta VALUES ('model_id', ?)", (MODEL_ID,))
        conn.execute("INSERT INTO meta VALUES ('dim', ?)", (str(DIM),))
        conn.commit()
    elif row[0] != MODEL_ID:
        raise RuntimeError(
            f"sidecar embedded with {row[0]!r}, code expects {MODEL_ID!r}; "
            "delete the sidecar to re-embed rather than mixing spaces")
    return conn


def default_encoder() -> Callable:
    """Lazy local model; never imported by the test suite."""
    from sentence_transformers import SentenceTransformer
    try:
        import torch
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        device = "cpu"
    model = SentenceTransformer(MODEL_ID, device=device)
    dim = model.get_sentence_embedding_dimension()
    if dim != DIM:
        raise RuntimeError(f"model dimension {dim} != pinned {DIM}")

    def encode(texts: list[str]):
        return model.encode(texts, batch_size=128,
                            normalize_embeddings=True,
                            convert_to_numpy=True,
                            show_progress_bar=False)

    encode.device = device  # type: ignore[attr-defined]
    return encode


def embed_missing(store_conn, sidecar, *, encoder: Optional[Callable] = None,
                  limit: Optional[int] = None,
                  batch: int = 512,
                  progress: Optional[Callable[[str], None]] = None,
                  receipt_path: Optional[Path] = None) -> dict:
    """Embed rows absent from the sidecar or whose text changed."""
    import numpy as np

    encoder = encoder or default_encoder()
    known = dict(sidecar.execute("SELECT notice_id, text_sha FROM vectors"))
    todo: list[tuple[str, str, str]] = []
    total = 0
    for row in store_conn.execute(
            "SELECT notice_id, title, description_prefix FROM notices"):
        total += 1
        text = doc_text(row[1], row[2])
        sha = _text_sha(text)
        if known.get(str(row[0])) != sha:
            todo.append((str(row[0]), sha, text))
            if limit and len(todo) >= limit:
                break
    started = time.monotonic()
    done = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        vecs = np.asarray(encoder([t[2] for t in chunk]), dtype=np.float32)
        sidecar.executemany(
            "INSERT INTO vectors (notice_id, text_sha, vec) VALUES (?,?,?) "
            "ON CONFLICT(notice_id) DO UPDATE SET "
            "text_sha=excluded.text_sha, vec=excluded.vec",
            [(nid, sha, vec.tobytes())
             for (nid, sha, _), vec in zip(chunk, vecs)])
        sidecar.commit()
        done += len(chunk)
        if progress and (done % (batch * 10) == 0 or done == len(todo)):
            rate = done / max(time.monotonic() - started, 1e-6)
            progress(f"{done}/{len(todo)} embedded ({rate:,.0f} rows/s)")
    _matrix_cache.clear()
    wall = time.monotonic() - started
    receipt = {
        "model_id": MODEL_ID,
        "dim": DIM,
        "device": getattr(encoder, "device", "injected"),
        "store_rows": total,
        "rows_embedded_this_pass": len(todo),
        "wall_seconds": round(wall, 1),
        "rows_per_second": round(len(todo) / wall, 1) if wall > 0 else None,
        "sidecar_rows": sidecar.execute(
            "SELECT COUNT(*) FROM vectors").fetchone()[0],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    path = Path(receipt_path) if receipt_path else RECEIPT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    return receipt


def _load_matrix(sidecar):
    import numpy as np
    key = id(sidecar)
    cached = _matrix_cache.get(key)
    count = sidecar.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    if cached and cached[2] == count:
        return cached[0], cached[1]
    ids: list[str] = []
    rows = sidecar.execute("SELECT notice_id, vec FROM vectors")
    vecs = []
    for nid, blob in rows:
        ids.append(nid)
        vecs.append(np.frombuffer(blob, dtype=np.float32))
    matrix = (np.vstack(vecs) if vecs
              else np.zeros((0, DIM), dtype=np.float32))
    _matrix_cache[key] = (ids, matrix, count)
    return ids, matrix


def dense_retrieve(query_texts: list[str], k: int = 500, *,
                   sidecar=None,
                   encoder: Optional[Callable] = None,
                   ) -> list[tuple[str, float]]:
    """Top-k (notice_id, cosine) where the score is the max over queries."""
    import numpy as np

    owned = sidecar is None
    if sidecar is None:
        sidecar = open_sidecar()
    try:
        queries = [q for q in (query_texts or []) if str(q).strip()]
        if not queries:
            return []
        encoder = encoder or default_encoder()
        q = np.asarray(
            encoder([QUERY_PREFIX + str(t) for t in queries]),
            dtype=np.float32)
        q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)
        ids, matrix = _load_matrix(sidecar)
        if not ids:
            return []
        scores = (matrix @ q.T).max(axis=1)
        top = scores.argsort()[::-1][:int(k)]
        return [(ids[i], float(scores[i])) for i in top]
    finally:
        if owned:
            sidecar.close()


def main(argv: Optional[list] = None) -> int:
    import argparse

    from tools.notice_store import connect

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--query", default=None)
    ap.add_argument("-k", type=int, default=10)
    args = ap.parse_args(argv)
    if args.embed:
        store = connect()
        sidecar = open_sidecar()
        try:
            receipt = embed_missing(
                store, sidecar, limit=args.limit,
                progress=lambda m: print(f"  {m}", flush=True))
            print(json.dumps(receipt, indent=1))
        finally:
            store.close()
            sidecar.close()
    if args.query:
        for nid, score in dense_retrieve([args.query], k=args.k):
            print(f"{score:7.4f}  {nid}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
