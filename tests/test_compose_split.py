"""P2 sectioned compose: partition exactness, assembly, cache economics.

Offline: a fake engine counts every deliberate call; the cache dir is
env-isolated. No LLM, no network.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.capture_brief import (  # noqa: E402
    CaptureBriefContent, _SECTION_FIELDS, _subset_pack,
    compose_capture_brief_sectioned,
)
from agents.reports.facts import Fact, FactPack  # noqa: E402


def test_section_fields_partition_the_schema_exactly():
    """Schema drift breaks THIS test, loudly, before it breaks a build."""
    union: set = set()
    total = 0
    for fields in _SECTION_FIELDS.values():
        union |= set(fields)
        total += len(fields)
    assert union == set(CaptureBriefContent.model_fields)
    assert total == len(CaptureBriefContent.model_fields)  # disjoint


class _FakeEngine:
    """Returns minimal valid payloads per section schema; counts calls."""

    def __init__(self):
        self.calls: list[str] = []

    def deliberate(self, *, layer, system_prompt, context, schema, now=None):
        self.calls.append(layer)
        stub = {
            "client_name": "Acme Federal", "subtitle": "sub",
            "meta_prepared_for": "Prepared for Acme Federal",
            "thesis": ["one", "two", "three"],
            "stats": [{"number": "{{COUNT:pursuits}}", "accent": "a",
                       "context": "c [F1]"}] * 5,
            "action_callout": "act now", "budget_bars": [],
            "kill_line_opps": "opps line", "kill_line_news": "news line",
            "footer_verification": "verified",
            "news_funding": [], "news_threat": [], "news_agency": [],
            "news_market": [], "opportunities": [], "pipeline": [],
            "partner_callout": "drive the partner motion",
        }
        return schema.model_validate(
            {k: v for k, v in stub.items() if k in schema.model_fields})


def _pack():
    return FactPack(
        client_name="Acme Federal", as_of=date(2026, 7, 11),
        facts=[
            Fact(id="F1", kind="market", text="market [$]", source="u1"),
            Fact(id="F2", kind="context", text="signal", source="u2"),
            Fact(id="F3", kind="opportunity", text="opp", source="u3"),
            Fact(id="F4", kind="competitor", text="comp", source="u4"),
        ],
        market_fact_ids=["F1"], opportunity_fact_ids=["F3"],
        competitor_fact_ids=["F4"])


def test_subsets_preserve_ids_and_route_kinds():
    pack = _pack()
    news = _subset_pack(pack, "news")
    boards = _subset_pack(pack, "boards")
    assert {f.id for f in news.facts} == {"F1", "F2"}
    assert {f.id for f in boards.facts} == {"F3", "F4"}
    # ids preserved verbatim — citations stay valid against the full pack
    assert news.market_fact_ids == ["F1"]
    assert boards.opportunity_fact_ids == ["F3"]
    assert _subset_pack(pack, "narrative") is pack


def test_sectioned_compose_assembles_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path))
    engine = _FakeEngine()
    pack = _pack()

    content, hits = compose_capture_brief_sectioned(
        pack, engine=engine, client="Acme Federal", scope="all")
    assert isinstance(content, CaptureBriefContent)
    assert content.thesis == ["one", "two", "three"]
    assert content.partner_callout.startswith("drive")
    assert hits == {"narrative": False, "news": False, "boards": False}
    assert len(engine.calls) == 3

    # unchanged inputs: zero engine calls, all sections hit
    content2, hits2 = compose_capture_brief_sectioned(
        pack, engine=engine, client="Acme Federal", scope="all")
    assert hits2 == {"narrative": True, "news": True, "boards": True}
    assert len(engine.calls) == 3
    assert content2.model_dump() == content.model_dump()

    # a directive busts every section it keys (retry economics: narrative
    # re-pays, and the OTHER sections' prior entries still stand for the
    # directive-free key)
    _, hits3 = compose_capture_brief_sectioned(
        pack, engine=engine, client="Acme Federal", scope="all",
        directive="populate budget_bars")
    assert hits3["narrative"] is False
    assert len(engine.calls) == 6  # directive keys all three fresh
    _, hits4 = compose_capture_brief_sectioned(
        pack, engine=engine, client="Acme Federal", scope="all")
    assert hits4 == {"narrative": True, "news": True, "boards": True}


def test_scope_isolation_and_evidence_change(tmp_path, monkeypatch):
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path))
    engine = _FakeEngine()
    pack = _pack()
    compose_capture_brief_sectioned(pack, engine=engine,
                                    client="Acme Federal", scope="all")
    # a scoped run never serves all-scope prose
    _, hits = compose_capture_brief_sectioned(
        pack, engine=engine, client="Acme Federal", scope="agency_dhs")
    assert hits == {"narrative": False, "news": False, "boards": False}
    # a news-fact change re-pays news (and narrative, which anchors on the
    # full pack) but boards hit
    pack2 = _pack()
    pack2.facts[1] = Fact(id="F2", kind="context", text="CHANGED", source="u2")
    _, hits2 = compose_capture_brief_sectioned(
        pack2, engine=engine, client="Acme Federal", scope="all")
    assert hits2["boards"] is True
    assert hits2["news"] is False and hits2["narrative"] is False


def test_step_cmd_passes_compose_split():
    flask = pytest.importorskip("flask")  # noqa: F841
    import ui.server as srv
    cmd = srv._step_cmd("report", "Acme Federal",
                        {"kind": "capture_brief", "compose_split": True})
    assert cmd[-1] == "--compose-split"
    cmd2 = srv._step_cmd("report", "Acme Federal", {"kind": "capture_brief"})
    assert "--compose-split" not in cmd2


def test_full_picture_review_is_cached_on_its_inputs(tmp_path, monkeypatch):
    """Press B lesson: the review is LLM output; uncached it varies per press
    and busts every downstream section key. Same evidence bundle -> one
    compose, then hits."""
    monkeypatch.setenv("LILA_COMPOSE_CACHE_DIR", str(tmp_path))
    from agents.reports.capture_brief import (
        FullPictureReview, cached_full_picture_review,
    )
    calls = []

    class _Eng:
        def deliberate(self, *, layer, system_prompt, context, schema, now=None):
            calls.append(layer)
            stub = {name: ([] if "list" in str(f.annotation).lower()
                           else "x")
                    for name, f in schema.model_fields.items()}
            # tolerate required non-list fields generically
            return schema.model_validate(stub)

    everything = {"sweep": {"n": 619}, "picture": "text"}
    r1, hit1 = cached_full_picture_review(
        "Acme Federal", scope="agency_dhs", everything=everything,
        engine=_Eng())
    r2, hit2 = cached_full_picture_review(
        "Acme Federal", scope="agency_dhs", everything=everything,
        engine=_Eng())
    assert (hit1, hit2) == (False, True) and len(calls) == 1
    assert isinstance(r1, FullPictureReview) and r2.model_dump() == r1.model_dump()
    # changed evidence recomposes
    _, hit3 = cached_full_picture_review(
        "Acme Federal", scope="agency_dhs",
        everything={"sweep": {"n": 620}, "picture": "text"}, engine=_Eng())
    assert hit3 is False and len(calls) == 2
