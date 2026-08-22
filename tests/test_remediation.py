"""Graceful QA layer: auto-fix / suppress / flag at the element level.
Hard requirement under test: remediation and rendering NEVER block."""
import os
from datetime import date

from agents.reports.capture_brief import (
    CBNewsItem, CBOpportunity, CBStat, CaptureBriefContent,
    render_capture_brief,
)
from agents.reports.facts import Fact, FactPack
from agents.reports.remediation import remediate


def _content(**over):
    base = dict(
        client_name="Testco",
        subtitle="Threat Intelligence · OSINT",
        meta_prepared_for="Keith · Testco",
        thesis=["One.", "Two with $5B.", "Three."],
        stats=[CBStat(number="5", accent=" live", context="c")] * 5,
        action_callout="File the DOE by Jul 9.",
        opportunities=[CBOpportunity(
            headline="NATO CTI Uplift", agency_abbr="NATO", agency_name="NATO NCIA",
            fit="DIRECT FIT", notice_id="RFQ-1", url="https://sam.gov/x",
            due_label="Jul 9, 2026, 5:00 p.m. ET", days_to_due=7,
            body="Core product.", urgent=True)],
        kill_line_opps="One gate, seven days.",
        news_funding=[CBNewsItem(recency="This week", headline="H",
                                 url="https://a.b/c", why="w")],
        news_threat=[], news_agency=[], news_market=[],
        kill_line_news="Tailwind.",
        partner_callout="Drive the Carahsoft motion now.",
        pipeline=[],
        footer_verification="Verified from SAM.gov notice records.",
    )
    base.update(over)
    return CaptureBriefContent(**base)


def _pack(*facts):
    return FactPack(client_name="Testco", as_of=date(2026, 7, 9),
                    facts=list(facts))


_F12 = Fact(id="F12", kind="competitor", source="https://u.example",
            text="GDIT pushed $893.7M across 16 subawards; ranked #2 of 8 "
                 "by subaward dollars; teaming target.")


def test_r1_rank_of_zero_autofixes_denominator():
    c = _content(thesis=["GDIT sits #2 of 0 in the pool [F12].", "Two.", "Three."])
    out, report = remediate(c, _pack(_F12), {})
    assert "#2 of 8" in out.thesis[0]
    assert any(a.rule == "R1" and a.action == "auto_fix" for a in report.actions)


def test_r2_zero_stat_derives_from_cited_facts():
    stats = [CBStat(number="{{COUNT:teaming_plays}}", accent="",
                    context="subaward primes [F12]")] + \
            [CBStat(number="5", accent="", context="c")] * 4
    out, report = remediate(_content(stats=stats), _pack(_F12),
                            {"teaming_plays": 0})
    assert out.stats[0].number == "8"  # derived from 'ranked #2 of 8'
    assert any(a.rule == "R2" and a.action == "suppress" for a in report.actions)


def test_r3_superlative_downgrades_to_neutral():
    c = _content(thesis=["GDIT is the largest conduit at $893.7M [F12].",
                         "Two.", "Three."])
    out, report = remediate(c, _pack(_F12), {})
    assert "among the largest" in out.thesis[0]
    assert any(a.rule == "R3" for a in report.actions)


def test_r4_unlabeled_median_dropped_from_card():
    stats = [CBStat(number="$53.4B", accent="",
                    context="total obligated, median award $361M, "
                            "anchor award $1.2B [F1]")] + \
            [CBStat(number="5", accent="", context="c")] * 4
    out, report = remediate(_content(stats=stats), _pack(), {})
    assert "median" not in out.stats[0].context.lower()
    assert "anchor award" in out.stats[0].context
    assert any(a.rule == "R4" and a.action == "suppress" for a in report.actions)


def test_r7_field_bleed_stripped():
    c = _content(meta_prepared_for="Keith Johnson · Osprey · "
                                   "Tyler Johnson · 2026-07-09")
    out, report = remediate(c, _pack(), {})
    assert "Tyler" not in out.meta_prepared_for
    assert "2026" not in out.meta_prepared_for
    assert "Keith Johnson" in out.meta_prepared_for
    assert any(a.rule == "R7" and a.action == "auto_fix" for a in report.actions)


def test_r9_em_dash_replaced():
    c = _content(kill_line_news="Momentum — not hype.")
    out, report = remediate(c, _pack(), {})
    assert "—" not in out.kill_line_news
    assert any(a.rule == "R9" for a in report.actions)


def test_safety_valve_sabotaged_rule_still_renders():
    """A lint rule that throws must NEVER break the report: the element
    renders unmodified and a FLAG records the failure."""
    os.environ["LILA_QA_SABOTAGE"] = "R3"
    try:
        c = _content(thesis=["GDIT is the largest conduit [F12].", "Two.", "Three."])
        out, report = remediate(c, _pack(_F12), {})
        assert out.thesis[0] == "GDIT is the largest conduit [F12]."  # unmodified
        assert any(a.rule == "R3" and a.action == "flag"
                   and "errored" in a.reason for a in report.actions)
        html = render_capture_brief(out, qa=report, state="draft")
        assert "QA Appendix" in html  # the report rendered anyway
    finally:
        del os.environ["LILA_QA_SABOTAGE"]


def test_draft_renders_band_appendix_and_markers():
    c = _content(stats=[CBStat(number="{{COUNT:x}}", accent="",
                               context="primes [F12] [F19]")] +
                       [CBStat(number="5", accent="", context="c")] * 4)
    out, report = remediate(c, _pack(), {"x": 0})  # no rank facts -> R2 flag
    html = render_capture_brief(out, qa=report, state="draft")
    assert "Draft · internal QA copy" in html
    assert "QA Appendix" in html
    assert "[REVIEW: R2:" in html and "qa-flag" not in html  # styled span, marker text intact
    assert report.has_flags


def test_release_renders_clean():
    html = render_capture_brief(_content(), qa=None, state="release")
    assert "Draft · internal QA copy" not in html
    assert "QA Appendix" not in html
    assert "[REVIEW:" not in html


# ── client-file doctrine (2026-07-10): the client file carries only
# verified claims and forward motion ──

def test_r14_deletes_self_grading_sentences():
    from agents.reports.facts import FactPack
    c = _content(kill_line_news="The demand tailwind holds. Four notices "
                                "returned no record this cycle.")
    out, report = remediate(c, FactPack(client_name="T", facts=[],
                                        as_of=__import__("datetime").date(2026, 7, 10)), {})
    assert "no record" not in out.kill_line_news
    assert "demand tailwind holds" in out.kill_line_news
    assert any(a.rule == "R14" and a.action == "suppress"
               and "no record" in a.before for a in report.actions)


def test_r14_drops_wholly_negative_elements_and_flags_stats():
    from agents.reports.facts import FactPack
    from datetime import date
    c = _content(
        news_agency=[CBNewsItem(recency="This week", headline="DISA screen",
                                url="https://a.b/c",
                                why="verification returned no record for "
                                    "four notices")],
        stats=[CBStat(number="0", accent="", context="notices could not "
                                                     "confirm coverage")]
        + [CBStat(number="5", accent="", context="c")] * 4)
    out, report = remediate(c, FactPack(client_name="T", facts=[],
                                        as_of=date(2026, 7, 10)), {})
    assert out.news_agency == []                       # element deleted whole
    assert any(a.rule == "R14" and a.action == "flag"
               and a.location == "stats[0]" for a in report.actions)


def test_r11_deletes_lane_claims_instead_of_caveating():
    from agents.reports.facts import Fact, FactPack
    from datetime import date
    pack = FactPack(client_name="T", as_of=date(2026, 7, 10), facts=[
        Fact(id="F7", kind="competitor", source="https://u.example",
             text="BIGCO pushed $900M; teaming target. LANE-LEVEL EVIDENCE "
                  "ONLY, NOT CAPABILITY-VERIFIED.")])
    c = _content(partner_callout="Open the BIGCO conversation now [F7]. "
                                 "Drive the Carahsoft motion in parallel.")
    out, report = remediate(c, pack, {})
    assert "[F7]" not in out.partner_callout           # deleted, not caveated
    assert "Carahsoft" in out.partner_callout          # verified copy survives
    assert any(a.rule == "R11" and a.action == "suppress"
               and "deleted from" in a.reason for a in report.actions)


def test_internal_file_carries_the_deleted_material():
    from agents.reports.facts import Fact, FactPack
    from agents.reports.internal_file import build_internal_md
    from datetime import date
    pack = FactPack(client_name="T", as_of=date(2026, 7, 10), facts=[
        Fact(id="F1", kind="competitor", source="https://u",
             text="FINDING: zero of 500 subaward records match."),
        Fact(id="F2", kind="competitor", source="https://u",
             text="BIGCO $900M. LANE-LEVEL EVIDENCE ONLY, NOT "
                  "CAPABILITY-VERIFIED.")])
    c = _content(kill_line_news="Verification returned no record for four "
                                "notices.")
    out, report = remediate(c, pack, {})
    md = build_internal_md("T", pack, report, {"passed": True, "note": "ok"})
    assert "INTERNAL" in md
    assert "no record" in md                # deleted text preserved
    assert "FINDING: zero of 500" in md     # verified negative logged
    assert "stand up a live watch" in md    # forward-action conversion
    assert "LANE-LEVEL EVIDENCE ONLY" in md


def test_r15_flags_bare_past_dates_and_passes_framed_ones():
    """2026-07-10: a bare past date reads as a past-due listing. Status
    framing (in force since / enacted / standing since) or forward language
    passes; bare past dates flag for a human rewrite."""
    from agents.reports.facts import FactPack
    pack = FactPack(client_name="T", facts=[], as_of=date(2026, 7, 10))
    bad = _content(kill_line_news="OMB M-26-14 replaced M-21-31 on "
                                  "May 22, 2026.")
    _, report = remediate(bad, pack, {})
    assert any(a.rule == "R15" and a.action == "flag" for a in report.actions)
    good = _content(kill_line_news="OMB M-26-14 has been in force since "
                                   "May 22, 2026.",
                    action_callout="File the response; the window closes "
                                   "August 12, 2026.")
    _, report2 = remediate(good, pack, {})
    assert not [a for a in report2.actions if a.rule == "R15"]
