"""Scope-workstation identity and discovery are strict and read-only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import agents.workstations as workstations
from agents.workstations import (
    WorkstationError,
    WorkstationRef,
    WorkstationScope,
    canonical_scope,
    discover_workstations,
    load_registry,
    workstation_id,
)


def _write_packet(root: Path, *, scope, status="approved",
                  client_name="NETSCOUT") -> Path:
    path = root / "netscout.review.json"
    path.write_text(json.dumps({
        "client_name": client_name,
        "status": status,
        "search_scope": scope,
        "strategy": {},
    }), encoding="utf-8")
    return path


def _write_sweep(root: Path, *, scope, wid="agency_dhs",
                 client_name="NETSCOUT", payload=None) -> Path:
    name = "searches_netscout.json" if wid == "all" \
        else f"searches_netscout.{wid}.json"
    body = payload if payload is not None else {
        "client": client_name,
        "search_scope": scope,
        "results": {},
    }
    path = root / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _roots(tmp_path: Path):
    roots = tuple(tmp_path / name for name in ("review", "cleaned", "reports"))
    for root in roots:
        root.mkdir()
    return roots


def _discover(tmp_path: Path):
    review, cleaned, reports = (
        tmp_path / name for name in ("review", "cleaned", "reports"))
    return discover_workstations(
        "NETSCOUT", review_dir=review, cleaned_dir=cleaned,
        report_dir=reports)


def _digest_tree(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_scope_identity_is_canonical_and_all_is_explicit():
    assert workstation_id(None) == "all"
    assert workstation_id({"all": True}) == "all"
    assert workstation_id({"agencies": ["DHS"]}) == "agency_dhs"
    # Catalog order is the stable canonical order.  It preserves the existing
    # documented family agency_dhs_cisa while remaining input-order agnostic.
    expected = {"agencies": ["DHS", "CISA"]}
    assert canonical_scope(
        {"agencies": ["CISA", "DHS", "CISA"]}).as_dict() == expected
    assert workstation_id(expected) == "agency_dhs_cisa"
    assert workstation_id(
        {"agencies": ["CISA", "DHS"]}) == "agency_dhs_cisa"
    assert workstation_id({
        "agencies": [{"abbr": "DHS", "name": "Department of Homeland Security"}],
        "mode": "focus",
    }) == "agency_dhs"


@pytest.mark.parametrize("scope", [
    {"agencies": ["NOT-A-REAL-AGENCY"]},
    {"agencies": ["D"]},  # fuzzy autocomplete is forbidden for identity
    {"all": True, "agencies": ["DHS"]},
    {"all": True, "agencies": []},
    {"all": False},
    {"all": False, "agencies": ["DHS"]},
    {"agencies": "DHS"},
    {},
    {"agencie": ["DHS"]},
    {"mode": "focus"},
    {"all": True, "mode": "focus"},
    [],
])
def test_malformed_or_ambiguous_scope_fails_loudly(scope):
    with pytest.raises(WorkstationError):
        workstation_id(scope)


def test_workstation_ref_is_deeply_immutable_and_label_is_derived():
    ref = WorkstationRef(
        id="agency_dhs", scope={"agencies": ["DHS"]}, label="DHS")
    assert ref.scope.agencies == ("DHS",)
    with pytest.raises(Exception):
        ref.scope.agencies += ("DoD",)
    with pytest.raises(Exception, match="label"):
        WorkstationRef(
            id="agency_dhs", scope={"agencies": ["DHS"]}, label="DoD")
    repaired_direct_model = WorkstationRef(
        id="agency_dhs_cisa",
        scope=WorkstationScope(agencies=("CISA", "DHS")),
        label="DHS + CISA")
    assert repaired_direct_model.scope.agencies == ("DHS", "CISA")
    with pytest.raises(Exception, match="requires a scope"):
        WorkstationRef(id="all", label="All Federal")


def test_missing_registry_is_empty_and_never_created(tmp_path):
    assert load_registry("NETSCOUT", review_dir=tmp_path) == ()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload, match", [
    ({"schema_version": "1", "client_name": "OTHER", "workstations": []},
     "client identity"),
    ({"schema_version": "1", "client_name": "NETSCOUT"}, "must be a list"),
    ({"schema_version": 1, "client_name": "NETSCOUT", "workstations": []},
     "schema"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [],
      "future": True}, "unknown field"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [{}]},
     "requires an id"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [
        {"id": "all", "scope": {}}]}, "explicitly declare"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [
        {"id": "all", "scope": {"all": False}}]}, "may only be true"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [
        {"id": "agency_dhs", "scope": {"agencies": ["D"]}}]}, "unknown agency"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [
        {"id": "agency_dhs", "scope": {"agencies": ["DHS"]},
         "label": "DoD"}]}, "label"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [
        {"id": "agency_dhs", "scope": {"agencies": ["DHS"]},
         "mystery": True}]}, "unknown field"),
    ({"schema_version": "1", "client_name": "NETSCOUT", "workstations": [
        {"id": "all", "scope": {"all": True}},
        {"id": "all", "scope": {"all": True}}]}, "duplicate"),
])
def test_registry_rejects_malformed_or_repaired_identity(tmp_path, payload, match):
    (tmp_path / "netscout.workstations.json").write_text(
        json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkstationError, match=match):
        load_registry("NETSCOUT", review_dir=tmp_path)


def test_legacy_packet_requires_exact_client_identity(tmp_path):
    review, cleaned, reports = _roots(tmp_path)
    _write_packet(review, scope={"all": True}, client_name="")
    with pytest.raises(WorkstationError, match="exact client"):
        discover_workstations(
            "NETSCOUT", review_dir=review, cleaned_dir=cleaned,
            report_dir=reports)


def test_catalog_current_dhs_and_dormant_all_are_independent(
        tmp_path, monkeypatch):
    review, cleaned, reports = _roots(tmp_path)
    _write_packet(review, scope={"agencies": ["DHS"]})
    _write_sweep(cleaned, scope={"agencies": ["DHS"]})
    # A valid old All sweep and report are history, never active progress.
    _write_sweep(cleaned, scope={"all": True}, wid="all")
    (reports / "netscout.federal_opportunity_assessment.html").write_text("old")
    monkeypatch.setattr(workstations, "_assess_status", lambda *_: ("pending", []))

    catalog = discover_workstations(
        "NETSCOUT", review_dir=review, cleaned_dir=cleaned, report_dir=reports)
    by_id = {row.ref.id: row for row in catalog.workstations}
    assert by_id["agency_dhs"].phase == "assess"
    assert by_id["agency_dhs"].is_legacy_current is True
    assert by_id["agency_dhs"].sweep_exists is True
    assert by_id["agency_dhs"].sweep_status == "current"
    assert by_id["all"].phase == "dormant"
    assert by_id["all"].boundary_status == "absent"
    assert by_id["all"].sweep_exists is False
    assert by_id["all"].historical_sweep_exists is True
    assert by_id["all"].sweep_status == "historical"
    assert by_id["all"].historical_artifacts == 1


def test_catalog_near_prefix_report_families_do_not_mix(tmp_path):
    review, cleaned, reports = _roots(tmp_path)
    _write_packet(review, scope={"all": True}, status="pending")
    (review / "netscout.workstations.json").write_text(json.dumps({
        "schema_version": "1", "client_name": "NETSCOUT",
        "workstations": [
            {"id": "agency_dhs", "scope": {"agencies": ["DHS"]}},
            {"id": "agency_dhs_cisa",
             "scope": {"agencies": ["DHS", "CISA"]}},
        ],
    }))
    (reports / "netscout.agency_dhs.assessment.client.html").write_text("dhs")
    (reports / "netscout.agency_dhs_cisa.assessment.client.html").write_text("both")
    by_id = {row.ref.id: row for row in _discover(tmp_path).workstations}
    assert by_id["agency_dhs"].historical_artifacts == 1
    assert by_id["agency_dhs_cisa"].historical_artifacts == 1


@pytest.mark.parametrize("status, sweep_payload, assess_status, release_ready, phase", [
    ("pending", None, None, None, "configure"),
    ("approved", None, None, None, "search"),
    ("approved", "valid", "pending", None, "assess"),
    ("approved", "valid", "approved", False, "produce"),
    ("approved", "valid", "approved", True, "target"),
])
def test_complete_current_phase_derivation(
        tmp_path, monkeypatch, status, sweep_payload, assess_status,
        release_ready, phase):
    review, cleaned, _reports = _roots(tmp_path)
    _write_packet(review, scope={"all": True}, status=status)
    if sweep_payload == "valid":
        _write_sweep(cleaned, scope={"all": True}, wid="all")
    if assess_status is not None:
        monkeypatch.setattr(
            workstations, "_assess_status", lambda *_: (assess_status, []))
    if release_ready is not None:
        monkeypatch.setattr(
            workstations, "_release_ready", lambda *_: release_ready)
    assert _discover(tmp_path).workstations[0].phase == phase


@pytest.mark.parametrize("packet_scope", [None, {"all": True}])
def test_pre_workstation_all_sweep_retains_narrow_legacy_compatibility(
        tmp_path, monkeypatch, packet_scope):
    """A legacy unqualified sweep may predate the search_scope field.

    The compatibility default is All Federal only.  It keeps existing clients
    operable without granting any filename or missing-field fallback to native
    or focused workstations.
    """
    review, cleaned, _reports = _roots(tmp_path)
    packet_path = _write_packet(
        review, scope={"all": True}, status="approved")
    if packet_scope is None:
        packet = json.loads(packet_path.read_text())
        packet.pop("search_scope")
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
    _write_sweep(cleaned, scope={"all": True}, wid="all", payload={
        "client": "NETSCOUT", "results": {},
    })
    monkeypatch.setattr(workstations, "_assess_status", lambda *_: ("pending", []))

    row = _discover(tmp_path).workstations[0]

    assert row.ref.id == "all"
    assert row.is_legacy_current is True
    assert row.phase == "assess"
    assert row.sweep_exists is True
    assert row.sweep_status == "current"
    assert row.diagnostics == ()


def test_missing_scope_sweep_remains_invalid_for_focused_legacy(tmp_path):
    review, cleaned, _reports = _roots(tmp_path)
    _write_packet(review, scope={"agencies": ["DHS"]}, status="approved")
    _write_sweep(
        cleaned, scope={"agencies": ["DHS"]}, payload={
            "client": "NETSCOUT", "results": {},
        })

    row = _discover(tmp_path).workstations[0]

    assert row.ref.id == "agency_dhs"
    assert row.phase == "search"
    assert row.sweep_exists is False
    assert row.sweep_status == "invalid"
    assert "no exact search_scope" in " ".join(row.diagnostics)


@pytest.mark.parametrize("kind", ["directory", "malformed", "wrong-client", "wrong-scope"])
def test_unproven_sweep_never_advances_current_scope(tmp_path, kind):
    review, cleaned, _reports = _roots(tmp_path)
    _write_packet(review, scope={"all": True})
    path = cleaned / "searches_netscout.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "malformed":
        path.write_text("{")
    elif kind == "wrong-client":
        _write_sweep(cleaned, scope={"all": True}, wid="all", client_name="OTHER")
    else:
        _write_sweep(cleaned, scope={"agencies": ["DHS"]}, wid="all")
    row = _discover(tmp_path).workstations[0]
    assert row.phase == "search"
    assert row.sweep_exists is False
    assert row.sweep_status == "invalid"
    assert row.diagnostics


def test_same_scope_sweep_older_than_boundary_revision_returns_to_search(
        tmp_path):
    """A saved keyword/NAICS amendment makes the prior sweep historical.

    Client+scope equality cannot make results generated from the old boundary
    current.  Until a post-revision sweep exists, no Assess/Produce/Target
    progress may survive under the edited boundary.
    """
    review, cleaned, reports = _roots(tmp_path)
    packet_path = _write_packet(
        review, scope={"agencies": ["DHS"]}, status="approved")
    packet = json.loads(packet_path.read_text())
    packet["revision_count"] = 4
    packet["revised_at"] = "2026-07-13T08:00:00Z"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    sweep_path = _write_sweep(cleaned, scope={"agencies": ["DHS"]})
    sweep = json.loads(sweep_path.read_text())
    sweep["generated_at"] = "2026-07-13T07:59:59+00:00"
    sweep_path.write_text(json.dumps(sweep), encoding="utf-8")

    row = discover_workstations(
        "NETSCOUT", review_dir=review, cleaned_dir=cleaned,
        report_dir=reports).workstations[0]
    assert row.phase == "search"
    assert row.sweep_exists is False
    assert any(
        word in " ".join(row.diagnostics).lower()
        for word in ("stale", "revision", "boundary"))


def test_discovery_and_error_paths_change_no_bytes(tmp_path):
    review, cleaned, reports = _roots(tmp_path)
    _write_packet(review, scope={"agencies": ["DHS"]})
    _write_sweep(cleaned, scope={"agencies": ["DHS"]})
    before = _digest_tree(tmp_path)
    discover_workstations(
        "NETSCOUT", review_dir=review, cleaned_dir=cleaned, report_dir=reports)
    assert _digest_tree(tmp_path) == before

    (review / "netscout.workstations.json").write_text("{")
    before_error = _digest_tree(tmp_path)
    with pytest.raises(WorkstationError):
        discover_workstations(
            "NETSCOUT", review_dir=review, cleaned_dir=cleaned,
            report_dir=reports)
    assert _digest_tree(tmp_path) == before_error
