"""Truth purges 1 and 2 (operator filing, 2026-08-03).

Purge 1 · picture stage: web-search assertions (the research picture, raw
web leads) are quarantined to internal surfaces; the compose chain never
sees them unless the operator explicitly re-admits them by env flag.

Purge 2 · intake routing: an uncited web-research result is a failed
attempt, not a success. It retries once, records the error, and the
profiling layer is told when rival/channel evidence is MISSING so a
product-only entity list can never read as a researched-empty market
(the apexanalytix packet class).

Offline; injected engines; no network, no LLM.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.company_research import CompanyResearch, _web_worker  # noqa: E402
from agents.reports.capture_brief import (  # noqa: E402
    PICTURE_TO_COMPOSE_ENV,
    quarantine_web_assertions,
)


# --------------------------------------------------------------------------- #
# purge 1 · the picture quarantine
# --------------------------------------------------------------------------- #
def _bundle():
    return {
        "source_sweep": {"pursue_notices": [{"id": "n1"}],
                         "web_leads": [{"title": "blog claim",
                                        "url": "https://x.example"}]},
        "research_picture": {"headline": "a web-synthesized market claim"},
        "pursuit_dossiers": [{"id": "d1"}],
        "fact_pack": {"facts": []},
    }


def test_web_assertions_are_quarantined_from_the_compose_bundle(monkeypatch):
    monkeypatch.delenv(PICTURE_TO_COMPOSE_ENV, raising=False)
    cleaned, removed = quarantine_web_assertions(_bundle())
    assert "research_picture" not in cleaned
    assert "web_leads" not in cleaned["source_sweep"]
    assert sorted(removed) == ["research_picture", "source_sweep.web_leads"]
    # structured stage outputs and the citable universe survive untouched
    assert cleaned["source_sweep"]["pursue_notices"] == [{"id": "n1"}]
    assert cleaned["pursuit_dossiers"] == [{"id": "d1"}]
    assert cleaned["fact_pack"] == {"facts": []}


def test_the_quarantine_never_mutates_the_callers_bundle(monkeypatch):
    monkeypatch.delenv(PICTURE_TO_COMPOSE_ENV, raising=False)
    bundle = _bundle()
    quarantine_web_assertions(bundle)
    assert "research_picture" in bundle
    assert "web_leads" in bundle["source_sweep"]


def test_the_operator_flag_readmits_explicitly(monkeypatch):
    monkeypatch.setenv(PICTURE_TO_COMPOSE_ENV, "1")
    cleaned, removed = quarantine_web_assertions(_bundle())
    assert removed == []
    assert "research_picture" in cleaned


def test_compose_full_picture_is_quarantined_even_called_directly(monkeypatch):
    """Defense in depth: the seam inside compose_full_picture cleans too,
    so a future caller that skips the runner's quarantine still cannot leak
    web assertions into the review context."""
    from agents.reports import capture_brief

    monkeypatch.delenv(PICTURE_TO_COMPOSE_ENV, raising=False)
    seen: dict = {}

    class _Engine:
        def deliberate(self, *, layer, system_prompt, context, schema):
            seen.update(context)
            return capture_brief.FullPictureReview(
                thesis_directive="t", storyline_connections=[],
                must_include=[], emphasis_ranking=[])

    capture_brief.compose_full_picture("Acme", _bundle(), engine=_Engine())
    assert "research_picture" not in seen
    assert "web_leads" not in (seen.get("source_sweep") or {})


# --------------------------------------------------------------------------- #
# purge 2 · uncited web research is a failed attempt
# --------------------------------------------------------------------------- #
class _WebEngine:
    """Scripted web_research results, in order."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def web_research(self, *, system_prompt, query, max_uses=5):
        self.calls += 1
        return self.results.pop(0)


def test_uncited_result_retries_once_and_recovers():
    engine = _WebEngine([("", []),
                         ("Rivals: Quorum. SOURCES: https://q.example",
                          ["https://q.example"])])
    research = CompanyResearch(company_name="FiscalNote")
    _web_worker(research, engine)
    assert engine.calls == 2
    assert research.web_citations == ["https://q.example"]
    assert research.errors == []
    assert research.context()["web_research_cited"] is True


def test_uncited_after_retry_is_a_recorded_error_not_silent_success():
    engine = _WebEngine([("", []), ("uncited prose with no urls", [])])
    research = CompanyResearch(company_name="apexanalytix")
    _web_worker(research, engine)
    assert engine.calls == 2
    assert len(research.errors) == 1
    assert "no cited findings" in research.errors[0]
    context = research.context()
    assert context["web_research_cited"] is False
    assert context["errors"] == research.errors


def test_cited_first_try_never_retries():
    engine = _WebEngine([("Findings. SOURCES: https://a.example",
                          ["https://a.example"])])
    research = CompanyResearch(company_name="Red Hat")
    _web_worker(research, engine)
    assert engine.calls == 1
    assert research.errors == []


def test_intake_prompt_names_the_missing_evidence_rule():
    """Tripwire: the profiling layer is told to disclose a missing rival
    side at the review gate instead of presenting product-only entities as
    a researched competitive landscape."""
    from agents.decisions.intake import SYSTEM_PROMPT
    assert "web_research_cited=false" in SYSTEM_PROMPT
    assert "MISSING" in SYSTEM_PROMPT
    assert "product-only" in SYSTEM_PROMPT
