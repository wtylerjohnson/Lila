"""Persistent size controls for every editable Signal Board mark shape."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from agents.reports import signal_board as board
from agents.reports import signal_board_presentation as presentation


def _sized(identity: str, percent: int, scale: str, source: str) -> None:
    pattern = (
        rf'data-brand-key="{re.escape(identity)}"[^>]*'
        rf'data-brand-size="{percent}"[^>]*'
        rf'style="--sb-brand-scale:{re.escape(scale)}"'
    )
    assert re.search(pattern, source), identity


def test_every_editable_mark_shape_renders_its_identity_size():
    person_claim = presentation.person_identity_label(
        "Jane Doe", "Department of Commerce", "SAM.gov", "notice-a")
    person_key = presentation.logo_identity(
        "ACME CORP", "person", person_claim)
    sizes = {
        "client:acme_corp": 150,
        "agency:doc": 80,
        "company:checkmarx": 120,
        "company:accenture": 135,
        "agency:dod": 110,
        person_key: 125,
    }

    header = board._brand_attrs("client", "ACME CORP", sizes)
    coverage = board._coverage({
        "dept": "COMMERCE", "components": "USPTO", "seal": "data:image/png;base64,AA==",
    }, sizes)
    opportunity = board._opp({
        "rank": "01", "code": "DOC", "agency_name": "Commerce",
        "title": "USPTO network", "url": "https://sam.gov/opp/a",
        "account": "COMMERCE", "seal": "data:image/png;base64,AA==",
        "evidence": [], "cells": [],
    }, sizes)
    competitor = board._competitor({
        "label": "ACTIVE", "title": "Checkmarx", "url": "#",
        "logo": "data:image/png;base64,AA==", "money": "$2M",
        "small": "OBLIGATED", "wedge": "Lane",
    }, sizes)
    team = board._team({
        "client": "ACME", "partner": "ACCENTURE", "target": "USPTO",
        "client_logo": "data:image/png;base64,AA==",
        "partner_logo": "data:image/png;base64,AA==",
        "angle": "Route", "proof": "Award",
    }, "ACME CORP", sizes)
    horizon = board._horizon({
        "label": "DARPA · FUTURE", "agency_name": "DARPA",
        "organization_marks": [{
            "kind": "agency", "label": "DARPA", "display": "DARPA",
            "logo": "data:image/png;base64,AA==",
        }],
        "title": "E-BOSS", "url": "https://www.darpa.mil/x",
        "value": "2028", "small": "WATCH",
    }, sizes)
    people = board._people_band([{
        "name": "Jane Doe", "role": "Contract Specialist",
        "agency": "Department of Commerce", "source_system": "SAM.gov",
        "source_record_id": "notice-a", "opp": "USPTO network",
        "opp_url": "https://sam.gov/opp/notice-a/view",
        "portrait_identity_label": person_claim,
        "portrait_identity": person_key,
    }], [], sizes)

    _sized("client:acme_corp", 150, "1.5", header)
    _sized("agency:doc", 80, "0.8", coverage)
    _sized("agency:doc", 80, "0.8", opportunity)
    _sized("company:checkmarx", 120, "1.2", competitor)
    _sized("client:acme_corp", 150, "1.5", team)
    _sized("company:accenture", 135, "1.35", team)
    _sized("agency:dod", 110, "1.1", horizon)
    _sized(person_key, 125, "1.25", people)


def test_logo_size_css_reflows_every_mark_shape_without_transform_scaling():
    template = board._load_template()
    selectors = (
        ".sb-brand[data-brand-kind] > .sb-header-logo",
        ".sb-agency-mark[data-brand-kind] > img",
        ".sb-agency-code[data-brand-kind] > .sb-agency-upload",
        ".sb-competitor[data-brand-kind] > .sb-competitor-identity > .sb-competitor-logo",
        ".sb-company[data-brand-kind] > .sb-company-logo",
        ".sb-person-portrait-target",
        ".sb-horizon-logo { display: none",
    )
    for selector in selectors:
        assert selector in template
    assert "calc(180px * var(--sb-brand-scale, 1))" in template
    assert "calc(32px * var(--sb-brand-scale, 1))" in template
    assert "calc(42px * var(--sb-brand-scale, 1))" in template
    assert "calc(28px * var(--sb-brand-scale, 1))" in template
    assert "calc(68px * var(--sb-brand-scale, 1))" in template
    assert "transform: scale(var(--sb-brand-scale, 1))" not in template
    assert "minmax(54px, max-content)" in template
    assert "@media (max-width: 640px)" in template
    assert "@media print" in template


def test_editor_and_reference_share_reflow_aware_logo_size_contract():
    root = Path(__file__).resolve().parents[1]
    editor = (root / "ui" / "signal_board_logo_editor.js").read_text()
    reference = (
        root / "docs" / "reference"
        / "federal_opportunity_signals.reference.html"
    ).read_text()
    for source in (editor, reference):
        assert "transform: scale(var(--sb-brand-scale, 1))" not in source
        assert ".sb-company[data-brand-kind] > .sb-company-logo" in source
        assert "minmax(54px, max-content)" in source
    assert ".sb-horizon-mark[data-brand-kind] > .sb-horizon-logo" in editor
    assert ".sb-person-portrait-target[data-brand-kind]" in editor
    assert "min-width: 0; min-height: 0; overflow: visible" in editor
    assert ".sb-horizon-logo { display: none" in reference


def test_header_editor_reserves_space_for_label_and_logo_controls():
    """The absolute Size/+ controls must not intercept Edit label clicks."""
    root = Path(__file__).resolve().parents[1]
    editor = (root / "ui" / "signal_board_logo_editor.js").read_text()

    assert ".sb-brand[data-brand-kind] {" in editor
    assert "padding-right: 74px" in editor
    assert ".sb-mark-size-button { right: 29px" in editor
    assert ".sb-mark-edit-button, .sb-mark-size-button" in editor


def test_header_logo_uses_intrinsic_width_beside_client_name():
    """Square seals must not reserve a wide, empty logo box in the header."""
    root = Path(__file__).resolve().parents[1]
    template = board._load_template()
    editor = (root / "ui" / "signal_board_logo_editor.js").read_text()

    for source in (template, editor):
        assert ".sb-brand[data-brand-kind]" in source
        assert "flex-wrap: nowrap" in source
        assert ".sb-brand[data-brand-kind] > .sb-header-logo" in source
        assert "width: auto" in source
        assert (
            "max-width: min(calc(180px * var(--sb-brand-scale, 1)), 42vw)"
            in source
        )
        assert (
            "max-width: min(calc(130px * var(--sb-brand-scale, 1)), 100%)"
            in source
        )
    assert (
        ".sb-brand.has-image { display: flex; align-items: center; gap: 8px;"
        in template
    )


def test_build_model_loads_client_scoped_logo_sizes(tmp_path, monkeypatch):
    from agents.reports import report_assets

    monkeypatch.setattr(presentation, "_ROOT", tmp_path)
    presentation.store_logo_size(
        "Testco", kind="client", label="Testco", percent=140,
        root=tmp_path)
    monkeypatch.setattr(
        "tools.capability.client_display_name", lambda name: name)
    monkeypatch.setattr(report_assets, "client_logo", lambda _name: "")

    model = board.build_model(
        SimpleNamespace(client_name="Testco", counts=lambda: {}),
        report_date="20 JUL 2026",
        figures=[{"retrieved_at": "2026-07-20T00:00:00+00:00"}],
    )

    assert model["logo_sizes"] == {"client:testco": 140}
