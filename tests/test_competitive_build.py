"""Proactive Competitive Intelligence Build: the 16 workflow proofs
(operator order 2026-08-05). The engine must CREATE competitive
completeness before composition, not detect its absence afterward."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agents.golden_press import competitive as comp
from agents.golden_press.records import (
    EvidencePack,
    GoldenRecord,
    LaneQuery,
    LaneStatus,
)

REPO = Path(__file__).resolve().parents[1]
TODAY = date(2026, 8, 5)


class _Entity:
    def __init__(self, kind, name):
        self.kind, self.name = kind, name


class _Strategy:
    def __init__(self, client_name, entities=(), keywords=(), naics=()):
        self.client_name = client_name
        self.research_entities = list(entities)
        self.keywords = list(keywords)
        self.inferred_naics = list(naics)

    def model_copy(self, update):
        clone = _Strategy(self.client_name, self.research_entities,
                          self.keywords, self.inferred_naics)
        for key, value in update.items():
            setattr(clone, key, value)
        return clone


def _award(rid, recipient, *, hits, end="2027-06-30", agency="DoD",
           sub="Defense Information Systems Agency", amount=100_000.0,
           description=""):
    return GoldenRecord(
        record_id=rid, lane="L2_entity_award", title=f"{recipient} order",
        agency=agency, sub_agency=sub, recipient=recipient,
        obligated_dollars=amount, period_end=end,
        description=description or f"{recipient} order",
        url=f"https://www.usaspending.gov/award/CONT_AWD_{rid}",
        entity_hits=list(hits))


def _pack_for(strategy, records, *, queries=None):
    entities: dict[str, list[str]] = {}
    for entity in strategy.research_entities:
        entities.setdefault(entity.kind, []).append(entity.name)
    queries = queries if queries is not None else [
        LaneQuery(lane="L2_entity_award", method="award_search",
                  endpoint="usaspending/search",
                  body={"filters": {"keywords": [e.name],
                                    "time_period": [{"start_date": "2019-01-01",
                                                     "end_date": "2026-08-05"}]},
                        "page": 1},
                  result_count=5, kept_after_screen=2)
        for e in strategy.research_entities if e.kind == "competitor"]
    return EvidencePack(
        client_name=strategy.client_name,
        generated_at="2026-08-05T12:00:00+00:00",
        records=list(records),
        queries=queries,
        lanes=[LaneStatus(lane="L1_notice", status="live"),
               LaneStatus(lane="L4_forecast", status="live")],
        research={"entities": entities},
    )


def _fake_builder(rounds):
    """A build_pack that serves scripted packs and records each strategy."""
    calls = []

    def build(strategy, **kwargs):
        calls.append(strategy)
        script = rounds[min(len(calls), len(rounds)) - 1]
        if isinstance(script, Exception):
            raise script
        return _pack_for(strategy, script(strategy)
                         if callable(script) else script)
    build.calls = calls
    return build


def _run(strategy, rounds, **kw):
    return comp.competitive_build(
        strategy, sweep=kw.pop("sweep", None), profile=kw.pop("profile", None),
        scope=None, root=REPO, today=TODAY,
        build_pack=_fake_builder(rounds) if not callable(rounds) else rounds,
        **kw)


# 1 · no supplied competitors: discovery still starts ------------------------ #
def test_no_supplied_competitors_triggers_automatic_discovery():
    sweep = {"results": {
        "usaspending.gov": [{
            "naics_code": "334118",
            "summary": {"top_incumbents": ["RivalWorks", "Thinklogical"]},
            "awards": [{"recipient": "RivalWorks", "awarding_agency": "DISA"}],
        }],
        "sam.gov": [{
            "source_id": "N-BRAND-1",
            "title": "Brand name or equal: OMNIVIEW matrix switch",
            "raw_payload": {"type": "Solicitation",
                            "description_snippet":
                                "brand name or equal OMNIVIEW KVM"},
        }],
    }}
    strategy = _Strategy("Thinklogical")
    seeds = comp.discover_seed_candidates(strategy, None, sweep)
    names = {s["name"] for s in seeds}
    assert "RivalWorks" in names          # top-incumbent path
    assert "OMNIVIEW" in names            # brand-name notice path
    assert all(s["origin"] in ("evidence_discovered", "account_incumbent")
               for s in seeds)
    assert "Thinklogical" not in names    # the client is never its own rival


# 2 · a supplied roster is expanded, never treated as complete --------------- #
def test_supplied_roster_expands_with_aliases_brands_and_registry():
    cands = [{"name": n, "origin": "operator_supplied", "confidence": "high",
              "evidence": []}
             for n in ("IHSE", "Guntermann & Drunck", "G&D", "Adder",
                       "Adder Technology", "Black Box", "Black Box Emerald",
                       "Vertiv", "Avocent", "Vertiv Avocent")]
    frame = comp.build_identity_graph(cands, root=REPO,
                                      client_name="Thinklogical")
    by_name = {c["canonical"]: c for c in frame["competitors"]}
    assert set(by_name) == {"IHSE", "Guntermann & Drunck", "Adder",
                            "Black Box", "Vertiv"}
    assert "G&D" in by_name["Guntermann & Drunck"]["aliases_ambiguous"]
    assert "Emerald" in [b["brand"]
                         for b in by_name["Black Box"]["product_brands"]]
    assert "Avocent" in [b["brand"]
                         for b in by_name["Vertiv"]["product_brands"]]
    # registry expansion reaches beyond anything supplied
    assert "Emerson Network Power" in (
        by_name["Vertiv"]["aliases_distinctive"]
        + by_name["Vertiv"]["aliases_ambiguous"])


# 3 + 7 · a competitor found mid-research joins the remaining lanes ---------- #
def test_award_discovered_competitor_is_searched_in_the_next_round():
    # The category gate decides who earns a search: a recipient whose
    # witness award carries market language joins the roster; a recipient
    # on a generic match (the General Electric class, witnessed on the
    # 2026-08-05 Thinklogical proof) stays an unsearched adjacency.
    strategy = _Strategy("Thinklogical",
                         [_Entity("competitor", "Black Box")],
                         keywords=[_kw("KVM matrix switch")])
    profile = {"capability_terms": {"core": ["KVM matrix switch"]}}

    def round_one(strat):
        return [
            _award("BB1", "OpticsRival Inc", hits=["Black Box"],
                   description="BLACK BOX KVM MATRIX SWITCH refresh"),
            _award("BB2", "General Electric Company", hits=["Black Box"],
                   description="aircraft black box flight recorder spares"),
        ]

    build = _fake_builder([round_one, round_one, round_one, round_one])
    result = comp.competitive_build(
        strategy, sweep=None, profile=profile, scope=None, root=REPO,
        today=TODAY, build_pack=build)
    searched_by_round = [
        {e.name for e in s.research_entities if e.kind == "competitor"}
        for s in build.calls]
    assert "OpticsRival Inc" not in searched_by_round[0]
    assert any("OpticsRival Inc" in names
               for names in searched_by_round[1:]), (
        "the recipient of a category-witnessed competitor award must be "
        "searched in the following round")
    assert all("General Electric Company" not in names
               for names in searched_by_round), (
        "a generic-match recipient never earns a search")
    rounds = result["discovery"]["rounds"]
    assert rounds[0]["new_material"] == ["OpticsRival Inc"]


# 4 · a product brand resolves to its corporate identity --------------------- #
def test_product_brand_leads_to_corporate_identity():
    cands = [
        {"name": "Black Box", "origin": "operator_supplied",
         "confidence": "high", "evidence": []},
        {"name": "Black Box Emerald", "origin": "evidence_discovered",
         "confidence": "medium", "evidence": []},
    ]
    frame = comp.build_identity_graph(cands, root=REPO, client_name="X")
    assert [c["canonical"] for c in frame["competitors"]] == ["Black Box"]
    brands = frame["competitors"][0]["product_brands"]
    assert brands and brands[0]["brand"] == "Emerald"


# 5 · a corporate acquisition expands the aliases ---------------------------- #
def test_acquisition_registry_expands_aliases_with_provenance():
    cands = [{"name": "Vertiv", "origin": "operator_supplied",
              "confidence": "high", "evidence": []}]
    frame = comp.build_identity_graph(cands, root=REPO, client_name="X")
    vertiv = frame["competitors"][0]
    aliases = set(vertiv["aliases_distinctive"] + vertiv["aliases_ambiguous"])
    assert {"Avocent", "Emerson Network Power"} <= aliases
    assert any(r["relation"] == "acquired_brand"
               for r in frame["registry_expansions"])


# 6 · an ambiguous acronym cannot establish a match by itself ---------------- #
def test_ambiguous_acronym_requires_anchors_and_never_searches_alone():
    from agents.golden_press.sam_lanes import reject_entity_hit

    cands = [{"name": n, "origin": "operator_supplied", "confidence": "high",
              "evidence": []} for n in ("Guntermann & Drunck", "G&D")]
    frame = comp.build_identity_graph(cands, root=REPO, client_name="X")
    gd = frame["competitors"][0]
    assert "G&D" in gd["aliases_ambiguous"]
    assert "Guntermann & Drunck" in gd["required_anchors_for_ambiguous"]
    assert "G&D" not in gd["required_anchors_for_ambiguous"]
    strategy = comp._strategy_with_roster(
        _Strategy("X"), [gd],
        type("E", (), {"model_validate": staticmethod(
            lambda d: _Entity(d["kind"], d["name"]))}))
    searched = {e.name for e in strategy.research_entities}
    assert "G&D" not in searched, "ambiguous aliases anchor, never query"
    # and at match time the shared guard still demands the vendor
    assert reject_entity_hit({"distinctive": False, "kind": "competitor"},
                             vendor_present=False, product_hits=set(),
                             text="G&D something") is not None


# 8 · two clean discovery rounds produce saturation -------------------------- #
def test_two_clean_rounds_saturate_with_receipts():
    strategy = _Strategy("X", [_Entity("competitor", "Vertiv")])
    result = _run(strategy, [[], [], []])
    assert result["discovery"]["saturated"] is True
    assert len(result["discovery"]["rounds"]) == 2
    assert result["completeness"]["points"]["iterated_to_saturation"]


# 9 · current and expired competitor records seat differently ---------------- #
def test_current_and_expired_records_are_seated_separately():
    strategy = _Strategy("X", [_Entity("competitor", "Vertiv")])
    records = [
        _award("CUR1", "Vertiv", hits=["Vertiv"], end="2027-01-01"),
        _award("OLD1", "Vertiv", hits=["Vertiv"], end="2025-01-01"),
    ]
    pack = _pack_for(strategy, records)
    frame = comp.build_identity_graph(
        [{"name": "Vertiv", "origin": "operator_supplied",
          "confidence": "high", "evidence": []}], root=REPO, client_name="X")
    accounts = comp.account_picture(pack, frame, today=TODAY, client_name="X")
    node = accounts["accounts"][0]
    assert [r["record_id"] for r in node["competitor_current"]] == ["CUR1"]
    assert [r["record_id"] for r in node["competitor_historical"]] == ["OLD1"]
    review = comp.adjudicate(pack, frame, [], today=TODAY, client_name="X")
    states = {r["record_id"]: r["current_vs_historical"]
              for r in review["rows"] if r["record_id"]}
    assert states == {"CUR1": "current", "OLD1": "historical"}


# 10 · channel holders are never product competitors ------------------------- #
def test_channel_holders_are_not_mislabeled_as_competitors():
    strategy = _Strategy("X", [_Entity("competitor", "Vertiv"),
                               _Entity("reseller", "Carahsoft")])
    records = [_award("CH1", "CARAHSOFT TECHNOLOGY", hits=["Carahsoft"])]
    pack = _pack_for(strategy, records)
    frame = comp.build_identity_graph([], root=REPO, client_name="X")
    review = comp.adjudicate(pack, frame, [], today=TODAY, client_name="X")
    row = next(r for r in review["rows"] if r["record_id"] == "CH1")
    assert row["classification"] == "reseller_or_channel_evidence"
    accounts = comp.account_picture(pack, frame, today=TODAY, client_name="X")
    node = accounts["accounts"][0]
    assert node["channel_current"] and not node["competitor_current"]
    assert node["recommended_motion"] == "approach the paper holder"


# 11 · a source failure preserves partial evidence and continues ------------- #
def test_source_failure_keeps_partial_evidence_and_names_the_gap():
    strategy = _Strategy("X", [_Entity("competitor", "Vertiv")])

    def round_one(strat):
        return [_award("V1", "RivalCo", hits=["Vertiv"])]

    result = _run(strategy, [round_one, RuntimeError("upstream 503")])
    assert result["pack"] is not None
    assert any(r["record_id"] == "V1"
               for r in result["review"]["rows"] if r["record_id"])
    failed = [c for c in result["search_ledger"]["cells"]
              if c.get("status") == "failed"]
    assert failed and "upstream 503" in str(failed[0].get("errors"))


# 12 · material amounts retain source links ---------------------------------- #
def test_material_amounts_keep_their_source_links():
    strategy = _Strategy("X", [_Entity("competitor", "Vertiv")])
    steady = lambda s: [_award("V9", "Vertiv", hits=["Vertiv"],  # noqa: E731
                               amount=2_400_000.0)]
    result = _run(strategy, [steady, steady, steady])
    row = next(r for r in result["review"]["rows"] if r["record_id"] == "V9")
    assert row["amount"] == 2_400_000.0
    assert row["source_link"].startswith("https://www.usaspending.gov/award/")
    node = result["accounts"]["accounts"][0]
    lane = node["competitor_current"][0]
    assert lane["amount"] == 2_400_000.0 and lane["url"]


# 13 · a fully researched zero is complete_zero ------------------------------ #
def _kw(term):
    return type("K", (), {"category": type("C", (), {"value": "capability"})(),
                          "term": term})()


def test_fully_researched_zero_scores_complete_zero():
    # An EARNED ten: the world defines its market and discovers along two
    # independent paths; the engine may not award unearned points.
    strategy = _Strategy("X", [_Entity("competitor", "Vertiv")],
                         keywords=[_kw("kvm matrix switch")])
    profile = {"named_competitors_and_incumbents": ["Vertiv"],
               "capability_terms": {"core": ["kvm matrix switch"]}}
    result = _run(strategy, [[], [], []], profile=profile)
    completeness = result["completeness"]
    assert completeness["score"] == 10, completeness["points"]
    assert completeness["status"] == "complete_zero"
    assert completeness["current_overlap_rows"] == 0


# 14 · findings produce account-specific actions ----------------------------- #
def test_findings_carry_account_specific_actions():
    strategy = _Strategy("X", [_Entity("competitor", "Vertiv")])
    records = [
        _award("D1", "Vertiv", hits=["Vertiv"], sub="DISA"),
        _award("D2", "X", hits=["X"], sub="DISA"),
        _award("D3", "Vertiv", hits=["Vertiv"], sub="NAVSEA",
               end="2025-01-01"),
        GoldenRecord(record_id="F1", lane="L4_forecast", title="KVM refresh",
                     agency="DoD", sub_agency="NAVSEA",
                     url="https://apfs-cloud.dhs.gov/forecast/1"),
    ]
    pack = _pack_for(strategy, records)
    frame = comp.build_identity_graph(
        [{"name": "Vertiv", "origin": "operator_supplied",
          "confidence": "high", "evidence": []}], root=REPO, client_name="X")
    accounts = comp.account_picture(pack, frame, today=TODAY, client_name="X")
    by_buyer = {a["buyer"]: a for a in accounts["accounts"]}
    assert by_buyer["DISA"]["recommended_motion"] == \
        "defend the installed position"
    assert by_buyer["NAVSEA"]["recommended_motion"] == \
        "shape the forecast requirement"
    assert all(a["why_this_matters"] for a in accounts["accounts"])


# 15 · deterministic replay reproduces every sidecar ------------------------- #
def test_replay_reproduces_roster_ledger_adjudication_and_score():
    def make():
        strategy = _Strategy("X", [_Entity("competitor", "Vertiv")])
        steady = lambda s: [_award("V1", "RivalCo", hits=["Vertiv"])]  # noqa: E731
        return _run(strategy, [steady, steady, steady, steady])
    one, two = make(), make()
    for key in ("frame", "discovery", "search_ledger", "review",
                "accounts", "completeness", "market_definition"):
        assert json.dumps(one[key], sort_keys=True) == \
            json.dumps(two[key], sort_keys=True), f"{key} must replay"


# 16 · existing wrong-domain and negative-context protections remain --------- #
def test_wrong_domain_and_negative_context_protections_survive():
    from agents.golden_press.render import (
        WRONG_DOMAIN_PREFIX,
        composer_demotions,
    )
    from agents.golden_press.sam_lanes import reject_entity_hit

    assert reject_entity_hit(
        {"distinctive": True, "kind": "competitor"},
        vendor_present=False, product_hits=set(),
        text="8504681597!PROBE,GASKON SENTRA",
        negative_phrases=("probe,gaskon",)) == \
        "witnessed_negative_context:probe,gaskon"
    strategy = _Strategy("Varonis", [_Entity("competitor", "Sentra")])
    pack = _pack_for(strategy, [_award("S1", "SZY HOLDINGS",
                                       hits=["Sentra"])])
    prose = {"why_S1": f"{WRONG_DOMAIN_PREFIX} a probe part."}
    assert set(composer_demotions(pack, prose)) == {"S1"}
