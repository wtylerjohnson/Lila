"""L19: scope designator — agency name + assessment designator carried
throughout; scope=all is the unqualified Federal Opportunity Assessment.

Offline — temp review dirs, no network, no LLM.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.review import (  # noqa: E402
    artifact_stem, scope_designator, sweep_artifact_path,
)


def test_designator_grammar():
    """all/absent mints NO designator; agency scopes mint 'agency_<abbr>'
    in both the gate form and the artifact form; multi-agency joins."""
    assert scope_designator(None) is None
    assert scope_designator({"all": True}) is None
    assert scope_designator({"agencies": []}) is None
    assert scope_designator({"agencies": ["DHS"]}) == "agency_dhs"
    assert scope_designator({"agencies": [{"abbr": "DHS", "name": "x"}]}) == "agency_dhs"
    assert scope_designator({"agencies": ["DHS", "CISA"]}) == "agency_dhs_cisa"


def _seed_packet(tmp_path, monkeypatch, scope):
    import agents.review as rv
    review = tmp_path / "data" / "review"
    review.mkdir(parents=True)
    monkeypatch.setattr(rv, "REVIEW_DIR", str(review))
    packet = {"client_name": "Acme Federal", "status": "approved",
              "strategy": {"client_name": "Acme Federal", "keywords": [],
                           "inferred_naics": [], "set_aside_angles": [],
                           "searches": [], "pursuit_strategy": ""},
              "search_scope": scope}
    with open(review / "acme_federal.review.json", "w") as f:
        json.dump(packet, f, default=str)
    cleaned = tmp_path / "data" / "cleaned"
    cleaned.mkdir(parents=True)
    return cleaned


def test_all_scope_resolves_unqualified_name(tmp_path, monkeypatch):
    cleaned = _seed_packet(tmp_path, monkeypatch, {"all": True})
    (cleaned / "searches_acme_federal.json").write_text("{}")
    p = sweep_artifact_path("Acme Federal")
    assert p.endswith("searches_acme_federal.json")
    assert artifact_stem("Acme Federal", "federal_opportunity_assessment") == \
        "acme_federal.federal_opportunity_assessment"


def test_scoped_gate_resolves_designated_artifact(tmp_path, monkeypatch):
    cleaned = _seed_packet(tmp_path, monkeypatch, {"agencies": ["DHS"]})
    (cleaned / "searches_acme_federal.agency_dhs.json").write_text("{}")
    # the all-scope artifact ALSO on disk: coexistence, no masquerade
    (cleaned / "searches_acme_federal.json").write_text("{}")
    p = sweep_artifact_path("Acme Federal")
    assert p.endswith("searches_acme_federal.agency_dhs.json")
    assert artifact_stem("Acme Federal", "assessment") == \
        "acme_federal.agency_dhs.assessment"


def test_scoped_gate_without_artifact_fails_loudly(tmp_path, monkeypatch):
    cleaned = _seed_packet(tmp_path, monkeypatch, {"agencies": ["DHS"]})
    # only the ALL-market artifact exists: never silently used for a scoped run
    (cleaned / "searches_acme_federal.json").write_text("{}")
    with pytest.raises(FileNotFoundError) as e:
        sweep_artifact_path("Acme Federal")
    assert "agency_dhs" in str(e.value)
    assert "run the searches step" in str(e.value)


def test_dashboard_follows_the_gate(tmp_path, monkeypatch):
    """Stage detection and deliverable resolution track the gate scope: the
    scoped engagement sees its own artifact family; flipping the gate to all
    flips the whole board to the unqualified family."""
    flask = pytest.importorskip("flask")  # noqa: F841
    import ui.server as srv
    from tests.test_ui import _mk_client
    root = str(tmp_path)
    review = _mk_client(root)
    monkeypatch.setattr(srv, "REVIEW_DIR", review)
    cleaned = os.path.join(root, "data", "cleaned")
    os.makedirs(cleaned, exist_ok=True)
    # gate scoped to DHS
    pkt_path = os.path.join(review, "acme_federal.review.json")
    pkt = json.load(open(pkt_path))
    pkt["search_scope"] = {"agencies": ["DHS"]}
    json.dump(pkt, open(pkt_path, "w"))

    assert srv._gate_designator("acme_federal", review) == "agency_dhs"
    assert srv._sweep_name("acme_federal", review) == \
        "searches_acme_federal.agency_dhs.json"

    # only the all-scope sweep exists -> the scoped engagement reads NOT searched
    open(os.path.join(cleaned, "searches_acme_federal.json"), "w").write("{}")
    s = srv.client_state("acme_federal", review)
    assert s["stages"]["searched"] is False
    # the scoped sweep lands -> searched flips true
    open(os.path.join(cleaned,
                      "searches_acme_federal.agency_dhs.json"), "w").write("{}")
    s = srv.client_state("acme_federal", review)
    assert s["stages"]["searched"] is True

    # gate back to all -> unqualified family again
    pkt["search_scope"] = {"all": True}
    json.dump(pkt, open(pkt_path, "w"))
    assert srv._sweep_name("acme_federal", review) == "searches_acme_federal.json"
