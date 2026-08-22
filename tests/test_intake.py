"""Step-1 tests: scraper parsing, profiling layer, approval gate, web search.

Offline — fixtures + fake Claude client + a temp review dir. No keys, no network.
Run via pytest OR `python3 tests/test_intake.py`.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.review as review  # noqa: E402
from agents.decisions import schemas as ds  # noqa: E402
from agents.decisions.engine import DecisionEngine  # noqa: E402
from agents.decisions.intake import build_strategy  # noqa: E402
from agents.schemas import IntakeSubmission  # noqa: E402
from tools.api.web_search import WebSearchSource  # noqa: E402
from tools.api.base import SourceQuery  # noqa: E402
from tools.scrape.site import _extract, scrape_site  # noqa: E402


SUBMISSION = IntakeSubmission(
    client_name="Acme Federal Solutions LLC",
    website="https://example.com",
    primary_services="Cloud migration and DevSecOps for federal agencies.",
    certifications=["SDVOSB"],
    known_naics=["541512"],
    target_agencies=["Department of Veterans Affairs"],
)


# ---- Scraper text/link extraction (no network) ----------------------------- #
def test_extract_strips_scripts_and_keeps_text():
    html = """<html><head><style>x{}</style></head><body>
      <h1>Capabilities</h1><script>var a=1;</script>
      <p>Cloud migration and DevSecOps.</p>
      <a href="/about">About</a><a href="https://other.com/x">ext</a>
    </body></html>"""
    text, links = _extract(html)
    assert "Cloud migration and DevSecOps." in text
    assert "var a=1" not in text  # script content dropped
    assert "Capabilities" in text
    assert "/about" in links


def test_scrape_site_handles_unreachable_host():
    # invalid TLD -> _fetch returns None -> empty bundle, no crash
    bundle = scrape_site("https://nonexistent.invalid", max_pages=2)
    assert bundle.pages == []
    assert bundle.sources == []


# ---- Profiling layer (fake client) ----------------------------------------- #
def _strategy_obj():
    return ds.IntakeStrategy(
        client_name="Acme Federal Solutions LLC",
        pursuit_strategy="Lead with SDVOSB set-aside on VA cloud work.",
        keywords=[
            ds.Keyword(term="cloud migration", category=ds.KeywordCategory.CAPABILITY,
                       rationale="core service"),
            ds.Keyword(term="541512", category=ds.KeywordCategory.NAICS, rationale="maps to IT svcs"),
        ],
        inferred_naics=["541512", "541519"],
        naics_meta=[
            ds.NaicsEntry(
                code="541512", title="Computer Systems Design Services",
                role="core", origin="system",
                rationale="Acme's cloud migration work maps to systems design."),
            ds.NaicsEntry(
                code="541519", title="Other Computer Related Services",
                role="boundary", origin="system",
                rationale="Acme's adjacent DevSecOps support fits this broader lane."),
        ],
        target_agencies=["Department of Veterans Affairs"],
        set_aside_angles=["SDVOSB"],
        searches=[
            ds.SearchSpec(source="sam.gov", query_terms=["cloud migration"],
                          naics_codes=["541512"], set_asides=["SDVOSB"], rationale="primary"),
            ds.SearchSpec(source="usaspending.gov", naics_codes=["541512"], rationale="incumbents"),
            ds.SearchSpec(source="web", query_terms=["VA cloud forecast"], rationale="forecasts"),
        ],
        confidence=0.8,
        sources_reviewed=["https://example.com"],
        review_gate="Approve keywords + the 3 searches before launch.",
    )


class _FakeParseMessages:
    def __init__(self, rec, obj):
        self.rec, self._obj = rec, obj

    def parse(self, **kwargs):
        self.rec["kwargs"] = kwargs
        return type("R", (), {"parsed_output": self._obj})()


class FakeParseClient:
    def __init__(self, obj):
        self.rec = {}
        self.messages = _FakeParseMessages(self.rec, obj)


def test_build_strategy_feeds_form_and_scrape_and_returns_3_searches():
    client = FakeParseClient(_strategy_obj())
    eng = DecisionEngine(client=client)
    strategy = build_strategy(SUBMISSION, scrape=None, engine=eng)

    user_msg = client.rec["kwargs"]["messages"][0]["content"]
    assert "DevSecOps" in user_msg          # form free-text reached the model
    sys_prompt = client.rec["kwargs"]["system"]
    assert "AT MINIMUM" in sys_prompt        # 3-search requirement in the prompt
    assert "exactly one record for every inferred_naics code" in sys_prompt

    sources = {s.source for s in strategy.searches}
    assert {"sam.gov", "usaspending.gov", "web"} <= sources
    assert strategy.requires_human_review is True
    assert [entry.code for entry in strategy.naics_meta] == strategy.inferred_naics
    assert all(entry.rationale for entry in strategy.naics_meta)


def test_new_intake_rejects_an_unexplained_boundary_code():
    incomplete = _strategy_obj().model_copy(update={"naics_meta": []})

    class IncompleteEngine:
        def deliberate(self, **_kwargs):
            return incomplete

    with pytest.raises(ValueError, match="one ordered naics_meta entry"):
        build_strategy(SUBMISSION, scrape=None, engine=IncompleteEngine())

    shallow = _strategy_obj().model_copy(deep=True)
    shallow.naics_meta[0].rationale = "system inferred boundary code"

    class ShallowEngine:
        def deliberate(self, **_kwargs):
            return shallow

    with pytest.raises(ValueError, match="explain every inferred NAICS code: 541512"):
        build_strategy(SUBMISSION, scrape=None, engine=ShallowEngine())


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"inferred_naics": [], "naics_meta": []}, "at least one NAICS code"),
        ({"inferred_naics": ["541512", "541512"],
          "naics_meta": [_strategy_obj().naics_meta[0], _strategy_obj().naics_meta[0]]},
         "must not duplicate"),
        ({"kept_out_naics": [_strategy_obj().naics_meta[0]]},
         "cannot invent operator-kept-out NAICS"),
        ({"kept_out": [_strategy_obj().keywords[0]]},
         "cannot invent operator-kept-out keywords"),
        ({"sources_reviewed": []}, "must identify the sources"),
        ({"sources_reviewed": ["   "]}, "must identify the sources"),
        ({"requires_human_review": False}, "must remain subject to human review"),
    ],
)
def test_new_intake_rejects_invalid_generated_state(update, message):
    generated = _strategy_obj().model_copy(update=update)

    class InvalidEngine:
        def deliberate(self, **_kwargs):
            return generated

    with pytest.raises(ValueError, match=message):
        build_strategy(SUBMISSION, scrape=None, engine=InvalidEngine())


def test_new_intake_rejects_reused_boilerplate_explanations():
    duplicated = _strategy_obj().model_copy(deep=True)
    duplicated.naics_meta[1].rationale = duplicated.naics_meta[0].rationale

    class DuplicatedEngine:
        def deliberate(self, **_kwargs):
            return duplicated

    with pytest.raises(ValueError, match="distinct explanation for each"):
        build_strategy(SUBMISSION, scrape=None, engine=DuplicatedEngine())


def test_new_intake_rejects_operator_note_and_known_code_omission():
    noted = _strategy_obj().model_copy(deep=True)
    noted.naics_meta[0].note = "model-authored note"

    class NotedEngine:
        def deliberate(self, **_kwargs):
            return noted

    with pytest.raises(ValueError, match="cannot invent operator notes"):
        build_strategy(SUBMISSION, scrape=None, engine=NotedEngine())

    omitted = _strategy_obj().model_copy(update={
        "inferred_naics": ["541519"],
        "naics_meta": [_strategy_obj().naics_meta[1]],
    })

    class OmittedEngine:
        def deliberate(self, **_kwargs):
            return omitted

    with pytest.raises(ValueError, match="omitted form-provided NAICS codes: 541512"):
        build_strategy(SUBMISSION, scrape=None, engine=OmittedEngine())


@pytest.mark.parametrize("history", [
    {"origin": "consultant"},
    {"origin": "edited", "edited_from": "old term"},
    {"note": "operator accepted this"},
])
def test_new_intake_rejects_model_authored_keyword_history(history):
    generated = _strategy_obj().model_copy(deep=True)
    for field, value in history.items():
        setattr(generated.keywords[0], field, value)

    class HistoryEngine:
        def deliberate(self, **_kwargs):
            return generated

    with pytest.raises(ValueError, match="cannot invent operator keyword history"):
        build_strategy(SUBMISSION, scrape=None, engine=HistoryEngine())


# ---- Approval gate (temp dir) ---------------------------------------------- #
def test_review_gate_blocks_until_approved():
    with tempfile.TemporaryDirectory() as tmp:
        review.REVIEW_DIR = tmp  # redirect persistence
        strat = _strategy_obj()

        alerts = []
        review.request_approval(strat, alert_fn=lambda p, m: alerts.append(p.client_name))
        assert alerts == ["Acme Federal Solutions LLC"]  # alerted

        # Searches blocked while pending.
        try:
            review.load_approved("Acme Federal Solutions LLC")
            raise AssertionError("expected PermissionError while pending")
        except PermissionError:
            pass

        # Approve -> now loadable.
        review.decide("Acme Federal Solutions LLC", approve=True)
        approved = review.load_approved("Acme Federal Solutions LLC")
        assert approved.client_name == "Acme Federal Solutions LLC"


def test_review_gate_blocks_after_rejection():
    with tempfile.TemporaryDirectory() as tmp:
        review.REVIEW_DIR = tmp
        review.request_approval(_strategy_obj(), alert_fn=lambda p, m: None)
        review.decide("Acme Federal Solutions LLC", approve=False, note="redo")
        try:
            review.load_approved("Acme Federal Solutions LLC")
            raise AssertionError("rejected strategy must stay blocked")
        except PermissionError:
            pass


# ---- Web search source (fake client: web_research + structure) ------------- #
class _FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeWebMessages:
    def parse(self, **kwargs):
        leads = ds.WebLeadList(leads=[
            ds.WebLead(title="VA Cloud Forecast FY25", url="https://va.gov/forecast",
                       summary="Upcoming cloud recompete", signal_type="forecast",
                       relevance=ds.WebRelevance.HIGH),
        ])
        return type("R", (), {"parsed_output": leads})()

    def create(self, **kwargs):
        result_block = _FakeBlock(
            type="web_search_tool_result",
            content=[_FakeBlock(url="https://va.gov/forecast", title="VA Cloud Forecast")],
        )
        text_block = _FakeBlock(type="text", text="Found a VA cloud forecast.")
        return type("R", (), {"content": [text_block, result_block], "stop_reason": "end_turn"})()


class FakeWebClient:
    def __init__(self):
        self.messages = _FakeWebMessages()


def test_desktop_alert_invokes_notifier_and_banner(capsys):
    import sys as _sys
    from agents import alerts

    calls = []

    def fake_runner(cmd, **kw):
        calls.append(cmd)
        return type("P", (), {"returncode": 0})()

    packet = review.ReviewPacket(client_name="Acme", strategy=_strategy_obj())
    alerts.desktop_alert(packet, "/tmp/x.md", runner=fake_runner)

    if _sys.platform == "darwin":
        assert calls and calls[0][0] == "osascript"
        assert any("display notification" in part for part in calls[0])
        assert any("Analyst Layer pending" in part for part in calls[0])
    assert "Analyst Layer pending your approval" in capsys.readouterr().out
    # banner always prints (carries approve/reject commands); non-darwin works


def test_web_search_source_returns_raw_opportunities():
    eng = DecisionEngine(client=FakeWebClient())
    src = WebSearchSource(engine=eng)
    out = src.search(SourceQuery(keywords=["VA cloud forecast"], naics_codes=["541512"]))
    assert len(out) == 1
    assert out[0].source == "web"
    assert out[0].source_id.startswith("web-")
    assert str(out[0].api_url) == "https://va.gov/forecast"


def test_web_search_pause_turn_exhaustion_is_not_a_screened_zero():
    class PausedMessages:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            return type(
                "R", (), {"content": [], "stop_reason": "pause_turn"}
            )()

    client = type("PausedClient", (), {})()
    client.messages = PausedMessages()
    source = WebSearchSource(engine=DecisionEngine(client=client))

    with pytest.raises(RuntimeError, match="terminal response"):
        source.search(SourceQuery(keywords=["cloud"], limit=15))
    assert client.messages.calls == 6


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)


# ── near-miss candidate tray (2026-07-09 client-flow refinement) ──

def test_intake_strategy_carries_near_misses():
    from agents.decisions.schemas import IntakeStrategy, TermCandidate
    s = IntakeStrategy(
        client_name="Acme", pursuit_strategy="p", confidence=0.9,
        review_gate="approve terms",
        near_misses=[TermCandidate(value="541330", kind="naics",
                                   reason="engineering-adjacent; cut: no PE work shown",
                                   confidence=0.55)])
    dumped = s.model_dump(mode="json")
    assert dumped["near_misses"][0]["value"] == "541330"
    # roundtrip survives packet persistence
    s2 = IntakeStrategy.model_validate(dumped)
    assert s2.near_misses[0].kind == "naics"


def test_review_candidates_is_additive_and_dedupes_locked_terms():
    from agents.decisions.intake import CandidateReview, review_candidates
    from agents.decisions.schemas import (
        IntakeStrategy, Keyword, KeywordCategory, TermCandidate,
    )
    locked = IntakeStrategy(
        client_name="Acme", pursuit_strategy="p", confidence=0.9,
        review_gate="g", inferred_naics=["541990"],
        keywords=[Keyword(term="aviation risk", category=KeywordCategory.CAPABILITY,
                          rationale="r")])

    class FakeEngine:
        def deliberate(self, *, layer, system_prompt, context, schema):
            assert layer == "intake-candidates"
            assert context["locked_strategy"]["client_name"] == "Acme"
            return CandidateReview(client_name="Acme", near_misses=[
                TermCandidate(value="541990", kind="naics", reason="dup",
                              confidence=0.9),           # already locked -> dropped
                TermCandidate(value="Aviation Risk", kind="keyword",
                              reason="dup case", confidence=0.9),  # locked (case) -> dropped
                TermCandidate(value="488190", kind="naics",
                              reason="airport support svcs; cut: thin evidence",
                              confidence=0.6),
            ])

    tray = review_candidates(locked, engine=FakeEngine())
    assert [c.value for c in tray] == ["488190"]         # locked terms never resurface


def test_amend_terms_keeps_approval_and_validates(tmp_path, monkeypatch):
    """The iterate-loop carve-out: keyword/NAICS tuning on an APPROVED
    strategy keeps the approval (revise() would 409), records a revision,
    and still validates through the schema."""
    import agents.review as review_mod
    from agents.decisions.schemas import IntakeStrategy
    from agents.review import ReviewPacket, ReviewStatus, amend_terms, load_packet
    monkeypatch.setattr(review_mod, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(review_mod, "_path",
                        lambda c: str(tmp_path / f"{c.lower()}.review.json"))
    s = IntakeStrategy(client_name="acme", pursuit_strategy="p",
                       confidence=0.9, review_gate="g",
                       inferred_naics=["541990"])
    p = ReviewPacket(client_name="acme", strategy=s,
                     status=ReviewStatus.APPROVED)
    open(review_mod._path("acme"), "w").write(p.model_dump_json())

    out = amend_terms("acme",
                      keywords=[{"term": "aviation threat intelligence",
                                 "category": "capability"}],
                      inferred_naics=["541990", "488190"])
    assert out.status == ReviewStatus.APPROVED          # approval INTACT
    assert out.revision_count == 1
    loaded = load_packet("acme")
    assert [k.term for k in loaded.strategy.keywords] == ["aviation threat intelligence"]
    assert loaded.strategy.keywords[0].rationale        # backfilled, never empty
    assert loaded.strategy.inferred_naics == ["541990", "488190"]
    # no-op never stamps a revision
    assert amend_terms("acme").revision_count == 1


def test_judgment_journal_accumulates_across_mutations(tmp_path, monkeypatch):
    """GTM Playbook stage 1: strategy v0 + every gate edit, term amendment,
    scope change, and decision land in the append-only journal so the debrief
    pass can diff what the machine thought against what the human taught."""
    import json
    import agents.review as review_mod
    from agents.decisions.schemas import IntakeStrategy
    from agents.review import (
        amend_terms, decide, request_approval, revise, set_scope,
    )
    monkeypatch.setattr(review_mod, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(review_mod, "_path",
                        lambda c: str(tmp_path / f"{c.lower()}.review.json"))
    s = IntakeStrategy(client_name="acme", pursuit_strategy="p",
                       confidence=0.9, review_gate="g",
                       inferred_naics=["541990"])
    request_approval(s, alert_fn=lambda p, m: None)
    revise("acme", {"inferred_naics": ["541990", "518210"]})
    set_scope("acme", {"agencies": ["CBP"]})
    decide("acme", approve=True, note="ship it")
    amend_terms("acme", inferred_naics=["541990", "518210", "488190"])

    lines = [json.loads(x) for x in
             open(tmp_path / "acme.journal.jsonl").read().splitlines()]
    events = [x["event"] for x in lines]
    assert events == ["inference_v0", "gate_revision", "scope_change",
                      "decision", "term_amendment"]
    v0 = lines[0]
    assert v0["state"]["inferred_naics"] == ["541990"]      # the machine's read
    rev = lines[1]
    assert rev["before"]["inferred_naics"] == ["541990"]    # diffable
    assert rev["after"]["inferred_naics"] == ["541990", "518210"]
    assert lines[2]["after"] == {"agencies": ["CBP"]}
    assert lines[3]["approved"] is True and lines[3]["note"] == "ship it"
    assert lines[4]["after"]["inferred_naics"][-1] == "488190"
    assert all("at" in x for x in lines)                    # timestamped


# ---- Research entities (GOLDEN_BUILD Phase 1, 2026-07-24) ------------------- #
def _entities():
    return [
        ds.ResearchEntity(name="AcmeFlow", kind="product",
                          rationale="named on the client's own product page",
                          source="https://example.com/products"),
        ds.ResearchEntity(name="RivalWorks", kind="competitor",
                          rationale="cited head-to-head in the market research"),
        ds.ResearchEntity(name="ChannelCo", kind="reseller",
                          rationale="listed on the client's partner page"),
    ]


def test_build_strategy_emits_research_entities_alongside_keywords():
    """GOLDEN_BUILD Phase 1: the profiling layer outputs the proper names
    (products/competitors/resellers) that drive entity-award retrieval,
    alongside keywords/NAICS. Prompt carries the instruction; output rides
    the strategy object round-trip."""
    obj = _strategy_obj().model_copy(update={"research_entities": _entities()})
    client = FakeParseClient(obj)
    strategy = build_strategy(SUBMISSION, scrape=None, engine=DecisionEngine(client=client))

    sys_prompt = client.rec["kwargs"]["system"]
    assert "research_entities" in sys_prompt
    assert "kind='product'" in sys_prompt          # all three kinds instructed
    assert "kind='competitor'" in sys_prompt
    assert "kind='reseller'" in sys_prompt
    got = {(e.kind, e.name) for e in strategy.research_entities}
    assert got == {("product", "AcmeFlow"), ("competitor", "RivalWorks"),
                   ("reseller", "ChannelCo")}
    assert all(e.rationale for e in strategy.research_entities)


def test_new_intake_rejects_blank_and_duplicate_research_entities():
    """New generations must ground every surfaced name; blanks and repeats
    fail loudly instead of feeding garbage queries to the entity lane."""
    blank = _strategy_obj().model_copy(update={"research_entities": [
        ds.ResearchEntity(name="AcmeFlow", kind="product", rationale="   ")]})

    class BlankEngine:
        def deliberate(self, **_kwargs):
            return blank

    with pytest.raises(ValueError, match="grounded"):
        build_strategy(SUBMISSION, scrape=None, engine=BlankEngine())

    dupe = _strategy_obj().model_copy(update={"research_entities": [
        ds.ResearchEntity(name="AcmeFlow", kind="product", rationale="page"),
        ds.ResearchEntity(name="acmeflow ", kind="product", rationale="again")]})

    class DupeEngine:
        def deliberate(self, **_kwargs):
            return dupe

    with pytest.raises(ValueError, match="repeat"):
        build_strategy(SUBMISSION, scrape=None, engine=DupeEngine())


def test_legacy_packet_without_research_entities_loads_empty():
    """Additive-field doctrine: packets written before the field existed
    load unchanged with an empty entity list."""
    dump = _strategy_obj().model_dump(mode="json")
    dump.pop("research_entities", None)
    loaded = ds.IntakeStrategy.model_validate(dump)
    assert loaded.research_entities == []


def test_research_entities_survive_gate_revision(tmp_path, monkeypatch):
    """The gate merge touches EDITABLE_FIELDS only; surfaced entities are the
    model's own read and must ride every operator edit untouched."""
    import agents.review as review_mod
    from agents.review import load_packet, request_approval, revise
    monkeypatch.setattr(review_mod, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(review_mod, "_path",
                        lambda c: str(tmp_path / f"{c.lower()}.review.json"))
    s = _strategy_obj().model_copy(update={"research_entities": _entities()})
    request_approval(s, alert_fn=lambda p, m: None)
    revise(s.client_name, {"pursuit_strategy": "sharpened by the operator"})
    loaded = load_packet(s.client_name)
    assert loaded.strategy.pursuit_strategy == "sharpened by the operator"
    assert [(e.kind, e.name) for e in loaded.strategy.research_entities] == [
        ("product", "AcmeFlow"), ("competitor", "RivalWorks"),
        ("reseller", "ChannelCo")]
