"""Regression coverage for the NAICS workshop's backend state contract."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import agents.review as review
from agents.decisions.schemas import IntakeStrategy, NaicsEntry, SearchSpec
from agents.review import (
    RevisionError, ReviewStatus, amend_terms, decide, load_packet,
    request_approval, revise,
)


def _strategy() -> IntakeStrategy:
    return IntakeStrategy(
        client_name="Testco",
        pursuit_strategy="pursue the exact lane",
        inferred_naics=["541512", "541519"],
        naics_meta=[
            NaicsEntry(
                code="541512", title="Computer Systems Design",
                role="core", origin="system", rationale="primary lane",
                note="retain exact provenance",
            ),
            NaicsEntry(
                code="541519", title="Other Computer Related Services",
                role="boundary", origin="consultant",
                rationale="adjacent managed-service lane", note="reviewed",
            ),
        ],
        searches=[
            SearchSpec(source="sam.gov", naics_codes=[], rationale="sam"),
            SearchSpec(source="usaspending.gov", naics_codes=[], rationale="usa"),
            # Deliberately stale: canonical persistence must clear web NAICS.
            SearchSpec(source="web", naics_codes=["541512"], rationale="web"),
        ],
        confidence=0.8,
        review_gate="approve",
    )


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
    request_approval(_strategy(), alert_fn=lambda *_args: None)
    return tmp_path


def _spec_codes(packet):
    return {spec.source: list(spec.naics_codes)
            for spec in packet.strategy.searches}


def test_search_reconciliation_uses_source_identity_and_survives_empty_restore(
        seeded):
    packet = load_packet("Testco")
    assert _spec_codes(packet) == {
        "sam.gov": ["541512", "541519"],
        "usaspending.gov": ["541512", "541519"],
        "web": [],
    }

    packet = revise("Testco", {"inferred_naics": []})
    assert _spec_codes(packet) == {
        "sam.gov": [], "usaspending.gov": [], "web": []}

    packet = revise("Testco", {"inferred_naics": ["541519"]})
    assert _spec_codes(packet) == {
        "sam.gov": ["541519"],
        "usaspending.gov": ["541519"],
        "web": [],
    }


def test_inferred_only_move_out_and_restore_is_lossless(seeded):
    original = {entry.code: entry.model_dump(mode="json")
                for entry in load_packet("Testco").strategy.naics_meta}

    moved = revise("Testco", {"inferred_naics": ["541512"]}).strategy
    assert [entry.code for entry in moved.naics_meta] == ["541512"]
    assert [entry.code for entry in moved.kept_out_naics] == ["541519"]
    assert moved.kept_out_naics[0].model_dump(mode="json") == original["541519"]

    restored = revise("Testco", {
        "inferred_naics": ["541512", "541519"],
        # The manual Add path can submit a generic replacement while removing
        # the historical kept-out card. The transition must restore the full
        # prior record rather than accepting this lossy placeholder.
        "naics_meta": [{
            "code": "541519", "title": "", "role": "boundary",
            "origin": "consultant", "rationale": "consultant added at review",
            "note": "consultant added",
        }],
        "kept_out_naics": [],
    }).strategy
    assert restored.kept_out_naics == []
    meta = {entry.code: entry.model_dump(mode="json")
            for entry in restored.naics_meta}
    assert meta["541519"] == original["541519"]


def test_restore_accepts_an_explicitly_edited_replacement(seeded):
    revise("Testco", {"inferred_naics": ["541512"]})
    restored = revise("Testco", {
        "inferred_naics": ["541512", "541519"],
        "naics_meta": [{
            "code": "541519", "title": "Refined service boundary",
            "role": "core", "origin": "edited",
            "rationale": "operator refined the restored code",
            "note": "explicit restore edit",
        }],
        "kept_out_naics": [],
    }).strategy
    entry = {item.code: item for item in restored.naics_meta}["541519"]
    assert entry.title == "Refined service boundary"
    assert entry.role == "core"
    assert entry.origin == "edited"
    assert entry.rationale == "operator refined the restored code"
    assert entry.note == "explicit restore edit"


def test_authoritative_boundary_resolves_active_kept_out_overlap_losslessly(
        seeded):
    # 541513 arrives in the authoritative boundary and in the kept-out payload.
    # Active wins, but the supplied record becomes its metadata rather than
    # being discarded.
    packet = revise("Testco", {
        "inferred_naics": ["541512", "541519", "541513"],
        "kept_out_naics": [{
            "code": "541513", "title": "Computer Facilities Management",
            "role": "boundary", "origin": "consultant",
            "rationale": "restorable judgment", "note": "preserve this",
        }],
    })
    strategy = packet.strategy
    assert "541513" not in [entry.code for entry in strategy.kept_out_naics]
    restored = {entry.code: entry for entry in strategy.naics_meta}["541513"]
    assert restored.rationale == "restorable judgment"
    assert restored.note == "preserve this"


@pytest.mark.parametrize("field,value", [
    ("inferred_naics", None),
    ("inferred_naics", {}),
    ("inferred_naics", [541512]),
    ("naics_meta", None),
    ("naics_meta", {}),
    ("naics_meta", ["541512"]),
    ("kept_out_naics", False),
    ("kept_out_naics", ""),
    ("kept_out_naics", {"code": "541512"}),
    ("naics_meta", [{"code": "54151"}]),
    ("naics_meta", [{"code": "541512", "role": "primary"}]),
    ("naics_meta", [{"code": "541512", "role": ""}]),
    ("kept_out_naics", [{"code": "541512", "origin": "alien"}]),
])
def test_malformed_naics_entries_are_rejected_without_persistence(
        seeded, field, value):
    path = review._path("Testco")
    before = open(path, encoding="utf-8").read()
    with pytest.raises(RevisionError):
        revise("Testco", {field: value})
    assert open(path, encoding="utf-8").read() == before


@pytest.mark.parametrize("payload", [
    {"code": "bad"},
    {"code": "５４１５１２"},
    {"code": "٥٤١٥١٢"},
    {"code": "541512", "role": "primary"},
    {"code": "541512", "role": ""},
    {"code": "541512", "origin": "alien"},
])
def test_naics_entry_schema_rejects_invalid_contract_values(payload):
    with pytest.raises(ValidationError):
        NaicsEntry.model_validate(payload)


def test_api_invalid_naics_shapes_are_400_and_do_not_persist(
        seeded, monkeypatch):
    flask = pytest.importorskip("flask")  # noqa: F841
    import ui.server as server

    monkeypatch.setattr(server, "REVIEW_DIR", str(seeded))
    client = server.app.test_client()
    path = review._path("Testco")
    before = open(path, encoding="utf-8").read()

    for updates in (
        {"inferred_naics": None},
        {"inferred_naics": [541512]},
        {"naics_meta": {}},
        {"naics_meta": ["541512"]},
        {"kept_out_naics": False},
    ):
        response = client.post("/api/strategy/revise", json={
            "client_name": "Testco", "updates": updates,
        })
        assert response.status_code == 400
        assert open(path, encoding="utf-8").read() == before

    decide("Testco", approve=True)
    approved_before = open(path, encoding="utf-8").read()
    for invalid in (
        {"kept_out_naics": {"code": "541512"}},
        {"inferred_naics": None},
        {"naics_meta": None},
        {"kept_out_naics": None},
    ):
        response = client.post("/api/strategy/terms", json={
            "client_name": "Testco", **invalid,
        })
        assert response.status_code == 400
        assert open(path, encoding="utf-8").read() == approved_before


def test_journal_captures_full_naics_before_and_after(seeded):
    revise("Testco", {"naics_meta": [
        {
            "code": "541512", "title": "Computer Systems Design",
            "role": "core", "origin": "edited", "rationale": "primary lane",
            "note": "operator changed this note",
        },
        load_packet("Testco").strategy.naics_meta[1].model_dump(mode="json"),
    ]})
    events = [json.loads(line) for line in
              open(review._journal_path("Testco"), encoding="utf-8")]
    revision = events[-1]
    assert revision["event"] == "gate_revision"
    assert revision["before"]["naics_meta"][0]["origin"] == "system"
    assert revision["after"]["naics_meta"][0]["origin"] == "edited"
    assert revision["after"]["naics_meta"][0]["note"] == (
        "operator changed this note")
    assert "kept_out_naics" in revision["before"]
    assert "kept_out_naics" in revision["after"]


def test_post_approval_inferred_only_amendment_keeps_approval_and_history(
        seeded):
    decide("Testco", approve=True)
    packet = amend_terms("Testco", inferred_naics=["541512"])
    assert packet.status == ReviewStatus.APPROVED
    assert [entry.code for entry in packet.strategy.kept_out_naics] == ["541519"]
    event = json.loads(
        open(review._journal_path("Testco"), encoding="utf-8").readlines()[-1])
    assert event["event"] == "term_amendment"
    assert event["after"]["kept_out_naics"][0]["code"] == "541519"


def test_packet_missing_additive_fields_remains_backward_compatible(
        seeded):
    path = review._path("Testco")
    payload = json.loads(open(path, encoding="utf-8").read())
    payload["strategy"].pop("naics_meta")
    payload["strategy"].pop("kept_out_naics")
    path_obj = seeded / "testco.review.json"
    path_obj.write_text(json.dumps(payload), encoding="utf-8")

    strategy = load_packet("Testco").strategy
    assert strategy.inferred_naics == ["541512", "541519"]
    assert strategy.naics_meta == []
    assert strategy.kept_out_naics == []
