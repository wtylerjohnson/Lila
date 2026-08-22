"""Relevance engine doctrine tests: keywords primary, codes as boundary.

The briefed cases: acronym collision (SCA without software context scores
zero), EXCLUDE kill-rules firing, off-code strong hits passing flagged,
adjacent-alone never sufficient, verbatim span capture.
"""
import pytest

from tools.relevance.engine import (
    OFF_CODE_STRONG_THRESHOLD,
    RELEVANCE_THRESHOLD,
    score_record,
)
from tools.relevance.taxonomy import load_taxonomy

TAX = load_taxonomy("Insignary")


def _verdict(text, naics=None, **extra):
    record = {"title": "Notice", "description": text, **extra}
    if naics:
        record["naics_code"] = naics
    return score_record(record, TAX)


def test_acronym_collision_sca_alone_scores_zero():
    v = _verdict("Sudden cardiac arrest response equipment and SCA training "
                 "for medical response teams.")
    assert v.score == 0 and not v.relevant
    assert v.core_terms == []
    # and the record-scoped medical kill fires
    assert any("cardiac" in k.lower() for k in v.killed)


def test_sca_with_software_context_is_core():
    v = _verdict("SCA tooling for software composition analysis of "
                 "delivered binaries.")
    assert "SCA" in v.core_terms
    assert "software composition analysis" in v.core_terms
    assert v.relevant


def test_exclude_kill_rule_fires_on_hardware_bom():
    v = _verdict("Procurement of hardware bill of materials tracking; "
                 "includes SBOM software component inventory for firmware.")
    assert any("hardware bill of materials" in k or "HBOM" in k
               for k in v.killed) or v.killed
    # the SBOM hit far from the kill window can survive; the one inside dies
    near = _verdict("Hardware bill of materials (SBOM) tracking system.")
    assert near.score == 0
    assert near.killed


def test_adjacent_alone_is_never_sufficient():
    v = _verdict("Enterprise DevSecOps pipeline support and secure software "
                 "supply chain services.")
    assert v.core_terms == []
    assert v.adjacent_terms
    assert v.score < RELEVANCE_THRESHOLD or not v.relevant
    assert not v.relevant


def test_code_match_contributes_zero():
    empty = _verdict("General IT support services.", naics="541512")
    assert empty.score == 0 and not empty.relevant


def test_off_code_strong_hit_passes_flagged():
    text = ("Binary SCA and SBOM software component analysis with "
            "reachability analysis of vulnerable dependencies.")
    v = _verdict(text, naics="238220")            # plumbing: out of universe
    assert v.score >= OFF_CODE_STRONG_THRESHOLD
    assert v.relevant and v.off_code
    weak = _verdict("SBOM software component inventory.", naics="238220")
    assert weak.excluded_by_code and not weak.relevant and not weak.off_code


def test_in_universe_code_never_flags_off_code():
    v = _verdict("Binary SCA with SBOM software dependency analysis.",
                 naics="541512")
    assert v.relevant and not v.off_code and not v.excluded_by_code


def test_structured_usaspending_codes_use_the_code_for_boundary():
    record = {
        "title": "Notice",
        "description": "Binary SCA with SBOM software dependency analysis.",
        "naics": {"code": "541512", "description": "Computer systems"},
        "psc": {"code": "DA01", "description": "Business applications"},
    }

    verdict = score_record(record, TAX)
    scalar = score_record({
        **record, "naics": "541512", "psc": "DA01",
    }, TAX)

    assert verdict.relevant
    assert not verdict.off_code
    assert not verdict.excluded_by_code
    assert verdict == scalar


def test_spans_are_verbatim_with_context():
    text = ("The contractor shall provide software composition analysis "
            "and SBOM generation for delivered software components.")
    v = _verdict(text)
    span = next(s for s in v.spans if s.term == "software composition analysis")
    assert span.matched_text == "software composition analysis"
    assert span.matched_text in span.context
    assert span.context in text
    assert span.field == "description"


@pytest.mark.parametrize("field", ["objective", "abstract", "summary"])
def test_official_program_narrative_fields_keep_native_span_provenance(field):
    text = "Software composition analysis for delivered federal systems."

    verdict = score_record({"title": "Program update", field: text}, TAX)

    assert verdict.relevant
    span = next(
        item for item in verdict.spans
        if item.term == "software composition analysis")
    assert span.field == field
    assert span.context in text


def test_adapter_matched_terms_alone_do_not_become_relevance_evidence():
    verdict = score_record({
        "title": "Program update",
        "matched_terms": ["software composition analysis"],
    }, TAX)

    assert not verdict.relevant
    assert verdict.spans == []


def test_stemmed_matching_reaches_inflections():
    v = _verdict("Reachability analyses of exploited software dependencies.")
    assert "reachability analysis" in v.core_terms


def test_verdict_stamps_taxonomy_identity():
    v = _verdict("SBOM software inventory.")
    assert v.taxonomy_client == "Insignary"
    assert v.taxonomy_version == 1
