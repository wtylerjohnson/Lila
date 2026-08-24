"""Pure contract tests for the shared Targeting inventory projection."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

import pytest

from agents.review import targeting_play_key
from agents.targeting_inventory import (
    load_target_inventory,
    prime_names_from_searches,
    project_target_inventory,
)
from tools.contact_graph.outreach import OutreachReadError


def _profile(name: str, agency: str):
    return SimpleNamespace(
        person_name=name,
        normalized_name=name.lower(),
        agency=agency,
        offices=[f"{agency} / BUYING OFFICE"],
        naics=["541512"],
        titles=["Contracting Officer"],
        role_types=["primary"],
        sighting_count=2,
        first_observed=date(2026, 8, 1),
        last_observed=date(2026, 8, 20),
        channels=[],
        rotation=None,
    )


def test_projection_preserves_control_room_reason_rank_and_bucket_contract():
    pursuit = SimpleNamespace(
        rank=1,
        title="Secure Network Refresh",
        source_id="P1",
        amendments=[{"source_id": "P1-A1"}],
    )
    watch = SimpleNamespace(id="W1", title="Forming Network Program")
    document = SimpleNamespace(
        board=SimpleNamespace(pursuits=[pursuit]),
        watchlist=SimpleNamespace(entries=[watch]),
    )
    observations = [
        SimpleNamespace(notice_id="P1", person_name="Jane Doe", agency="DOD"),
        SimpleNamespace(notice_id="W1", person_name="Sam Vale", agency="GSA"),
        SimpleNamespace(notice_id="P1-A1", person_name="Nora Poc", agency="DOD"),
    ]
    outreach = [
        {"id": "dod||jane doe", "person_name": "Jane Doe",
         "normalized_name": "jane doe", "agency": "DOD",
         "sighting_count": 3},
        {"id": "epa||manual target", "person_name": "Manual Target",
         "normalized_name": "manual target", "agency": "EPA",
         "client": "acme", "sighting_count": 0},
        {"id": "gsa||sam vale", "person_name": "Sam Vale",
         "normalized_name": "sam vale", "agency": "GSA",
         "company": "HII Mission Technologies Corp", "sighting_count": 2},
    ]
    original = deepcopy(outreach)

    projected = project_target_inventory(
        slug="acme",
        document=document,
        profiles=[_profile("Nora Poc", "DOD")],
        observations=observations,
        outreach_entries=outreach,
        prime_names={"hii mission technologies"},
        today=date(2026, 8, 24),
        logo_domain=lambda _agency, _office: "agency.example",
    )

    assert outreach == original
    assert [row["person_name"] for row in projected["targets"]] == [
        "Jane Doe", "Manual Target", "Sam Vale"]
    assert [row["reason_kind"] for row in projected["targets"]] == [
        "pursuit", "manual", "watchlist"]
    assert [row["bucket"] for row in projected["targets"]] == [
        "solicitation", "candidate", "prime"]
    assert projected["targets"][0]["play_id"] == targeting_play_key(
        "POC on pursuit #1 · Secure Network Refresh")
    assert projected["pursuit_pocs"][0]["person_name"] == "Nora Poc"
    assert projected["pursuit_pocs"][0]["logo_domain"] == "agency.example"


def test_prime_name_projection_matches_existing_sweep_shape():
    searches = {"results": {"subawards": {"primes": [
        {"name": "HII Mission Technologies, Inc."},
        {"name": "  Leidos  "},
        {"not_name": "ignored"},
    ]}}}

    assert prime_names_from_searches(searches) == {
        "hii mission technologies", "leidos"}
    assert prime_names_from_searches(None) == set()


def test_live_loader_reads_manual_target_without_mutating_sources(
        tmp_path, monkeypatch):
    review = tmp_path / "data" / "review"
    graph = tmp_path / "data" / "state" / "contact_graph"
    review.mkdir(parents=True)
    graph.mkdir(parents=True)
    (review / "acme.review.json").write_text(json.dumps({
        "client_name": "Acme",
        "status": "approved",
        "strategy": {},
        "search_scope": {"all": True},
    }), encoding="utf-8")
    outreach = graph / "outreach.json"
    outreach.write_text(json.dumps({
        "version": 1,
        "entries": [{
            "id": "gsa||alex buyer",
            "person_name": "Alex Buyer",
            "normalized_name": "alex buyer",
            "agency": "GSA",
            "title": "Contracting Officer",
            "client": "acme",
            "added_at": "2026-08-20",
            "added_by": "manual",
            "source_note": "manual add",
            "overrides": {},
            "enrich_status": "none",
        }],
        "removed": [],
    }), encoding="utf-8")
    before = outreach.read_bytes()
    monkeypatch.delenv("LILA_CONTACT_GRAPH_DIR", raising=False)

    rows = load_target_inventory(
        "Acme", tmp_path, today=date(2026, 8, 24))

    assert [row["person_name"] for row in rows] == ["Alex Buyer"]
    assert rows[0]["reason"] == "added for this client"
    assert rows[0]["bucket"] == "candidate"
    assert outreach.read_bytes() == before
    assert sorted(path.name for path in graph.iterdir()) == ["outreach.json"]


def test_live_loader_refuses_corrupt_outreach_without_repairing_it(
        tmp_path, monkeypatch):
    graph = tmp_path / "data" / "state" / "contact_graph"
    graph.mkdir(parents=True)
    outreach = graph / "outreach.json"
    corrupt_bytes = b'{"version": 1, "entries": ['
    outreach.write_bytes(corrupt_bytes)
    monkeypatch.delenv("LILA_CONTACT_GRAPH_DIR", raising=False)

    with pytest.raises(OutreachReadError, match="invalid outreach JSON"):
        load_target_inventory("Acme", tmp_path, today=date(2026, 8, 24))

    assert outreach.read_bytes() == corrupt_bytes
    assert list(graph.glob("outreach.corrupt-*.json")) == []
