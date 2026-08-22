"""Derived pack rollups: prime_rollup and competitor_landscape.

The fixture is deliberately small and the numbers are hand-checkable, because
the thing these tests exist to protect is arithmetic honesty: a sum over a
capped set must never present itself as a market total.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press import rollups as ro  # noqa: E402
from agents.golden_press.records import EvidencePack, GoldenRecord  # noqa: E402


def _rec(record_id, *, recipient=None, dollars=None, agency="Department of X",
         sub_agency=None, naics="541519", psc="DA10", lane="L2_entity_award",
         hits=(), vehicle=None):
    return GoldenRecord(
        record_id=record_id, lane=lane, title=f"award {record_id}",
        agency=agency, sub_agency=sub_agency, recipient=recipient,
        obligated_dollars=dollars, naics=naics, psc=psc,
        entity_hits=list(hits), vehicle=vehicle,
        url=f"https://www.usaspending.gov/award/{record_id}")


def _pack(records, *, screened=None, packed=None, entities=None, aliases=None):
    return EvidencePack(
        client_name="Acme",
        generated_at="2026-07-28T00:00:00Z",
        records=records,
        research={"entities": entities if entities is not None else {
            "product": ["AcmeFlow"], "reseller": ["Reseller Co"],
            "competitor": ["RivalCorp", "GhostRival"]}},
        client_entity_aliases=list(aliases or []),
        selection=({"screened_relevant": screened, "packed": packed,
                    "rule": "test rule"} if screened is not None else None))


# A capped pack: 4 records held, 100 screened. The whole point.
def _capped_pack():
    return _pack(
        [_rec("A1", recipient="Big Prime LLC", dollars=1_000_000.0,
              agency="Department of Health", hits=["AcmeFlow"], psc="DA10"),
         _rec("A2", recipient="Big Prime LLC", dollars=500_000.0,
              agency="Department of Health", hits=["AcmeFlow"], psc="DA10"),
         _rec("A3", recipient="Reseller Co", dollars=250_000.0,
              agency="Department of Transport", hits=["RivalCorp"], psc="7A21"),
         _rec("A4", lane="L4_forecast", naics="541512", psc=None,
              agency="Department of Health", hits=[])],
        screened={"L2_core": 60, "L2_competitor": 30, "L4_forecast": 10},
        packed={"L2_core": 2, "L2_competitor": 1, "L4_forecast": 1})


# ---- coverage: the qualifier that must ride every number ------------------- #
def test_capped_pack_is_never_a_market_total():
    cov = ro.coverage(_capped_pack())
    assert cov["basis"] == "capped display set"
    assert cov["is_market_total"] is False
    assert cov["packed_total"] == 4
    assert cov["screened_relevant_total"] == 100
    assert "not over the market" in cov["qualifier"]
    assert "sweep" in cov["to_make_it_a_market_total"]


def test_uncapped_pack_reports_itself_as_a_market_total():
    pack = _pack([_rec("A1", recipient="P", dollars=10.0)],
                 screened={"L2_core": 1}, packed={"L2_core": 1})
    cov = ro.coverage(pack)
    assert cov["basis"] == "full qualifying set"
    assert cov["is_market_total"] is True
    assert cov["to_make_it_a_market_total"] is None


def test_every_rollup_carries_the_coverage_block():
    built = ro.build_rollups(_capped_pack())
    for name in ("prime_rollup", "competitor_landscape"):
        assert built[name]["coverage"]["is_market_total"] is False


# ---- prime_rollup ---------------------------------------------------------- #
def test_prime_rollup_sums_and_ranks_by_obligated():
    pri = ro.prime_rollup(_capped_pack())
    assert [r["prime"] for r in pri["rows"]] == ["Big Prime LLC", "Reseller Co"]
    assert pri["rows"][0]["obligated"] == 1_500_000.0
    assert pri["rows"][0]["records"] == 2
    assert pri["obligated_across_primes"] == 1_750_000.0
    assert round(pri["rows"][0]["share_of_awarded"], 4) == round(1.5 / 1.75, 4)


def test_named_reseller_is_recognised_from_the_research_entities():
    pri = ro.prime_rollup(_capped_pack())
    by = {r["prime"]: r for r in pri["rows"]}
    assert by["Reseller Co"]["relationship"] == "named_reseller"
    assert by["Big Prime LLC"]["relationship"] == "independent"


def test_an_alias_turns_a_third_party_win_into_the_clients_own_revenue():
    """The Red Hat case: DLT Solutions is TD SYNNEX Public Sector and carries
    ~40% of obligated dollars. Award data never says so; the alias list is
    where a human says it once."""
    plain = ro.prime_rollup(_capped_pack())
    assert {r["prime"]: r for r in plain["rows"]}[
        "Big Prime LLC"]["relationship"] == "independent"

    aliased = ro.prime_rollup(_capped_pack(), aliases=["Acme", "Big Prime"])
    row = {r["prime"]: r for r in aliased["rows"]}["Big Prime LLC"]
    assert row["relationship"] == "client_entity"
    assert aliased["by_relationship"]["client_entity"]["obligated"] == 1_500_000.0


def test_alias_matching_survives_legal_form():
    assert ro.normalize_entity("DLT SOLUTIONS, LLC") == "dlt"
    assert ro.normalize_entity("DLT Solutions") == "dlt"
    assert (ro.normalize_entity("TD SYNNEX Public Sector")
            == ro.normalize_entity("TD Synnex"))


def test_alias_list_is_editable_and_defaults_to_the_client_name():
    pack = _capped_pack()
    assert pack.client_entity_aliases == []          # editable, empty by default
    assert ro.default_client_aliases(pack) == ["Acme"]
    pack.client_entity_aliases = ["Acme", "Acme Federal"]
    assert ro.prime_rollup(pack)["client_entity_aliases"] == [
        "Acme", "Acme Federal"]


# ---- the two tables share zero rows ---------------------------------------- #
def _dual_role_pack():
    """RivalCorp is a named competitor AND wins prime awards."""
    return _pack(
        [_rec("D1", recipient="RivalCorp", dollars=900_000.0,
              agency="Department of Health", hits=["RivalCorp"]),
         _rec("D2", recipient="Big Prime LLC", dollars=100_000.0,
              agency="Department of Health", hits=["AcmeFlow"])],
        screened={"L2_core": 50}, packed={"L2_core": 2})


def test_an_entity_that_is_both_lands_in_prime_rollup_only():
    pack = _dual_role_pack()
    pri = ro.prime_rollup(pack)
    comp = ro.competitor_landscape(pack)
    row = {r["prime"]: r for r in pri["rows"]}["RivalCorp"]
    assert row["relationship"] == "competitor"
    assert "excluded from competitor_landscape" in row["dual_role"]
    assert "RivalCorp" not in {r["competitor"] for r in comp["rows"]}
    assert comp["excluded_as_primes"] == ["RivalCorp"]


def test_the_two_tables_never_share_a_row():
    pack = _dual_role_pack()
    primes = {ro.normalize_entity(r["prime"])
              for r in ro.prime_rollup(pack)["rows"]}
    rivals = {ro.normalize_entity(r["competitor"])
              for r in ro.competitor_landscape(pack)["rows"]}
    assert primes & rivals == set()


# ---- competitor_landscape -------------------------------------------------- #
def test_overlap_agencies_is_the_intersection_with_client_corridors():
    """A rival in an agency the client never touches is market colour. A
    rival inside a client corridor is a displacement fight."""
    pack = _pack(
        [_rec("C1", recipient="P", dollars=100.0, agency="Shared Agency",
              hits=["AcmeFlow"]),                        # client corridor
         _rec("C2", recipient="Q", dollars=200.0, agency="Shared Agency",
              hits=["RivalCorp"]),                       # rival, same agency
         _rec("C3", recipient="Q", dollars=300.0, agency="Rival Only Agency",
              hits=["RivalCorp"])],                      # rival, elsewhere
        screened={"L2_core": 9}, packed={"L2_core": 3})
    comp = ro.competitor_landscape(pack)
    row = {r["competitor"]: r for r in comp["rows"]}["RivalCorp"]
    assert row["records"] == 2
    assert row["obligated"] == 500.0
    assert row["agencies"] == ["Rival Only Agency", "Shared Agency"]
    assert row["overlap_agencies"] == ["Shared Agency"]
    assert row["contested"] is True


def test_a_rival_with_no_records_still_gets_a_row_showing_zero():
    """Reporting the zero is the point. An absent row reads as 'not checked'."""
    comp = ro.competitor_landscape(_capped_pack())
    ghost = {r["competitor"]: r for r in comp["rows"]}["GhostRival"]
    assert ghost["records"] == 0
    assert ghost["obligated"] == 0
    assert ghost["overlap_agencies"] == []
    assert ghost["contested"] is False


def test_contested_rivals_sort_above_uncontested():
    comp = ro.competitor_landscape(_capped_pack())
    counts = [r["overlap_count"] for r in comp["rows"]]
    assert counts == sorted(counts, reverse=True)


# ---- rendering ------------------------------------------------------------- #
def test_tables_never_print_a_capped_sum_without_the_qualifier():
    text = ro.format_tables(ro.build_rollups(_capped_pack()), client="Acme")
    assert "BASIS: CAPPED DISPLAY SET" in text
    assert "market total: False" in text
    assert "TO FIX:" in text
    for heading in ("PRIME ROLLUP", "COMPETITOR LANDSCAPE"):
        assert heading in text


def test_rollups_do_not_mutate_the_pack():
    pack = _capped_pack()
    before = pack.model_dump_json()
    ro.build_rollups(pack)
    assert pack.model_dump_json() == before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---- pre-cap aggregates and category_spend --------------------------------- #
def _pack_with_aggregates():
    pack = _capped_pack()
    pack.pre_cap_aggregates = {
        "basis": "categorized set, pre-cap",
        "categorized_total": 100,
        "segments": [
            {"segment": "L2_core", "count": 60, "money_basis": "obligated",
             "money_label": "obligated dollars as reported",
             "dollars": 90_000_000.0, "records_with_dollars": 58,
             "records_without_dollars": 2,
             "by_fiscal_year": {"2025": 60},
             "by_agency": {"Department of Health": {"n": 40, "dollars": 8e7}},
             "by_prime": {"Big Prime LLC": {"n": 30, "dollars": 7e7}},
             "by_entity_hit": {"AcmeFlow": 60},
             "filter_statement": "CATEGORIZED set for L2_core: 60 ... "
                                 "search-row fields, NOT detail-enriched ..."},
            {"segment": "L4_forecast", "count": 10, "money_basis": "floor",
             "money_label": "summed published lower bounds (a FLOOR, not a total)",
             "dollars": 5_000_000.0, "records_with_dollars": 9,
             "records_without_dollars": 1,
             "by_fiscal_year": {"2026": 10}, "by_agency": {}, "by_prime": {},
             "by_entity_hit": {}, "filter_statement": "..."},
        ]}
    return pack


def test_category_spend_refuses_to_compute_without_the_precap_block():
    """The refusal is the feature. A number here would be a 6% sample under
    the word 'category'."""
    cat = ro.category_spend(_capped_pack())
    assert cat["available"] is False
    assert cat["rows"] == []
    assert "cannot be recovered offline" in cat["reason"]
    assert "Press this client again" in cat["how_to_get_it"]


def test_category_spend_reads_the_precap_block_when_present():
    cat = ro.category_spend(_pack_with_aggregates())
    assert cat["available"] is True
    assert cat["categorized_total"] == 100
    core = {r["segment"]: r for r in cat["rows"]}["L2_core"]
    assert core["records"] == 60          # the categorized set, not the 4 packed
    assert core["dollars"] == 90_000_000.0
    assert core["agencies"] == 1 and core["primes"] == 1


def test_award_dollars_and_forecast_floors_are_never_added_together():
    """One is a reported obligation, the other a published lower bound."""
    cat = ro.category_spend(_pack_with_aggregates())
    assert cat["obligated_across_awards"] == 90_000_000.0
    assert cat["forecast_floor_across_forecasts"] == 5_000_000.0
    assert "total" not in cat            # no combined figure exists at all


def test_every_segment_states_its_filter_and_its_enrichment_limit():
    cat = ro.category_spend(_pack_with_aggregates())
    core = {r["segment"]: r for r in cat["rows"]}["L2_core"]
    assert "CATEGORIZED" in core["filter_statement"]
    assert "NOT detail-enriched" in core["filter_statement"]


def test_unavailable_category_spend_still_prints_without_a_number():
    text = ro.format_tables(ro.build_rollups(_capped_pack()), client="Acme")
    assert "CATEGORY SPEND" in text
    assert "UNAVAILABLE" in text
    assert "HOW TO GET IT" in text
