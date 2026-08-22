"""Arbiter panel over the assessment compose (client-flow F). No network."""

from datetime import date

from agents.decisions.assessment_arbiters import (
    arbitrate_assessment, content_as_draft,
)
from agents.reports.facts import FactPack
from agents.reports.schemas import ReportKind, SpecificityAudit, SpecViolation


def _content():
    import sys
    sys.path.insert(0, "tests")
    from test_capture_brief import _content as c
    return c()


def test_content_flattens_into_auditable_draft():
    draft = content_as_draft(_content())
    assert draft.kind == ReportKind.ASSESSMENT
    heads = [s.heading for s in draft.sections]
    assert "Thesis" in heads and "Marquee stats" in heads
    assert any(h.startswith("Opportunity 1:") for h in heads)
    text = draft.full_text()
    assert "NATO CTI Uplift" in text and "One gate, seven days." in text


def test_panel_pass_and_violation_directive(monkeypatch):
    import agents.decisions.assessment_arbiters as aa
    pack = FactPack(client_name="Testco", facts=[], as_of=date(2026, 7, 9))

    # consensus pass -> no directive
    monkeypatch.setattr(aa, "run_arbiters", lambda p, d, e: (
        SpecificityAudit(passed=True, violations=[]),
        {"arbiter-anthropic": SpecificityAudit(passed=True, violations=[])}))
    merged, audits, directive = arbitrate_assessment(pack, _content(), engine=object())
    assert merged.passed and directive is None and "arbiter-anthropic" in audits

    # a violation -> precise fix directive feeding the convergence loop
    v = SpecViolation(section="Thesis", quote="$9T market",
                      issue="unsupported_claim",
                      fix="cite F1 or cut the figure")
    monkeypatch.setattr(aa, "run_arbiters", lambda p, d, e: (
        SpecificityAudit(passed=False, violations=[v]),
        {"arbiter-anthropic": SpecificityAudit(passed=False, violations=[v])}))
    merged, _, directive = arbitrate_assessment(pack, _content(), engine=object())
    assert not merged.passed
    assert "ARBITER PANEL FAILED" in directive
    assert "unsupported_claim in Thesis" in directive and "cite F1" in directive


def test_openai_antagonist_stays_dormant_without_key(monkeypatch):
    """The cross-vendor antagonist is DORMANT-READY: registered, off without
    OPENAI_API_KEY + toggle, active the moment .env carries both."""
    from agents.decisions.arbiters import ARBITERS
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LILA_ENABLE_ARBITER_OPENAI", raising=False)
    names = [a.name for a in ARBITERS.all()]
    assert "arbiter-openai" in names                    # registered
    assert "arbiter-openai" not in [a.name for a in ARBITERS.active()]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LILA_ENABLE_ARBITER_OPENAI", "on")
    assert "arbiter-openai" in [a.name for a in ARBITERS.active()]
