"""Phase 4 permanence: learnings have teeth, the standard is enforced."""
import os
import re
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEARNINGS = os.path.join(ROOT, "pipeline", "LEARNINGS.md")


def test_learnings_file_exists_with_required_entries():
    text = open(LEARNINGS, encoding="utf-8").read()
    for needle in ("L1", "L2", "L3", "L4", "PROPOSED", "ENCODED",
                   "NAICS is a filing location",
                   "Named incumbents are the highest-yield search key",
                   "Verified negatives are billable findings",
                   "Every claim carries its evidence level"):
        assert needle in text, needle


def test_no_learning_stays_proposed_over_30_days():
    """Promotion rule: an unenforced learning is a wish. Any entry PROPOSED
    for more than 30 days fails the build until it is ENCODED or removed."""
    text = open(LEARNINGS, encoding="utf-8").read()
    stale = []
    for m in re.finditer(r"## (L\d+) · (\d{4}-\d{2}-\d{2}) · PROPOSED", text):
        entry_date = date.fromisoformat(m.group(2))
        if date.today() - entry_date > timedelta(days=30):
            stale.append(m.group(1))
    assert not stale, f"PROPOSED > 30 days, encode or drop: {stale}"


def test_no_em_dash_in_output_reachable_strings():
    """Constraint: no em dashes in any output-reachable string. Tests the
    OUTPUTS (fact texts, rendered sections, QA reasons); composed copy is
    covered by R9 remediation + lint_emdash at build time."""
    from agents.reports.capture_brief import _buyer_map_section
    from agents.reports.remediation import remediate
    from agents.reports.facts import build_fact_pack
    from tests.test_capability_inversion import PROFILE, _results
    from tests.test_remediation import _content

    pack = build_fact_pack("Testco", searches={"results": _results()},
                           qualify_report={"candidates": []}, profile=PROFILE)
    assert all("—" not in f.text for f in pack.facts)
    bm = {"population_label": "matched records",
          "buyers": [{"buyer": "X OFFICE", "products": ["ForeFlight"],
                      "total": 1000.0, "records": [],
                      "next_pop_end": "2026-12-01",
                      "displacement_window": True, "sole_source": True,
                      "sole_source_notices": [{"quote": "q", "url": "https://x",
                                               "notice_id": "N1"}]}],
          "displacement_windows": []}
    assert "—" not in _buyer_map_section(bm)
    _, report = remediate(_content(kill_line_news="A — B"), pack, {})
    assert all("—" not in a.reason for a in report.actions)


def test_whitelabel_blocklist_covers_prior_clients():
    from agents.reports.lint import lint_client_bleed
    html = "<html><body>as we did for Recorded Future last quarter</body></html>"
    v = lint_client_bleed(html, "Osprey Flight Solutions").violations
    assert v, "prior client name must trip the bleed gate"


def test_buyer_map_section_requires_population_label():
    """Signal-density rule: a statistic without its population label does
    not render."""
    from agents.reports.capture_brief import _buyer_map_section
    bm = {"buyers": [{"buyer": "X", "products": ["P"], "total": 1.0,
                      "records": [], "sole_source_notices": []}],
          "displacement_windows": []}
    assert _buyer_map_section(bm) == ""            # no label -> no table
    bm["population_label"] = "matched records, trailing 5 years"
    assert "Population:" in _buyer_map_section(bm)
