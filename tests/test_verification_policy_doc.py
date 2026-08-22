"""docs/VERIFICATION_POLICY.md has teeth (2026-07-16).

The arbitration rule set is fixed text; the enforcement code cites it. This
test pins the three rules' load-bearing phrases and that every enforcement
surface the doc names actually exists, so the doc cannot silently drift from
the code (house pattern: test_learnings_and_standards).
"""
import os
import re

_DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "docs", "VERIFICATION_POLICY.md")


def _text() -> str:
    """Whitespace-normalized: the RULE text is pinned, not the line wrap."""
    with open(_DOC, encoding="utf-8") as f:
        return re.sub(r"\s+", " ", f.read())


def test_policy_doc_exists_with_the_three_rules():
    text = _text()
    # rule 1: primary record wins automatically
    assert "primary federal record" in text
    assert "wins automatically" in text
    assert "never override a primary record" in text
    # rule 2: frozen figure, blocked artifact, human resolution, append-only log
    assert "the figure is frozen" in text
    assert "cannot pass the release gate" in text
    assert "a human resolves it" in text
    assert "logged append-only" in text
    for field in ("figure", "both values", "records cited", "decision",
                  "decider", "timestamp"):
        assert field in text
    # rule 3: no self-resolution
    assert "No model resolves a dispute over a figure it authored." in text


def test_policy_doc_enforcement_names_exist():
    from agents.reports import verification as v
    text = _text()
    for name in ("PRIMARY_SOURCE_SYSTEMS", "arbitrate", "append_resolution",
                 "freshness_violations", "data_current_date",
                 "FRESHNESS_MAX_AGE_DAYS"):
        assert name in text, f"policy doc must cite {name}"
        assert hasattr(v, name), f"policy doc cites missing {name}"
    assert v.FRESHNESS_MAX_AGE_DAYS == 14
    # the worked example the doc points the verify pass at
    assert "test_worked_example_insignary_odos_iii" in text
    from tests import test_figure_freshness_gate as gate_tests
    assert hasattr(gate_tests, "test_worked_example_insignary_odos_iii")
