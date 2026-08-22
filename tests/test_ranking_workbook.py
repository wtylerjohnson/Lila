"""Discernment ranking workbook (2026-07-25): pipeline truth -> ranked xlsx.

Offline: builds a pressed world through the live-provider stubs, then reads
the workbook back and checks sheets, rows, and dropdown validations.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

openpyxl = pytest.importorskip("openpyxl")

import run_candidate_review as driver  # noqa: E402
import run_candidate_review_watch as watch_cli  # noqa: E402
import run_ranking_workbook as workbook_cli  # noqa: E402
import agents.candidate_review_v1.live_providers as live_mod  # noqa: E402

_WIRING_PATH = Path(__file__).parent / "test_candidate_review_v1_wiring.py"


def _wiring():
    spec = importlib.util.spec_from_file_location("_crv1_wiring", _WIRING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sam_row():
    return SimpleNamespace(
        source_id="WORKBOOK1", title="Enterprise Network Observability",
        agency="US Army Corps of Engineers", response_deadline="2026-08-21",
        api_url="https://sam.gov/opp/WORKBOOK1/view",
        raw_payload={"active": "Yes", "solicitationNumber": "W1",
                     "description": "The Government requires monitoring."})


def test_workbook_ranks_the_pressed_document(tmp_path, monkeypatch):
    registry = _wiring()._approved_world(tmp_path, monkeypatch)
    sam = SimpleNamespace(queries=[], rows=[_sam_row()])
    sam.search = lambda q: (sam.queries.append(q) or sam.rows)
    real_loader = live_mod.load_live_provider_runtime
    monkeypatch.setattr(
        live_mod, "load_live_provider_runtime",
        lambda **kw: real_loader(
            **kw, sam_source=sam,
            award_fetch=lambda url: {},
            quota_guard=lambda purpose: True))
    state_root = tmp_path / "data" / "state" / "candidate_review_v1"
    watch_cli.run_watch_generation(
        "riverbed", root=tmp_path, registry_path=registry,
        state_root=state_root, live=True)
    driver.run_candidate_review(
        "riverbed", root=tmp_path, state_root=state_root,
        certified_at=datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc))

    out = tmp_path / "riverbed.ranking_workbook.xlsx"
    path = workbook_cli.build_ranking_workbook(
        "riverbed", root=tmp_path, out=out)
    assert path.is_file()

    loaded = openpyxl.load_workbook(path)
    assert loaded.sheetnames == ["Opportunities", "Keywords", "NAICS"]
    opportunities = loaded["Opportunities"]
    assert opportunities.max_row >= 2
    assert opportunities.cell(row=1, column=6).value == "Ed fit (1-5)"
    assert any(
        "1,2,3,4,5" in validation.formula1
        for validation in opportunities.data_validations.dataValidation)
    keywords = loaded["Keywords"]
    assert keywords.max_row >= 2
    naics = loaded["NAICS"]
    assert naics.cell(row=2, column=1).value == "541512"


def test_workbook_fails_named_without_a_pressed_document(tmp_path):
    # A client with no pressed document anywhere fails named, writes nothing.
    code = workbook_cli.main([
        "--client", "no-such-client-workbook",
        "--out", str(tmp_path / "never.xlsx")])
    assert code == 2
    assert not (tmp_path / "never.xlsx").exists()
