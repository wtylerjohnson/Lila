"""A sourced recompete event stays separate from the candidate angle.

The event line may state its record label/date/identity. It never appends a
generic strategy claim to an evidence-derived partnership angle.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from test_assessment_document import _searches  # noqa: E402
from test_recompete_context import ODOS_IV_FORECAST, USCIS_CAL  # noqa: E402
from test_recompete_render import _ODOS_PLAY  # noqa: E402

from agents.reports import signal_board as sb  # noqa: E402
from agents.reports.board_content import SignalBoardContent  # noqa: E402
from agents.reports.document import build_document  # noqa: E402

def _model(recompete):
    doc = build_document("Testco", searches=_searches(2), qualify=None,
                         as_of=date(2026, 7, 17))
    content = SignalBoardContent(client_name="Testco",
                                 teaming=[dict(_ODOS_PLAY)], figures=[])
    return sb.build_model(doc, report_date="17 JUL 2026",
                          content=content, recompete=recompete)


def test_known_play_keeps_angle_untouched_and_renders_sourced_event():
    model = _model({"calendar": USCIS_CAL, "forecasts": [ODOS_IV_FORECAST]})
    angle = model["teaming"][0]["angle"]
    assert angle == "BINARY ANALYSIS + SBOM EVIDENCE"
    html = sb.render_signal_board(model)
    assert "Recompete on the calendar" in html
    assert "F2026075501" in html
    assert "2026-11-15" in html


def test_not_found_and_gap_angles_stay_untouched():
    covered = {"generated": "2026-07-17", "attack": [
        {"award_id": "OTHER1", "awarding_agency":
         "Department of Homeland Security", "pop_end": "2027-01-01",
         "description": "unrelated"}], "defend": []}
    for inputs in ({"calendar": covered, "forecasts": []},   # not_found
                   {"calendar": None, "forecasts": []}):     # gap
        model = _model(inputs)
        assert model["teaming"][0]["angle"] == "BINARY ANALYSIS + SBOM EVIDENCE"


def test_separate_event_line_passes_the_existing_lint_battery():
    from agents.reports.lint import (
        lint_client_terminology, lint_emdash, lint_whitelabel,
    )
    model = _model({"calendar": USCIS_CAL, "forecasts": [ODOS_IV_FORECAST]})
    html = sb.render_signal_board(model)
    assert "RECOMPETE DIFFERENTIATION" not in html
    assert "Recompete on the calendar" in html
    assert "RECOMPETE-DEFENSE" not in html
    assert lint_whitelabel(html).ok
    assert lint_emdash(html).ok
    assert lint_client_terminology(html).ok
