"""Regression coverage for Cycle 5 review item 16."""

from __future__ import annotations

import pytest

from agents.reports.views import (
    _teaming_block,
    gate_for_sales,
    render_assessment,
)
from tests.test_partnering_board import _doc


def test_public_sales_routes_keep_distinct_pursuit_and_monitor_copy(
        tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    pursue = next(row for row in doc.partnering.plays
                  if row.kind == "pursue")
    monitor = next(row for row in doc.partnering.plays
                   if row.kind == "monitor")

    html = render_assessment(doc, "sales")

    assert f"Teaming path · pursuit #{pursue.rank}" in html
    assert ("Named evidence-graded candidates and their cited award records "
            "are client content.") in html
    assert f"Teaming watch · {monitor.agency}" in html
    assert ("The notice and the named partner candidates with cited award "
            "evidence are client content.") in html


def test_sales_pursuit_card_renderer_rejects_monitor_play(
        tmp_path, monkeypatch):
    gated = gate_for_sales(_doc(tmp_path, monkeypatch))
    monitor = next(row for row in gated.partnering.plays
                   if row.kind == "monitor")

    with pytest.raises(ValueError, match="pursuit-only"):
        _teaming_block(monitor, "sales")
