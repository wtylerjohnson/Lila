"""Deterministic contract tests for the FPDS-NG ezsearch ATOM adapter.

Fixture provenance (REAL bytes, not invented):
tests/fixtures/sources/fpds_atom.xml was retrieved live on
2026-08-08T13:20:17Z (UTC) from
https://www.fpds.gov/ezsearch/FEEDS/ATOM?FEEDNAME=PUBLIC&q=W912DY15D0039&start=0
(HTTP 200, 10 entries, feed header advertising rel="next"). To stay inside
the fixture size budget it was trimmed to the untouched feed header plus
three whole entries: one ns1:IDV (the W912DY15D0039 vehicle, mod P00006)
and two ns1:award delivery-order mods that carry referencedIDVID,
solicitationID, UEI, and the exercised-versus-ceiling dollar pair. No byte
inside any kept element was edited.

No test below touches the network; every pull rides an injected fetcher.
The one live test at the bottom is opt-in via LILA_LIVE_SMOKE.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.api.base import SourceKind, SourceQuery
from tools.api.fpds_atom import (
    DOD_DELAY_LIMITATION,
    MAX_ACTIONS,
    MAX_PAGES,
    UEI_FREE_TEXT_LIMITATION,
    FpdsAtomSource,
    parse_feed_page,
)

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "sources" / "fpds_atom.xml")
FIXTURE = FIXTURE_PATH.read_text(encoding="utf-8")

#: the verified "valid empty feed" shape FPDS returns past the last page
#: (and for unindexed field prefixes such as UEI:). In-test construction,
#: clearly synthetic; the recorded fixture above stays real bytes.
EMPTY_FEED = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    "<title>FPDS-NG search results</title><modified/></feed>"
)


def _entry_blocks(text):
    blocks = []
    cursor = 0
    while True:
        start = text.find("<entry>", cursor)
        if start == -1:
            break
        end = text.find("</entry>", start) + len("</entry>")
        blocks.append(text[start:end])
        cursor = end
    return blocks


FIXTURE_HEADER = FIXTURE[: FIXTURE.find("<entry>")]
FIXTURE_ENTRIES = _entry_blocks(FIXTURE)


class RecordingFetch:
    """Injected fetcher: serves canned pages keyed by &start=N, records URLs."""

    def __init__(self, pages=None, default=EMPTY_FEED):
        self.pages = pages or {}
        self.default = default
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        query = parse_qs(urlsplit(url).query)
        start = int(query.get("start", ["0"])[0])
        page = self.pages.get(start, self.default)
        if isinstance(page, Exception):
            raise page
        return page

    def q_of_call(self, index=0):
        return parse_qs(urlsplit(self.calls[index]).query)["q"][0]


def _source(fetch):
    return FpdsAtomSource(fetch=fetch)


# --------------------------------------------------------------------------- #
# Fixture normalization
# --------------------------------------------------------------------------- #
def test_fixture_parses_three_actions_with_native_join_keys():
    actions, dropped, next_advertised = parse_feed_page(FIXTURE)
    assert len(actions) == 3
    assert dropped == 0
    assert next_advertised is True  # real header advertised a next page

    idv = actions[0]
    assert idv["kind"] == "idv"
    assert idv["piid"] == "W912DY15D0039"
    assert idv["mod_number"] == "P00006"
    assert idv["agency_id"] == "9700"
    assert idv["agency_name"] == "DEPT OF DEFENSE"
    assert idv["contracting_office_agency_id"] == "2100"
    assert idv["contracting_office_agency_name"] == "DEPT OF THE ARMY"
    assert idv["department_id"] == "9700"
    assert idv["department_name"] == "DEPT OF DEFENSE"
    assert idv["contracting_office_id"] == "W912DY"
    assert idv["referenced_idv_piid"] is None
    assert idv["solicitation_id"] == "W912DY12R0046"
    assert idv["naics_code"] == "541330"
    assert idv["naics_description"] == "ENGINEERING SERVICES"
    assert idv["psc_code"] == "R425"
    assert idv["uei"] == "V8KAWJEMMES7"
    assert idv["vendor_name"] == "SITELOGIQ GOVERNMENT SOLUTIONS LLC"
    assert idv["base_and_exercised_options_value"] is None  # IDVs omit it
    assert idv["last_modified"] == "2022-02-17T16:33:17"
    assert idv["citation_url"].startswith(
        "https://www.fpds.gov/ezsearch/search.do")

    order = actions[1]
    assert order["kind"] == "award"
    assert order["piid"] == "0001"
    assert order["mod_number"] == "9"
    assert order["transaction_number"] == "0"
    # task order -> parent vehicle chain
    assert order["referenced_idv_piid"] == "W912DY15D0039"
    assert order["referenced_idv_agency_id"] == "9700"
    # award -> SAM notice join
    assert order["solicitation_id"] == "W912DY12R0046"
    assert order["signed_date"] == "2024-02-05"
    assert order["obligated_amount"] == pytest.approx(75572.24)
    assert order["base_and_exercised_options_value"] == pytest.approx(75572.24)
    assert order["base_and_all_options_value"] == pytest.approx(75572.24)
    assert order["uei"] == "V8KAWJEMMES7"
    assert order["uei_legal_business_name"] == (
        "SITELOGIQ GOVERNMENT SOLUTIONS LLC")
    assert order["last_modified"] == "2025-03-31T10:02:31"
    assert actions[2]["kind"] == "award"
    assert actions[2]["mod_number"] == "4"
    for action in actions:
        assert action["citation_url"], "every action carries its citation URL"


# --------------------------------------------------------------------------- #
# actions_for_piid: payload shape + provenance envelope
# --------------------------------------------------------------------------- #
def test_actions_for_piid_payload_and_envelope():
    fetch = RecordingFetch(pages={0: FIXTURE})
    out = _source(fetch).actions_for_piid("W912DY15D0039")

    assert fetch.q_of_call(0) == 'PIID:"W912DY15D0039"'
    # fixture header advertises next, second page is the valid empty feed
    assert len(fetch.calls) == 2
    assert out["pages_fetched"] == 2
    assert len(out["actions"]) == 3
    assert out["total_matched"] == 3
    assert "truncated" not in out
    assert out["malformed_dropped"] == 0
    assert out["query"] == 'PIID:"W912DY15D0039"'
    assert out["feed_url"].startswith(
        "https://www.fpds.gov/ezsearch/FEEDS/ATOM?")

    env = out["_provenance"]
    assert env["source"] == "fpds_atom"
    assert env["status"] == "complete"
    assert env["retrieval_mode"] == "live"
    assert env["record_count"] == 3
    assert env["retrieved_at"]
    # data_as_of comes from the feed's own lastModifiedDate stamps
    assert env["data_as_of"] == "2025-03-31T10:02:31"
    assert any("90-day" in item for item in env["limitations"])
    assert DOD_DELAY_LIMITATION in env["limitations"]


def test_page_cap_is_disclosed_when_feed_advertises_more():
    # every requested page returns the real fixture, which advertises next
    fetch = RecordingFetch(default=FIXTURE)
    out = _source(fetch).actions_for_piid("W912DY15D0039")

    assert len(fetch.calls) == MAX_PAGES
    starts = [
        int(parse_qs(urlsplit(url).query)["start"][0]) for url in fetch.calls]
    assert starts == [0, 10, 20]
    assert out["pages_fetched"] == MAX_PAGES
    assert len(out["actions"]) == 9
    assert out["truncated"] is True
    assert "lower bound" in out["truncated_note"]
    assert str(MAX_ACTIONS) in out["truncated_note"]
    assert out["_provenance"]["status"] == "partial"
    assert any(
        "lower bound" in item for item in out["_provenance"]["limitations"])


def test_empty_result_is_a_complete_zero_record_payload():
    fetch = RecordingFetch()  # every page is the valid empty feed
    out = _source(fetch).actions_for_piid("PIIDTHATMATCHESNOTHING")
    assert out["actions"] == []
    assert out["total_matched"] == 0
    assert out["pages_fetched"] == 1
    assert "error" not in out
    assert out["_provenance"]["status"] == "complete"
    assert out["_provenance"]["record_count"] == 0
    assert out["_provenance"]["data_as_of"] is None


# --------------------------------------------------------------------------- #
# Malformed entries: drop + count, never crash
# --------------------------------------------------------------------------- #
def test_malformed_entries_dropped_and_counted():
    no_ns1_content = (
        "<entry><title>orphan</title>"
        "<modified>2025-01-01 00:00:00</modified>"
        '<content type="application/xml"></content></entry>'
    )
    award_without_piid = FIXTURE_ENTRIES[1].replace(
        "<ns1:PIID>0001</ns1:PIID>", "<ns1:PIID></ns1:PIID>", 1)
    mangled = (
        FIXTURE_HEADER + FIXTURE_ENTRIES[0] + no_ns1_content
        + award_without_piid + "</feed>")

    fetch = RecordingFetch(pages={0: mangled})
    out = _source(fetch).actions_for_piid("W912DY15D0039")
    assert len(out["actions"]) == 1
    assert out["actions"][0]["piid"] == "W912DY15D0039"
    assert out["malformed_dropped"] == 2
    assert out["_provenance"]["status"] == "partial"
    assert any(
        "2 malformed" in item for item in out["_provenance"]["limitations"])


def test_non_feed_first_page_fails_honestly_without_raising():
    # a WAF/maintenance HTML page IS well-formed XML; it must not be
    # counterfeited into a complete zero-record result
    for body in ("<html>WAF block page</html>", "<feed truncated mid-str"):
        fetch = RecordingFetch(pages={0: body})
        out = _source(fetch).actions_for_piid("W912DY15D0039")
        assert out["actions"] == []
        assert "not a parseable ATOM feed" in out["error"]
        assert out["_provenance"]["status"] == "failed"


# --------------------------------------------------------------------------- #
# Failure isolation
# --------------------------------------------------------------------------- #
def test_network_failure_returns_honest_failed_payload():
    fetch = RecordingFetch(pages={0: RuntimeError("connect timeout")})
    out = _source(fetch).actions_for_piid("W912DY15D0039")
    assert out["actions"] == []
    assert out["total_matched"] == 0
    assert "connect timeout" in out["error"]
    env = out["_provenance"]
    assert env["source"] == "fpds_atom"
    assert env["status"] == "failed"
    assert env["record_count"] == 0
    assert DOD_DELAY_LIMITATION in env["limitations"]


def test_second_page_failure_keeps_first_page_as_partial():
    fetch = RecordingFetch(
        pages={0: FIXTURE, 10: RuntimeError("socket timeout")})
    out = _source(fetch).actions_for_piid("W912DY15D0039")
    assert len(out["actions"]) == 3
    assert out["pages_fetched"] == 1
    assert "page 2 fetch failed" in out["error"]
    assert out["_provenance"]["status"] == "partial"
    assert any(
        "partial pull" in item for item in out["_provenance"]["limitations"])


def test_blank_identifiers_fail_without_network():
    fetch = RecordingFetch()
    src = _source(fetch)
    for out in (src.actions_for_piid("  "), src.actions_for_uei("")):
        assert out["_provenance"]["status"] == "failed"
        assert "empty" in out["error"]
    assert fetch.calls == []


# --------------------------------------------------------------------------- #
# Query construction
# --------------------------------------------------------------------------- #
def test_actions_for_uei_rides_free_text_and_discloses_it():
    fetch = RecordingFetch(pages={0: FIXTURE})
    out = _source(fetch).actions_for_uei(" V8KAWJEMMES7 ")
    assert fetch.q_of_call(0) == "V8KAWJEMMES7"  # no unindexed UEI: prefix
    assert UEI_FREE_TEXT_LIMITATION in out["_provenance"]["limitations"]
    assert out["actions"][0]["uei"] == "V8KAWJEMMES7"


def test_recent_actions_builds_last_mod_window_plus_naics_term():
    fetch = RecordingFetch(pages={0: FIXTURE})
    query = SourceQuery(naics_codes=["541330", "541512"], keywords=["ignored"])
    out = _source(fetch).recent_actions(query, days=14)

    q = fetch.q_of_call(0)
    match = re.fullmatch(
        r"LAST_MOD_DATE:\[(\d{4}/\d{2}/\d{2}),(\d{4}/\d{2}/\d{2})\] "
        r'PRINCIPAL_NAICS_CODE:"541330"',
        q,
    )
    assert match, q
    start = datetime.strptime(match.group(1), "%Y/%m/%d").date()
    end = datetime.strptime(match.group(2), "%Y/%m/%d").date()
    assert (end - start).days == 14
    assert out["_provenance"]["source"] == "fpds_atom"


def test_recent_actions_without_naics_uses_window_only():
    fetch = RecordingFetch()
    _source(fetch).recent_actions(SourceQuery(), days=3)
    assert "PRINCIPAL_NAICS_CODE" not in fetch.q_of_call(0)
    assert fetch.q_of_call(0).startswith("LAST_MOD_DATE:[")


# --------------------------------------------------------------------------- #
# Adapter contract
# --------------------------------------------------------------------------- #
def test_enrichment_only_search_raises():
    with pytest.raises(NotImplementedError):
        _source(RecordingFetch()).search(SourceQuery())


def test_identity_and_registration_follow_the_catalog():
    assert FpdsAtomSource.name == "fpds_atom"
    assert FpdsAtomSource.kind is SourceKind.ENRICHMENT
    from tools.api.base import REGISTRY
    from tools.api.source_catalog import SOURCE_BY_ADAPTER
    registered = {source.name for source in REGISTRY.all()}
    if "fpds_atom" in SOURCE_BY_ADAPTER:
        assert "fpds_atom" in registered
    else:  # lead has not landed the SourceSpec row yet; import stays clean
        assert "fpds_atom" not in registered


def test_healthcheck_reports_failure_with_injected_fetcher():
    def down(url, **kw):
        raise RuntimeError("HTTP 503 upstream maintenance")

    ok, detail = _source(down).healthcheck()
    assert ok is False
    assert "unreachable" in detail
    assert "503" in detail


def test_healthcheck_reports_reachable_with_entry_count():
    ok, detail = _source(RecordingFetch(default=FIXTURE)).healthcheck()
    assert ok is True
    assert "3 actions" in detail


# --------------------------------------------------------------------------- #
# Opt-in live smoke (smallest possible query: one vehicle, first page stops)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.environ.get("LILA_LIVE_SMOKE"),
    reason="live smoke only when LILA_LIVE_SMOKE is set",
)
def test_live_smoke_single_piid_pull():
    out = FpdsAtomSource().actions_for_piid("W912DY15D0039")
    env = out["_provenance"]
    assert env["source"] == "fpds_atom"
    assert env["status"] in {"complete", "partial"}
    assert isinstance(out["actions"], list)
    if out["actions"]:
        assert out["actions"][0]["piid"] == "W912DY15D0039"
