"""Award intelligence: the cap says so, the client's own record survives,
and rank-time data is real.

AUDIT FINDINGS 2026-07-30 (the award-intelligence C+):
  1. L2's 200-row amount-desc cap SATURATED on a live press (NetScout
     200/200, SolarWinds 200/194) and the log line read as a census. What
     falls off the bottom of a dollar sort is precisely the small recent
     task-order band where renewal-cadence and displacement signals live.
  2. The polysemy guard dropped the CLIENT'S OWN awards when the award text
     described the work without naming the vendor - the Osprey precedent
     erased $2M+ of proven delivery.
  3. extent-competed was fetched by award_repull and dropped before the
     pack, so sole-source/8(a) direct-award intelligence - which never
     appears as a notice - existed nowhere.
  4. Records were ranked and cut BEFORE detail enrichment, so an award
     whose option-extended end sits inside the 18-month clock window could
     be scored clockless and cut.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import retrieval  # noqa: E402

TODAY = date(2026, 7, 30)


def _row(i, *, description="network monitoring services by Acme Networks",
         recipient="ACME NETWORKS INC", amount=1_000_000, potential=None):
    return {
        "Award ID": f"AW-{i}", "Recipient Name": recipient,
        "Award Amount": amount, "Description": description,
        "Start Date": "2026-01-01", "End Date": "2026-12-31",
        "Period of Performance Potential End Date": potential,
        "Awarding Agency": "DHS", "Awarding Sub Agency": "CISA",
        "Contract Award Type": "DELIVERY ORDER", "NAICS": "541512",
        "PSC": "DA10", "generated_internal_id": f"CONT_AWD_{i}",
    }


def _post_pages(pages):
    """A fake USAspending: pages[i] is the result list for page i+1 of the
    amount pass; the LAST entry serves the recency pass when present."""
    calls = []

    def post(url, json=None, **kw):
        calls.append(json)
        if json["sort"] == "Start Date":
            rows = pages[-1]
        else:
            idx = json["page"] - 1
            rows = pages[idx] if idx < len(pages) else []
        return {"results": rows,
                "page_metadata": {"hasNext": True}}

    post.calls = calls
    return post


def _run(post, terms=None, vendor="Acme Networks"):
    return retrieval.run_l2(
        terms or [("competitor", "NetScout")], vendor=vendor,
        capability_terms=["network monitoring"], naics_boundary=["541512"],
        today=TODAY, post=post)


# --------------------------------------------------------------------------- #
# 1. saturation
# --------------------------------------------------------------------------- #
def test_a_saturated_term_is_stamped_and_gets_a_recency_pass():
    full = [_row(i, description="network monitoring by NetScout")
            for i in range(retrieval.L2_PAGE_LIMIT)]
    full2 = [_row(100 + i, description="network monitoring by NetScout")
             for i in range(retrieval.L2_PAGE_LIMIT)]
    recency = [_row(999, description="network monitoring by NetScout",
                    amount=120_000)]
    post = _post_pages([full, full2, recency])
    records, queries = _run(post)
    sat = next(q for q in queries if "SATURATED" in (q.note or ""))
    assert "200" in sat.note and "more matches" in sat.note
    rec = next(q for q in queries if q.method.endswith(":recency"))
    assert rec.result_count == 1
    assert rec.kept_after_screen == 1
    assert any(r.record_id == "AW-999" for r in records)
    # the recency pass really sorted by recency
    assert post.calls[-1]["sort"] == "Start Date"


def test_the_recency_pass_dedupes_against_the_dollar_pass():
    full = [_row(i, description="network monitoring by NetScout")
            for i in range(retrieval.L2_PAGE_LIMIT)]
    full2 = [_row(100 + i, description="network monitoring by NetScout")
             for i in range(retrieval.L2_PAGE_LIMIT)]
    post = _post_pages([full, full2, [full[0]]])   # recency returns a dupe
    records, queries = _run(post)
    assert sum(1 for r in records if r.record_id == "AW-0") == 1
    rec = next(q for q in queries if q.method.endswith(":recency"))
    assert rec.kept_after_screen == 0              # dupe kept nothing new


def test_an_unsaturated_term_gets_no_stamp_and_no_recency_pass():
    post = _post_pages([[_row(1, description="network monitoring by NetScout")]])
    _records, queries = _run(post)
    assert not any("SATURATED" in (q.note or "") for q in queries)
    assert not any(q.method.endswith(":recency") for q in queries)
    assert all(c["sort"] == "Award Amount" for c in post.calls)


# --------------------------------------------------------------------------- #
# 2. the client's own award survives on recipient identity
# --------------------------------------------------------------------------- #
def test_the_osprey_case_the_clients_award_survives_without_its_name():
    """Award text describes the WORK, names nobody; the recipient is the
    client. Identity outranks description."""
    post = _post_pages([[_row(
        7, description="aviation risk analytics and data delivery services",
        recipient="OSPREY FLIGHT SOLUTIONS LTD")]])
    records, _ = _run(post, terms=[("client", "Osprey Flight Solutions")],
                      vendor="Osprey Flight Solutions")
    assert len(records) == 1
    assert records[0].relevance_method == "client_recipient_identity"
    assert "OSPREY" in records[0].relevance_matched[0]


def test_a_single_word_common_client_name_does_not_claim_lookalikes():
    """THE TRAP: _is_client_recipient accepts a company-key prefix, so
    client "Riverbed" would claim RIVERBED RESTORATION LLC. A one-word
    dictionary-word client earns no recipient exemption."""
    post = _post_pages([[_row(
        8, description="streambank stabilization and habitat restoration",
        recipient="RIVERBED RESTORATION LLC")]])
    records, _ = _run(post, terms=[("client", "Riverbed")], vendor="Riverbed")
    assert records == []


def test_a_competitor_never_gets_the_recipient_exemption():
    post = _post_pages([[_row(
        9, description="generic services with no entity name",
        recipient="NETSCOUT SYSTEMS INC")]])
    records, _ = _run(post)
    assert records == []


# --------------------------------------------------------------------------- #
# 3. competition fields reach the pack
# --------------------------------------------------------------------------- #
def test_competition_fields_are_carried_by_detail_enrichment():
    from agents.golden_press.records import GoldenRecord

    record = GoldenRecord(
        record_id="AW-1", lane="L2_entity_award",
        generated_internal_id="CONT_AWD_1")
    payload = {
        "piid": "AW-1", "description": "sole source order",
        "total_obligation": 2_000_000,
        "period_of_performance": {"start_date": "2026-01-01",
                                  "end_date": "2026-12-31"},
        "latest_transaction_contract_data": {
            "extent_competed": "G",
            "extent_competed_description": "NOT COMPETED UNDER SAP",
            "type_set_aside_description": "8(a) sole source",
        },
        "recipient": {"recipient_name": "ACME"},
    }
    retrieval.enrich_details([record], get=lambda url: payload)
    assert record.competition == "NOT COMPETED UNDER SAP"
    assert record.competition_code == "G"


def test_prose_can_cite_competition_language_verbatim():
    """The whole point of carrying it: "awarded without competition" becomes
    provable pack text instead of an unbackable claim."""
    from agents.golden_press.compose import _pack_verbatim_text
    from agents.golden_press.records import EvidencePack, GoldenRecord

    pack = EvidencePack(
        client_name="Acme", generated_at="2026-07-30T00:00:00",
        records=[GoldenRecord(record_id="A1", lane="L2_entity_award",
                              competition="NOT COMPETED UNDER SAP")],
        research={})
    assert "not competed under sap" in _pack_verbatim_text(pack)


# --------------------------------------------------------------------------- #
# 4. rank-time data is real
# --------------------------------------------------------------------------- #
def test_potential_end_date_arrives_at_search_time_and_flags_the_clock():
    """Ranked-then-enriched was backwards: an option-extended award inside
    the 18-month window was scored clockless and cut. The search endpoint
    serves the field (verified live 2026-07-30); the clock must see it
    BEFORE selection."""
    post = _post_pages([[_row(
        3, description="network monitoring by NetScout",
        potential="2027-06-30")]])       # inside the 548-day window
    records, _ = _run(post)
    assert records[0].potential_end_date == "2027-06-30"
    flagged = retrieval.flag_clocks(records, today=TODAY)
    assert flagged == 1 and records[0].research_clock is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
