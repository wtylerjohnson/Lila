"""Title strategy opens the TARGET stage and feeds the contact plan."""
from unittest import mock

from agents.decisions.contacts import build_contact_plan, generate_title_strategy
from agents.decisions.schemas import BuyerPersona, ContactPlan, TitleStrategy


def _titles():
    return TitleStrategy(
        client_name="Testco",
        buying_committee_note="Program owns pain; contracting owns paper.",
        personas=[
            BuyerPersona(title="Director, Cyber Threat Intelligence", function="security operations",
                         seniority="director", why="evaluates feeds [src]", org_types=["agency office"]),
            BuyerPersona(title="Contracting Officer", function="procurement/contracting",
                         seniority="staff", why="owns the vehicle [src]", org_types=["agency office"]),
            BuyerPersona(title="VP Capture", function="channel/prime",
                         seniority="vp", why="teaming path [src]", org_types=["prime"]),
            BuyerPersona(title="Insider Threat Program Manager", function="mission/program",
                         seniority="manager", why="mission owner [src]", org_types=["agency office"]),
        ],
    )


def test_title_strategy_runs_first_and_flows_into_plan_context():
    engine = mock.Mock()
    engine.deliberate.side_effect = [
        _titles(),
        ContactPlan(client_name="Testco", strategy_note="angle", review_gate="approve searches"),
    ]
    build_contact_plan("Testco", {"candidates": []}, engine=engine)

    assert engine.deliberate.call_count == 2
    first, second = engine.deliberate.call_args_list
    assert first.kwargs["layer"] == "title-strategy"
    assert second.kwargs["layer"] == "contacts"
    ctx = second.kwargs["context"]
    assert ctx["title_strategy"]["personas"][0]["title"] == "Director, Cyber Threat Intelligence"


def test_injected_titles_skip_the_opener():
    engine = mock.Mock()
    engine.deliberate.return_value = ContactPlan(
        client_name="Testco", strategy_note="angle", review_gate="gate")
    build_contact_plan("Testco", {"candidates": []}, engine=engine, titles=_titles())
    assert engine.deliberate.call_count == 1
    assert engine.deliberate.call_args.kwargs["layer"] == "contacts"


def test_generate_title_strategy_uses_assess_context():
    engine = mock.Mock()
    engine.deliberate.return_value = _titles()
    generate_title_strategy("Testco", {"candidates": []}, engine=engine)
    ctx = engine.deliberate.call_args.kwargs["context"]
    assert "qualified_candidates" in ctx and "pursuit_strategy" in ctx
