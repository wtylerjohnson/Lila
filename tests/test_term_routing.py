"""Every intake term lands in exactly one bucket, with a stated reason."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.decisions.term_routing import route_frame, route_term  # noqa: E402

ENTITIES = {"product": ["SteelHead", "Aternity", "Riverbed AppResponse"],
            "competitor": ["SolarWinds"], "reseller": ["Carahsoft"]}
NAMES = [n for v in ENTITIES.values() for n in v] + ["Riverbed"]


def _route(term):
    return route_term(term, entity_names=NAMES)


# ---- the defect that forced this ------------------------------------------- #
@pytest.mark.parametrize("brand", ["SteelHead", "Aternity"])
def test_a_brand_name_routes_to_entity_not_capability(brand):
    """THE GUARD FAILURE. Intake filed these as capability. Capability terms
    are exempt from the entity guard by design, so a brand name there walks
    straight past it: "SteelHead" matched a sole-source to Innovasea because
    steelhead is a trout."""
    assert _route(brand)[0] == "entity"


def test_an_uncatalogued_product_name_still_routes_to_entity():
    """SteelCentral is a real Riverbed product that intake put ONLY in the
    capability list, so exact matching cannot save it. Internal
    capitalisation is what gives it away."""
    bucket, reason = _route("SteelCentral")
    assert bucket == "entity"
    assert "capitalisation" in reason


# ---- the four rejection classes -------------------------------------------- #
def test_sentence_shaped_terms_are_rejected():
    bucket, reason = _route("Riverbed observability maintenance renewal")
    assert bucket == "reject" and "sentence-shaped" in reason


def test_entity_pairs_are_rejected_and_name_both():
    bucket, reason = _route("Carahsoft Riverbed")
    assert bucket == "reject"
    assert "carahsoft" in reason and "riverbed" in reason


def test_search_breaking_punctuation_is_rejected():
    bucket, reason = _route("NPM+")
    assert bucket == "reject" and "punctuation" in reason


def test_unsplit_and_compounds_are_rejected():
    bucket, reason = _route("WAN optimization and application acceleration")
    assert bucket == "reject" and "'and'" in reason


def test_vendor_category_phrases_are_rejected():
    bucket, reason = _route("unified observability")
    assert bucket == "reject" and "zero hits" in reason


# ---- qualifier vs capability ----------------------------------------------- #
def test_a_federal_attribute_qualifies_and_never_generates():
    """"FedRAMP High" as a search term returns every cloud notice in the
    government. It is context, not capability."""
    bucket, reason = _route("FedRAMP High")
    assert bucket == "qualifier" and "never generates" in reason


def test_procurement_language_survives_as_capability():
    assert _route("network performance monitoring")[0] == "capability"
    assert _route("container orchestration")[0] == "capability"


# ---- ordering is load-bearing ---------------------------------------------- #
def test_a_real_product_beats_the_entity_pair_rule():
    """"Riverbed AppResponse" contains two proper nouns and IS a product.
    Exact entity match is checked first for exactly this reason."""
    assert _route("Riverbed AppResponse")[0] == "entity"


# ---- the frame ------------------------------------------------------------- #
def test_every_term_lands_in_exactly_one_bucket():
    terms = ["network performance monitoring", "NPM+", "SteelHead",
             "FedRAMP High", "Carahsoft Riverbed"]
    out = route_frame(terms, ENTITIES, vendor="Riverbed")
    placed = sum(len(v) for v in out["buckets"].values())
    assert placed >= len(terms)
    for t in terms:
        hits = [b for b, v in out["buckets"].items() if t in v]
        assert len(hits) == 1, f"{t} landed in {hits}"


def test_every_decision_carries_a_reason():
    out = route_frame(["unified observability", "NPM+"], ENTITIES,
                      vendor="Riverbed")
    assert all(d["reason"] for d in out["decisions"])
    assert all(d["was"] for d in out["decisions"])


def test_the_client_name_counts_for_pair_detection():
    """Without the vendor name only "Carahsoft" is a listed entity, the pair
    check sees one name, and the pair survives into capability."""
    without = route_frame(["Carahsoft Riverbed"], ENTITIES)
    with_vendor = route_frame(["Carahsoft Riverbed"], ENTITIES, vendor="Riverbed")
    assert without["buckets"]["capability"] == ["Carahsoft Riverbed"]
    assert with_vendor["buckets"]["reject"] == ["Carahsoft Riverbed"]


def test_an_empty_term_is_rejected_not_crashed():
    assert route_term("")[0] == "reject"
    assert route_term(None)[0] == "reject"


# ---- the three classes the router used to pass through --------------------- #
@pytest.mark.parametrize("term", ["Department of the Navy",
                                  "Department of Veterans Affairs",
                                  "Office of the Chief Information Officer"])
def test_agency_names_are_rejected(term):
    """NetApp's cold intake emitted agency names as KEYWORDS. They match
    thousands of notices and carry no capability signal."""
    bucket, reason = route_term(term, entity_names=NAMES)
    assert bucket == "reject" and "agency or organization" in reason


@pytest.mark.parametrize("term", ["334112", "518210", "541512"])
def test_bare_numeric_codes_are_rejected(term):
    """A NAICS is a FILTER. As a search term it means nothing."""
    bucket, reason = route_term(term, entity_names=NAMES)
    assert bucket == "reject" and "boundary" in reason


def test_bare_acronyms_are_rejected(term="DISA"):
    bucket, reason = route_term(term, entity_names=NAMES)
    assert bucket == "reject" and "acronym" in reason


def test_a_real_product_acronym_still_routes_to_entity():
    """ONTAP is a product. Exact entity match is checked before the acronym
    rule for exactly this reason."""
    assert route_term("ONTAP", entity_names=["ONTAP", "NetApp"])[0] == "entity"
