"""Report system tests: FactPack, lint, number verification, chain, renderer.

Offline — fake engine, synthetic artifacts. No keys, no network.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.build import build_report, verify_numbers  # noqa: E402
from agents.reports.facts import build_fact_pack  # noqa: E402
from agents.reports.lint import lint_text  # noqa: E402
from agents.reports.render import render_markdown  # noqa: E402
from agents.reports.schemas import (  # noqa: E402
    ReportDraft,
    ReportKind,
    ReportSection,
    SpecificityAudit,
)

SEARCHES = {
    "results": {
        "usaspending.gov": [
            {
                "naics_code": "541512",
                "agency_filter_used": None,
                "summary": {
                    "award_count": 42,
                    "total_obligated": 128_500_000.0,
                    "median_award": 1_200_000.0,
                    "max_award": 30_000_000.0,
                    "top_incumbents": ["BigPrime Inc", "MidTier LLC"],
                },
                "sample_awards": [{"url": "https://www.usaspending.gov/award/XYZ"}],
            },
            {"naics_code": "541519", "summary": {"award_count": 0, "top_incumbents": []}},
        ]
    }
}

QUALIFY = {
    "candidates": [
        {
            "opportunity": {
                "source": "sam.gov", "source_id": "n1", "title": "VA network modernization",
                "agency": "VETERANS AFFAIRS, DEPARTMENT OF", "naics_code": "541512",
                "set_aside": "SDVOSB", "estimated_value": 4_200_000.0,
                "response_deadline": "2026-08-30", "api_url": "https://sam.gov/opp/n1",
            },
            "verified": True,
            "fit_rationale": {"verdict": "strong_fit"},
        },
        {
            "opportunity": {
                "source": "web", "source_id": "w1", "title": "Reachable forecast lead",
                "api_url": "https://example.gov/forecast/w1",
            },
            # Compatibility fixture: old Qualify artifacts marked a web page
            # verified when its URL was reachable.
            "verified": True,
            "fit_rationale": {"verdict": "strong_fit"},
        },
    ]
}


def _pack():
    return build_fact_pack("Acme", searches=SEARCHES, qualify_report=QUALIFY,
                           as_of=date(2026, 7, 1))


# ---- FactPack ---------------------------------------------------------------- #
def test_fact_pack_ids_kinds_and_warnings():
    pack = _pack()
    assert pack.market_fact_ids == ["F1"]
    assert pack.competitor_fact_ids == ["F2"]
    assert pack.opportunity_fact_ids == ["F3"]  # stale verified web lead excluded
    assert "$128,500,000" in pack.get("F1").text
    assert "BigPrime Inc" in pack.get("F2").text
    assert any("541519" in w for w in pack.warnings)  # zero-award NAICS surfaces as a warning


def test_fact_pack_opportunity_carries_source_url():
    pack = _pack()
    assert pack.get("F3").source == "https://sam.gov/opp/n1"


def test_stale_verified_web_candidate_never_becomes_notice_fact():
    web_only = {"candidates": [QUALIFY["candidates"][1]]}
    pack = build_fact_pack("Acme", searches=SEARCHES,
                           qualify_report=web_only, as_of=date(2026, 7, 1))
    assert pack.opportunity_fact_ids == []
    assert pack.facts  # market context still supports a zero-live assessment
    assert not any(
        f.tier == "notice"
        and ((f.value or {}).get("opportunity") or {}).get("source") == "web"
        for f in pack.facts
    )
    assert any("no verified opportunities" in w for w in pack.warnings)


def test_non_sam_monitor_misbucket_never_becomes_notice_fact():
    searches = {"results": {
        "sam.gov": [{
            "source": "web", "source_id": "web-monitor",
            "title": "Trade article about a possible requirement",
            "api_url": "https://example.gov/news/web-monitor",
            "raw_payload": {"url": "https://example.gov/news/web-monitor"},
        }],
        "triage": {"web-monitor": {
            "verdict": "monitor", "reason": "legacy misclassification"}},
    }}
    pack = build_fact_pack("Acme", searches=searches,
                           qualify_report={"candidates": []},
                           as_of=date(2026, 7, 1))
    assert pack.monitor_fact_ids == []
    assert not any(f.tier == "notice" for f in pack.facts)


def test_client_awards_stay_internal_never_factpack():
    """WE NEVER TELL THE CLIENT WHAT THEY ALREADY KNOW. The client's own
    awards must not become FactPack facts (FactPack facts become client copy);
    they surface only via client_footprint(), the review layer's INTERNAL
    judgment context."""
    from agents.reports.facts import client_footprint
    searches = {
        "results": {
            **SEARCHES["results"],
            "client_awards": {
                "rows": [
                    {"award_id": "70T02023C7554N002", "amount": 994455.0,
                     "awarding_agency": "DHS", "awarding_sub_agency": "TSA",
                     "start_date": "2023-07-14", "end_date": "2025-07-13",
                     "url": "https://www.usaspending.gov/award/CONT_AWD_70T0"},
                ],
                "total_amount": 994455.0,
            },
        }
    }
    pack = build_fact_pack("Acme", searches=searches, qualify_report=QUALIFY,
                           as_of=date(2026, 7, 1))
    # the client's own record NEVER enters the client-copy evidence
    assert not any("70T02023C7554N002" in f.text for f in pack.facts)
    assert not any("past performance" in f.text.lower() for f in pack.facts)
    # ...but the footprint exists for the review layer, flagged internal
    fp = client_footprint(searches["results"])
    assert fp and fp["awards"][0]["award_id"] == "70T02023C7554N002"
    assert "INTERNAL" in fp["note"] and "never restate" in fp["note"]
    assert client_footprint({}) is None                  # absent layer -> None


# ---- lint --------------------------------------------------------------------- #
def test_lint_catches_verbosity_emdash_and_citations():
    text = ("We leverage cutting-edge synergies — seamlessly. "
            "The market totals $128,500,000 across 42 awards.")
    r = lint_text(text, valid_fact_ids={"F1"})
    rules = {v.rule for v in r.violations}
    assert not r.ok
    assert {"banned_phrase", "em_dash", "uncited_number"} <= rules


def test_lint_passes_clean_cited_text():
    text = "The market totals $128,500,000 across 42 awards [F1]. BigPrime Inc leads [F2]."
    r = lint_text(text, valid_fact_ids={"F1", "F2"})
    assert r.ok


def test_lint_flags_unknown_fact_citation():
    r = lint_text("Spend was $5 [F99].", valid_fact_ids={"F1"})
    assert any(v.rule == "unknown_citation" for v in r.violations)


# ---- deterministic number check ------------------------------------------------ #
def test_verify_numbers_catches_mismatch():
    pack = _pack()
    draft = ReportDraft(
        client_name="Acme", kind=ReportKind.TEASER, title="T",
        sections=[ReportSection(heading="Market size",
                                body="Total spend was $999,999,999 [F1].")],
    )
    v = verify_numbers(draft, pack)
    assert len(v) == 1 and "$999,999,999" in v[0].fix


def test_verify_numbers_passes_exact_figures():
    pack = _pack()
    draft = ReportDraft(
        client_name="Acme", kind=ReportKind.TEASER, title="T",
        sections=[ReportSection(heading="Market size",
                                body="Obligations total $128,500,000 [F1].")],
    )
    assert verify_numbers(draft, pack) == []


# ---- chain (fake engine) -------------------------------------------------------- #
class FakeEngine:
    """Composer emits a draft with one wrong number; critic passes; the hard check
    must still force a revision. Second compose emits the corrected draft."""

    def __init__(self):
        self.compose_calls = 0
        self.edit_calls = 0

    def deliberate(self, *, layer, system_prompt, context, schema):
        if layer == "report_compose":
            self.compose_calls += 1
            body = ("Total obligations were $999 [F1]." if self.compose_calls == 1
                    else "Total obligations were $128,500,000 [F1].")
            return ReportDraft(
                client_name="Acme", kind=ReportKind.TEASER, title="Federal teaser",
                sections=[ReportSection(heading="Market size", body=body)],
                fact_ids_used=["F1"],
            )
        if layer == "report_audit":
            return SpecificityAudit(passed=True)
        if layer == "report_edit":
            self.edit_calls += 1
            return context["draft"] if isinstance(context["draft"], ReportDraft) \
                else ReportDraft.model_validate(context["draft"])
        raise AssertionError(layer)


def test_chain_hard_number_check_forces_revision_and_ships_clean():
    pack = _pack()
    engine = FakeEngine()
    bundle = build_report(pack, ReportKind.TEASER, engine=engine)
    assert engine.compose_calls == 2          # revision forced by verify_numbers, not the critic
    assert bundle.audit.passed
    assert bundle.lint_ok
    assert "$128,500,000" in bundle.draft.full_text()


# ---- renderer ------------------------------------------------------------------- #
def test_render_footnotes_and_gate_stamp():
    pack = _pack()
    engine = FakeEngine()
    bundle = build_report(pack, ReportKind.TEASER, engine=engine)
    md = render_markdown(bundle, pack)
    assert "[^1]" in md and "## Sources" in md
    assert "usaspending.gov" in md            # the source URL surfaces
    assert "DO NOT SEND" not in md            # gates passed -> no stamp

    bundle.lint_ok = False                    # simulate a failed gate
    assert "DO NOT SEND" in render_markdown(bundle, pack)


def test_md_deliverables_carry_gtm_logo():
    """Logos on ALL client documents: markdown reports open with the GTM logo
    img (base64, renders in the dashboard preview and any md->HTML export)."""
    pack = _pack()
    bundle = build_report(pack, ReportKind.TEASER, engine=FakeEngine())
    md = render_markdown(bundle, pack)
    first_line = md.splitlines()[0]
    assert first_line.startswith('<img id="gtmLogoImg" src="data:')
    assert ";base64," in first_line
