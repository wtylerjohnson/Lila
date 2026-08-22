"""Calibration harness: disagreements advisory, stored artifacts untouched."""
import json

from tools.relevance.calibrate import calibrate_client


def _sweep():
    return {
        "client": "Insignary",
        "results": {
            "triage": {"N1": {"verdict": "monitor"},
                       "N2": {"verdict": "discard"}},
            "sam.gov": [
                {"source_id": "N1", "title": "Generic IT Gateway",
                 "raw_payload": {"description_snippet":
                                 "innovation gateway for broad solutions"}},
                {"source_id": "N2", "title": "Janitorial services"},
                {"source_id": "N3", "title": "SBOM tooling",
                 "raw_payload": {"description_snippet":
                                 "software bill of materials generation and "
                                 "binary SCA for delivered software"}},
            ],
            "usaspending.gov": [{"awards": [
                {"award_id": "A1", "recipient": "X CORP",
                 "description": "software composition analysis licenses",
                 "matched_terms": ["SBOM"], "capability_score": 2},
            ]}],
        },
    }


def test_calibration_flags_both_directions_with_spans(tmp_path):
    sweep = _sweep()
    before = json.dumps(sweep)
    result = calibrate_client("Insignary", sweep)
    assert json.dumps(sweep) == before          # advisory: input untouched

    fp_refs = {r["record_ref"] for r in result["fp_candidates"]}
    fn_refs = {r["record_ref"] for r in result["fn_candidates"]}
    assert "N1" in fp_refs                       # included, zero evidence
    assert "N3" in fn_refs                       # untriaged, core evidence
    assert "N2" not in fp_refs | fn_refs         # discard + zero: agreement
    assert "A1" not in fp_refs                   # included AND relevant

    fn = next(r for r in result["fn_candidates"] if r["record_ref"] == "N3")
    assert "software bill of materials" in fn["matched_spans"]
    assert fn["taxonomy"].startswith("v1")
    fp = next(r for r in result["fp_candidates"] if r["record_ref"] == "N1")
    assert fp["matched_spans"] == "(no capability span)"
