"""Engagement scope: the agency universe as a second exclude-only boundary.

Briefed cases: an off-scope strong hit flags and never counts; an unscoped
client passes through marked; alias normalization resolves department
codes and subtier names; the lint fires on an out-of-scope
machine-screened render; calibration separates out-of-scope rows from
FP/FN math.
"""
import json

from tools.relevance.calibrate import calibrate_client
from tools.relevance.engine import (
    relevance_basis_violations,
    score_record,
)
from tools.relevance.scope import (
    EngagementScope,
    in_scope,
    resolve_department,
)
from tools.relevance.taxonomy import load_taxonomy

TAX = load_taxonomy("Insignary")
DHS_SCOPE = EngagementScope(departments=["DHS"])

_STRONG_TEXT = ("Binary SCA and SBOM software component analysis with "
                "reachability analysis of vulnerable software dependencies.")


def test_off_scope_strong_hit_flags_and_never_counts():
    record = {"title": "DoD notice", "description": _STRONG_TEXT,
              "agency": "Department of Defense"}
    v = score_record(record, TAX, engagement_scope=DHS_SCOPE)
    assert v.off_scope and v.off_scope_signal
    assert not v.relevant                      # a boundary never includes
    assert v.scope_basis == "off-scope:DoD"
    assert v.core_terms                        # it still scored normally
    weak = score_record({"title": "DoD notice",
                         "description": "SBOM software inventory.",
                         "agency": "Department of Defense"},
                        TAX, engagement_scope=DHS_SCOPE)
    assert weak.off_scope and not weak.off_scope_signal


def test_unscoped_client_passes_through_marked():
    record = {"title": "DoD notice", "description": _STRONG_TEXT,
              "agency": "Department of Defense"}
    v = score_record(record, TAX, engagement_scope=None)
    assert not v.off_scope
    assert v.relevant
    assert v.scope_basis == "UNSCOPED"


def test_alias_normalization_department_code_and_subtier_name():
    scoped = EngagementScope(departments=["DHS"],
                             aliases={"DHS": "DHS",
                                      "Dept. of Homeland Sec.": "DHS"})
    # bare department code resolves only through the alias map
    assert resolve_department({"agency": "DHS"}, scoped) == "DHS"
    assert in_scope({"agency": "DHS"}, scoped)[0]
    assert in_scope({"agency": "Dept. of Homeland Sec."}, scoped)[0]
    # a subtier NAME resolves through the curated catalog without an alias
    uscis = {"agency": "U.S. Citizenship and Immigration Services"}
    assert resolve_department(uscis, scoped) == "DHS"
    assert in_scope(uscis, scoped)[0]
    # and an unresolvable string never excludes (ignorance is not foreignness)
    inside, basis = in_scope({"agency": "Mystery Bureau"}, scoped)
    assert inside and basis == "unresolved"


def test_generated_id_toptier_code_resolves_agencyless_rows():
    """Buyer-map rows carry no agency fields; the USAspending generated id
    encodes the awarding toptier (verified live: 1406 = DOI). The Cloudflare
    DDoS row therefore resolves OFF-SCOPE for a DHS engagement."""
    row = {"kind": "award", "recipient": "COLOSSAL CONTRACTING LLC",
           "description": ("BRAND NAME (CLOUDFLARE) DISTRIBUTED DENIAL OF "
                           "SERVICE (DDOS) PROTECTION"),
           "url": ("https://www.usaspending.gov/award/"
                   "CONT_AWD_140D0426F0204_1406_NNG15SD72B_8000")}
    assert resolve_department(row, DHS_SCOPE) == "DOI"
    inside, basis = in_scope(row, DHS_SCOPE)
    assert not inside and basis == "off-scope:DOI"


def test_lint_fires_on_out_of_scope_machine_screened_render():
    record = {"title": "DoD notice", "description": _STRONG_TEXT,
              "agency": "Department of Defense"}
    v = score_record(record, TAX, engagement_scope=DHS_SCOPE)
    violations = relevance_basis_violations(
        [("DoD Opportunity", v)], machine_screened=True)
    assert any("out of engagement scope" in x for x in violations)
    assert relevance_basis_violations(
        [("DoD Opportunity", v)], machine_screened=False) == []


def test_calibration_separates_out_of_scope_from_fp_fn(monkeypatch):
    import tools.relevance.calibrate as cal
    monkeypatch.setattr(cal, "load_taxonomy", lambda _c: TAX)
    sweep = {
        "client": "Insignary",
        "search_scope": {"agencies": [{"abbr": "DHS",
                                       "name": "Department of Homeland Security"}]},
        "results": {
            "triage": {"N1": {"verdict": "monitor"}},
            "sam.gov": [
                {"source_id": "N1", "title": "USCIS SBOM tooling",
                 "agency": "U.S. Citizenship and Immigration Services",
                 "raw_payload": {"description_snippet":
                                 "software bill of materials and binary SCA "
                                 "for delivered software"}},
                {"source_id": "N2", "title": "Army SBOM tooling",
                 "agency": "Department of the Army",
                 "raw_payload": {"description_snippet": _STRONG_TEXT}},
            ],
        },
    }
    before = json.dumps(sweep)
    result = calibrate_client("Insignary", sweep)
    assert json.dumps(sweep) == before
    assert result["scope_basis"] == "SWEEP-DERIVED"
    fn_refs = {r["record_ref"] for r in result["fn_candidates"]}
    off_refs = {r["record_ref"]: r for r in result["off_scope_rows"]}
    assert "N2" in off_refs and "N2" not in fn_refs
    assert off_refs["N2"]["disagreement"] == "off_scope_signal"
    assert off_refs["N2"]["would_be_score"] >= 6
    assert "N1" not in off_refs            # in-scope stays in FP/FN math
