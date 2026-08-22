"""NAICS Boundary Workshop (2026-07-12): consultant-grade NAICS refinement.

inferred_naics stays the authoritative boundary; naics_meta + kept_out_naics
are additive/backward-compatible. revise()/amend_terms() remain the persistence
owners; the single strategy approval remains the only approval leg; removal is
non-destructive and restorable. NAICS stays a coarse boundary filter.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.review as review  # noqa: E402
from agents.decisions.schemas import (  # noqa: E402
    IntakeStrategy, Keyword, SearchSpec,
)
from agents.review import (  # noqa: E402
    RevisionError, amend_terms, decide, load_packet, request_approval, revise,
)


def _strategy():
    return IntakeStrategy(
        client_name="Testco", pursuit_strategy="n.",
        keywords=[Keyword(term="packet capture", category="capability",
                          rationale="core")],
        inferred_naics=["541512", "541519"],
        target_agencies=["DHS"], set_aside_angles=[],
        searches=[
            SearchSpec(source="sam.gov", query_terms=["packet capture"],
                       naics_codes=["541512", "541519"], set_asides=[], rationale="s"),
            SearchSpec(source="usaspending.gov", query_terms=["Testco"],
                       naics_codes=["541512", "541519"], set_asides=[], rationale="u"),
            SearchSpec(source="web", query_terms=["Testco cmmc"],
                       naics_codes=[], set_asides=[], rationale="w"),
        ],
        confidence=0.8, review_gate="approve")


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
    request_approval(_strategy(), alert_fn=lambda *_a: None)
    return str(tmp_path)


def test_legacy_packet_without_naics_metadata_loads(seeded):
    s = load_packet("Testco").strategy
    assert s.naics_meta == []            # additive, default empty
    assert s.kept_out_naics == []
    assert s.inferred_naics == ["541512", "541519"]  # authoritative, intact


def test_legacy_move_out_and_restore_never_fabricates_a_boundary_rationale(seeded):
    moved = revise("Testco", {"inferred_naics": ["541512"]}).strategy
    entry = moved.kept_out_naics[0]
    assert entry.code == "541519"
    assert entry.rationale == ""
    assert entry.note == "moved out of the boundary by the operator"

    restored = revise("Testco", {
        "inferred_naics": ["541512", "541519"],
    }).strategy
    restored_entry = {entry.code: entry for entry in restored.naics_meta}["541519"]
    assert restored_entry.rationale == ""


def test_existing_codes_round_trip_with_provenance(seeded):
    revise("Testco", {
        "inferred_naics": ["541512", "541519"],
        "naics_meta": [
            {"code": "541512", "title": "Computer Systems Design",
             "role": "core", "origin": "system", "rationale": "primary lane",
             "note": "exact"},
            {"code": "541519", "role": "boundary", "origin": "system",
             "rationale": "adjacent"},
        ],
    })
    s = load_packet("Testco").strategy
    assert s.inferred_naics == ["541512", "541519"]
    meta = {m.code: m for m in s.naics_meta}
    assert meta["541512"].role == "core" and meta["541512"].title == "Computer Systems Design"
    assert meta["541519"].role == "boundary"


def test_malformed_code_is_rejected_before_persistence(seeded):
    for bad in (["541512", "54151"], ["541512", "abcdef"], ["5415123"]):
        with pytest.raises(RevisionError, match="six digits"):
            revise("Testco", {"inferred_naics": bad})
    # nothing persisted from the rejected edits
    assert load_packet("Testco").strategy.inferred_naics == ["541512", "541519"]


def test_duplicate_codes_are_deduped(seeded):
    revise("Testco", {
        "inferred_naics": ["541512", "541512", "541519"],
        "naics_meta": [{"code": "541512", "origin": "system"},
                       {"code": "541512", "origin": "consultant"}],
    })
    s = load_packet("Testco").strategy
    assert s.inferred_naics.count("541512") == 1
    assert [m.code for m in s.naics_meta].count("541512") == 1


def test_blank_rationale_is_preserved_without_a_fabricated_explanation(seeded):
    revise("Testco", {
        "inferred_naics": ["541512", "541519"],
        "naics_meta": [{
            "code": "541512", "title": "Operator-refined title",
            "role": "core", "origin": "edited", "rationale": "",
            "note": "still needs a code-specific explanation",
        }],
    })
    entry = load_packet("Testco").strategy.naics_meta[0]
    assert entry.title == "Operator-refined title"
    assert entry.role == "core"
    assert entry.rationale == ""


def test_orphan_meta_is_pruned_when_boundary_shrinks(seeded):
    revise("Testco", {
        "inferred_naics": ["541512", "541519"],
        "naics_meta": [{"code": "541512", "origin": "system"},
                       {"code": "541519", "origin": "system"}],
    })
    # drop 541519 from the boundary WITHOUT re-sending meta
    revise("Testco", {"inferred_naics": ["541512"]})
    s = load_packet("Testco").strategy
    assert [m.code for m in s.naics_meta] == ["541512"]  # orphan pruned


def test_move_out_and_restore_before_approval(seeded):
    # move 541519 out (kept, not deleted), then restore it
    revise("Testco", {
        "inferred_naics": ["541512"],
        "kept_out_naics": [{"code": "541519", "origin": "system",
                            "rationale": "too broad", "note": "moved out"}],
    })
    s = load_packet("Testco").strategy
    assert s.inferred_naics == ["541512"]
    assert [e.code for e in s.kept_out_naics] == ["541519"]
    # restore
    revise("Testco", {"inferred_naics": ["541512", "541519"],
                      "kept_out_naics": []})
    s = load_packet("Testco").strategy
    assert "541519" in s.inferred_naics and s.kept_out_naics == []


def test_move_out_and_restore_after_approval_keeps_approval(seeded):
    decide("Testco", approve=True)
    amend_terms("Testco", inferred_naics=["541512"],
                kept_out_naics=[{"code": "541519", "role": "boundary",
                                 "rationale": "x", "note": "post-lock move"}])
    pkt = load_packet("Testco")
    assert pkt.status.value == "approved"        # no new approval leg
    assert pkt.strategy.inferred_naics == ["541512"]
    assert [e.code for e in pkt.strategy.kept_out_naics] == ["541519"]


def test_candidate_promotion_preserves_reason_and_provenance(seeded):
    # a promoted judgment candidate persists as consultant-origin with its reason
    revise("Testco", {
        "inferred_naics": ["541512", "541519", "541513"],
        "naics_meta": [{"code": "541513", "origin": "consultant",
                        "rationale": "the MDR keyword points at a managed-service lane",
                        "note": "promoted from needs-judgment"}],
    })
    m = {e.code: e for e in load_packet("Testco").strategy.naics_meta}
    assert m["541513"].origin == "consultant"
    assert "managed-service" in m["541513"].rationale


def test_reconciliation_mirrors_sam_and_usaspending_never_web(seeded):
    revise("Testco", {"inferred_naics": ["541512", "541611"]})
    specs = {sp.source: sp.naics_codes
             for sp in load_packet("Testco").strategy.searches}
    assert specs["sam.gov"] == ["541512", "541611"]
    assert specs["usaspending.gov"] == ["541512", "541611"]
    assert specs["web"] == []                    # web never carries NAICS


def test_api_shapes_unchanged_additive_fields_flow(tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")  # noqa: F841
    import ui.server as srv
    monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
    request_approval(_strategy(), alert_fn=lambda *_a: None)
    client = srv.app.test_client()
    # revise accepts the additive fields; response keys are unchanged
    r = client.post("/api/strategy/revise", json={
        "client_name": "Testco", "updates": {
            "inferred_naics": ["541512"],
            "naics_meta": [{"code": "541512", "role": "core", "origin": "system"}],
            "kept_out_naics": [{"code": "541519", "origin": "system"}],
        }})
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"client_name", "status", "revision_count",
                         "revised_at", "strategy"}
    assert body["strategy"]["inferred_naics"] == ["541512"]
    # malformed code -> 400, nothing persisted
    bad = client.post("/api/strategy/revise", json={
        "client_name": "Testco", "updates": {"inferred_naics": ["54151"]}})
    assert bad.status_code == 400


# ── UI source contracts (the three lanes + inspector; MDR generalized) ───────
def _index():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(root, "ui", "index.html"), encoding="utf-8").read()


def test_ui_has_three_lane_naics_workshop_and_inspector():
    html = _index()
    assert "function renderNaicsWorkshop" in html
    assert "In the boundary" in html and "Needs your judgment" in html
    assert "Kept out ·" in html
    assert "function buildNaicsInspector" in html
    assert "naics-card" in html                  # cards, not chips
    assert "buildNaicsJudgment" in html and "promoteNaicsCandidate" in html


def test_ui_generalizes_mdr_into_a_rules_table_no_hardcode():
    html = _index()
    assert "NAICS_RECONSIDER_RULES" in html       # data-driven
    # the old inline hard-coded nudge is gone
    assert "if (mdr && !g.inferred_naics.includes('541513'))" not in html


def test_ui_naics_edit_uses_no_browser_prompt():
    html = _index()
    # isolate the NAICS workshop FUNCTIONS block (from its constants to chipList)
    start = html.find("const NAICS_PROV_ICON")
    end = html.find("function chipList(", start)
    block = html[start:end]
    assert start != -1 and end != -1
    assert "renderNaicsWorkshop" in block and "buildNaicsInspector" in block
    assert "prompt(" not in block                 # in-page inspector only


def test_ui_naics_has_narrow_layout():
    html = _index()
    assert ".naics-cards{grid-template-columns:1fr 1fr}" in html  # phone reflow
