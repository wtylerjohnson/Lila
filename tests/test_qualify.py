"""Step-2 consolidation tests (offline). Run via pytest OR `python3 tests/test_qualify.py`."""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.qualify as qualify  # noqa: E402
from agents.decisions import schemas as ds  # noqa: E402
from tools.scrape import site  # noqa: E402

_orig_check = qualify.check_url
_orig_fetch = site._fetch


def _strategy():
    return ds.IntakeStrategy(
        client_name="PlanetiQ",
        pursuit_strategy="RO data buys.",
        keywords=[ds.Keyword(term="radio occultation", category=ds.KeywordCategory.CAPABILITY,
                             rationale="core")],
        inferred_naics=["541512", "541715"],
        target_agencies=["NOAA"],
        set_aside_angles=[],
        searches=[ds.SearchSpec(source="sam.gov", rationale="x")],
        confidence=0.8, sources_reviewed=[], review_gate="approve",
    )


def _write_searches(dirpath):
    data = {
        "client": "PlanetiQ",
        "results": {
            "sam.gov": [{
                "source": "sam.gov", "source_id": "S1", "title": "Radio Occultation Data Buy",
                "agency": "NOAA", "naics_code": "541512",
                "posted_date": "2026-01-01", "response_deadline": "2026-12-01",
                "api_url": "https://sam.gov/opp/S1/view", "raw_payload": {},
            }],
            "web": [{
                "source": "web", "source_id": "web-abc", "title": "NOAA RODB-2 forecast",
                "api_url": "https://www.nesdis.noaa.gov/x", "raw_payload": {},
            }],
            "usaspending.gov": [
                {"naics_code": "541512", "summary": {"award_count": 50, "median_award": 5000000,
                                                     "top_incumbents": ["Acme"]}},
            ],
        },
    }
    with open(os.path.join(dirpath, "searches_planetiq.json"), "w") as f:
        json.dump(data, f)


# ---- check_url ------------------------------------------------------------- #
def test_check_url_reachable_and_404():
    site._fetch = lambda url, timeout=15.0: "<html><body>Real page content here.</body></html>"
    try:
        ok, snip = site.check_url("https://x")
        assert ok and "Real page content" in snip
    finally:
        site._fetch = _orig_fetch

    site._fetch = lambda url, timeout=15.0: "<html><body>404 Not Found</body></html>"
    try:
        ok, _ = site.check_url("https://x")
        assert ok is False
    finally:
        site._fetch = _orig_fetch

    site._fetch = lambda url, timeout=15.0: None  # unreachable
    try:
        ok, _ = site.check_url("https://x")
        assert ok is False
    finally:
        site._fetch = _orig_fetch


# ---- consolidate ----------------------------------------------------------- #
def test_consolidate_qualifies_sam_and_preserves_web_in_sweep():
    with tempfile.TemporaryDirectory() as tmp:
        _write_searches(tmp)
        qualify._CLEANED = tmp
        qualify.check_url = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("web leads must not enter live qualification"))
        try:
            records, market = qualify.consolidate("PlanetiQ")
            with open(os.path.join(tmp, "searches_planetiq.json")) as f:
                saved = json.load(f)
        finally:
            qualify.check_url = _orig_check

    assert [r.opportunity.source_id for r in records] == ["S1"]
    by_src = {r.opportunity.source: r for r in records}
    # SAM: field-verified, market evidence attached by NAICS
    assert by_src["sam.gov"].verified is True
    assert by_src["sam.gov"].market_evidence["summary"]["award_count"] == 50
    # Web remains available to the developing-intelligence lane.
    assert saved["results"]["web"][0]["source_id"] == "web-abc"
    assert "541512" in market


def test_consolidate_rejects_non_sam_record_misbucketed_as_sam():
    with tempfile.TemporaryDirectory() as tmp:
        _write_searches(tmp)
        path = os.path.join(tmp, "searches_planetiq.json")
        with open(path) as f:
            data = json.load(f)
        data["results"]["sam.gov"].append(data["results"]["web"][0])
        with open(path, "w") as f:
            json.dump(data, f)
        qualify._CLEANED = tmp
        records, _ = qualify.consolidate("PlanetiQ")
    assert [r.opportunity.source_id for r in records] == ["S1"]
    assert all(r.opportunity.source == "sam.gov" for r in records)


def test_profile_from_strategy_maps_fields():
    p = qualify.profile_from_strategy("PlanetiQ", _strategy())
    assert p.naics_codes == ["541512", "541715"]
    assert p.target_agencies == ["NOAA"]
    assert "radio occultation" in p.tech_stack


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
