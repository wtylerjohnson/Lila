"""Daily-extract adapter — the quota-free discovery path."""

import io
from datetime import date

import pytest

import tools.api.sam_extract as se
from tools.api import REGISTRY, SourceQuery
from tools.api.sam_extract import SamExtractSource, iter_rows

CSV = """\
NoticeId,Title,Sol#,Department/Ind.Agency,Sub-Tier,PostedDate,Type,SetASide,ResponseDeadLine,NaicsCode,ClassificationCode,Active,Link,Description,PrimaryContactFullname,PrimaryContactEmail
N-1,Cyber Threat Intelligence Platform,S-1,DEPT OF HOMELAND SECURITY,CISA,2026-06-20 09:00:00-04,Solicitation,,2026-08-15 17:00:00-04,541512,DA10,Yes,https://sam.gov/opp/N-1/view,Enterprise threat intelligence platform with dark web coverage,Jane Doe,jane@cisa.gov
N-2,Janitorial Services Building 4,S-2,GSA,PBS,2026-06-01 09:00:00-04,Solicitation,,2026-07-30 17:00:00-04,561720,S201,Yes,https://sam.gov/opp/N-2/view,Routine custodial services,,
N-3,Expired CTI Buy,S-3,DEPT OF DEFENSE,DISA,2026-01-05 09:00:00-04,Solicitation,,2026-02-01 17:00:00-04,541512,DA10,Yes,https://sam.gov/opp/N-3/view,threat intelligence subscription renewal,,
N-4,Archived Notice,S-4,DOJ,FBI,2026-05-05 09:00:00-04,Solicitation,,2026-09-01 17:00:00-04,541512,DA10,No,https://sam.gov/opp/N-4/view,inactive record,,
N-5,Sources Sought - Attack Surface Management,S-5,DEPT OF STATE,DS,2026-06-28 09:00:00-04,Sources Sought,SBA,,541519,DA01,Yes,https://sam.gov/opp/N-5/view,market research for attack surface management capability,,
N-1,Cyber Threat Intelligence Platform DUPE,S-1,DHS,CISA,2026-06-20 09:00:00-04,Solicitation,,2026-08-15 17:00:00-04,541512,DA10,Yes,https://sam.gov/opp/N-1/view,dupe row,,
"""


@pytest.fixture
def extract_file(tmp_path, monkeypatch):
    p = tmp_path / "opportunities_2026-07-03.csv"
    p.write_text(CSV)
    monkeypatch.setattr(se, "download_extract", lambda force=False: p)
    monkeypatch.setenv("LILA_SAM_EXTRACT_DIR", str(tmp_path))
    return p


def test_registered():
    assert REGISTRY.get("sam_extract") is not None


def test_iter_rows_normalizes_headers():
    rows = list(iter_rows(io.StringIO(CSV)))
    assert rows[0]["notice_id"] == "N-1"
    assert rows[0]["naics"] == "541512"
    assert rows[0]["poc_email"] == "jane@cisa.gov"
    assert rows[0]["agency"] == "DEPT OF HOMELAND SECURITY"


def test_iter_rows_preserves_place_of_performance_fields():
    text = (
        "NoticeId,Title,PopStreetAddress,PopCity,PopState,PopZip,PopCountry\n"
        "N-POP,Records platform,123 Main St,Landover,MD,20785,USA\n"
    )
    row = next(iter(iter_rows(io.StringIO(text))))
    assert row == {
        "notice_id": "N-POP",
        "title": "Records platform",
        "pop_street": "123 Main St",
        "pop_city": "Landover",
        "pop_state": "MD",
        "pop_zip": "20785",
        "pop_country": "USA",
    }


def test_search_filters_and_maps(extract_file):
    q = SourceQuery(naics_codes=["541512"], keywords=['"attack surface"'],
                    deadline_from=date(2026, 7, 3))
    out = SamExtractSource().search(q)
    ids = [o.source_id for o in out]
    assert "N-1" in ids      # NAICS hit, live deadline
    assert "N-5" in ids      # keyword hit, no deadline (sources sought kept)
    assert "N-2" not in ids  # wrong NAICS, no keyword
    assert "N-3" not in ids  # deadline passed
    assert "N-4" not in ids  # inactive
    assert ids.count("N-1") == 1  # deduped
    n1 = next(o for o in out if o.source_id == "N-1")
    assert n1.source == "sam.gov"
    assert str(n1.api_url) == "https://sam.gov/opp/N-1/view"
    assert n1.response_deadline == date(2026, 8, 15)
    assert n1.contacts[0].email == "jane@cisa.gov"
    assert n1.raw_payload["via"] == "daily-extract"
    n5 = next(o for o in out if o.source_id == "N-5")
    assert "attack surface" in n5.raw_payload["matched"]


def test_search_screens_the_whole_description(tmp_path, monkeypatch):
    """2026-07-30 ("we preserve data detail"): the old 2,000-char screen
    window covered 36.9% of description bytes and left 26.6% of notices
    partially screened - a client keyword at char 2,500 was a silent miss on
    a live solicitation. The screen now reads everything GSA serves."""
    marker = "network performance monitoring"
    description = "x" * 2600 + " " + marker + " " + "y" * 200
    text = (
        "NoticeId,Title,PostedDate,Type,ResponseDeadLine,NaicsCode,Active,"
        "Link,Description\n"
        f"N-DEEP,Network services,2026-07-01,Solicitation,"
        f"2026-08-15,541512,Yes,https://sam.gov/opp/N-DEEP/view,{description}\n"
    )
    path = tmp_path / "opportunities_2026-07-03.csv"
    path.write_text(text)
    monkeypatch.setattr(se, "download_extract", lambda force=False: path)
    out = SamExtractSource().search(SourceQuery(
        keywords=[marker], deadline_from=date(2026, 7, 3)))
    assert [row.source_id for row in out] == ["N-DEEP"]
    assert marker in out[0].raw_payload["matched"]
    evidence = out[0].raw_payload["screen_evidence_matches"]
    assert evidence[0]["term"] == marker
    assert evidence[0]["field"] == "description"
    assert evidence[0]["matched_text"] == marker
    assert marker in evidence[0]["context"]
    assert marker not in out[0].raw_payload["description_snippet"]


def test_snippet_stays_capped_while_the_screen_does_not(
        tmp_path, monkeypatch):
    """The description_snippet bounds downstream payload cost; it is no
    longer the screen surface and the two must not be conflated again."""
    marker = "network performance monitoring"
    description = "x" * 900 + " " + marker + " " + "y" * 1500
    text = (
        "NoticeId,Title,PostedDate,Type,ResponseDeadLine,NaicsCode,Active,"
        "Link,Description\n"
        f"N-DEEP,Network services,2026-07-01,Solicitation,"
        f"2026-08-15,541512,Yes,https://sam.gov/opp/N-DEEP/view,{description}\n"
    )
    path = tmp_path / "opportunities_2026-07-03.csv"
    path.write_text(text)
    monkeypatch.setattr(se, "download_extract", lambda force=False: path)
    out = SamExtractSource().search(SourceQuery(
        keywords=[marker], deadline_from=date(2026, 7, 3)))
    assert [row.source_id for row in out] == ["N-DEEP"]
    stored = out[0].raw_payload["description_snippet"]
    assert marker in stored
    assert len(stored) == se.SCREEN_DESCRIPTION_CHARS


def test_keyword_screen_requires_token_boundaries(tmp_path, monkeypatch):
    """Short product tokens must not match inside unrelated words."""
    text = (
        "NoticeId,Title,PostedDate,Type,ResponseDeadLine,NaicsCode,Active,"
        "Link,Description\n"
        "N-FALSE,Magnitude study,2026-07-01,Solicitation,2026-08-15,"
        "999999,Yes,https://sam.gov/opp/N-FALSE/view,Determine magnitude\n"
        "N-TRUE,AGNI network access,2026-07-01,Solicitation,2026-08-15,"
        "999999,Yes,https://sam.gov/opp/N-TRUE/view,AGNI network identity\n"
    )
    path = tmp_path / "opportunities_2026-07-03.csv"
    path.write_text(text)
    monkeypatch.setattr(se, "download_extract", lambda force=False: path)

    out = SamExtractSource().search(SourceQuery(
        keywords=["AGNI"], deadline_from=date(2026, 7, 3)))

    assert [row.source_id for row in out] == ["N-TRUE"]
    assert out[0].raw_payload["matched"] == ["AGNI"]


def test_search_returns_census_not_ceiling(extract_file):
    """L17: the old MAX_RESULTS=300 truncated matches mid-file and read as a
    census. The screen now returns EVERY match and reports last_census so
    screened-N claims can reconcile against ground truth."""
    src = SamExtractSource()
    out = src.search(SourceQuery(naics_codes=["541512"],
                                 deadline_from=date(2026, 7, 3)))
    assert len(out) >= 1                        # nothing truncated
    c = src.last_census
    assert c["matched"] == len(out)
    assert c["active_screened"] >= c["matched"]
    assert c["rows_scanned"] >= c["active_screened"]
    assert c["complete"] is True


def test_healthcheck_prefers_local_file(extract_file, monkeypatch):
    # opportunities_2026-07-03.csv exists in the dir → no network needed
    ok, detail = SamExtractSource().healthcheck()
    assert ok and "quota-free" in detail


def test_focus_filters_paid_flow_census_keeps_market(extract_file):
    """L18 filter-first (operator decision 2026-07-10): with a gate focus the
    returned list is the FOCUS SLICE (what triage pays for); the free census
    still counts the whole market, so a thin slice can never silently read
    as a dead market. Department focus captures components (CISA under DHS)."""
    src = SamExtractSource()
    q = SourceQuery(naics_codes=["541512", "541519"],
                    keywords=['"attack surface"'],
                    agencies=["DHS"], deadline_from=date(2026, 7, 3))
    out = src.search(q)
    ids = [o.source_id for o in out]
    assert ids == ["N-1"]                      # DHS/CISA only reaches triage
    c = src.last_census
    assert c["focus"] == ["DHS"]
    assert c["matched"] == 1                   # the slice
    assert c["market_matched"] >= 2            # N-1 + N-5 matched market-wide
    assert c["complete"] is True

    # no focus: identical query returns the whole matched market
    src2 = SamExtractSource()
    out2 = src2.search(SourceQuery(naics_codes=["541512", "541519"],
                                   keywords=['"attack surface"'],
                                   deadline_from=date(2026, 7, 3)))
    assert {o.source_id for o in out2} >= {"N-1", "N-5"}
    assert src2.last_census["focus"] is None
    assert src2.last_census["matched"] == src2.last_census["market_matched"]


def test_attachment_candidates_are_actionable_scope_bound_and_thread_deduped(
        tmp_path, monkeypatch):
    from tools.relevance.scope import EngagementScope
    from tools.relevance.taxonomy import (
        CapabilityTaxonomy, CodeUniverse, TaxonomyTerm)

    text = """\
NoticeId,Title,Sol#,Department/Ind.Agency,Sub-Tier,PostedDate,Type,ResponseDeadLine,NaicsCode,Active,Link,Description
N-OLD,Unified network performance RFI,SOL-1,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-01,Presolicitation,2026-08-15,517810,Yes,https://sam.gov/opp/N-OLD/view,unified network performance requirement
N-NEW,Unified network performance RFI,SOL-1,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-17,Presolicitation,2026-09-15,517810,Yes,https://sam.gov/opp/N-NEW/view,unified network performance requirement
N-DOD,Unified network performance RFI,SOL-2,DEPT OF DEFENSE,ARMY,2026-07-18,Sources Sought,2026-08-16,517810,Yes,https://sam.gov/opp/N-DOD/view,unified network performance requirement
N-AWARD,Unified network performance award,SOL-3,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-19,Award Notice,2026-08-17,517810,Yes,https://sam.gov/opp/N-AWARD/view,unified network performance requirement
N-THIN,Network support,SOL-4,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-19,Solicitation,2026-08-18,541519,Yes,https://sam.gov/opp/N-THIN/view,network support
N-OBSOLETE-OLD,Unified network performance RFI,SOL-5,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-01,Presolicitation,2026-08-15,517810,Yes,https://sam.gov/opp/N-OBSOLETE-OLD/view,unified network performance requirement
N-OBSOLETE-NEW,Unified network performance RFI,SOL-5,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-20,Presolicitation,2026-07-20,517810,Yes,https://sam.gov/opp/N-OBSOLETE-NEW/view,unified network performance requirement
N-SAME-OLD,Unified network performance RFI,SOL-6,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-18 09:00:00-04,Presolicitation,2026-08-20,517810,Yes,https://sam.gov/opp/N-SAME-OLD/view,unified network performance requirement
N-SAME-NEW,Unified network performance RFI,SOL-6,NATIONAL AERONAUTICS AND SPACE ADMINISTRATION,NASA,2026-07-18 17:00:00-04,Presolicitation,2026-08-20,517810,Yes,https://sam.gov/opp/N-SAME-NEW/view,unified network performance requirement
"""
    path = tmp_path / "opportunities_2026-07-21.csv"
    path.write_text(text)
    monkeypatch.setattr(se, "download_extract", lambda force=False: path)
    taxonomy = CapabilityTaxonomy(
        client_name="Testco", version=1, updated="2026-07-21",
        core=[TaxonomyTerm(term="network performance monitoring")],
        adjacent=[TaxonomyTerm(term="unified observability")],
        code_universe=CodeUniverse(naics=["541519"]),
    )
    candidates = SamExtractSource().attachment_candidates(
        SourceQuery(deadline_from=date(2026, 7, 21), agencies=["NASA"]), taxonomy,
        EngagementScope(preset="civilian"), limit=8)
    candidate_ids = [candidate.source_id for candidate in candidates]
    assert set(candidate_ids) == {"N-NEW", "N-SAME-NEW"}
    assert "N-OBSOLETE-OLD" not in candidate_ids
    selected = next(row for row in candidates if row.source_id == "N-NEW")
    assert selected.raw_payload["attachment_candidate_basis"][
        "anchors"] == ["network", "performance", "unified"]

    outside_focus = SamExtractSource().attachment_candidates(
        SourceQuery(deadline_from=date(2026, 7, 21), agencies=["DHS"]), taxonomy,
        EngagementScope(preset="civilian"), limit=8)
    assert outside_focus == []
