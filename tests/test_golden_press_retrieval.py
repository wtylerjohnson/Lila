"""GOLDEN_BUILD Phase 2 tests: lanes, relevance screen, dedup, clocks,
sufficiency, integrity, recall scoring. Offline doctrine: fake HTTP callables,
no network, no LLM."""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decisions import schemas as ds  # noqa: E402
from agents.golden_press import recall, retrieval  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402

TODAY = date(2026, 7, 24)


def _strategy(entities=None):
    return ds.IntakeStrategy(
        client_name="Acme Networks",
        pursuit_strategy="p",
        confidence=0.9,
        review_gate="g",
        inferred_naics=["541519"],
        keywords=[
            ds.Keyword(term="network monitoring",
                       category=ds.KeywordCategory.CAPABILITY, rationale="core"),
            ds.Keyword(term="observability",
                       category=ds.KeywordCategory.TECHNOLOGY, rationale="core"),
        ],
        research_entities=entities if entities is not None else [
            ds.ResearchEntity(name="AcmeFlow", kind="product", rationale="site"),
            ds.ResearchEntity(name="RivalWorks", kind="competitor", rationale="news"),
            ds.ResearchEntity(name="ChannelCo", kind="reseller", rationale="partners"),
        ],
    )


# ---- relevance screen ------------------------------------------------------ #
def test_relevance_screen_accepts_tech_and_rejects_dredging():
    """The plan's own example: 'riverbed' the common noun. A dredging award
    with no capability term never enters the pack."""
    assert retrieval.screen_relevance(
        naics="237990", psc="Z2GZ",
        description="Riverbed stabilization and dredging near mile marker 12",
        capability_terms=["network monitoring", "observability"]) is None
    # A CODE ALONE NO LONGER ADMITS ANYTHING (operator directive 2026-07-29:
    # keywords generate, NAICS qualifies). These two used to assert the
    # auto-pass and were pinning it in place. Measured cost of that auto-pass
    # on a delivered report: 26 of 39 packed apexanalytix records qualified as
    # "tech_naics" carrying ZERO capability keywords, i.e. they were in a
    # client report because they had a software NAICS code and nothing else.
    assert retrieval.screen_relevance(
        naics="541519", psc=None, description="misc",
        capability_terms=[]) is None
    assert retrieval.screen_relevance(
        naics="237990", psc="7A20", description="software renewal",
        capability_terms=[]) is None
    # The code is still load-bearing: it QUALIFIES a keyword hit that would
    # otherwise fail tier 2.
    assert retrieval.screen_relevance(
        naics="541519", psc=None,
        description="enterprise network monitoring refresh",
        capability_terms=["network monitoring"]) is not None
    # ... and the CLIENT'S OWN boundary is what counts, not a global tech list.
    assert retrieval.screen_relevance(
        naics="541219", psc="R704",
        description="Recovery Audit Services for the Office of Finance",
        capability_terms=["recovery audit"]) is None
    assert retrieval.screen_relevance(
        naics="541219", psc="R704",
        description="Recovery Audit Services for the Office of Finance",
        capability_terms=["recovery audit"],
        naics_boundary=["541219", "541611"]) is not None
    # OR -> AND for tier 2 (2026-07-28). A multi-word procurement phrase can
    # no longer carry a record on its own: with no tech code, "network
    # monitoring" was admitting Navy substation relays and a State Department
    # health-insurance provider network.
    assert retrieval.screen_relevance(
        naics=None, psc=None,
        description="AcmeFlow network monitoring appliance refresh",
        capability_terms=["network monitoring"]) is None
    # A DISTINCTIVE name still stands alone, because it carries its own
    # evidence: nothing else is called AcmeFlow.
    method, matched = retrieval.screen_relevance(
        naics=None, psc=None,
        description="AcmeFlow network monitoring appliance refresh",
        capability_terms=["AcmeFlow"], distinctive_names=["AcmeFlow"])
    assert method == "term_cooccurrence" and matched == ["AcmeFlow"]


def test_word_hits_use_boundaries_not_substrings():
    assert retrieval._word_hits("ARM the system", ["arm"]) == ["arm"]
    assert retrieval._word_hits("disarmament corps", ["arm"]) == []


# ---- L2 -------------------------------------------------------------------- #
def _l2_payload(rows):
    return {"results": rows, "page_metadata": {"hasNext": False}}


def test_l2_queries_carry_only_entity_terms_and_screen_records():
    """Integrity rule: query bodies contain exactly the generic entity terms.
    Records failing the relevance screen never appear."""
    strategy = _strategy()
    terms = retrieval.entity_terms(strategy, include_resellers=False)
    assert terms == [("client", "Acme Networks"), ("product", "AcmeFlow"),
                     ("competitor", "RivalWorks")]

    bodies = []

    def fake_post(url, json=None, **kw):
        bodies.append(json)
        return _l2_payload([
            {"Award ID": "GS35F001", "Recipient Name": "ChannelCo LLC",
             "Award Amount": 1000000.0, "Description": "AcmeFlow appliance refresh",
             "Start Date": "2025-01-01", "End Date": "2026-12-31",
             "Awarding Agency": "GSA", "Awarding Sub Agency": "FAS",
             "Contract Award Type": "DELIVERY ORDER", "NAICS": "541519",
             "PSC": "7A20", "generated_internal_id": "CONT_AWD_GS35F001_X_Y_Z"},
            {"Award ID": "W912DREDGE", "Recipient Name": "Dredge Bros",
             "Award Amount": 2000000.0,
             "Description": "riverbed dredging and streambank stabilization",
             "Start Date": "2025-01-01", "End Date": "2026-12-31",
             "Awarding Agency": "USACE", "Awarding Sub Agency": None,
             "Contract Award Type": "BPA CALL", "NAICS": "237990",
             "PSC": "Z2GZ", "generated_internal_id": "CONT_AWD_W912_X_Y_Z"},
        ])

    records, queries = retrieval.run_l2(
        terms, ["network monitoring"], today=TODAY, post=fake_post)
    # ONCE, FOR THE TERM THAT IS ACTUALLY IN IT. This asserted 3, one per
    # term search, which only held because a tech NAICS auto-passed the award
    # for every term regardless of text. Searching "RivalWorks" and keeping an
    # award whose description says "AcmeFlow" is how a COMPETITOR lane fills
    # up with the client's own wins. The award names AcmeFlow, so it is kept
    # for AcmeFlow and for nothing else.
    assert len(records) == 1
    assert records[0].record_id == "GS35F001"
    assert records[0].entity_hits == ["AcmeFlow"]
    searched = [b["filters"]["keywords"] for b in bodies]
    assert searched == [["Acme Networks"], ["AcmeFlow"], ["RivalWorks"]]
    for body in bodies:                 # full-field capture requested
        assert "Description" in body["fields"]
        assert body["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert all(q.result_count == 2 for q in queries)


def test_l2_failure_is_recorded_never_silent():
    def dead_post(url, json=None, **kw):
        raise RuntimeError("boom 503")

    records, queries = retrieval.run_l2(
        [("client", "Acme Networks")], [], today=TODAY, post=dead_post)
    assert records == []
    assert len(queries) == 1 and "FAILED" in queries[0].note


def test_l2_reuses_the_l1_entity_guard_for_polysemous_product_names():
    """SteelHead is a trout until the Riverbed vendor is present too."""
    rows = [
        {"Award ID": "FISH", "Recipient Name": "Aquaculture Lab",
         "Award Amount": 100000.0,
         "Description": ("steelhead migration monitoring software and "
                         "telemetry for fisheries"),
         "Start Date": "2025-01-01", "End Date": "2026-12-31",
         "Awarding Agency": "Commerce", "Awarding Sub Agency": "NOAA",
         "Contract Award Type": "DELIVERY ORDER", "NAICS": "541519",
         "PSC": "7A20", "generated_internal_id": "CONT_AWD_FISH_X_Y_Z"},
        {"Award ID": "NET", "Recipient Name": "Network Integrator",
         "Award Amount": 200000.0,
         "Description": ("Riverbed SteelHead network monitoring software "
                         "for enterprise routers"),
         "Start Date": "2025-01-01", "End Date": "2026-12-31",
         "Awarding Agency": "GSA", "Awarding Sub Agency": "FAS",
         "Contract Award Type": "DELIVERY ORDER", "NAICS": "541519",
         "PSC": "7A20", "generated_internal_id": "CONT_AWD_NET_X_Y_Z"},
    ]

    records, _ = retrieval.run_l2(
        [("product", "SteelHead")], ["network monitoring"],
        entities={"product": ["SteelHead"]}, vendor="Riverbed",
        today=TODAY, post=lambda *a, **k: _l2_payload(rows))

    assert [record.record_id for record in records] == ["NET"]


# ---- dedup + L3 ------------------------------------------------------------ #
def test_dedupe_merges_entity_hits_and_prefers_enriched():
    a = GoldenRecord(record_id="GS-35F-001", lane="L2_entity_award",
                     entity_hits=["AcmeFlow"])
    b = GoldenRecord(record_id="GS35F001", lane="L2_entity_award",
                     entity_hits=["Acme Networks"], detail_enriched=True,
                     research_clock=True)
    out = retrieval.dedupe([a, b])
    assert len(out) == 1
    kept = out[0]
    assert kept.detail_enriched is True
    assert set(kept.entity_hits) == {"AcmeFlow", "Acme Networks"}
    assert kept.research_clock is True


def test_clock_flags_only_l2_ends_inside_18_months():
    inside = GoldenRecord(record_id="A", lane="L2_entity_award",
                          period_end="2027-07-01")
    outside = GoldenRecord(record_id="B", lane="L2_entity_award",
                           period_end="2028-06-01")
    past = GoldenRecord(record_id="C", lane="L2_entity_award",
                        period_end="2026-07-01")
    notice = GoldenRecord(record_id="D", lane="L1_notice",
                          period_end="2027-01-01")
    flagged = retrieval.flag_clocks([inside, outside, past, notice], today=TODAY)
    assert flagged == 1
    assert inside.research_clock and not outside.research_clock
    assert not past.research_clock and not notice.research_clock


# ---- L1 -------------------------------------------------------------------- #
def test_l1_screens_caps_and_emits_canonical_public_links():
    guids = [f"{i:032x}" for i in range(20)]
    sweep = {
        "client": "Acme Networks", "generated_at": "2026-07-24T09:00:00Z",
        "results": {"sam.gov": (
            [{"source": "sam.gov", "source_id": guid,
              "title": "network monitoring modernization", "agency": "DHS",
              "naics_code": "541519", "psc_code": "D316", "set_aside": None,
              "posted_date": "2026-07-01", "response_deadline": "2026-08-01",
              "api_url": f"https://sam.gov/workspace/contract/opp/{guid}/view",
              "raw_payload": {}, "contacts": []} for guid in guids]
            + [{"source": "webscrape", "source_id": "BAD1", "title":
                "network monitoring", "api_url": "https://x", "raw_payload": {}},
               {"source": "sam.gov", "source_id": guids[0],
                "title": "janitorial services", "api_url": "https://y",
                "raw_payload": {}}])},
    }
    records, queries = retrieval.run_l1_from_sweep(
        sweep, ["network monitoring"], cap=15)
    assert len(records) == 15                      # loud cap
    assert all(r.lane == "L1_notice" for r in records)
    # LINKAGE LAW: pack carries the canonical PUBLIC notice form, never the
    # sweep's login-walled workspace URL.
    assert all(r.url == f"https://sam.gov/opp/{r.record_id}/view"
               for r in records)
    assert queries[0].result_count == 21           # provenance-eligible rows
    assert queries[0].kept_after_screen == 15


# ---- sufficiency + escalation ---------------------------------------------- #
def _mk_records(n, lane="L2_entity_award", clock_first=True):
    out = []
    for i in range(n):
        out.append(GoldenRecord(
            record_id=f"R{lane[:2]}{i}", lane=lane,
            research_clock=(clock_first and i == 0 and lane == "L2_entity_award")))
    return out


def test_sufficiency_counts_clock_flag_as_lane():
    records = (_mk_records(10) + _mk_records(3, "L4_forecast")
               + _mk_records(2, "L1_notice"))
    verdict = retrieval.assess_sufficiency(records, escalated=False)
    assert verdict.unique_records == 15
    assert verdict.met is True
    assert "L3_research_clock" in verdict.lanes_represented


def test_build_pack_escalates_with_resellers_then_notes_scarcity():
    strategy = _strategy()
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append(json["filters"]["keywords"][0])
        return _l2_payload([])

    def fake_get(url, **kw):
        raise AssertionError("no details should be pulled for zero records")

    pack = retrieval.build_evidence_pack(
        strategy, sweep=None, today=TODAY, post=fake_post, get=fake_get,
        forecast_fetch=lambda: [], sam_degraded_note="quota exhausted")
    # base pass then reseller escalation, every query recorded
    assert calls == ["Acme Networks", "AcmeFlow", "RivalWorks", "ChannelCo"]
    assert pack.sufficiency is not None and pack.sufficiency.met is False
    assert pack.sufficiency.escalated is True
    assert pack.scarcity_note and pack.scarcity_note["scarce"] is True
    assert pack.research["entities"]["product"] == ["AcmeFlow"]
    lanes = {l.lane: l.status for l in pack.lanes}
    assert lanes["L1_notice"] == "degraded"
    assert lanes["L2_entity_award"] == "live"


def test_entity_terms_strip_client_prefix_from_products():
    """Generic transform: '<Client> <Product>' also searches the bare product
    suffix (award text drops vendor prefixes); short suffixes stay out."""
    strategy = _strategy(entities=[
        ds.ResearchEntity(name="Acme Networks FlowScope", kind="product",
                          rationale="site"),
        ds.ResearchEntity(name="Acme Networks IQ", kind="product",
                          rationale="site"),
    ])
    terms = retrieval.entity_terms(strategy, include_resellers=False)
    assert ("product", "Acme Networks FlowScope") in terms
    assert ("product", "FlowScope") in terms            # suffix searched too
    assert not any(name == "IQ" for _, name in terms)   # under 4 chars: out


def test_select_pack_curates_by_affinity_corridor_and_caps():
    strategy = _strategy()
    records = []
    # 25 core records at two agencies; dollars descending; one clock
    for i in range(25):
        records.append(GoldenRecord(
            record_id=f"CORE{i:03d}", lane="L2_entity_award",
            agency="IRS" if i % 2 else "SSA",
            obligated_dollars=1_000_000 - i * 1000,
            research_clock=(i == 24), entity_hits=["AcmeFlow"]))
    # competitor rows: corridor (IRS) small dollars vs off-corridor big dollars
    records.append(GoldenRecord(
        record_id="COMP-IRS", lane="L2_entity_award", agency="IRS",
        obligated_dollars=50_000, entity_hits=["RivalWorks"]))
    records.append(GoldenRecord(
        record_id="COMP-USDA", lane="L2_entity_award", agency="USDA",
        obligated_dollars=9_000_000, entity_hits=["RivalWorks"]))
    records.append(GoldenRecord(
        record_id="FC1", lane="L4_forecast", agency="DHS"))
    records.append(GoldenRecord(
        record_id="N1", lane="L1_notice", agency="DHS"))

    selected, selection = retrieval.select_pack(records, strategy)
    ids = [r.record_id for r in selected]
    # clocked corridor rep leads, anchors for unrepresented corridors follow,
    # all 25 core rows fit under the cap
    assert ids[0] == "CORE024"
    assert "CORE000" in ids and "CORE001" in ids
    assert len([i for i in ids if i.startswith("CORE")]) == 25
    # per-competitor cap of 2 keeps both rows; corridor row ranks first
    assert ids.index("COMP-IRS") < ids.index("COMP-USDA")
    assert "FC1" in ids and "N1" in ids
    assert selection["screened_relevant"]["L2_core"] == 25
    assert selection["packed"]["L2_core"] == 25
    assert "IRS" in selection["corridor_agencies"]


def test_select_pack_keeps_seven_figure_clocks_and_mega_anchors():
    """The golden shape: a corridor's clocked rep does not evict either the
    corridor's mega-anchor (the historical platform) or another seven-figure
    live clock in the same corridor."""
    strategy = _strategy()
    mega = GoldenRecord(record_id="MEGA", lane="L2_entity_award",
                        agency="Treasury", sub_agency="IRS",
                        obligated_dollars=49_000_000.0,
                        entity_hits=["AcmeFlow"])
    rep = GoldenRecord(record_id="REP", lane="L2_entity_award",
                       agency="Treasury", sub_agency="IRS",
                       obligated_dollars=9_400_000.0, research_clock=True,
                       entity_hits=["AcmeFlow"])
    clock2 = GoldenRecord(record_id="CLK2", lane="L2_entity_award",
                          agency="Treasury", sub_agency="IRS",
                          obligated_dollars=1_900_000.0, research_clock=True,
                          entity_hits=["AcmeFlow"])
    small = GoldenRecord(record_id="SMALL", lane="L2_entity_award",
                         agency="Treasury", sub_agency="IRS",
                         obligated_dollars=40_000.0, research_clock=True,
                         entity_hits=["AcmeFlow"])
    selected, _ = retrieval.select_pack([mega, rep, clock2, small], strategy)
    ids = [r.record_id for r in selected]
    assert ids[0] == "REP"              # corridor's largest live clock leads
    assert "CLK2" in ids                # seven-figure clock packs
    assert "MEGA" in ids                # mega-anchor packs despite repped corridor
    assert "SMALL" in ids               # fill (cap not reached)


def test_engagement_scope_is_exclude_only_and_never_guessed():
    """Operator boundary doctrine: no scope config passes everything
    (UNSCOPED); a configured civilian preset drops DoD rows with a count,
    regardless of capability strength."""
    from tools.relevance.scope import EngagementScope
    rows = [
        GoldenRecord(record_id="CIV1", lane="L2_entity_award",
                     agency="Department of the Treasury",
                     sub_agency="Internal Revenue Service"),
        GoldenRecord(record_id="MIL1", lane="L2_entity_award",
                     agency="Department of Defense",
                     sub_agency="Defense Information Systems Agency",
                     obligated_dollars=10_000_000.0),
    ]
    inside, outside = retrieval.apply_engagement_scope(rows, None)
    assert [r.record_id for r in inside] == ["CIV1", "MIL1"]
    assert outside == []
    scope = EngagementScope(preset="civilian")
    inside, outside = retrieval.apply_engagement_scope(rows, scope)
    assert [r.record_id for r in inside] == ["CIV1"]
    assert [r.record_id for r in outside] == ["MIL1"]


def test_forecast_scoring_needs_keyword_signal_not_naics_alone():
    """Plan L4 screen: keywords AND NAICS. Exact NAICS with zero keyword
    signal never qualifies (that is how maintenance-renewal noise won the
    cap over network/observability programs)."""
    phrases = ["network observability", "network performance monitoring"]
    unigrams = {"network": 2, "observability": 1, "performance": 1,
                "monitoring": 1}
    kw = dict(phrases=phrases, unigrams=unigrams, naics_codes=["541519"])
    score, reasons = retrieval.score_forecast(
        "Enterprise Network Operations Center Services",
        "provide network operations and monitoring services", "541519 - Other",
        **kw)
    assert score >= 4 and any("terms:" in r for r in reasons)
    assert any("exact NAICS 541519" in r for r in reasons)
    score, _ = retrieval.score_forecast(
        "Oracle Hardware Maintenance", "renewal of licenses", "541519", **kw)
    assert score == 0.0                      # NAICS alone: out
    score, _ = retrieval.score_forecast(
        "Data observability platform", "server telemetry software", "513210",
        phrases=phrases, unigrams=unigrams, naics_codes=["513210"])
    assert score >= 3                        # unigram + exact NAICS: in
    assert retrieval._value_magnitude("$50M - $100M") == 100e6
    assert retrieval._value_magnitude("Over $1B") == 1e9
    assert retrieval._value_magnitude(None) == 0.0


def test_forecast_tier_ladder_rejects_polysemy_even_with_a_tech_code():
    phrases = ["network monitoring", "observability"]
    unigrams = {"network": 1, "monitoring": 1, "observability": 1}
    kwargs = dict(phrases=phrases, unigrams=unigrams,
                  naics_codes=["541519"])

    score, _ = retrieval.score_forecast(
        "Substation Network Monitoring",
        "protection and automation devices continuously monitor switch status",
        "541519", **kwargs)
    assert score == 0.0

    score, _ = retrieval.score_forecast(
        "Low-observable power systems",
        "reduce observability through lower acoustic and electromagnetic emissions",
        "541519", **kwargs)
    assert score == 0.0


def test_forecast_tier2_phrase_needs_a_qualifying_client_code():
    score, _ = retrieval.score_forecast(
        "Enterprise network monitoring",
        "network monitoring for routers and servers", "237990",
        phrases=["network monitoring"],
        unigrams={"network": 1, "monitoring": 1},
        naics_codes=["541519"])
    assert score == 0.0


def test_forecast_niche_slots_rescue_high_relevance_small_dollar_rows():
    """Operator-ratified 2026-07-24: up to two reserved slots for
    keyword-strong sub-$5M forecasts the value-first cap would drop; a
    sub-$5M row already inside the cap consumes no slot."""
    strategy = _strategy()
    l4 = []
    for i in range(retrieval.PACK_FORECAST_CAP):
        l4.append(GoldenRecord(
            record_id=f"BIG{i}", lane="L4_forecast",
            forecast_score=10.0 - i, forecast_value=100e6))
    niche_hi = GoldenRecord(record_id="NICHE-HI", lane="L4_forecast",
                            forecast_score=4.0, forecast_value=2e6,
                            forecast_rarity=1.4)   # rare defining term wins
    niche_lo = GoldenRecord(record_id="NICHE-LO", lane="L4_forecast",
                            forecast_score=9.0, forecast_value=1e6,
                            forecast_rarity=0.9)
    niche_3rd = GoldenRecord(record_id="NICHE-3", lane="L4_forecast",
                             forecast_score=9.5, forecast_value=1e6,
                             forecast_rarity=0.8)
    big_dropped = GoldenRecord(record_id="BIG-DROPPED", lane="L4_forecast",
                               forecast_score=9.9, forecast_value=50e6)
    # incoming order is value-first (as run_l4 emits)
    rows = l4 + [big_dropped, niche_hi, niche_lo, niche_3rd]
    selected, selection = retrieval.select_pack(rows, strategy)
    ids = [r.record_id for r in selected if r.lane == "L4_forecast"]
    # niche slots ADD to the cap; the value-first main picks are untouched
    assert len(ids) == retrieval.PACK_FORECAST_CAP + 2
    assert ids[:retrieval.PACK_FORECAST_CAP] == [
        f"BIG{i}" for i in range(retrieval.PACK_FORECAST_CAP)]
    assert "NICHE-HI" in ids and "NICHE-LO" in ids     # two niche slots used
    assert "NICHE-3" not in ids                        # only two reserved
    assert "BIG-DROPPED" not in ids                    # $50M is not niche
    assert selection["niche_forecast_slots"] == ["NICHE-HI", "NICHE-LO"]


# ---- recall (post-retrieval only) ------------------------------------------ #
GOLDEN_SNIPPET = """
<a href="https://www.usaspending.gov/award/CONT_AWD_GS35F001_4730_PARENT_9999">x</a>
<a href="https://www.usaspending.gov/award/CONT_AWD_MISSING1_1111_-NONE-_-NONE-">y</a>
<a href="https://apfs-cloud.dhs.gov/record/70806/public-print/">z</a>
<a href="https://apfs-cloud.dhs.gov/record/99999/public-print/">w</a>
"""


def test_recall_scores_matches_and_misses_by_identity_not_url_shape():
    records = [
        GoldenRecord(record_id="GS35F001", lane="L2_entity_award",
                     generated_internal_id="CONT_AWD_GS35F001_4730_PARENT_9999"),
        GoldenRecord(record_id="F2025070806", lane="L4_forecast",
                     url="https://apfs-cloud.dhs.gov/forecast/70806"),
    ]
    score = recall.score_recall(records, GOLDEN_SNIPPET)
    assert score["golden_total"] == 4
    assert score["matched_count"] == 2
    assert any("GS35F001" in m for m in score["matched"])
    assert "APFS:70806" in score["matched"]
    assert "APFS:99999" in score["missed"]
    assert score["non_forecast_total"] == 2
    assert score["non_forecast_matched"] == 1


def test_recall_module_is_not_imported_by_retrieval():
    """Integrity rule: the benchmark is unreachable from query construction."""
    import ast

    import agents.golden_press.retrieval as r
    tree = ast.parse(open(r.__file__, encoding="utf-8").read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any("recall" in m for m in imported)
    assert not any("golden" in m and "fixtures" in m for m in imported)


def test_pack_round_trips_through_json():
    pack = EvidencePack(
        client_name="Acme Networks", generated_at="2026-07-24T18:00:00Z",
        records=[GoldenRecord(record_id="A1", lane="L2_entity_award")])
    loaded = EvidencePack.model_validate_json(pack.model_dump_json())
    assert loaded.records[0].record_id == "A1"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---- pre-cap aggregates (2026-07-28) --------------------------------------- #
def test_aggregates_are_taken_before_the_cap_cuts_the_segment():
    """The whole point: 40 core records enter, PACK_CORE_CAP keeps 26, and the
    aggregate must report 40. Reporting 26 under the word "category" is the
    mislabel this block exists to prevent."""
    strategy = _strategy()
    records = [
        GoldenRecord(record_id=f"C{i:03d}", lane="L2_entity_award",
                     agency=f"Agency{i % 7}", recipient=f"Prime{i % 5}",
                     obligated_dollars=1_000.0, fiscal_year="2025",
                     entity_hits=["AcmeFlow"])
        for i in range(40)]
    selected, selection = retrieval.select_pack(records, strategy)
    block = selection["pre_cap_aggregates"]
    core = {s["segment"]: s for s in block["segments"]}["L2_core"]

    assert len(selected) == retrieval.PACK_CORE_CAP        # the cap did cut
    assert core["count"] == 40                             # the aggregate did not
    assert core["dollars"] == 40_000.0
    assert len(core["by_agency"]) == 7
    assert len(core["by_prime"]) == 5
    assert block["categorized_total"] == 40


def test_forecast_segment_sums_a_published_floor_never_a_midpoint():
    """A forecast publishes a band. The lower bound is the only figure that
    can be summed without inventing one."""
    strategy = _strategy()
    records = [
        GoldenRecord(record_id="F1", lane="L4_forecast", agency="DHS",
                     estimated_value_range="$1M to $5M"),
        GoldenRecord(record_id="F2", lane="L4_forecast", agency="DHS",
                     estimated_value_range="$250K to $1M"),
    ]
    _, selection = retrieval.select_pack(records, strategy)
    seg = {s["segment"]: s
           for s in selection["pre_cap_aggregates"]["segments"]}["L4_forecast"]
    assert seg["money_basis"] == "floor"
    assert seg["dollars"] == 1_250_000.0          # 1M + 250K, the lower bounds
    assert seg["dollars"] != 6_000_000.0          # not the upper bounds
    assert seg["dollars"] != 3_625_000.0          # not the midpoints
    assert "FLOOR" in seg["money_label"]


def test_published_floor_reads_the_lowest_band_and_survives_junk():
    assert retrieval.published_floor("$1M to $5M") == 1_000_000.0
    assert retrieval.published_floor("$250K - $1M") == 250_000.0
    assert retrieval.published_floor("$1.5B and up") == 1_500_000_000.0
    assert retrieval.published_floor("to be determined") == 0.0
    assert retrieval.published_floor(None) == 0.0


def test_filter_statement_says_categorized_and_names_the_enrichment_limit():
    strategy = _strategy()
    records = [GoldenRecord(record_id="C1", lane="L2_entity_award",
                            agency="IRS", entity_hits=["AcmeFlow"])]
    _, selection = retrieval.select_pack(records, strategy)
    stmt = selection["pre_cap_aggregates"]["segments"][0]["filter_statement"]
    assert "CATEGORIZED set" in stmt
    assert "before any display cap" in stmt
    assert "NOT " in stmt and "detail-enriched" in stmt
    assert "SCREENED" in stmt and "double-counts" in stmt


# ---- term tiers and the shared guards (2026-07-28) -------------------------- #
def test_tier2_phrase_needs_a_tech_code():
    from agents.golden_press import term_tiers as tt
    text = "Enterprise network monitoring services for the data center"
    assert tt.screen_terms_tiered(text, ["network monitoring"],
                                  has_tech_code=False) == []
    got = tt.screen_terms_tiered(text, ["network monitoring"], has_tech_code=True)
    assert got and got[0]["tier"] == tt.TIER_PHRASE


def test_tier3_common_word_needs_a_tech_code_and_a_domain_anchor():
    from agents.golden_press import term_tiers as tt
    bare = "The system shall improve observability of the process."
    anchored = "The software shall improve observability of the server."
    assert tt.screen_terms_tiered(bare, ["observability"], has_tech_code=True) == []
    assert tt.screen_terms_tiered(anchored, ["observability"], has_tech_code=True)


def test_tier1_distinctive_name_still_stands_alone():
    from agents.golden_press import term_tiers as tt
    got = tt.screen_terms_tiered("Aternity licences", ["Aternity"],
                                 has_tech_code=False,
                                 distinctive_names=["Aternity"])
    assert got and got[0]["tier"] == tt.TIER_DISTINCTIVE


@pytest.mark.parametrize("text,term", [
    # every one of these is a real notice that fooled the old screen
    ("in-country claims administration, provider network management, and "
     "direct coordination with local medical providers", "network management"),
    ("coordination of protection and automation devices. Network Monitoring "
     "- continuously monitor switch status", "network monitoring"),
    ("integrated energy storage can reduce observability by enabling the "
     "reduction of acoustic and electromagnetic emissions", "observability"),
])
def test_negative_context_kills_the_real_world_polysemy(text, term):
    from agents.golden_press import term_tiers as tt
    assert tt.screen_terms_tiered(text, [term], has_tech_code=True) == []


def test_guards_are_shared_reference_data_not_client_gate_values():
    from agents.golden_press import term_tiers as tt
    g = tt.load_guards()
    assert "provider network" in g["negative"]
    assert "acoustic" in g["negative"]
    assert g["proximity"] == 400
    assert "unified observability" in g["vendor_phrases"]


def test_naics_list_covers_both_vintages():
    """The 2022 revision moved software publishing 511 -> 513 and rebuilt 517.
    The award lane reaches back to 2017, so both must qualify."""
    assert "513210".startswith(retrieval.TECH_NAICS_PREFIXES)   # 2022
    assert "511210".startswith(retrieval.TECH_NAICS_PREFIXES)   # 2017
    assert "517810".startswith(retrieval.TECH_NAICS_PREFIXES)   # NASA UNNO
    assert not "524114".startswith(retrieval.TECH_NAICS_PREFIXES)  # insurance
    assert not "335314".startswith(retrieval.TECH_NAICS_PREFIXES)  # relays


# ---- operator golden targets (2026-08-04) ---------------------------------- #
def test_target_scoring_measures_presence_and_core_seating():
    from agents.golden_press.recall import score_against_targets
    from agents.golden_press.records import GoldenRecord

    records = [
        GoldenRecord(
            record_id="70B04C18F00000184", lane="L2_entity_award",
            title="CBP order", agency="DHS",
            url=("https://www.usaspending.gov/award/"
                 "CONT_AWD_70B04C18F00000184_7014_NNG15SD01B_8000")),
        GoldenRecord(
            record_id="74529", lane="L4_forecast", title="APFS forecast",
            agency="DHS", url="https://apfs-cloud.dhs.gov/forecast/74529"),
    ]
    payload = {"marked_by": "operator", "marked_at": "2026-08-04",
               "targets": [
                   {"id": "CONT_AWD_70B04C18F00000184_7014_NNG15SD01B_8000",
                    "kind": "award_gid", "must_lead": True},
                   {"id": "APFS:74529", "kind": "apfs", "must_lead": False},
                   {"id": "CONT_AWD_MISSING_0000_X_0", "kind": "award_gid",
                    "must_lead": False},
               ]}
    seated = {"70B04C18F00000184"}
    score = score_against_targets(records, seated, payload)
    assert score["targets_total"] == 3
    assert score["present_count"] == 2
    assert score["missing"] == ["CONT_AWD_MISSING_0000_X_0"]
    assert score["lead_total"] == 1 and score["lead_count"] == 1

    unseated = score_against_targets(records, set(), payload)
    assert unseated["lead_count"] == 0
    assert unseated["lead_missing"] == [
        "CONT_AWD_70B04C18F00000184_7014_NNG15SD01B_8000"]


def test_target_file_loading_is_none_when_unmarked(tmp_path):
    from agents.golden_press.recall import load_golden_targets

    assert load_golden_targets(tmp_path, "riverbed") is None
    target_dir = tmp_path / "data" / "reference" / "golden_targets"
    target_dir.mkdir(parents=True)
    (target_dir / "riverbed.json").write_text('{"targets": []}',
                                              encoding="utf-8")
    assert load_golden_targets(tmp_path, "riverbed") is None  # empty = unmarked


def test_seeded_skeleton_is_markable_and_never_leads_by_default():
    from agents.golden_press.recall import seed_targets_skeleton

    html = ('<a href="https://www.usaspending.gov/award/'
            'CONT_AWD_P1_9700_B_9700">x</a>'
            '<a href="https://apfs-cloud.dhs.gov/forecast/11">y</a>')
    payload = seed_targets_skeleton(html, "riverbed")
    assert {row["id"] for row in payload["targets"]} == {
        "CONT_AWD_P1_9700_B_9700", "APFS:11"}
    assert all(row["must_lead"] is False for row in payload["targets"])
