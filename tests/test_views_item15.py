"""Regression coverage for Cycle 5 review item 15.

The internal dossier wrapper must not create an empty QA panel when a pursuit
has a teaming path but neither dossier depth nor amendment history.
"""

from __future__ import annotations

from agents.reports.document import build_document
from agents.reports.views import render_assessment
from tests.test_assessment_document import _amended_searches
from tests.test_partnering_board import AS_OF, _doc


def test_internal_teaming_only_card_has_no_empty_qa_panel(
        tmp_path, monkeypatch):
    doc = _doc(tmp_path, monkeypatch)
    play = next(row for row in doc.partnering.plays if row.kind == "pursue")
    pursuit = next(row for row in doc.board.pursuits
                   if row.source_id == play.source_id)
    assert pursuit.dossier is None
    assert pursuit.amendments == []

    html = render_assessment(doc, "internal")

    assert f"Teaming play · pursuit #{play.rank}" in html
    assert '<div class="v-qa"></div>' not in html


def test_internal_amendment_only_card_keeps_populated_qa_rule():
    doc = build_document(
        "Testco", searches=_amended_searches(), qualify=None,
        as_of=AS_OF, _live_report=False,
    )
    amended = next(row for row in doc.board.pursuits if row.amendments)
    assert amended.dossier is None

    html = render_assessment(doc, "internal")

    assert '<div class="v-qa"><div>Rule: the grade keys to the latest amendment' \
        in html
    assert '<div class="v-qa"></div>' not in html
