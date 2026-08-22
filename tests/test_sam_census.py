"""L17: pagination to totalRecords, budget guard, and the census lint.

Offline — mocked SAM pages, env-isolated ledger/cache (conftest), no network.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.api.sam_gov as sg  # noqa: E402
import tools.api.sam_quota as sq  # noqa: E402
from agents.reports.lint import lint_screen_census  # noqa: E402
from tools.api.base import SourceQuery  # noqa: E402

TOMORROW = date.today() + timedelta(days=30)


def _notice(i: int) -> dict:
    return {"noticeId": f"N-{i:05d}", "title": f"Notice {i}",
            "naicsCode": "541512", "postedDate": "2026-07-01",
            "responseDeadLine": TOMORROW.isoformat(),
            "uiLink": f"https://sam.gov/opp/N-{i:05d}/view"}


def _pages(total: int, page_size: int):
    """Serve get_json by offset: realistic totalRecords + page slices."""
    def fake_get_json(url, params=None, timeout=None, headers=None):
        off = int(params.get("offset", 0))
        rows = [_notice(i) for i in range(off, min(off + page_size, total))]
        return {"totalRecords": total, "opportunitiesData": rows}
    return fake_get_json


def _query(limit=1000):
    today = date.today()
    return SourceQuery(naics_codes=["541512"], posted_from=today - timedelta(days=180),
                       posted_to=today, deadline_from=today, limit=limit)


def test_paginates_across_three_pages_to_total_records(monkeypatch):
    """2,500 totalRecords at limit=1000 -> exactly 3 pages, offsets 0/1000/2000,
    every record retrieved, census complete."""
    monkeypatch.setenv("SAM_GOV_API_KEY", "test-key")
    calls = []
    fake = _pages(2500, 1000)

    def spy(url, params=None, **kw):
        calls.append(int(params["offset"]))
        return fake(url, params=params)

    monkeypatch.setattr(sg, "get_json", spy)
    src = sg.SamGovSource()
    out = src.search(_query())
    assert calls == [0, 1000, 2000]
    assert len(out) == 2500                       # no ceiling, full census
    c = src.last_status["census"]["541512"]
    assert c == {"retrieved": 2500, "total_records": 2500, "complete": True}
    assert src.last_status["complete"] is True


def test_budget_guard_trips_incomplete_without_corrupting_results(monkeypatch):
    """Budget exhausts after page 1 of 3: stored results keep page 1 intact,
    the pass is INCOMPLETE, retrieved vs totalRecords recorded."""
    monkeypatch.setenv("SAM_GOV_API_KEY", "test-key")
    monkeypatch.setattr(sg, "get_json", _pages(2500, 1000))
    allowed = iter([True, False, False])
    monkeypatch.setattr(sg.sam_quota, "guard", lambda purpose: next(allowed))
    src = sg.SamGovSource()
    out = src.search(_query())
    assert len(out) == 1000                       # page 1 intact, nothing lost
    assert all(o.source_id for o in out)
    c = src.last_status["census"]["541512"]
    assert c["retrieved"] == 1000 and c["total_records"] == 2500
    assert c["complete"] is False
    assert src.last_status["complete"] is False


def test_pass_cap_records_every_omitted_naics_and_keyword(monkeypatch):
    """Pagination success is not full-screen success when approved lanes cap."""
    monkeypatch.setenv("SAM_GOV_API_KEY", "test-key")
    calls = []

    def empty_page(url, params=None, **kwargs):
        calls.append({key: params.get(key) for key in ("ncode", "title")})
        return {"totalRecords": 0, "opportunitiesData": []}

    monkeypatch.setattr(sg, "get_json", empty_page)
    today = date.today()
    query = SourceQuery(
        naics_codes=["541511", "541512", "541513", "541519", "518210"],
        keywords=["packet capture", "network visibility"],
        posted_from=today - timedelta(days=180), posted_to=today,
        deadline_from=today, limit=1000,
    )
    src = sg.SamGovSource()
    assert src.search(query) == []
    manifest = src.last_status["attempt_manifest"]
    assert len(manifest["requested"]) == 7
    assert len(manifest["executed"]) == src.MAX_PASSES == len(calls)
    assert [row["id"] for row in manifest["omitted"]] == [
        "naics:518210", "keyword:packet capture", "keyword:network visibility"]
    assert src.last_status["complete"] is False


def test_guard_warns_at_80_percent_and_stops_at_quota(monkeypatch, capsys):
    monkeypatch.setenv("LILA_SAM_DAILY_QUOTA", "10")
    for _ in range(8):
        sq.note_call("test")
    assert sq.guard("test") is True               # 8/10: warns, still allowed
    assert "80%" in capsys.readouterr().err
    sq.note_call("test")
    sq.note_call("test")
    assert sq.calls_today() == 10
    assert sq.guard("test") is False              # 10/10: refused


def test_partial_screen_rendering_as_complete_fails_lint():
    incomplete_hidden = ('<span data-counts-verified="1" data-screen-census="1" '
                         'data-screened="300" data-census="4127" '
                         'data-complete="0">This cycle the screen behind the '
                         'sweep: 300 notices triaged in the client\'s NAICS '
                         'lanes, every one accounted for.</span>')
    res = lint_screen_census(incomplete_hidden)
    assert not res.ok
    assert res.violations[0].rule == "partial_screen_as_complete"

    reconciled = incomplete_hidden.replace(
        "every one accounted for.</span>",
        "every one accounted for. The sweep screened 300 of 4,127 records; "
        "the shortfall is flagged in the QA appendix.</span>")
    assert lint_screen_census(reconciled).ok

    complete = incomplete_hidden.replace('data-complete="0"', 'data-complete="1"')
    assert lint_screen_census(complete).ok


def test_held_strict_census_must_disclose_and_reconcile_the_hold():
    held = ('<span data-counts-verified="1" data-screen-census="1" '
            'data-screened="4" data-census="5" data-complete="1" '
            'data-held-closed="1">The live-source evidence gate is held '
            'closed: the evidence set reconciles 4 of 5 census records.</span>')
    assert lint_screen_census(held).ok

    undisclosed = held.replace("is held closed", "is paused")
    result = lint_screen_census(undisclosed)
    assert not result.ok
    assert any(row.rule == "strict_live_hold_not_disclosed"
               for row in result.violations)

    unreconciled = held.replace("4 of 5", "3 of 5")
    result = lint_screen_census(unreconciled)
    assert not result.ok
    assert any(row.rule == "strict_live_hold_unreconciled"
               for row in result.violations)

    false_empty = held.replace(
        "the evidence set reconciles 4 of 5 census records",
        "the pull returned none; 4 of 5 census records remain")
    result = lint_screen_census(false_empty)
    assert not result.ok
    assert any(row.rule == "nonzero_census_called_empty"
               for row in result.violations)


def test_views_feed_reconciles_incomplete_census(monkeypatch):
    """The screening line's FEED adds 'X of Y' + QA-appendix sentence when the
    census is incomplete; the contract-surface template itself is unchanged."""
    from agents.reports.document import build_document
    from agents.reports.views import render_assessment
    from tests.test_assessment_document import _searches
    s = _searches(0)
    s["results"]["sam_census"] = {"matched": 300, "active_screened": 4127,
                                  "retrieved": 300, "complete": False,
                                  "source": "sam_extract"}
    doc = build_document("Testco", searches=s, qualify=None)
    assert any("SAM screen INCOMPLETE" in g for g in doc.gaps)
    html = render_assessment(doc, "internal")
    assert 'data-screen-census="1"' in html
    assert "of 4,127" in html and "QA appendix" in html
    assert lint_screen_census(html).ok
    # complete census: line renders without the shortfall clause and passes
    s["results"]["sam_census"]["complete"] = True
    doc2 = build_document("Testco", searches=s, qualify=None)
    html2 = render_assessment(doc2, "internal")
    assert "shortfall" not in html2
    assert lint_screen_census(html2).ok
