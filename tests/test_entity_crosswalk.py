"""L14: entity-lineage crosswalk. One canonical door per corporate entity;
alias resolution is normalized and word-boundary; same-door rows merge with
summed dollars and one lineage note; unmerged same-door rows fail lint;
unresolved names pass through and are logged.

Offline — fixture crosswalk in tmp (LILA_ENTITIES_DIR via conftest), no
network, no LLM. The evolving seed crosswalk at data/entities/ is never read.
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.entity_lineage import (  # noqa: E402
    company_matches, door_key, lineage_of, normalize_company, resolve_entity,
    shared_door, unresolved_path,
)
from agents.reports.lint import lint_entity_lineage  # noqa: E402
from tests.test_assessment_document import _searches  # noqa: E402

FIXTURE_XWALK = {
    "entities": [
        {"canonical_id": "peraton", "canonical_name": "Peraton",
         "aliases": [
             {"name": "Perspecta", "type": "acquisition",
              "effective_date": "2021-05-06", "source_url": "https://example.test/peraton"},
             {"name": "Perspecta Enterprise Solutions", "type": "subsidiary",
              "effective_date": "2021-05-06", "source_url": "https://example.test/peraton"}],
         "ueis": [], "notes": "Perspecta acquired by Peraton, May 2021"},
        {"canonical_id": "riverbed", "canonical_name": "Riverbed Technology",
         "aliases": [], "ueis": [], "notes": "one door"},
        {"canonical_id": "gigamon", "canonical_name": "Gigamon",
         "aliases": [], "ueis": [], "notes": "one door"},
        {"canonical_id": "gdit",
         "canonical_name": "General Dynamics Information Technology",
         "aliases": [{"name": "CSRA", "type": "acquisition",
                      "effective_date": "2018-04-03",
                      "source_url": "https://example.test/gdit"}],
         "ueis": [], "notes": "CSRA acquired by General Dynamics, April 2018"},
        {"canonical_id": "general-dynamics", "canonical_name": "General Dynamics",
         "aliases": [], "ueis": [], "notes": "parent door"},
        {"canonical_id": "arbor-day", "canonical_name": "Arbor Day Foundation",
         "aliases": [], "ueis": [], "notes": "single-token trap control"},
        {"canonical_id": "netscout", "canonical_name": "NETSCOUT Systems",
         "aliases": [{"name": "Arbor", "type": "dba", "effective_date": None,
                      "source_url": "https://example.test/netscout"}],
         "ueis": [], "notes": "Arbor is a NETSCOUT product brand"},
    ]
}


def _seed_xwalk(tmp_path):
    d = os.environ["LILA_ENTITIES_DIR"]  # conftest points this at tmp
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "crosswalk.json"), "w") as f:
        json.dump(FIXTURE_XWALK, f)


# ---- 1. alias resolution ----------------------------------------------------
def test_alias_resolution(tmp_path):
    _seed_xwalk(tmp_path)
    assert resolve_entity("PERSPECTA ENTERPRISE SOLUTIONS LLC") == "peraton"
    assert resolve_entity("Perspecta") == "peraton"
    assert resolve_entity("PERATON INC.") == "peraton"
    assert resolve_entity("Gigamon Inc.") == "gigamon"
    assert resolve_entity("CSRA LLC") == "gdit"
    # longest alias wins: GDIT rows never fall to the General Dynamics door
    assert resolve_entity(
        "GENERAL DYNAMICS INFORMATION TECHNOLOGY, INC.") == "gdit"
    assert resolve_entity("GENERAL DYNAMICS MISSION SYSTEMS, INC.") \
        == "general-dynamics"
    # lineage_of: legacy names carry (parent, note); the door itself is None
    assert lineage_of("Perspecta")[0] == "Peraton"
    assert lineage_of("Peraton Inc.") is None
    assert shared_door("Perspecta", "PERATON INC.")


# ---- 2. suffix stripping ----------------------------------------------------
def test_suffix_stripping(tmp_path):
    _seed_xwalk(tmp_path)
    assert normalize_company("ThunderCat Technology, LLC") == "thundercat"
    assert normalize_company("Gigamon, Inc.") == normalize_company("GIGAMON")
    assert normalize_company("SolarWinds Corporation") == "solarwinds"
    # door_key merges pure suffix variants even with no crosswalk row
    assert door_key("BOOZ ALLEN HAMILTON INC") == door_key("Booz Allen Hamilton Inc.")


# ---- 3. word-boundary negative + single-token safety ------------------------
def test_word_boundary_negative(tmp_path):
    _seed_xwalk(tmp_path)
    assert not company_matches("River Edge Systems", "Riverbed")
    assert resolve_entity("River Edge Systems") is None
    # single-token aliases resolve exact-only: 'Arbor' never claims the
    # Arbor Day Foundation, and the foundation keeps its own door
    assert resolve_entity("Arbor") == "netscout"
    assert resolve_entity("Arbor Day Foundation") == "arbor-day"


# ---- 4. dollar-sum on merge -------------------------------------------------
def test_merge_sums_dollars_and_notes_once(tmp_path):
    _seed_xwalk(tmp_path)
    from agents.reports.document import build_document
    s = copy.deepcopy(_searches(3))
    lane = s["results"]["usaspending.gov"][0]
    lane["summary"]["top_incumbents"] = ["PERSPECTA ENTERPRISE SOLUTIONS LLC",
                                         "PERATON INC."]
    lane["awards"] = [
        {"recipient": "PERSPECTA ENTERPRISE SOLUTIONS LLC", "amount": 5_000_000.0,
         "awarding_agency": "Department of Defense", "award_id": "A1",
         "url": "https://www.usaspending.gov/award/A1"},
        {"recipient": "PERATON INC.", "amount": 3_000_000.0,
         "awarding_agency": "Department of Homeland Security", "award_id": "A2",
         "url": "https://www.usaspending.gov/award/A2"},
    ]
    s["results"]["subawards"] = []
    doc = build_document("Testco", searches=s, qualify=None)
    doors = [c for c in doc.competitive.competitors
             if door_key(c.name) == "peraton"]
    assert len(doors) == 1                       # merged, not two rows
    c = doors[0]
    assert c.name == "Peraton"                   # the canonical door
    assert c.dollars == 8_000_000.0              # summed across variants
    assert sorted(c.merged_names) == ["PERATON INC.",
                                      "PERSPECTA ENTERPRISE SOLUTIONS LLC"]
    assert c.lineage_note and "single corporate door" in c.lineage_note
    assert "Perspecta acquired by Peraton" in c.lineage_note
    # evidence rows keep their as-of-contract names
    assert {a["recipient_as_of_contract"] for a in c.awards} == {
        "PERSPECTA ENTERPRISE SOLUTIONS LLC", "PERATON INC."}
    # both agencies attach to the one door
    assert set(c.agencies) == {"Department of Defense",
                               "Department of Homeland Security"}

    # the note renders internal-only; client copy carries the door, no note
    from agents.reports.views import render_assessment
    internal = render_assessment(doc, "internal")
    assert "single corporate door" in internal
    client = render_assessment(doc, "client")
    assert "single corporate door" not in client
    sales = render_assessment(doc, "sales")
    assert "Perspecta" not in sales              # gated model carries no names


# ---- 5. lint: unmerged same-door rows fail ----------------------------------
def test_lint_fails_unmerged_same_door_rows(tmp_path):
    _seed_xwalk(tmp_path)
    bad = ('<table><tbody>'
           '<tr data-entity="competitor"><td><strong>PERSPECTA ENTERPRISE '
           'SOLUTIONS LLC</strong></td><td>$5.0M</td></tr>'
           '<tr data-entity="competitor"><td><strong>PERATON INC.</strong>'
           '</td><td>$3.0M</td></tr></tbody></table>')
    res = lint_entity_lineage(bad)
    assert not res.ok
    assert "one corporate door" in res.violations[0].detail

    merged = ('<tr data-entity="competitor"><td><strong>Peraton</strong></td>'
              '<td>$8.0M</td></tr>'
              '<tr data-entity="competitor"><td><strong>Booz Allen Hamilton'
              '</strong></td><td>$1.0M</td></tr>')
    assert lint_entity_lineage(merged).ok


# ---- 6. unresolved passthrough + traffic log --------------------------------
def test_unresolved_passthrough_and_log(tmp_path):
    _seed_xwalk(tmp_path)
    assert resolve_entity("Zeta Integrators LLC") is None
    assert resolve_entity("Zeta Integrators LLC") is None
    assert door_key("Zeta Integrators LLC") == "zeta integrators"  # passthrough
    with open(unresolved_path()) as f:
        log = json.load(f)
    row = log["zeta integrators"]
    assert row["count"] == 3      # two resolves + door_key's resolve
    assert row["raw"] == "Zeta Integrators LLC"
    assert row["first_seen"]
