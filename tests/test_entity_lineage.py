"""Corporate lineage notes + rank-carrying teaming facts.

The 2026-07-09 review caught two data-integrity failures in one section:
GDIT called "the largest subaward-flow prime" while Perspecta sat $45M higher
in the same list (an inferred superlative), and Perspecta/Peraton listed as
two independent teaming doors (one corporate lineage, split across legacy
contract records)."""

import json
import os

import pytest

from agents.reports.facts import _Counter, _teaming_facts
from tools.entity_lineage import lineage_of, shared_door


@pytest.fixture(autouse=True)
def _seed_lineage_crosswalk(_isolate_sam_state):
    """L14 migration: the lineage map moved from a hardcoded dict to
    data/entities/crosswalk.json. Seed the isolated LILA_ENTITIES_DIR with
    the same Peraton family the old map carried; every behavioral assertion
    below is unchanged."""
    d = os.environ["LILA_ENTITIES_DIR"]
    os.makedirs(d, exist_ok=True)
    xwalk = {"entities": [{
        "canonical_id": "peraton", "canonical_name": "Peraton",
        "aliases": [
            {"name": "Perspecta", "type": "acquisition",
             "effective_date": "2021-05-06", "source_url": "https://example.test/peraton"},
            {"name": "Perspecta Enterprise Solutions", "type": "subsidiary",
             "effective_date": "2021-05-06", "source_url": "https://example.test/peraton"},
            {"name": "Perspecta Engineering", "type": "subsidiary",
             "effective_date": "2021-05-06", "source_url": "https://example.test/peraton"},
            {"name": "Peraton Enterprise Solutions", "type": "subsidiary",
             "effective_date": None, "source_url": "https://example.test/peraton"},
            {"name": "Vencore", "type": "acquisition",
             "effective_date": "2018-06-01", "source_url": "https://example.test/peraton"}],
        "ueis": [],
        "notes": "Perspecta was acquired by Peraton (May 2021); records under "
                 "both names are one corporate door"}]}
    with open(os.path.join(d, "crosswalk.json"), "w") as f:
        json.dump(xwalk, f)


def test_lineage_map_resolves_legacy_names():
    parent, note = lineage_of("PERSPECTA ENTERPRISE SOLUTIONS LLC")
    assert parent == "Peraton" and "May 2021" in note
    assert lineage_of("GENERAL DYNAMICS INFORMATION TECHNOLOGY") is None
    assert shared_door("PERSPECTA ENGINEERING INC",
                       "PERATON ENTERPRISE SOLUTIONS LLC") is not None
    assert shared_door("GDIT", "PERATON INC") is None


def _results(primes):
    return {"subawards": {"primes": primes}}


def test_teaming_facts_carry_rank_and_lineage_notes():
    primes = [
        {"name": "GENERAL DYNAMICS INFORMATION TECHNOLOGY, INC.",
         "total": 893_700_000.0, "subaward_count": 16},
        {"name": "PERSPECTA ENTERPRISE SOLUTIONS LLC",
         "total": 938_700_000.0, "subaward_count": 19},
        {"name": "PERATON ENTERPRISE SOLUTIONS LLC",
         "total": 559_600_000.0, "subaward_count": 9},
    ]
    facts = _teaming_facts(_results(primes), _Counter())
    # 3 per-entity facts + the post-merge leaderboard (Perspecta/Peraton
    # share one corporate door, so the merged ranking ships as its own fact)
    assert len(facts) == 4
    assert "corporate lineage merged" in facts[3].text
    # sorted by dollars: the TOP fact carries the superlative, others the rank
    assert "PERSPECTA" in facts[0].text and "LARGEST subaward conduit" in facts[0].text
    assert "GENERAL DYNAMICS" in facts[1].text and "ranked #2 of 3" in facts[1].text
    assert "ranked #3 of 3" in facts[2].text
    # only ONE fact may claim largest; superlatives are copied, never inferred
    assert sum("LARGEST" in f.text for f in facts) == 1
    # Perspecta shares a door with the listed Peraton entity: note on the
    # legacy-named fact, naming the parent, so one door never reads as two
    assert "LINEAGE NOTE" in facts[0].text and "Peraton" in facts[0].text
    assert "never present these records as a teaming door separate" in facts[0].text
    # GDIT has no lineage overlap: no note
    assert "LINEAGE NOTE" not in facts[1].text


def test_no_lineage_note_when_parent_not_listed():
    primes = [
        {"name": "PERSPECTA ENTERPRISE SOLUTIONS LLC",
         "total": 938_700_000.0, "subaward_count": 19},
        {"name": "GENERAL DYNAMICS INFORMATION TECHNOLOGY, INC.",
         "total": 893_700_000.0, "subaward_count": 16},
    ]
    facts = _teaming_facts(_results(primes), _Counter())
    # Peraton is not in the listed set, so no intra-list double-door exists
    assert all("LINEAGE NOTE" not in f.text for f in facts)


def test_teaming_facts_house_style():
    facts = _teaming_facts(_results([
        {"name": "X CORP", "total": 5.0, "subaward_count": 1}]), _Counter())
    assert all("—" not in f.text for f in facts)


def test_post_merge_leaderboard_fact_names_number_one():
    """'#2 of 8' must never beg the question of an unnamed, mergeable #1:
    when listed primes share a corporate door, one citable fact carries the
    combined figure and the post-merge order (2026-07-09 review)."""
    primes = [
        {"name": "PERSPECTA ENTERPRISE SOLUTIONS LLC", "total": 938_679_345.60, "subaward_count": 10},
        {"name": "GENERAL DYNAMICS INFORMATION TECHNOLOGY, INC.", "total": 893_698_808.53, "subaward_count": 16},
        {"name": "PERATON ENTERPRISE SOLUTIONS LLC", "total": 559_600_000.0, "subaward_count": 4},
    ]
    facts = _teaming_facts(_results(primes), _Counter())
    board = [f for f in facts if "corporate lineage merged" in f.text]
    assert len(board) == 1
    t = board[0].text
    assert "#1 Peraton" in t and "one corporate door" in t
    assert "#2 GENERAL DYNAMICS" in t
    assert "PERSPECTA" in t  # the merged entities are named, not hidden


def test_no_leaderboard_fact_without_shared_doors():
    primes = [
        {"name": "LEIDOS, INC.", "total": 500.0, "subaward_count": 1},
        {"name": "CGI FEDERAL INC.", "total": 400.0, "subaward_count": 1},
    ]
    facts = _teaming_facts(_results(primes), _Counter())
    assert not [f for f in facts if "corporate lineage merged" in f.text]
