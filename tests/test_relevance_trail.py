"""Justification trail + code-only lint + adjudication seam (2026-07-17).

Doctrine: every rendered opportunity carries matched spans, tier, score,
and taxonomy version in the INTERNAL sidecar. A machine-screened board
cannot render a zero-capability-evidence card (lint-level impossibility);
operator-included content renders with its scores visible. Adjudications
are decider-stamped, append-only.
"""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from tools.relevance.adjudicate import (  # noqa: E402
    RelevanceAdjudication,
    append_adjudication,
    load_adjudications,
)
from tools.relevance.engine import (  # noqa: E402
    relevance_basis_violations,
    score_opportunity_evidence,
)
from tools.relevance.taxonomy import load_taxonomy  # noqa: E402

TAX = load_taxonomy("Insignary")

_SWEEP = {"results": {"usaspending.gov": [{"awards": [
    {"award_id": "70B04C25F00001054", "recipient": "GOVPLACE, LLC",
     "description": "SONATYPE NEXUS LIFECYCLE AND SBOM MANAGER for software "
                    "component inventory",
     "url": "https://www.usaspending.gov/award/CONT_AWD_70B04C25F00001054_X"},
    {"award_id": "ZZ000TESTCODE01", "recipient": "CODE ONLY CORP",
     "description": "General information technology support services.",
     "naics_code": "541512"},
]}]}}


def test_opportunity_evidence_scores_union_of_citing_rows():
    v = score_opportunity_evidence(_SWEEP, ["70B04C25F00001054"], TAX)
    assert "SBOM" in v.core_terms
    assert v.relevant
    assert any("SONATYPE NEXUS LIFECYCLE AND SBOM MANAGER" in s.context
               for s in v.spans)
    assert v.taxonomy_version == 1


def test_machine_screened_zero_evidence_is_a_violation():
    v = score_opportunity_evidence(_SWEEP, ["ZZ000TESTCODE01"], TAX)
    assert v.core_terms == []
    violations = relevance_basis_violations(
        [("Code Only Opportunity", v)], machine_screened=True)
    assert len(violations) == 1
    assert "code membership alone never renders" in violations[0]
    assert "taxonomy v1" in violations[0]


def test_operator_included_zero_evidence_renders_with_visible_score():
    v = score_opportunity_evidence(_SWEEP, ["ZZ000TESTCODE01"], TAX)
    assert relevance_basis_violations(
        [("Operator Card", v)], machine_screened=False) == []


def test_press_sidecar_carries_trail_with_taxonomy_stamp():
    """The live Insignary sidecar (pressed by the runner) carries the trail
    section, the taxonomy version stamp, quoted spans, and the visible
    zero-evidence rows for operator-included cards."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "reports",
                        "insignary.federal_opportunity_signals.internal.md")
    if not os.path.exists(path):
        pytest.skip("no pressed Insignary sidecar on this machine")
    text = open(path, encoding="utf-8").read()
    assert "## Relevance trail · taxonomy v1 (Insignary)" in text
    assert "Codes contribute zero relevance" in text
    assert "operator-included content" in text
    assert "included on operator judgment" in text


def test_adjudication_seam_is_append_only_and_decider_stamped(tmp_path):
    row = RelevanceAdjudication(
        record_ref="70SBUR22F00000113", kind="false_positive",
        engine_score=1, matched_spans=["DEVSECOPS SERVICES (ODOS) III"],
        decision="defer", decider="operator:william",
        taxonomy_version=1,
        decided_at=datetime(2026, 7, 17, 16, 0, tzinfo=timezone.utc))
    path = append_adjudication("Insignary", row, log_dir=str(tmp_path))
    append_adjudication("Insignary", row.model_copy(
        update={"decision": "confirm"}), log_dir=str(tmp_path))
    lines = open(path, encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["decision"] == "defer"
    rows = load_adjudications("Insignary", log_dir=str(tmp_path))
    assert rows[-1].decision == "confirm"
    naive = row.model_copy(update={"decided_at": datetime(2026, 7, 17)})
    with pytest.raises(ValueError, match="timezone-aware"):
        append_adjudication("Insignary", naive, log_dir=str(tmp_path))
