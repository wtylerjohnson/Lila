"""Strategy revision at the approval gate: hand-edit fields, re-validate,
reconcile the derived search plan, and the state transitions. Offline."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.review as review  # noqa: E402
from agents.decisions.schemas import IntakeStrategy, Keyword, SearchSpec  # noqa: E402
from agents.review import (  # noqa: E402
    RevisionError, ReviewStatus, decide, load_packet, request_approval, revise,
)


def _strategy():
    return IntakeStrategy(
        client_name="Testco",
        pursuit_strategy="Original narrative.",
        keywords=[Keyword(term="threat intel", category="capability", rationale="core"),
                  Keyword(term="soc", category="technology", rationale="stack")],
        inferred_naics=["541512", "513210"],
        target_agencies=["Department of Defense"],
        set_aside_angles=[],
        searches=[
            SearchSpec(source="sam.gov", query_terms=["threat intel"],
                       naics_codes=["541512", "513210"], set_asides=[], rationale="sam"),
            SearchSpec(source="usaspending.gov", query_terms=[],
                       naics_codes=["541512", "513210"], set_asides=[], rationale="usa"),
            SearchSpec(source="web", query_terms=["threat intel", "soc"],
                       naics_codes=[], set_asides=[], rationale="web"),
        ],
        confidence=0.8, review_gate="approve keywords + NAICS")


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
    request_approval(_strategy(), alert_fn=lambda *_a: None)  # writes pending packet + .md
    return str(tmp_path)


def test_revise_edits_revalidates_and_stamps(seeded):
    packet = revise("Testco", {
        "pursuit_strategy": "  Revised narrative.  ",
        "inferred_naics": ["541512", "541519", "541512"],   # add one, dedupe
        "target_agencies": ["Department of Defense", "Department of Homeland Security"],
        "keywords": [{"term": "threat intel", "category": "capability", "rationale": "core"},
                     {"term": "attack surface", "category": "technology", "rationale": ""}],
    })
    assert packet.revision_count == 1 and packet.revised_at is not None
    assert packet.status == ReviewStatus.PENDING
    s = packet.strategy
    assert s.pursuit_strategy == "Revised narrative."          # trimmed
    assert s.inferred_naics == ["541512", "541519"]            # deduped, order kept
    assert [k.term for k in s.keywords] == ["threat intel", "attack surface"]
    # a blank rationale on a hand-added keyword gets honest provenance
    assert s.keywords[1].rationale == "added at review by the operator"
    # persisted, and the human-readable .md regenerated to match
    reloaded = load_packet("Testco")
    assert reloaded.strategy.pursuit_strategy == "Revised narrative."
    md = open(os.path.join(review.REVIEW_DIR, "testco.review.md")).read()
    assert "541519" in md and "attack surface" in md
    assert md.startswith("# Analyst Layer: pursuit strategy for Testco")
    assert 'approve.py "Testco" --approve' in md


def test_naics_edit_reconciles_into_the_mirroring_specs(seeded):
    """The verified drift bug: sam.gov/usaspending specs pin the NAICS, so an
    inferred_naics edit must propagate there or it silently would not run."""
    packet = revise("Testco", {"inferred_naics": ["999999"]})
    by_src = {sp.source: sp for sp in packet.strategy.searches}
    assert by_src["sam.gov"].naics_codes == ["999999"]        # mirrored spec updated
    assert by_src["usaspending.gov"].naics_codes == ["999999"]
    assert by_src["web"].naics_codes == []                    # web carried none, untouched


def test_set_aside_edit_reconciles_non_web_specs(seeded):
    packet = revise("Testco", {"set_aside_angles": ["Total Small Business"]})
    by_src = {sp.source: sp for sp in packet.strategy.searches}
    assert by_src["sam.gov"].set_asides == ["Total Small Business"]
    assert by_src["web"].set_asides == []                     # web never filters set-asides


def test_bad_keyword_category_rejected_loudly_and_nothing_persists(seeded):
    before = open(review._path("Testco")).read()
    with pytest.raises(RevisionError, match="schema"):
        revise("Testco", {"keywords": [{"term": "x", "category": "not_a_category"}]})
    assert open(review._path("Testco")).read() == before      # untouched on disk


def test_only_editable_fields_apply(seeded):
    packet = revise("Testco", {"confidence": 0.1, "review_gate": "hacked",
                               "pursuit_strategy": "just this"})
    assert packet.strategy.pursuit_strategy == "just this"
    assert packet.strategy.confidence == 0.8                  # ignored, not overwritten
    assert packet.strategy.review_gate == "approve keywords + NAICS"


def test_no_op_revision_is_not_stamped(seeded):
    packet = revise("Testco", {})
    assert packet.revision_count == 0 and packet.revised_at is None


def test_approved_strategy_cannot_be_revised(seeded):
    decide("Testco", approve=True)
    with pytest.raises(RevisionError, match="already approved"):
        revise("Testco", {"pursuit_strategy": "too late"})


def test_revising_a_rejected_strategy_reopens_it_pending(seeded):
    decide("Testco", approve=False, note="add attack-surface keyword")
    assert load_packet("Testco").status == ReviewStatus.REJECTED
    packet = revise("Testco", {"keywords": [{"term": "attack surface",
                                             "category": "technology"}]})
    assert packet.status == ReviewStatus.PENDING              # revise = fresh proposal
    assert packet.decided_at is None and packet.reviewer_note == ""


# ── endpoint ──────────────────────────────────────────────────────────────────
def _client(tmp_path, monkeypatch):
    import ui.server as srv
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(srv, "REVIEW_DIR", str(tmp_path))
    request_approval(_strategy(), alert_fn=lambda *_a: None)
    return srv.app.test_client()


def test_endpoint_revises_and_surfaces_errors(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/strategy/revise", json={"client_name": "Testco",
        "updates": {"inferred_naics": ["541512", "888888"]}})
    assert r.status_code == 200
    body = r.get_json()
    assert body["revision_count"] == 1
    assert "888888" in body["strategy"]["inferred_naics"]

    bad = client.post("/api/strategy/revise", json={"client_name": "Testco",
        "updates": {"keywords": [{"term": "x", "category": "bogus"}]}})
    assert bad.status_code == 400 and "schema" in bad.get_json()["error"]


def test_endpoint_409_on_approved_and_gate_detail_exposes_strategy(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    import ui.server as srv
    # gate detail carries the structured, editable strategy for the inline editor
    steps = srv.build_steps("testco")
    gate = next(s for s in steps if s["key"] == "approve")
    assert gate["detail"]["editable"] is True
    assert gate["detail"]["strategy"]["inferred_naics"] == ["541512", "513210"]

    decide("Testco", approve=True)
    r = client.post("/api/strategy/revise", json={"client_name": "Testco",
        "updates": {"pursuit_strategy": "too late"}})
    assert r.status_code == 409
    steps2 = srv.build_steps("testco")
    gate2 = next(s for s in steps2 if s["key"] == "approve")
    assert gate2["detail"]["editable"] is False               # no editing once approved


# ── Keyword doctrine: approved capability/technology/search terms are the
# SAM.gov opportunity text screen. Agency/set-aside/NAICS terms remain lenses;
# usaspending (vendor vocabulary) and web (credential probes) keep their
# curated terms.

def test_keyword_edit_now_reaches_the_sam_spec(seeded):
    packet = revise("Testco", {
        "keywords": [{"term": "packet capture", "category": "capability"},
                     {"term": "ddos mitigation", "category": "capability"}],
    })
    by_src = {sp.source: sp for sp in packet.strategy.searches}
    assert by_src["sam.gov"].query_terms == ["packet capture", "ddos mitigation"]
    # curated lanes stay put: award vocabulary and web probes are not keywords
    assert by_src["usaspending.gov"].query_terms == []
    assert by_src["web"].query_terms == ["threat intel", "soc"]


def test_procurement_lens_only_edit_clears_sam_text_terms(seeded):
    packet = revise("Testco", {
        "keywords": [
            {"term": "Department of Defense", "category": "agency"},
            {"term": "HUBZone", "category": "set_aside"},
            {"term": "541512", "category": "naics"},
        ],
    })
    sam = next(spec for spec in packet.strategy.searches
               if spec.source == "sam.gov")
    assert sam.query_terms == []


def test_effective_query_terms_is_the_search_time_guarantee():
    """A stale packet whose sam spec predates the keyword edit can no longer
    override the operator: the seam returns the keywords for sam.gov and the
    curated terms everywhere else."""
    from agents.review import effective_query_terms
    strategy = _strategy()  # keywords: threat intel, soc
    stale_sam = strategy.searches[0]
    assert stale_sam.query_terms == ["threat intel"]  # pre-edit curation
    assert effective_query_terms(strategy, stale_sam) == ["threat intel", "soc"]
    usa = strategy.searches[1]
    web = strategy.searches[2]
    assert effective_query_terms(strategy, usa) == ["threat intel", "soc"]  # empty spec falls back
    assert effective_query_terms(strategy, web) == ["threat intel", "soc"]
    assert effective_query_terms(strategy, None) == ["threat intel", "soc"]
    assert effective_query_terms(
        strategy, None, source="sam.gov") == ["threat intel", "soc"]


def test_sam_terms_exclude_procurement_lenses_but_keep_capabilities():
    from agents.review import effective_query_terms
    strategy = _strategy().model_copy(update={"keywords": [
        Keyword(term="packet capture", category="capability", rationale="core"),
        Keyword(term="Aternity", category="technology", rationale="product"),
        Keyword(term="network visibility", category="search_term", rationale="query"),
        Keyword(term="Department of Defense", category="agency", rationale="lens"),
        Keyword(term="HUBZone", category="set_aside", rationale="lens"),
        Keyword(term="541512", category="naics", rationale="lens"),
    ]})
    sam = SearchSpec(source="sam.gov", query_terms=["stale agency term"],
                     naics_codes=[], set_asides=[], rationale="sam")
    assert effective_query_terms(strategy, sam) == [
        "packet capture", "Aternity", "network visibility"]


def test_effective_query_terms_keeps_curated_nonempty_lanes():
    from agents.review import effective_query_terms
    strategy = _strategy()
    vendor_spec = SearchSpec(source="usaspending.gov",
                             query_terms=["NETSCOUT", "Carahsoft"],
                             naics_codes=[], set_asides=[], rationale="vendor")
    assert effective_query_terms(strategy, vendor_spec) == [
        "NETSCOUT", "Carahsoft"]
    web_spec = SearchSpec(source="web",
                          query_terms=["NETSCOUT CMMC certification status"],
                          naics_codes=[], set_asides=[], rationale="probe")
    assert effective_query_terms(strategy, web_spec) == [
        "NETSCOUT CMMC certification status"]


def test_keywordless_strategy_falls_back_to_the_sam_spec():
    from agents.review import effective_query_terms
    strategy = _strategy().model_copy(update={"keywords": []})
    sam = strategy.searches[0]
    assert effective_query_terms(strategy, sam) == ["threat intel"]


# ── Keyword Strategy Workshop (2026-07-12): three lanes, provenance, kept-out.
# The workshop rides the existing revise() contract; these pin the additive
# persistence the UI depends on and prove the approval/contract shape is
# unchanged. ──

def _workshop_strategy():
    return IntakeStrategy(
        client_name="Testco",
        pursuit_strategy="Narrative.",
        keywords=[Keyword(term="network observability", category="capability",
                          rationale="core capability", source="https://vendor")],
        inferred_naics=["541512"], target_agencies=[], set_aside_angles=[],
        searches=[
            SearchSpec(source="sam.gov", query_terms=["network observability"],
                       naics_codes=["541512"], set_asides=[], rationale="sam"),
            SearchSpec(source="usaspending.gov", query_terms=["NETSCOUT"],
                       naics_codes=["541512"], set_asides=[], rationale="usa"),
            SearchSpec(source="web", query_terms=["x"], naics_codes=[],
                       set_asides=[], rationale="web"),
        ],
        confidence=0.8, review_gate="approve keywords + NAICS")


@pytest.fixture
def workshop(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "REVIEW_DIR", str(tmp_path))
    request_approval(_workshop_strategy(), alert_fn=lambda *_a: None)
    return str(tmp_path)


def test_existing_terms_load_without_loss(workshop):
    s = load_packet("Testco").strategy
    assert [k.term for k in s.keywords] == ["network observability"]
    assert s.keywords[0].source == "https://vendor"        # provenance preserved
    assert s.keywords[0].origin == "system"                # default applied


def test_edit_keeps_term_approved_and_records_prior_wording(workshop):
    packet = revise("Testco", {"keywords": [
        {"term": "network visibility", "category": "capability",
         "rationale": "core", "origin": "edited",
         "edited_from": "network observability", "source": "https://vendor"}]})
    k = packet.strategy.keywords[0]
    assert k.term == "network visibility" and k.origin == "edited"
    assert k.edited_from == "network observability"
    # still an in-search keyword; the sam spec mirrors the edited term
    sam = next(s for s in packet.strategy.searches if s.source == "sam.gov")
    assert sam.query_terms == ["network visibility"]


def test_move_out_preserves_history_and_restores(workshop):
    # × moves a term to kept_out (not deleted); the record survives intact
    moved = revise("Testco", {
        "keywords": [],
        "kept_out": [{"term": "network observability", "category": "capability",
                      "rationale": "core capability", "origin": "system",
                      "source": "https://vendor",
                      "note": "moved out of the search at review"}]})
    assert [k.term for k in moved.strategy.keywords] == []
    assert [k.term for k in moved.strategy.kept_out] == ["network observability"]
    assert moved.strategy.kept_out[0].source == "https://vendor"  # provenance kept
    # restore: back to keywords, empty kept_out
    restored = revise("Testco", {
        "keywords": [{"term": "network observability", "category": "capability",
                      "rationale": "core capability", "origin": "system",
                      "source": "https://vendor"}],
        "kept_out": []})
    assert [k.term for k in restored.strategy.keywords] == ["network observability"]
    assert restored.strategy.kept_out == []


def test_borderline_promotion_persists_with_provenance(workshop):
    packet = revise("Testco", {"keywords": [
        {"term": "network observability", "category": "capability", "rationale": "core"},
        {"term": "zero trust visibility", "category": "technology",
         "rationale": "promoted from the judgment queue", "origin": "system",
         "note": "promoted from needs judgment at review"}]})
    terms = {k.term: k for k in packet.strategy.keywords}
    assert "zero trust visibility" in terms
    assert terms["zero trust visibility"].note.startswith("promoted from")


def test_consultant_added_term_keeps_authorship(workshop):
    packet = revise("Testco", {"keywords": [
        {"term": "network observability", "category": "capability", "rationale": "core"},
        {"term": "managed detection and response", "category": "capability",
         "rationale": "consultant added at review", "origin": "consultant",
         "note": "consultant added"}]})
    added = next(k for k in packet.strategy.keywords
                if k.term == "managed detection and response")
    assert added.origin == "consultant" and added.note == "consultant added"


def test_kept_out_is_whitelisted_but_approval_shape_is_unchanged(workshop):
    # kept_out flows through the editable whitelist; the approval binding /
    # POST-run contract is untouched by keyword-workshop edits
    assert "kept_out" in review.EDITABLE_FIELDS
    packet = revise("Testco", {
        "kept_out": [{"term": "firewall", "category": "search_term",
                      "rationale": "too broad", "origin": "system"}]})
    # revising leaves the packet PENDING (never auto-approves) and touches no
    # scope/approval field
    assert packet.status == ReviewStatus.PENDING
    dumped = packet.strategy.model_dump(mode="json")
    assert dumped["kept_out"][0]["term"] == "firewall"
    # legacy structural fields intact
    assert dumped["inferred_naics"] == ["541512"]


def test_legacy_packet_without_kept_out_still_loads(workshop):
    # simulate a pre-workshop packet: strip kept_out from disk, ensure it loads
    import json
    path = review._path("Testco")
    raw = json.loads(open(path).read())
    raw["strategy"].pop("kept_out", None)
    for k in raw["strategy"]["keywords"]:
        for f in ("origin", "edited_from", "source", "note"):
            k.pop(f, None)
    open(path, "w").write(json.dumps(raw))
    s = load_packet("Testco").strategy       # defaults fill; no crash
    assert s.kept_out == []
    assert s.keywords[0].origin == "system"


def test_duplicate_blank_terms_are_dropped_not_persisted(workshop):
    packet = revise("Testco", {"keywords": [
        {"term": "network observability", "category": "capability", "rationale": "core"},
        {"term": "   ", "category": "capability", "rationale": "blank"}]})
    assert [k.term for k in packet.strategy.keywords] == ["network observability"]


def test_operator_supplies_the_rival_side_at_the_gate(seeded):
    """Truth-purge completion (2026-08-03): when web research returns no
    cited findings, the gate accepts operator-supplied entities with
    operator provenance; blanks and unknown kinds refuse loudly."""
    packet = revise("Testco", {"research_entities": [
        {"name": "Quorum", "kind": "competitor"},
        {"name": "Quorum", "kind": "competitor"},          # dedupes
        {"name": "Bloomberg Government", "kind": "competitor",
         "rationale": "named by the operator at approval",
         "source": "operator"},
    ]})
    ents = packet.strategy.research_entities
    assert [(e.kind, e.name) for e in ents] == [
        ("competitor", "Quorum"), ("competitor", "Bloomberg Government")]
    assert ents[0].rationale == "supplied at the review gate by the operator"
    with pytest.raises(RevisionError):
        revise("Testco", {"research_entities": [{"name": "", "kind": "competitor"}]})
    with pytest.raises(RevisionError):
        revise("Testco", {"research_entities": [{"name": "X", "kind": "ally"}]})
