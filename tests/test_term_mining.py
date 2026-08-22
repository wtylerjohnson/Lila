"""Frame audit and term mining: the finding is the deliverable.

The audit's job is to say WHY a frame cannot produce notices, which is the
thing the pipeline never surfaced: Riverbed's approved frame returned zero
solicitations against 78,577 stored notices and nothing explained it.
"""

from __future__ import annotations

import sqlite3

import pytest

from agents.golden_press.term_mining import audit_frame, frame_report, mine_terms


@pytest.fixture()
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE notices (notice_id TEXT, title TEXT, "
                 "description_prefix TEXT)")
    rows = [
        ("N1", "Network monitoring hardware refresh", "network monitoring tools"),
        ("N2", "Enterprise subscription renewal", "software subscription renewal"),
        ("N3", "Indefinite delivery indefinite quantity", "part number listing"),
        ("N4", "Indefinite delivery vehicle", "part number schedule"),
    ]
    conn.executemany("INSERT INTO notices VALUES (?,?,?)", rows)
    conn.commit()
    return conn


def test_audit_flags_vendor_marketing_from_the_shared_guards(store):
    rows = {r["term"]: r for r in audit_frame(
        ["unified observability", "network monitoring"], conn=store)}
    marketing = rows["unified observability"]
    assert marketing["usable"] is False
    assert marketing["guard_class"] == "vendor_marketing"
    assert rows["network monitoring"]["usable"] is True


def test_audit_flags_a_term_no_notice_can_ever_match(store):
    row = audit_frame(["digital employee experience is nowhere"], conn=store)[0]
    assert row["store_hits"] == 0
    assert row["usable"] is False
    assert "no stored notice" in row["why"]


def test_audit_counts_real_store_presence(store):
    row = audit_frame(["network monitoring"], conn=store)[0]
    assert row["store_hits"] == 1 and row["usable"] is True


def test_audit_degrades_without_a_store_never_blocks():
    rows = audit_frame(["network monitoring"], conn=None)
    assert rows and rows[0]["term"] == "network monitoring"


def _records(*descriptions):
    return [{"record_id": f"R{i}", "description": d}
            for i, d in enumerate(descriptions, 1)]


def test_mining_requires_two_distinct_records(store):
    once = _records("network monitoring appliance", "unrelated widget purchase")
    assert mine_terms(once, conn=store, min_records=2) == []
    twice = _records("network monitoring appliance", "network monitoring refresh")
    terms = [r["term"].casefold() for r in mine_terms(twice, conn=store, min_records=2)]
    assert "network monitoring" in terms


def test_mining_drops_boilerplate_by_measured_share(store):
    # "indefinite delivery" sits in 2 of 4 stored notices (50%), far above the
    # 1% ceiling, so it is boilerplate by measurement rather than hand-list.
    recs = _records("indefinite delivery indefinite quantity award",
                    "indefinite delivery vehicle award")
    terms = [r["term"].casefold() for r in mine_terms(store and recs, conn=store,
                                                      min_records=2)]
    assert not any("indefinite delivery" in t for t in terms)


def test_mining_requires_store_presence(store):
    recs = _records("zzz nonexistent phrase here", "zzz nonexistent phrase again")
    assert mine_terms(recs, conn=store, min_records=2) == []


def test_mining_never_reproposes_an_existing_frame_term(store):
    recs = _records("network monitoring appliance", "network monitoring refresh")
    terms = [r["term"].casefold() for r in mine_terms(
        recs, existing_terms=["network monitoring"], conn=store, min_records=2)]
    assert "network monitoring" not in terms


def test_mining_suppresses_sub_phrases(store):
    recs = _records("software subscription renewal now",
                    "software subscription renewal again")
    terms = [r["term"].casefold() for r in mine_terms(recs, conn=store, min_records=2)]
    # the longest surviving phrase wins; its fragments are not also proposed
    assert not ({"software subscription", "subscription renewal"} <= set(terms))


def test_mining_strips_identifiers_and_igf_markers(store):
    recs = _records("2032H5-19-F-00718 network monitoring IGF::OT::IGF",
                    "network monitoring 47QTCA22D003X IGF::CL::IGF")
    terms = [r["term"].casefold() for r in mine_terms(recs, conn=store, min_records=2)]
    assert "network monitoring" in terms
    assert not any(any(ch.isdigit() for ch in t) for t in terms)


def test_frame_report_proposes_without_writing_anything(store):
    pack = {
        "client_name": "TestCo",
        "research": {"capability_terms": ["unified observability"],
                     "entities": {"product": ["Widget"]}},
        "records": _records("network monitoring appliance",
                            "network monitoring refresh"),
    }
    report = frame_report(pack, conn=store)
    assert report["client"] == "TestCo"
    assert report["unusable_terms"] == 1
    assert "operator-owned" in report["note"]
    # the approved frame is unchanged by the report
    assert pack["research"]["capability_terms"] == ["unified observability"]
