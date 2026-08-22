"""Live-data ingestion tests: SAM parsing, USAspending parsing, verification, fit.

Offline — uses fixtures and a fake Claude client. No keys, no network.
Run via pytest OR `python3 tests/test_ingest.py`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date as _date  # noqa: E402

from agents.decisions.fit import assess_fit  # noqa: E402
from agents.decisions.schemas import FitRationale  # noqa: E402
from tools.api import sam_gov as sam_mod  # noqa: E402
from tools.api.base import SourceQuery  # noqa: E402
from tools.api.sam_gov import SamGovSource, map_notice  # noqa: E402
from tools.api.usaspending import parse_awards, summarize_market  # noqa: E402
from tools.api.verification import Severity, verify_opportunity  # noqa: E402
from tests.fixtures import PROFILE  # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAM = json.load(open(os.path.join(_HERE, "data", "raw", "sample_sam_response.json")))
_orig_get = sam_mod.get_json  # restored after each monkeypatched test


# ---- SAM.gov parsing (verified against real-shape payload) ----------------- #
def test_sam_maps_core_fields():
    opp = map_notice(_SAM["opportunitiesData"][0])
    assert opp.source == "sam.gov"
    assert opp.source_id == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    assert opp.naics_code == "541512"
    assert opp.set_aside == "SDVOSB"  # SAM code SDVOSBC normalized to the eligibility label
    assert opp.estimated_value == 4_200_000.0
    assert str(opp.api_url).startswith("https://sam.gov/opp/")


def test_sam_parses_dates():
    opp = map_notice(_SAM["opportunitiesData"][0])
    assert opp.posted_date == date(2024, 9, 15)
    assert opp.response_deadline == date(2024, 10, 30)  # ISO datetime -> date


def test_sam_handles_null_award_and_setaside():
    opp = map_notice(_SAM["opportunitiesData"][1])
    assert opp.estimated_value is None
    assert opp.set_aside is None  # full and open


# ---- SAM search: per-keyword fan-out + rate-limit resilience (monkeypatched) #
def _q(keywords):
    return SourceQuery(keywords=keywords, posted_from=_date(2026, 1, 1),
                       posted_to=_date(2026, 6, 1), limit=10)


def _notice(nid, title):
    return {"noticeId": nid, "title": title, "uiLink": f"https://sam.gov/opp/{nid}/view"}


def test_sam_search_one_call_per_keyword_and_dedups(monkeypatch=None):
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params["title"])
        # same notice returned for two terms -> must dedupe
        return {"opportunitiesData": [_notice("N1", "Radio Occultation Data Buy")]}

    sam_mod.get_json = fake_get
    try:
        out = SamGovSource(api_key="x").search(_q(["radio occultation", "GNSS-RO"]))
    finally:
        sam_mod.get_json = _orig_get
    assert calls == ["radio occultation", "GNSS-RO"]  # one call per keyword
    assert len(out) == 1  # deduped by notice id


def test_sam_attempts_every_requested_naics_and_keyword():
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        return {"opportunitiesData": []}

    sam_mod.get_json = fake_get
    try:
        q = SourceQuery(keywords=["radio occultation"], naics_codes=["518210", "541512"],
                        posted_from=_date(2026, 1, 1), posted_to=_date(2026, 6, 1))
        SamGovSource(api_key="x").search(q)
    finally:
        sam_mod.get_json = _orig_get
    assert [c.get("ncode") for c in calls] == ["518210", "541512", None]
    assert [c.get("title") for c in calls] == [
        None, None, "radio occultation"]


def test_sam_search_caps_passes_at_max():
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params.get("title"))
        return {"opportunitiesData": []}

    sam_mod.get_json = fake_get
    try:
        SamGovSource(api_key="x").search(_q(["a", "b", "c", "d", "e", "f"]))
    finally:
        sam_mod.get_json = _orig_get
    assert len(calls) == SamGovSource.MAX_PASSES  # 6 keywords -> capped to 4


def test_sam_search_tolerates_partial_failure():
    def fake_get(url, params=None, **kw):
        if params["title"] == "bad":
            raise RuntimeError("retryable 429")
        return {"opportunitiesData": [_notice("OK1", "Good notice")]}

    sam_mod.get_json = fake_get
    try:
        out = SamGovSource(api_key="x").search(_q(["bad", "good"]))
    finally:
        sam_mod.get_json = _orig_get
    assert [o.source_id for o in out] == ["OK1"]  # bad pass skipped, good kept


def test_sam_search_rejects_oversized_date_span():
    # >1-year span must fail fast with a clear error (SAM returns 400 otherwise).
    q = SourceQuery(keywords=["x"], posted_from=_date(2025, 1, 1), posted_to=_date(2026, 6, 30))
    try:
        SamGovSource(api_key="x").search(q)
        raise AssertionError("expected ValueError on >1yr span")
    except ValueError as e:
        assert "< 1 year" in str(e)


def test_sam_search_raises_when_all_passes_fail():
    def fake_get(url, params=None, **kw):
        raise RuntimeError("retryable 429")

    sam_mod.get_json = fake_get
    try:
        SamGovSource(api_key="x").search(_q(["a", "b"]))
        raise AssertionError("expected RuntimeError when every pass fails")
    except RuntimeError as e:
        assert "SAM passes failed" in str(e)
    finally:
        sam_mod.get_json = _orig_get


# ---- USAspending parsing / summary ----------------------------------------- #
_USA = {
    "results": [
        {"Award ID": "A1", "Recipient Name": "Acme", "Award Amount": 5_000_000,
         "Awarding Agency": "VA", "Period of Performance Start Date": "2023-01-01",
         "generated_internal_id": "CONT_AWD_1"},
        {"Award ID": "A2", "Recipient Name": "Acme", "Award Amount": 1_000_000,
         "Awarding Agency": "VA", "Period of Performance Start Date": "2023-02-01",
         "generated_internal_id": "CONT_AWD_2"},
        {"Award ID": "A3", "Recipient Name": "Globex", "Award Amount": 3_000_000,
         "Awarding Agency": "VA", "Period of Performance Start Date": "2023-03-01",
         "generated_internal_id": "CONT_AWD_3"},
    ]
}


def test_usaspending_parse_builds_award_urls():
    awards = parse_awards(_USA)
    assert awards[0]["url"] == "https://www.usaspending.gov/award/CONT_AWD_1"
    assert awards[0]["recipient"] == "Acme"


def test_usaspending_parse_accepts_explicit_empty_results():
    assert parse_awards({"results": []}) == []


def test_usaspending_parse_rejects_placeholder_award_row():
    with pytest.raises(ValueError, match="result omitted award identity"):
        parse_awards({"results": [{"message": "maintenance"}]})


def test_usaspending_market_summary():
    s = summarize_market(parse_awards(_USA))
    assert s["award_count"] == 3
    assert s["total_obligated"] == 9_000_000.0
    assert s["median_award"] == 3_000_000.0
    assert s["max_award"] == 5_000_000
    assert s["top_incumbents"][0] == "Acme"  # most frequent recipient


# ---- Verification ---------------------------------------------------------- #
def test_verify_passes_good_record():
    opp = map_notice(_SAM["opportunitiesData"][0])
    vr = verify_opportunity(opp, today=date(2024, 9, 20))
    assert vr.verified is True
    assert vr.source_url


def test_verify_blocks_untraceable_record():
    opp = map_notice(_SAM["opportunitiesData"][0])
    opp.api_url = None  # remove traceability anchor
    vr = verify_opportunity(opp, today=date(2024, 9, 20))
    assert vr.verified is False
    assert any(c.field == "api_url" and c.severity == Severity.BLOCKER for c in vr.checks)


def test_verify_blocks_deadline_before_posted():
    opp = map_notice(_SAM["opportunitiesData"][0])
    opp.response_deadline = date(2024, 9, 1)  # before posted 2024-09-15
    vr = verify_opportunity(opp, today=date(2024, 9, 20))
    assert vr.verified is False


# ---- LLM fit-logic (fake client) ------------------------------------------- #
class _FakeMessages:
    def __init__(self, rec):
        self.rec = rec

    def parse(self, **kwargs):
        self.rec["kwargs"] = kwargs
        assert kwargs["output_format"] is FitRationale
        rationale = FitRationale(
            opportunity_id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
            verdict="strong_fit",
            fit_summary="NAICS 541512 + SDVOSB set-aside align with the client; SOW asks "
                        "for cloud migration and DevSecOps, both in past performance.",
            matched_capabilities=[
                {"capability": "cloud migration", "evidence": "title: Cloud Migration ..."},
            ],
            gaps=[],
            risks=["Incumbent Acme holds large prior VA awards."],
            market_evidence_note="VA spends regularly on 541512; median award ~$3M.",
            recommended_action="pursue as prime",
            confidence=0.86,
            citations=["https://sam.gov/opp/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/view"],
        )
        return type("R", (), {"parsed_output": rationale})()


class FakeClient:
    def __init__(self):
        self.rec = {}
        self.messages = _FakeMessages(self.rec)


def test_fit_grounds_on_verified_opportunity_and_market_evidence():
    from agents.decisions.engine import DecisionEngine

    opp = map_notice(_SAM["opportunitiesData"][0])
    evidence = {"summary": summarize_market(parse_awards(_USA)),
                "sample_awards": parse_awards(_USA)[:3]}
    eng = DecisionEngine(client=FakeClient())
    rationale = assess_fit(opp, PROFILE, evidence, engine=eng)

    user_msg = eng.client.rec["kwargs"]["messages"][0]["content"]
    assert "541512" in user_msg          # verified opportunity field reached the model
    assert "top_incumbents" in user_msg  # market evidence reached the model
    sys_prompt = eng.client.rec["kwargs"]["system"]
    assert "GROUND EVERY CLAIM" in sys_prompt
    assert "CITE SOURCES" in sys_prompt

    assert rationale.verdict.value == "strong_fit"
    assert rationale.requires_human_review is True
    assert rationale.citations  # traceability


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
