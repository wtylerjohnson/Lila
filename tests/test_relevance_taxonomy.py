"""Capability taxonomy schema + the Insignary taxonomy artifact."""
from tools.relevance.taxonomy import (
    CapabilityTaxonomy,
    KillRule,
    TaxonomyTerm,
    derived_taxonomy,
    load_taxonomy,
)


def test_insignary_taxonomy_loads_with_briefed_tiers():
    tax = load_taxonomy("Insignary")
    assert tax is not None and tax.version == 1
    core_terms = {t.term.lower() for t in tax.core}
    for required in ("binary sca", "sbom", "software composition analysis",
                     "delivered software assurance"):
        assert required in core_terms
    assert any("reachability" in t.term.lower() for t in tax.core)
    adjacent = {t.term.lower() for t in tax.adjacent}
    assert "devsecops" in adjacent
    assert "secure software supply chain" in adjacent
    kills = {k.term.lower() for k in tax.exclude}
    assert "hardware bill of materials" in kills
    assert any("biological" in k or "cardiac" in k for k in kills)


def test_acronym_terms_require_expansion_context():
    tax = load_taxonomy("Insignary")
    sca = next(t for t in tax.core if t.term == "SCA")
    assert sca.mode == "acronym"
    assert "software" in [c.lower() for c in sca.expansion_context]
    assert "composition" in [c.lower() for c in sca.expansion_context]


def test_schema_rejects_versionless_and_accepts_minimal():
    import pytest
    with pytest.raises(Exception):
        CapabilityTaxonomy(client_name="X", version=0, updated="2026-07-17")
    minimal = CapabilityTaxonomy(client_name="X", version=1,
                                 updated="2026-07-17",
                                 core=[TaxonomyTerm(term="widget")],
                                 exclude=[KillRule(term="anti-widget")])
    assert minimal.core[0].mode == "stemmed"          # default
    assert minimal.exclude[0].scope == "span"          # default


def test_derived_taxonomy_marks_its_provenance(monkeypatch):
    from tools.capability import CapabilityTerms, ClientProfile
    import tools.capability as cap
    monkeypatch.setattr(cap, "load_profile", lambda _c: ClientProfile(
        client_name="Testco",
        capability_terms=CapabilityTerms(core=["video wall"],
                                         adjacent=["av integration"],
                                         excluded=[]),
        named_competitors_and_incumbents=[], mission_components=[],
        naics_boundary=["334310"]))
    tax = derived_taxonomy("Testco")
    assert tax is not None
    assert tax.core[0].term == "video wall"
    assert "DERIVED" in tax.core[0].note
    assert tax.exclude == [] and tax.code_universe is None
