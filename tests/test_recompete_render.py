"""Recompete lines on teaming cards, end to end (2026-07-17).

The ODOS worked example through the real adapter and renderer: a fixture
play on 70SBUR22F00000113 renders RECOMPETE KNOWN referencing the ODOS IV
forecast; the same fixture minus the calendar/forecast entries renders
NO RECOMPETE FOUND in client HTML; an unindexed program family demotes to
CALENDAR GAP in the INTERNAL sidecar only, never client HTML.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from test_assessment_document import _searches  # noqa: E402
from test_recompete_context import (  # noqa: E402
    ODOS_IV_FORECAST,
    USCIS_CAL,
)

from agents.reports import signal_board as sb  # noqa: E402
from agents.reports.board_content import SignalBoardContent  # noqa: E402
from agents.reports.document import build_document  # noqa: E402

_ODOS_PLAY = {
    "client": "INSIGNARY", "partner": "DV UNITED",
    "target": "USCIS ODOS III / 3.1",
    "angle": "BINARY ANALYSIS + SBOM EVIDENCE",
    "proof": "0 SUBAWARDS · ENTRY THESIS",
    "award_id": "70SBUR22F00000113", "idv_piid": "HHSN316201200193W",
    "program": "USCIS ODOS III / 3.1",
    "agency": "Department of Homeland Security",
}


def _content(**team_overrides):
    return SignalBoardContent(
        client_name="Testco",
        teaming=[dict(_ODOS_PLAY, **team_overrides)],
        figures=[])


def _model(recompete, **team_overrides):
    doc = build_document("Testco", searches=_searches(2), qualify=None,
                         as_of=date(2026, 7, 17))
    return sb.build_model(doc, report_date="17 JUL 2026",
                          content=_content(**team_overrides),
                          recompete=recompete)


def test_known_record_backed_renders_event_with_source():
    model = _model({"calendar": USCIS_CAL, "forecasts": [ODOS_IV_FORECAST]})
    html = sb.render_signal_board(model)
    assert "Recompete on the calendar" in html
    assert "F2026075501" in html
    line = html.split('class="sb-team-recompete"')[1][:400]
    assert "2026-11-15" in line
    assert model["teaming"][0]["recompete"]["state"] == "known"
    assert model["teaming"][0]["recompete"]["event"]["tier"] == "record"


def test_known_via_forecast_renders_apfs_reference():
    model = _model({"calendar": None, "forecasts": [ODOS_IV_FORECAST]})
    html = sb.render_signal_board(model)
    line = html.split('class="sb-team-recompete"')[1][:500]
    assert "ODOS IV" in line
    assert "F2026075501" in line
    assert "apfs-cloud.dhs.gov" in line   # source link, link-integrity rules
    # the forecast's retrieval moment joins the figure provenance rows
    assert {"retrieved_at": "2026-07-17T15:06:03+00:00"} in (
        model["figures"] or [])


def test_analyst_match_reads_as_signal_never_as_record():
    fuzzy = dict(ODOS_IV_FORECAST,
                 description="Five-year multiple-award SB set-aside via GSA MAS.")
    model = _model({"calendar": None, "forecasts": [fuzzy]},
                   award_id="", idv_piid="")
    html = sb.render_signal_board(model)
    line = html.split('class="sb-team-recompete"')[1][:400]
    assert "Possible forecast successor signal" in line
    assert "Recompete on the calendar" not in line
    assert "ANALYST" not in html          # tier is internal vocabulary


def test_generic_record_forecast_renders_neutral_successor_signal():
    generic = dict(
        ODOS_IV_FORECAST,
        title="Outcome-based Delivery Services acquisition forecast",
        description=(
            "Planning record for predecessor task order "
            "70SBUR22F00000113. Acquisition approach remains under review."
        ),
    )

    html = sb.render_signal_board(
        _model({"calendar": None, "forecasts": [generic]}))
    line = html.split('class="sb-team-recompete"')[1][:500]

    assert "Forecast successor signal" in line
    assert "Recompete on the calendar" not in line


def test_unknown_event_kind_fails_closed():
    source = sb._team_recompete_line({
        "recompete": {
            "state": "known",
            "event": {
                "kind": "unrecognized",
                "tier": "record",
                "label": "must not render",
                "source_record_id": "UNKNOWN-1",
            },
        },
    })

    assert source == ""


def test_not_found_states_the_absence_explicitly():
    covered = {"generated": "2026-07-17", "attack": [
        {"award_id": "OTHER1", "awarding_agency":
         "Department of Homeland Security", "pop_end": "2027-01-01",
         "description": "unrelated"}], "defend": []}
    model = _model({"calendar": covered, "forecasts": []},
                   award_id="ZZ999", program="No Such Program")
    html = sb.render_signal_board(model)
    assert "No recompete identified in the current screening window" in html


def _body(html: str) -> str:
    return html.split("</style>", 1)[1]


def test_gap_rides_internal_sidecar_never_client_html():
    model = _model({"calendar": None, "forecasts": []})
    body = _body(sb.render_signal_board(model))
    assert 'class="sb-team-recompete"' not in body
    gap_notes = [n for n in model["internal_notes"]
                 if "recompete calendar gap" in n]
    assert len(gap_notes) == 1
    assert "USCIS ODOS III / 3.1" in gap_notes[0]
    assert "CALENDAR GAP" not in body and "calendar gap" not in body


def test_no_recompete_inputs_means_no_lines_and_no_notes():
    model = _model(None)
    body = _body(sb.render_signal_board(model))
    assert 'class="sb-team-recompete"' not in body
    assert not any("recompete" in n for n in model["internal_notes"])


def test_civilian_scope_excludes_dod_successor_from_client_card(monkeypatch):
    from tools.relevance.scope import EngagementScope

    monkeypatch.setattr(
        "tools.relevance.scope.load_engagement_scope",
        lambda _client: EngagementScope(preset="civilian"),
    )
    army_gid = "CONT_AWD_W58P0525C0002_9700_-NONE-_-NONE-"
    calendar = {
        "generated": "2026-07-21",
        "attack": [{
            "award_id": "W58P0525C0002",
            "internal_id": army_gid,
            "awarding_agency": "Department of Defense",
            "awarding_office": "Department of the Army",
            "description": "SOLARWINDS SOFTWARE AND SERVICES",
            "pop_end": "2027-03-30",
        }],
        "defend": [],
    }

    model = _model(
        {"calendar": calendar, "forecasts": []},
        award_id="2032H525F00130", idv_piid="",
        program="SolarWinds account", target="SolarWinds account",
        agency="Department of the Treasury",
    )
    html = sb.render_signal_board(model)

    assert army_gid not in html
    assert "W58P0525C0002" not in html
    assert "recompete" not in model["teaming"][0]
    assert any(
        "engagement scope excluded 1 recompete or forecast row" in note
        for note in model["internal_notes"])


def test_touched_template_stays_white_label_and_emdash_free():
    from agents.reports.lint import lint_emdash, lint_whitelabel
    model = _model({"calendar": USCIS_CAL, "forecasts": [ODOS_IV_FORECAST]})
    html = sb.render_signal_board(model)
    assert lint_whitelabel(html).ok
    assert lint_emdash(html).ok
    assert "—" not in sb._load_template()
