"""Step6 native consumers preserve source decisions and distinct populations."""
from copy import deepcopy

from agents.decisions.triage import deterministic_prefilter
from agents.leadgen.from_assess import draft_lead_rows
from agents.reports.facts import _buyer_map_facts, _funded_demand_facts, _Counter
from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm

from tests.test_leadgen_from_assess import _run, _live_research, _live_bid_now


TAXONOMY = CapabilityTaxonomy(
    client_name="Synthetic", version=1, updated="2026-09-13",
    core=[TaxonomyTerm(term="packet capture")])


def notice(identity, posted, text):
    return {"source": "sam.gov", "source_id": identity,
            "title": "Network support", "description": text,
            "posted_date": posted, "notice_type": "Solicitation",
            "raw_payload": {"solicitation": "NETWORK-01",
                            "agency": "Synthetic Agency", "office": "Office A"}}


def test_latest_zero_span_member_remains_in_compact_family_and_suppression_count():
    rows = [notice("OLDER", "2026-09-01", "The contractor shall provide packet capture."),
            notice("LATEST", "2026-09-02", "Deliver only projectors.")]
    before = deepcopy(rows)
    candidates, dispositions, receipt = deterministic_prefilter(rows, TAXONOMY)
    assert not candidates and dispositions["OLDER"]["superseded_by"] == "LATEST"
    assert receipt["superseded_revisions"] == 1
    assert receipt["positive_candidate_superseded_revisions"] == 0
    diagnostic = receipt["requirement_family_diagnostic"]
    family = diagnostic["families"][0]
    assert family["notice_ids"] == ["OLDER", "LATEST"]
    assert family["representative_id"] == "LATEST"
    assert family["members"][1]["span_count"] == 0
    assert family["members"][0]["superseded_by"] == "LATEST"
    compact = diagnostic["families_by_term_field"][0]
    assert compact["notice_ids"] == ["OLDER", "LATEST"]
    assert compact["matched_notice_ids"] == ["OLDER"]
    assert diagnostic["term_field_counts"][0]["requested_families"] == 0
    assert rows == before


def test_family_with_no_lexical_match_remains_in_complete_diagnostic():
    rows = [notice("ONE", "2026-09-01", "Deliver projectors."),
            notice("TWO", "2026-09-02", "Deliver cables.")]
    _, _, receipt = deterministic_prefilter(rows, TAXONOMY)
    diagnostic = receipt["requirement_family_diagnostic"]
    assert diagnostic["unique_lexical_families"] == 0
    assert diagnostic["families_by_term_field"] == []
    assert diagnostic["families"][0]["representative_id"] == "TWO"
    assert len(diagnostic["families"][0]["members"]) == 2


def test_research_notice_remains_parent_without_invented_live_bid_child():
    run = _run(live=[_live_research()])
    before = run.model_dump(mode="json")
    result = draft_lead_rows(run)
    assert len(result.parents) == 1 and not result.leads
    assert result.parents[0].live_classification.value == "market_research"
    assert result.parents[0].live_recommendation.value == "monitor"
    assert result.traces and "parent research" in str(result.traces)
    assert run.model_dump(mode="json") == before


def test_current_bid_control_retains_draft_child_and_existing_hold():
    result = draft_lead_rows(_run(live=[_live_bid_now()]))
    assert len(result.parents) == len(result.leads) == 1
    assert result.leads[0].buying_motion.kind.value == "live_bid"
    assert result.leads[0].lead_tier.value == "HOLD"
    assert result.leads[0].communication_permission.value == "none"


def test_context_facts_keep_source_dates_and_mentions_without_funding_or_displacement():
    source = {"funded_demand": {"hits": {"packet capture": [
        {"title": "Budget context", "collection": "BILLS", "date": "2026-09-01",
         "url": "https://www.govinfo.gov/context"}]}},
        "incumbent_buyer_map": {"buyers": [{"buyer": "Agency", "products": ["Tools"],
            "total": 100, "next_pop_end": "2026-09-14", "displacement_window": True,
            "records": [{"recipient": "Supplier", "amount": 100,
                         "description": "Historical tools", "url": "https://example.gov/award"}]}]}}
    facts = _funded_demand_facts(source, _Counter()) + _buyer_map_facts(source, {}, _Counter())
    text = " ".join(f.text for f in facts)
    assert "packet capture" in text and "2026-09-14" in text
    assert "FUNDED-DEMAND SIGNAL" not in text and "DISPLACEMENT WINDOW" not in text
    assert "Confirm any follow-on purchase" in text
