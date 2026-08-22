"""G2: catalogued agencies resolve under USAspending display names."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.golden_press.report_templates import find_mark  # noqa: E402


@pytest.mark.parametrize("display", [
    "Internal Revenue Service",
    "Department of Veterans Affairs",
    "General Services Administration",
    "National Aeronautics and Space Administration",
    "Transportation Security Administration",
    "U.S. Coast Guard",
    "U.S. Customs and Border Protection",
    "Office of the Comptroller of the Currency",
    "Social Security Administration",
    "U.S. Citizenship and Immigration Services",
])
def test_usaspending_display_name_resolves_a_catalogued_mark(display):
    entry = find_mark(display, kind="agency")
    assert entry and entry.get("src", "").startswith("data:image/"), display


def test_agency_render_level_resolution_meets_the_coverage_floor():
    """Mark-coverage round (2026-08-04): across every pack on disk, at
    least 95% of distinct (subtier, agency) pairs resolve an OFFICIAL mark
    at render level (subtier seal, else the record's parent seal). The
    residual renders monograms and is receipted in the method band."""
    import glob
    import json

    from agents.golden_press.report_templates import find_mark
    rows, seen = [], set()
    for f in glob.glob("data/state/candidate_review_v1/*/"
                       "[a-z_]*.golden_report.evidence_pack.json"):
        raw = json.load(open(f))
        for r in raw.get("records") or []:
            key = (r.get("sub_agency"), r.get("agency"))
            if key in seen or key == (None, None):
                continue
            seen.add(key)
            rows.append(bool(
                (r.get("sub_agency")
                 and find_mark(r["sub_agency"], kind="agency"))
                or (r.get("agency")
                    and find_mark(r["agency"], kind="agency"))))
    assert rows, "no packs on disk; coverage test would be vacuous"
    rate = sum(rows) / len(rows)
    assert rate >= 0.95, f"agency render-level resolve {rate:.1%} < 95%"
