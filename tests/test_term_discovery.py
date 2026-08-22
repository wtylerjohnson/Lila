"""Pre-press vocabulary discovery: propose from the corpus, never decide.

The failure this ends: three times in one day a client's approved frame
missed the language the government writes, and three times the fix was a
hand-edited synonym list AFTER the miss. A hand-maintained list only ever
learns from the miss you already noticed.

The two laws that make it safe to run automatically:
  it PROPOSES, never applies. Capability terms are the engagement boundary
  and stay operator-owned; a proposal reaches a search only once the
  operator moves it into the profile.
  it costs NOTHING. Local store only, zero model calls, zero credits, and a
  failure is disclosed rather than sinking the press.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import term_discovery as td  # noqa: E402

_COLS = ("notice_id", "title", "description_prefix", "naics", "psc",
         "agency", "subtier", "posted")


@pytest.fixture()
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE notices (%s)" % ", ".join(
        f"{c} TEXT" for c in _COLS))
    # 40 notices in the client's narrow NAICS that all say the term of art,
    # against 2,000 unrelated ones that do not.
    for i in range(40):
        conn.execute("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?)", (
            f"rel{i}", "Audit Remediation Services",
            "financial statement audit remediation support", "541219", "R704",
            "VA", "VHA", "2026-07-01"))
    for i in range(2000):
        conn.execute("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?)", (
            f"bg{i}", "Grounds Maintenance",
            "mowing and landscaping the contractor shall be responsible",
            "561730", "S208", "ARMY", "USACE", "2026-07-01"))
    conn.commit()
    yield conn
    conn.close()


def test_it_costs_nothing_and_calls_nobody(store):
    out = td.mine_candidates(store, ["audit"], naics=["541219"])
    receipt = out["receipt"]
    assert receipt["network_calls"] == 0
    assert receipt["model_calls"] == 0
    assert receipt["credits_spent"] == 0


def test_it_finds_the_term_of_art_the_frame_did_not_have(store):
    """THE POINT. The frame says 'audit'; the government says 'audit
    remediation', and that phrase is what surfaces the DIA RFI."""
    out = td.mine_candidates(store, ["audit"], naics=["541219"])
    terms = [c["term"] for c in out["candidates"]]
    assert any("remediation" in t for t in terms), terms


def test_every_candidate_carries_its_real_yield_and_sample_titles(store):
    """The operator reviews evidence, never a suggestion. The titles are the
    receipt; the count is only the index."""
    out = td.mine_candidates(store, ["audit"], naics=["541219"])
    for candidate in out["candidates"]:
        assert candidate["store_hits"] > 0
        assert candidate["lift"] >= td.MIN_LIFT
        assert candidate["samples"]
        assert candidate["samples"][0]["title"]
        assert candidate["why"]


def test_boilerplate_is_not_proposed_as_a_term_of_art(store):
    """'the contractor shall be' is the most common phrase in federal text.
    Lift, not frequency, is what separates a subject from a template."""
    out = td.mine_candidates(store, ["audit"], naics=["541219"])
    for candidate in out["candidates"]:
        assert "shall be" not in candidate["term"]
        assert "the contractor" not in candidate["term"]


def test_a_naics_code_too_broad_to_name_a_subject_is_dropped(store):
    """541512 holds 1,414 notices and describes an industry; 541219 holds
    100 and every one is in this client's world."""
    for i in range(td.NAICS_SEED_MAX + 50):
        store.execute("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?)", (
            f"broad{i}", "Systems Design", "generic", "541512", "D302",
            "GSA", "FAS", "2026-07-01"))
    store.commit()
    kept = td._naics_seed_codes(store, ["541219", "541512"])
    assert kept == ["541219"]


def test_a_frame_that_matches_nothing_says_so_rather_than_guessing(store):
    out = td.mine_candidates(store, ["nonexistent phrase here"], naics=[])
    assert out["candidates"] == []
    assert "no stored notice" in out["receipt"]["why"]


def test_a_term_already_in_the_frame_is_never_re_proposed(store):
    out = td.mine_candidates(store, ["audit remediation"], naics=["541219"])
    assert not any(c["term"] == "audit remediation"
                   for c in out["candidates"])


# ===== the press wiring: propose, never apply ============================= #
def test_the_press_step_writes_a_review_artifact_and_applies_nothing(
        tmp_path, monkeypatch):
    from agents.golden_press.press import _term_discovery

    root = tmp_path
    (root / "clients" / "acme").mkdir(parents=True)
    (root / "clients" / "acme" / "profile.json").write_text(json.dumps({
        "client_name": "acme",
        "capability_terms": {"core": ["audit"]},
        "naics_boundary": ["541219"]}), encoding="utf-8")
    monkeypatch.setenv("LILA_NOTICE_STORE_DIR", str(tmp_path / "store"))

    lines: list = []
    _term_discovery("acme", "acme", root, lines.append)
    joined = " ".join(lines)
    # it ran, it said so, and it applied nothing
    assert "term discovery" in joined
    profile = json.loads(
        (root / "clients" / "acme" / "profile.json").read_text())
    assert "discovered_terms_approved" not in profile
    assert profile["capability_terms"]["core"] == ["audit"]


def test_a_discovery_failure_never_sinks_the_press(tmp_path):
    """Disclosed and survivable: the press continues on the approved frame."""
    from agents.golden_press.press import _term_discovery

    lines: list = []
    out = _term_discovery("nobody", "nobody", tmp_path, lines.append)
    assert out is None
    assert any("no client profile" in ln or "skipped" in ln for ln in lines)


def test_only_operator_approved_discoveries_reach_the_search(tmp_path,
                                                             monkeypatch):
    """THE GATE. A proposal is inert until the operator moves it into the
    profile, which keeps the engagement boundary operator-owned."""
    from types import SimpleNamespace

    from agents.golden_press import retrieval

    monkeypatch.setattr(
        retrieval, "_approved_discoveries", lambda strategy: [])
    strategy = SimpleNamespace(client_name="acme", research_entities=[])
    before = retrieval.screen_vocabulary(["improper payment prevention"],
                                         strategy)
    assert not any("audit remediation" == t for t in before)

    monkeypatch.setattr(retrieval, "_approved_discoveries",
                        lambda strategy: ["audit remediation"])
    after = retrieval.screen_vocabulary(["improper payment prevention"],
                                        strategy)
    assert "audit remediation" in after
