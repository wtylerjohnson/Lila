"""Research picture — the synthesis pass that makes the fan-out worth it."""

import json

from agents.decisions.research_picture import (
    ResearchPicture, TopOpportunity, compose_research_picture, distill,
    render_markdown,
)

RESULTS = {
    "sam.gov": [
        {"source_id": "N-1", "title": "CTI Platform BPA", "agency": "DHS",
         "notice_type": "Solicitation", "response_deadline": "2026-08-01"},
        {"source_id": "N-2", "title": "Janitorial Services", "agency": "GSA",
         "notice_type": "Solicitation", "response_deadline": "2026-07-20"},
    ],
    "triage": {
        "N-1": {"verdict": "pursue", "reason": "direct CTI buy"},
        "N-2": {"verdict": "discard", "reason": "wrong lane"},
    },
    "contract_awards": {"recompetes": [{"awardee": "BigCo", "piid": "P-1",
                                        "completion": "2026-12-01", "agency": "DHS"}],
                        "incumbents": [{"name": "BigCo", "contracts": 3}]},
    "subawards": {"primes": [{"name": "PrimeCo", "subaward_count": 9}]},
    "news": {"items": [{"title": "DHS expands CTI", "source": "CyberScoop",
                        "published": "2026-06-30"}]},
    "federal_register": {"threat intelligence": [
        {"title": "Cyber EO implementation", "type": "Notice"}]},
    "cisa_kev": {"recent_count": 12, "window_days": 30, "matched": []},
    "web": [{"title": "Budget doc", "url": "https://example.gov/x"}],
}


def _picture(**over):
    base = dict(
        client_name="Recorded Future",
        headline="DHS demand is live and the incumbent's ground shakes loose in December.",
        top_opportunities=[TopOpportunity(id="N-1", title="CTI Platform BPA",
                                          why_now="Pursue-grade notice; BigCo's P-1 expires 12/2026.",
                                          deadline="2026-08-01")],
        demand_signals=["CyberScoop: DHS expands CTI program"],
        market_structure="BigCo holds 3 contracts; PrimeCo pushes the most sub work.",
        watchlist=["Cyber EO implementation notice in Federal Register"],
        next_action="Respond to N-1 before 2026-08-01.",
        gaps=["No dollar values in this sweep"],
    )
    base.update(over)
    return ResearchPicture(**base)


class FakeEngine:
    def __init__(self):
        self.calls = []

    def deliberate(self, layer, system_prompt, context, schema):
        self.calls.append({"layer": layer, "context": context, "schema": schema})
        return _picture()


def test_distill_slims_and_joins_triage():
    d = distill(RESULTS)
    assert d["notice_counts"] == {"total": 2, "pursue": 1, "monitor": 0}
    assert d["pursue_notices"][0]["id"] == "N-1"
    assert d["pursue_notices"][0]["triage_reason"] == "direct CTI buy"
    assert not d["monitor_notices"]
    assert d["incumbents"][0]["name"] == "BigCo"
    assert d["subaward_primes"][0]["name"] == "PrimeCo"
    assert d["news"][0]["source"] == "CyberScoop"
    assert d["federal_register_docs"][0]["keyword"] == "threat intelligence"
    assert d["kev"]["recent_count"] == 12
    assert d["web_leads"][0]["url"] == "https://example.gov/x"


def test_distill_skips_errored_sources():
    d = distill({"contract_awards": {"error": "boom"}, "sam.gov": []})
    assert "recompetes" not in d
    assert d["notice_counts"]["total"] == 0


def test_distill_is_compact():
    # 200 notices must not blow up the prompt: caps hold.
    many = {"sam.gov": [{"source_id": f"N-{i}", "title": "t"} for i in range(200)],
            "triage": {f"N-{i}": {"verdict": "pursue", "reason": "r"} for i in range(200)}}
    d = distill(many)
    assert len(d["pursue_notices"]) == 15
    assert len(json.dumps(d)) < 20000


def test_compose_uses_engine_and_passes_sweep():
    eng = FakeEngine()
    pic = compose_research_picture("Recorded Future", "sell CTI", RESULTS, engine=eng)
    assert pic.headline == "Research signals require original-source verification."
    ctx = eng.calls[0]["context"]
    assert ctx["client_name"] == "Recorded Future"
    assert ctx["sweep"]["notice_counts"]["pursue"] == 1
    assert eng.calls[0]["schema"] is ResearchPicture


def test_render_markdown_covers_every_section():
    md = render_markdown(_picture())
    for needle in ["# Research Picture · Recorded Future", "verification",
                   "## Demand signals", "## Market structure", "## Watchlist",
                   "## Next action", "Verify original notice", "## Gaps",
                   "No dollar values"]:
        assert needle in md


def test_legacy_render_exposes_missing_evidence_even_with_empty_model_gaps():
    md = render_markdown(_picture(gaps=[]))
    assert "Legacy picture has no validated evidence registry" in md


def test_compose_receives_operator_focus_from_the_gate():
    """L18 (2026-07-10): the gate's search_scope threads into the composer as
    operator_focus — the picture ORIENTS on the operator's engagement focus.
    A lens for emphasis, never a filter; all-agencies passes {'all': True}."""
    eng = FakeEngine()
    focus = {"agencies": [{"abbr": "DHS",
                           "name": "Department of Homeland Security"}],
             "mode": "focus"}
    compose_research_picture("NETSCOUT", "sell visibility", RESULTS,
                             engine=eng, operator_focus=focus)
    assert eng.calls[0]["context"]["operator_focus"] == focus

    compose_research_picture("NETSCOUT", "sell visibility", RESULTS, engine=eng)
    assert eng.calls[1]["context"]["operator_focus"] == {"all": True}
