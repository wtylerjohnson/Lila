"""Generating the three client files from an APPROVED review packet.

Offline: packets are built in the test, files land in tmp_path.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decisions.client_files import (  # noqa: E402
    HUMAN_REQUIRED, generate, write_generated,
)


def _packet(**over):
    strategy = {
        "client_name": "NetApp",
        "keywords": [
            {"term": "enterprise data storage", "category": "capability"},
            {"term": "object storage", "category": "capability"},
            {"term": "ONTAP", "category": "technology"},
            {"term": "Department of the Navy", "category": "agency"},
            {"term": "334112", "category": "naics"},
            {"term": "storage as a service", "category": "capability"},
        ],
        "research_entities": [
            {"name": "ONTAP", "kind": "product"},
            {"name": "Pure Storage", "kind": "competitor"},
            {"name": "Carahsoft", "kind": "reseller"},
        ],
        "inferred_naics": ["334112", "518210"],
        "target_agencies": ["Department of the Navy"],
    }
    strategy.update(over.pop("strategy", {}))
    return {"client_name": "NetApp", "status": "approved",
            "strategy": strategy, **over}


# ---- what it derives ------------------------------------------------------- #
def test_capability_core_survives_routing():
    out = generate(_packet(), as_of="2026-07-28")
    core = out["files"]["profile"]["capability_terms"]["core"]
    assert "enterprise data storage" in core
    assert "object storage" in core


@pytest.mark.parametrize("junk", ["ONTAP", "Department of the Navy", "334112",
                                  "storage as a service"])
def test_the_router_keeps_junk_out_of_the_profile(junk):
    """A brand name, an agency name, a bare NAICS and a sentence. All four
    were in a real NetApp intake and all four reached the search vocabulary
    before the router existed."""
    out = generate(_packet(), as_of="2026-07-28")
    assert junk not in out["files"]["profile"]["capability_terms"]["core"]


def test_entities_become_the_competitor_list_and_the_client_leads_it():
    out = generate(_packet(), as_of="2026-07-28")
    rivals = out["files"]["profile"]["named_competitors_and_incumbents"]
    assert rivals[0] == "NetApp"
    assert "ONTAP" in rivals and "Pure Storage" in rivals
    assert "Carahsoft" not in rivals, "a reseller is not a competitor"


def test_naics_and_agencies_carry_through():
    out = generate(_packet(), as_of="2026-07-28")
    p = out["files"]["profile"]
    assert p["naics_boundary"] == ["334112", "518210"]
    assert p["mission_components"] == ["Department of the Navy"]
    assert out["files"]["taxonomy"]["code_universe"]["naics"] == ["334112", "518210"]


# ---- what it refuses to invent --------------------------------------------- #
def test_the_seven_undecidable_fields_are_left_empty():
    """A blank stops the sweep and asks. A wrong exclusion list silently
    poisons every search for the life of the engagement."""
    out = generate(_packet(), as_of="2026-07-28")
    p, t, s = (out["files"][k] for k in ("profile", "taxonomy", "scope"))
    assert p["capability_summary"] == ""
    assert p["capability_terms"]["excluded"] == []
    assert p["capability_terms"]["adjacent"] == []
    assert t["exclude"] == []
    assert t["code_universe"]["psc"] == []
    assert s["preset"] == ""
    assert s["remove"] == []


def test_every_undecidable_field_is_reported_with_a_reason():
    out = generate(_packet(), as_of="2026-07-28")
    fields = {h["field"] for h in out["human_required"]}
    assert "profile.capability_terms.excluded" in fields
    assert "scope.preset" in fields
    assert len(out["human_required"]) == len(HUMAN_REQUIRED)
    assert all(h["needs"] and h["why"] for h in out["human_required"])


def test_the_polysemy_list_is_named_as_the_one_a_model_cannot_produce():
    out = generate(_packet(), as_of="2026-07-28")
    why = next(h["why"] for h in out["human_required"]
               if h["field"] == "profile.capability_terms.excluded")
    assert "steelhead" in why.lower() or "polysemy" in why.lower()


# ---- the gate stays shut --------------------------------------------------- #
def test_generation_never_reports_sweep_ready():
    """Generating files does not open the sweep gate. An empty preset keeps
    it shut until an operator decides the engagement."""
    out = generate(_packet(), as_of="2026-07-28")
    assert out["sweep_ready"] is False
    assert "scope.preset" in out["sweep_blocked_by"]


# ---- writing --------------------------------------------------------------- #
def test_it_writes_three_files(tmp_path):
    res = write_generated(_packet(), root=str(tmp_path), as_of="2026-07-28")
    assert sorted(res["written"]) == ["capability_taxonomy.json",
                                      "engagement_scope.json", "profile.json"]
    for name in res["written"]:
        assert json.load(open(os.path.join(res["dir"], name)))


def test_it_never_overwrites_a_hand_authored_file(tmp_path):
    """Riverbed's exclusion list took a human who knew the name collides with
    waterways. Regenerating over it would silently delete 22 guards."""
    res = write_generated(_packet(), root=str(tmp_path), as_of="2026-07-28")
    path = os.path.join(res["dir"], "profile.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"client_name": "NetApp", "hand": "authored"}, fh)
    again = write_generated(_packet(), root=str(tmp_path), as_of="2026-07-28")
    assert "profile.json" in again["kept_existing"]
    assert "profile.json" not in again["written"]
    assert json.load(open(path))["hand"] == "authored"


def test_a_thin_packet_does_not_crash(tmp_path):
    out = generate({"client_name": "Acme", "strategy": {}}, as_of="2026-07-28")
    assert out["files"]["profile"]["capability_terms"]["core"] == []
    assert out["human_required"]
