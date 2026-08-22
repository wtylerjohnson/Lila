"""The polysemy corpus: provenance is the admission ticket.

The corpus (data/reference/polysemy_corpus.json) is the one record of every
measured vocabulary collision, and guards derive their negative-context
strings from it. The rule these tests enforce: an entry a guard consumes
must carry the notice_id that witnessed the collision, and an entry whose
evidence is not yet on record (status 'unproven') contributes nothing to any
guard until a real id is attached. A guard with no witness is a guess.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agents.golden_press import sam_lanes as sl
from agents.golden_press import term_tiers as tt

CORPUS = Path(__file__).resolve().parents[1] / "data" / "reference" / "polysemy_corpus.json"

# The store's notice ids are 32 lowercase hex characters. The shape check is
# what a hermetic suite CAN verify; it exists so a placeholder like "TBD" or
# a pasted title can never ride in the id field.
# Operator decision 2026-08-04: a witnessed USAspending award id is
# provenance with the same standing as a 32-hex SAM notice id (the Sentra
# collisions were witnessed in the award lane). Lowercase prose and blank
# ids still never pass.
_ID_SHAPE = re.compile(r"^(?:[0-9a-f]{32}|[A-Z0-9][A-Z0-9_\-]{3,63})$")


def _corpus() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def test_every_guard_consumed_entry_carries_a_real_notice_id():
    data = _corpus()
    consumed = [e for e in data["entries"]
                if (e.get("guard") or {}).get("consumers")]
    assert consumed, "the corpus must feed at least one guard"
    for entry in consumed:
        nid = str(entry.get("notice_id") or "")
        assert _ID_SHAPE.match(nid), (
            f"guard-consumed entry for {entry.get('term')!r} has no real "
            f"notice id (got {nid!r}); attach the witness or mark it unproven")
        assert entry.get("status") != "unproven", (
            f"entry for {entry.get('term')!r} is consumed while unproven")


def test_unproven_entries_are_not_consumed(tmp_path):
    """An unproven entry, or one with a blank id, never reaches the screen."""
    data = _corpus()
    live = tt.corpus_negative_phrases()
    data["entries"].append({
        "term": "example",
        "collision_domain": "transcript-only evidence",
        "resolution": "negative_context",
        "guard": {"consumers": ["term_tiers.negative_context"],
                  "phrase": "transcript ghost"},
        "notice_id": "",
        "status": "unproven",
    })
    data["entries"].append({
        "term": "example2",
        "collision_domain": "id never attached",
        "resolution": "negative_context",
        "guard": {"consumers": ["term_tiers.negative_context"],
                  "phrase": "idless ghost"},
        "notice_id": "",
        "status": "proven",
    })
    scratch = tmp_path / "corpus.json"
    scratch.write_text(json.dumps(data), encoding="utf-8")
    loaded = tt.corpus_negative_phrases(str(scratch))
    assert "transcript ghost" not in loaded
    assert "idless ghost" not in loaded
    assert loaded == live


def test_corpus_backs_the_loaded_negative_guards_exactly():
    """load_guards' negative list IS the corpus-consumed set, in entry
    order: the loader and the data cannot drift apart silently."""
    data = _corpus()
    expected = []
    for e in data["entries"]:
        g = e.get("guard") or {}
        if "term_tiers.negative_context" in (g.get("consumers") or []):
            ph = str(g["phrase"]).casefold()
            if ph not in expected:
                expected.append(ph)
    tt.load_guards.cache_clear()
    try:
        assert tt.load_guards()["negative"] == expected
    finally:
        tt.load_guards.cache_clear()


def test_common_word_products_load_from_the_corpus():
    words = sl._common_word_products()
    assert words, "corpus word list must load"
    assert words == frozenset(
        w.lower() for w in _corpus()["common_word_products"]["words"])
    # the guard class's witness is on record and real
    assert _ID_SHAPE.match(
        _corpus()["common_word_products"]["class_witness_notice_id"])


def test_missing_corpus_weakens_never_fails(tmp_path):
    assert tt.corpus_negative_phrases(str(tmp_path / "absent.json")) == []
    assert sl._common_word_products(tmp_path / "absent.json") == frozenset()
