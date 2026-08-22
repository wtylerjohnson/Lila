"""Recompete events join the horizon band under evidence discipline.

Doctrine (2026-07-17): a record-backed successor event inside the 36-month
window, carrying a linkable primary record or APFS forecast id, joins the
horizon cards. Analyst-tier matches and linkless calendar events never do;
one event joins once however many plays it touches.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from test_assessment_document import _searches  # noqa: E402
from test_recompete_context import ODOS_IV_FORECAST  # noqa: E402
from test_recompete_render import _ODOS_PLAY  # noqa: E402

from agents.reports import signal_board as sb  # noqa: E402
from agents.reports.board_content import SignalBoardContent  # noqa: E402
from agents.reports.document import build_document  # noqa: E402


def _model(recompete, plays=None, *, federal_link_status=None,
           composition_mode="operator"):
    doc = build_document("Testco", searches=_searches(2), qualify=None,
                         as_of=date(2026, 7, 17))
    scale_fields = ({
        "scale_label": "Client-footprint obligated history",
        "scale": {
            "basis": "client_obligated_to_date",
            "rows": [],
            "total": "",
        },
    } if composition_mode == "machine" else {})
    content = SignalBoardContent(client_name="Testco",
                                 composition_mode=composition_mode,
                                 teaming=plays or [dict(_ODOS_PLAY)],
                                 figures=[], **scale_fields)
    return sb.build_model(doc, report_date="17 JUL 2026",
                          content=content, recompete=recompete,
                          federal_link_status=federal_link_status)


def _recompete_cards(model):
    return [h for h in model["horizon"] if "RECOMPETE" in (h.get("label") or "")]


def test_record_backed_event_joins_horizon_with_apfs_id():
    model = _model({"calendar": None, "forecasts": [ODOS_IV_FORECAST]})
    cards = _recompete_cards(model)
    assert len(cards) == 1
    card = cards[0]
    assert card["url"] == "https://apfs-cloud.dhs.gov/forecast/"
    assert "F2026075501" in card["small"]
    assert "SUCCEEDS USCIS ODOS III / 3.1" in card["small"]
    assert card["value"] == "2026-11-15"
    html = sb.render_signal_board(model)
    assert 'href="https://apfs-cloud.dhs.gov/forecast/"' in html


def test_machine_content_keeps_c1_as_the_only_horizon_producer():
    model = _model(
        {"calendar": None, "forecasts": [ODOS_IV_FORECAST]},
        composition_mode="machine",
    )

    assert _recompete_cards(model) == []
    assert model["teaming"][0]["recompete"]["state"] == "known"


def test_analyst_tier_never_joins_horizon():
    fuzzy = dict(ODOS_IV_FORECAST,
                 description="Five-year multiple-award SB set-aside via GSA MAS.")
    plays = [dict(_ODOS_PLAY, award_id="", idv_piid="")]
    model = _model({"calendar": None, "forecasts": [fuzzy]}, plays=plays)
    assert model["teaming"][0]["recompete"]["event"]["tier"] == "analyst"
    assert _recompete_cards(model) == []


def test_event_beyond_36_months_stays_off_horizon():
    far = dict(ODOS_IV_FORECAST, anticipated_solicitation="2031-01-01")
    model = _model({"calendar": None, "forecasts": [far]})
    assert model["teaming"][0]["recompete"]["state"] == "known"
    assert _recompete_cards(model) == []


def test_linkless_calendar_event_renders_line_but_not_horizon():
    cal = {"generated": "2026-07-17", "attack": [
        {"award_id": "70SBUR22F00000113",
         "awarding_agency": "Department of Homeland Security",
         "pop_end": "2026-09-24", "description": "ODOS III"}], "defend": []}
    model = _model({"calendar": cal, "forecasts": []})
    assert model["teaming"][0]["recompete"]["state"] == "known"
    assert _recompete_cards(model) == []          # no url, no horizon seat
    html = sb.render_signal_board(model)
    assert "Award period end on the calendar" in html


def test_shared_event_joins_once():
    plays = [dict(_ODOS_PLAY),
             dict(_ODOS_PLAY, partner="SECOND PARTNER")]
    model = _model({"calendar": None, "forecasts": [ODOS_IV_FORECAST]},
                   plays=plays)
    assert len(_recompete_cards(model)) == 1


def test_linkful_calendar_award_is_builder_derived_and_never_fetched(tmp_path):
    from agents.reports.link_integrity import LinkClass, audit_client_links
    from agents.reports.links import (
        USASPENDING_AWARD_BUILDER,
        parse_client_anchors,
    )
    from agents.reports.lint import lint_federal_link_construction

    generated_id = (
        "CONT_AWD_70SBUR22F00000113_7003_"
        "HHSN316201200193W_7529"
    )
    calendar = {"generated": "2026-07-17", "attack": [{
        "award_id": "70SBUR22F00000113",
        "internal_id": generated_id,
        "awarding_agency": "Department of Homeland Security",
        "pop_end": "2026-09-24", "description": "ODOS III",
        # The composer must never trust or parse this stored free string.
        "url": "https://www.usaspending.gov/award/WRONG",
    }], "defend": []}
    key = (USASPENDING_AWARD_BUILDER, generated_id)
    model = _model(
        {"calendar": calendar, "forecasts": []},
        federal_link_status={key: True},
    )
    expiry_cards = [
        row for row in model["horizon"]
        if "EXPIRY" in str(row.get("label") or "")
    ]
    assert len(expiry_cards) == 1
    assert "AWARD PERIOD END" in expiry_cards[0]["small"]
    assert "NOT CONFIRMED RECOMPETE" in expiry_cards[0]["small"]
    assert "SUCCEEDS" not in expiry_cards[0]["small"]
    manifest = sb.canonical_federal_link_manifest(model)
    html = sb.render_signal_board(model)
    canonical = f"https://www.usaspending.gov/award/{generated_id}"

    assert len([link for link in manifest if link.record_id == generated_id]) == 2
    assert html.count(canonical) == 2  # teaming source + horizon card
    assert "/award/WRONG" not in html
    award_manifest = tuple(
        link for link in manifest if link.record_id == generated_id)
    award_html = "".join(
        anchor.raw for anchor in parse_client_anchors(html)
        if anchor.href == canonical
    )
    assert lint_federal_link_construction(
        award_html, expected_links=award_manifest,
    ).ok

    def bomb(_url):
        raise AssertionError("USAspending reached the HTTP checker")

    checked = audit_client_links(
        award_html, expected_federal_links=award_manifest,
        cache_dir=tmp_path,
        fetch=bomb,
    )
    award = next(result for result in checked if result.url == canonical)
    assert award.classification == LinkClass.BUILDER_RECONCILED

    blocked = _model({"calendar": calendar, "forecasts": []})
    blocked_manifest = sb.canonical_federal_link_manifest(blocked)
    blocked_html = sb.render_signal_board(blocked)
    blocked_award_manifest = tuple(
        link for link in blocked_manifest if link.record_id == generated_id)
    blocked_award_html = "".join(
        anchor.raw for anchor in parse_client_anchors(blocked_html)
        if anchor.href == canonical
    )
    blocked_result = next(
        result for result in audit_client_links(
            blocked_award_html,
            expected_federal_links=blocked_award_manifest,
            cache_dir=tmp_path / "blocked", fetch=bomb,
        )
        if result.url == canonical
    )
    assert blocked_result.classification == LinkClass.BUILDER_BLOCKED
