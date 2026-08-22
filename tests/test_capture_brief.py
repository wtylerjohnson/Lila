"""Capture brief: deterministic render + QA gates. No LLM, no network."""
from agents.reports.capture_brief import (
    CBNewsItem, CBOpportunity, CBStat, CaptureBriefContent,
    qa_capture_brief, render_capture_brief,
)


def _content(**over):
    base = dict(
        client_name="Testco",
        subtitle="Threat Intelligence · OSINT",
        meta_prepared_for="Keith · Testco",
        thesis=["One.", "Two with $5B.", "Three."],
        stats=[CBStat(number="5", accent=" live", context="c")] * 5,
        action_callout="File the DOE by Jul 9.",
        opportunities=[CBOpportunity(
            headline="NATO CTI Uplift", agency_abbr="NATO", agency_name="NATO NCIA",
            fit="DIRECT FIT", notice_id="RFQ-1", url="https://sam.gov/x",
            due_label="Jul 9, 2026, 5:00 p.m. ET", days_to_due=7,
            body="Core product.", urgent=True)],
        kill_line_opps="One gate, seven days.",
        news_funding=[CBNewsItem(recency="This week", headline="H", url="https://a.b/c", why="w")],
        news_threat=[], news_agency=[], news_market=[],
        kill_line_news="Tailwind.",
        partner_callout="Drive the Carahsoft motion now.",
        pipeline=[],
        footer_verification="Verified from SAM.gov notice records.",
    )
    base.update(over)
    return CaptureBriefContent(**base)


def test_render_contains_locked_components():
    html = render_capture_brief(_content())
    for marker in ["cover-eyebrow", "stat-band", "ghost-num", "play-card",
                   "fit-chip fit-direct", "Capture runway", "sigbar",
                   "Contract vehicle", "GTM Group analyst judgment",
                   "Tyler Johnson · GTM Group"]:
        assert marker.lower().replace(" ", "") in html.lower().replace(" ", ""), marker


def test_render_escapes_client_content():
    html = render_capture_brief(_content(client_name="A<B>&C"))
    assert "A&lt;B&gt;&amp;C" in html


def test_qa_flags_missing_urls_and_past_due():
    bad = _content()
    bad.opportunities[0].url = "sam.gov/x"  # not http
    assert any("no source URL" in p for p in qa_capture_brief(bad))


def test_qa_clean_on_good_content():
    assert qa_capture_brief(_content()) == []


def test_vehicles_confirmed_only_in_table():
    html = render_capture_brief(_content())
    table = html.split("Tracked, pending verification")[0]
    assert "MAIDI" not in table  # unverified entry must not render as confirmed


# ── deadline-aware depth folded into the locked skin ──

_DOSSIER = {
    "id": "RFQ-1", "title": "NATO CTI Uplift",
    "evaluation_criteria": ["Technical approach", "Past performance"],
    "incumbent_signals": ["References BigCo's existing license."],
    "vehicle": "GSA MAS",
    "submission_requirements": ["10-page limit", "Submit via portal by Jul 9"],
    "win_themes": ["Dark-web depth", "Existing NATO footprint"],
    "fit_verdict": "strong_fit", "next_action": "Request Q&A attachment.",
}


def test_render_folds_matching_dossier_into_opp_card():
    html = render_capture_brief(_content(), dossiers=[_DOSSIER])
    for needle in ("Evaluated on:", "Technical approach", "How you win:",
                   "Dark-web depth", "Bid mechanics", "GSA MAS",
                   "Incumbency:", "10-page limit"):
        assert needle in html, needle


def test_render_without_dossiers_is_unchanged_and_unmatched_skipped():
    plain = render_capture_brief(_content())
    assert "Bid mechanics" not in plain and "Evaluated on:" not in plain
    unmatched = render_capture_brief(
        _content(), dossiers=[{**_DOSSIER, "id": "OTHER-99", "title": "Different"}])
    assert "Bid mechanics" not in unmatched


def test_dossier_matches_by_url_or_title_fallback():
    from agents.reports.capture_brief import _match_dossier
    o = _content().opportunities[0]
    assert _match_dossier(o, [{**_DOSSIER, "id": "https://sam.gov/x"}]) is not None
    assert _match_dossier(o, [{**_DOSSIER, "id": "zzz", "title": "nato cti uplift"}]) is not None
    assert _match_dossier(o, [{**_DOSSIER, "id": "zzz", "title": "unrelated"}]) is None


# ── full-picture review: the final product weighs every stage ──

def test_compose_wraps_context_with_review_directive():
    from agents.reports.capture_brief import FullPictureReview, compose_capture_brief
    from agents.reports.facts import FactPack

    class Eng:
        def deliberate(self, layer, system_prompt, context, schema):
            self.layer, self.ctx = layer, context
            return _content()

    review = FullPictureReview(
        thesis_directive="DHS demand is live; lead with the BPA.",
        storyline_connections=["Dossier incumbent + expiring P-1 = one story"],
        must_include=["N-1"], do_not_claim=["No proof of FY27 funding"],
        emphasis_ranking=["N-1"])
    from datetime import date
    pack = FactPack(client_name="Testco", facts=[], warnings=[], as_of=date(2026, 7, 3))
    eng = Eng()
    compose_capture_brief(pack, engine=eng, review=review)
    assert eng.layer == "capture-brief"
    assert eng.ctx["full_picture_review"]["thesis_directive"].startswith("DHS demand")
    assert "never contradict" in eng.ctx["instruction"]
    assert eng.ctx["fact_pack"]["client_name"] == "Testco"
    # without a review the context stays the plain pack dump (backward compat)
    eng2 = Eng()
    compose_capture_brief(pack, engine=eng2)
    assert "full_picture_review" not in eng2.ctx
    assert eng2.ctx.get("client_name") == "Testco"


def test_fix_directive_outranks_the_review():
    """The 2026-07-09 Osprey fail: the arbiter fix list was appended after
    'follow the review' and lost the argument twice. A directive must read
    FIRST and carry explicit authority over the review."""
    from agents.reports.capture_brief import FullPictureReview, compose_capture_brief
    from agents.reports.facts import FactPack

    class Eng:
        def deliberate(self, layer, system_prompt, context, schema):
            self.ctx = context
            return _content()

    review = FullPictureReview(
        thesis_directive="GDIT is the priority teaming door.",
        storyline_connections=[], must_include=["F12"],
        do_not_claim=[], emphasis_ranking=["GDIT"])
    from datetime import date
    pack = FactPack(client_name="Testco", facts=[], warnings=[], as_of=date(2026, 7, 9))
    eng = Eng()
    fix = "ARBITER PANEL FAILED THE DRAFT. Fix exactly these and change nothing else: ..."
    compose_capture_brief(pack, engine=eng, review=review, directive=fix)
    instr = eng.ctx["instruction"]
    assert instr.startswith("ARBITER PANEL FAILED")   # directive reads first
    assert "the directive wins" in instr              # explicit authority
    assert instr.index("ARBITER PANEL") < instr.index("never contradict")
    # review still rides along for context
    assert eng.ctx["full_picture_review"]["must_include"] == ["F12"]


def test_patch_repairs_the_standing_draft_not_the_pack():
    """Repair rounds patch the existing draft (only quoted lines change);
    the fact pack and review are NOT re-sent, so audited-clean text
    survives verbatim and the round runs on the fast tier."""
    from agents.reports.capture_brief import patch_capture_brief

    class Eng:
        def deliberate(self, layer, system_prompt, context, schema):
            self.layer, self.ctx, self.schema = layer, context, schema
            return _content()

    eng = Eng()
    draft = _content()
    out = patch_capture_brief(draft, "fix: change 'top' to '#2 of 8'", engine=eng)
    assert eng.layer == "capture-brief-patch"
    assert eng.ctx["current_draft"]["client_name"] == draft.client_name
    assert "verbatim" in eng.ctx["instruction"]
    assert "fact_pack" not in eng.ctx           # no re-reasoning over the pack
    assert "full_picture_review" not in eng.ctx  # the fix list is sole authority
    assert out.client_name == draft.client_name


# ── the approved visual standard must survive every render ──

def test_render_carries_the_full_visual_standard():
    from agents.reports.capture_brief import CBBudgetBar
    html = render_capture_brief(_content(budget_bars=[
        CBBudgetBar(label="SEWP VI ceiling", amount_label="$60B · 10 YRS", relative=1.0),
        CBBudgetBar(label="DOD cyberspace FY26", amount_label="$14.5B", relative=0.24),
    ]))
    for needle in (
        "cover-art",                    # the cover background art
        "logo-plate", "data:image/",    # GTM logo embedded from the repo asset
        "agency-strip", "agency-badge", # agencies in play
        "Money in motion", "$60B · 10 YRS",  # budget chart
        "zero-statement", "donut-wrap", "segment-list",  # channel visuals
        "Carahsoft",                    # channel content from vehicles.json
    ):
        assert needle in html, needle
    # client mark present for clients with a mark on file
    _rf = render_capture_brief(_content(client_name="Recorded Future"))
    assert "rf-mark" in _rf and 'data-brand-asset="1"' in _rf  # brand mark present + R13-exempt


def test_visual_standard_never_blocks_a_render(monkeypatch, tmp_path):
    # a client with no mark, no budget bars: brief still renders, no broken tags
    html = render_capture_brief(_content(client_name="Unknown Newco LLC"))
    assert 'svg class="rf-mark"' not in html
    assert "Money in motion" not in html
    assert "<html" in html and "cover-art" in html


def test_identity_assets_always_present_and_linted():
    """Regression: the 2026-07-05 11:18 brief shipped with every identity asset
    silently omitted. Identity assets are template constants — a minimal brief
    must carry all three, and lint must hard-fail when any is stripped."""
    from agents.reports.lint import lint_brief_identity

    html = render_capture_brief(_content())
    # 1. GTM logo: img#gtmLogoImg with a base64 payload
    assert 'id="gtmLogoImg"' in html and ";base64," in html
    # 2. cover art svg
    assert '<svg class="cover-art"' in html
    # 3. a client logo element inside .cover-logo (monogram when no mark on file)
    logo_block = html.split('<div class="cover-logo">', 1)[1][:600]
    assert "<svg" in logo_block
    # and the lint agrees the render is clean
    assert lint_brief_identity(html).ok

    # lint hard-fails when each asset is stripped (the 07-05 failure mode)
    import re
    no_gtm = html.replace('id="gtmLogoImg"', "")
    assert any(v.rule == "missing_gtm_logo" for v in lint_brief_identity(no_gtm).violations)
    no_art = html.replace('<svg class="cover-art"', '<svg class="x"')
    assert any(v.rule == "missing_cover_art" for v in lint_brief_identity(no_art).violations)
    no_logo = re.sub(r'(<div class="cover-logo">).*?(<div class="rf-wordmark">)',
                     r"\1\2", html, flags=re.S)
    assert any(v.rule == "missing_client_logo" for v in lint_brief_identity(no_logo).violations)


def test_client_without_mark_gets_monogram_not_blank():
    html = render_capture_brief(_content(client_name="Unknown Newco LLC"))
    logo_block = html.split('<div class="cover-logo">', 1)[1][:600]
    assert "client-mark-monogram" in logo_block
    assert ">UN<" in logo_block  # deterministic initials


def test_qa_budget_bars_floor():
    """>=3 cited dollar facts and no budget bars = QA problem; below the
    threshold (or bars present) = clean."""
    from datetime import date as _date
    from agents.reports.capture_brief import CBBudgetBar
    from agents.reports.facts import Fact, FactPack

    def pack(n_dollar):
        facts = [Fact(id=f"F{i+1}", kind="market", text=f"Program funded at ${i+2}00M.",
                      source="https://x.gov/f") for i in range(n_dollar)]
        facts.append(Fact(id="F99", kind="context", text="No figures here.",
                          source="https://x.gov/c"))
        return FactPack(client_name="Testco", as_of=_date(2026, 7, 5), facts=facts)

    bare = _content()  # no budget_bars
    assert any("money-in-motion" in p for p in qa_capture_brief(bare, pack=pack(3)))
    assert not any("money-in-motion" in p for p in qa_capture_brief(bare, pack=pack(2)))
    # bars present -> clean regardless of fact count
    with_bars = _content(budget_bars=[CBBudgetBar(label="DHS CDM", amount_label="$1.2B",
                                                  relative=1.0)])
    assert qa_capture_brief(with_bars, pack=pack(5)) == []
    # packless call keeps old behavior
    assert qa_capture_brief(bare) == []


def test_client_terminology_rename():
    """Client-facing rename: rendered HTML carries the new deliverable name and
    ZERO occurrences of the internal term in any casing."""
    import re
    from agents.reports.lint import lint_client_terminology

    html = render_capture_brief(_content(client_name="Recorded Future"))
    assert "Recorded Future · Federal Opportunity Assessment" in html   # <title>
    assert "Federal Opportunity Assessment · Confidential" in html      # eyebrow
    assert not re.search(r"capture[\s\-_]?brief", html, re.I)
    assert lint_client_terminology(html).ok

    # the old term injected anywhere (e.g. via generated copy) fails the build
    leaked = html.replace("</body>", "<p>see the Capture Brief for detail</p></body>")
    r = lint_client_terminology(leaked)
    assert not r.ok and r.violations[0].rule == "internal_term_in_client_copy"
    # separator variants can't sneak past either
    assert not lint_client_terminology("<p>the capture-brief shows</p>").ok
    assert not lint_client_terminology("<p>CAPTURE BRIEF</p>").ok


def test_reference_data_prose_stays_client_neutral():
    """The vehicles reference block renders into EVERY client's brief, so its
    prose must be client-neutral: no internal deliverable name, no other
    client's name or product terms (the 2026-07-09 Osprey brief shipped
    Recorded Future channel copy from this block), and no em dashes."""
    import re
    from agents.reports.lint import lint_client_bleed, lint_emdash
    html = render_capture_brief(_content())
    assert "this brief" not in html.lower()
    assert not re.search(r"\bthe brief\b", html.lower())
    assert "recorded future" not in html.lower()
    assert "RF subscriptions" not in html
    assert lint_client_bleed(html, "Testco").ok
    assert lint_emdash(html).ok


def test_lint_client_bleed_catches_other_clients_terms(tmp_path, monkeypatch):
    """Proven failure mode: another client's name/product in an outbound
    brief. Names auto-derive from review packets; curated terms extend them;
    the current client is exempt; cited third-party news is exempt."""
    import json as _json
    from agents.reports import lint as lint_mod
    from agents.reports.lint import lint_client_bleed
    review = tmp_path / "review"
    review.mkdir()
    (review / "a.review.json").write_text(_json.dumps({"client": "Recorded Future"}))
    (review / "b.review.json").write_text(_json.dumps(
        {"client_name": "Osprey Flight Solutions"}))
    terms = tmp_path / "client_terms.json"
    terms.write_text(_json.dumps({
        "_meta": {"purpose": "documentation, never a client term"},
        "Recorded Future": ["RF subscriptions"],
    }))
    monkeypatch.setattr(lint_mod, "_REVIEW_DIR", review)
    monkeypatch.setattr(lint_mod, "_TERMS_PATH", terms)

    page = ("<html><head><title>Osprey Flight Solutions · Federal Opportunity "
            "Assessment</title></head><body><p>RF subscriptions can be quoted "
            "on task orders.</p></body></html>")
    r = lint_client_bleed(page)                      # client from <title>
    assert not r.ok and r.violations[0].rule == "client_bleed"
    assert "Recorded Future" in r.violations[0].detail

    # the same terms in Recorded Future's OWN brief are fine
    rf_page = page.replace("Osprey Flight Solutions", "Recorded Future")
    assert lint_client_bleed(rf_page, "Recorded Future").ok
    # own name never trips; other client's name does
    own = page.replace("RF subscriptions", "Osprey Flight Solutions")
    assert lint_client_bleed(own, "Osprey Flight Solutions").ok
    named = page.replace("RF subscriptions", "Recorded Future")
    assert not lint_client_bleed(named, "Osprey Flight Solutions").ok
    # cited third-party news naming a prior client is coverage, not bleed
    news = page.replace(
        "<p>RF subscriptions can be quoted on task orders.</p>",
        '<a data-thirdparty="1" href="https://x">Recorded Future wins IC deal</a>')
    assert lint_client_bleed(news, "Osprey Flight Solutions").ok
    assert lint_client_bleed(
        page.replace("RF subscriptions", "purpose"),
        "Osprey Flight Solutions").ok


def test_lint_emdash_and_normalizer():
    """House style bans em dashes: composed prose normalizes at render, the
    lint fails any that survive in authored copy, third-party headlines stay
    verbatim."""
    from agents.reports.capture_brief import normalize_house_style
    from agents.reports.lint import lint_emdash
    assert normalize_house_style("quiet cycle — and accounted for") == \
        "quiet cycle, and accounted for"
    assert lint_emdash("<p>a — b</p>").ok is False
    assert lint_emdash('<a data-thirdparty="1" href="x">DoD — AI push</a>').ok
    # composed content with em dashes renders clean via the normalizer
    c = _content(thesis=["One — two.", "Three.", "Four — five."])
    html = render_capture_brief(c)
    assert lint_emdash(html).ok
    assert "One, two." in html


def _seed_graph_channel(email="kayani@state.gov"):
    from datetime import datetime, timedelta, date as _date
    from tools.contact_graph.schemas import ContactObservation
    from tools.contact_graph.store import ContactGraphStore
    store = ContactGraphStore()
    store.append([ContactObservation(
        notice_id="N-CG", person_name="Bilal Kayani", channel_kind="email",
        channel_value=email, agency="STATE, DEPARTMENT OF",
        source_url="https://sam.gov/opp/N-CG/view",
        observed_at=_date.today() - timedelta(days=10),
        harvested_at=datetime(2026, 7, 6, 9, 0, 0))])
    return store


def test_graded_contact_render_pass():
    """The 2026-07-05 DO-NOT-SEND regression: composed copy containing a bare
    federal email must render wrapped with grade + source from the graph, so
    the contact gate passes. Unknown channels stay bare and keep failing."""
    from agents.reports.capture_brief import grade_contact_channels
    from agents.reports.lint import lint_contact_rendering

    _seed_graph_channel()
    page = "<html><body><p>Submit to kayani@state.gov by July 9.</p></body></html>"
    fixed = grade_contact_channels(page)
    assert 'data-contact="1" data-grade="A"' in fixed
    assert 'data-source="https://sam.gov/opp/N-CG/view"' in fixed
    assert lint_contact_rendering(fixed).ok

    # unknown to the graph: left bare, gate still protects
    bare = grade_contact_channels("<p>mystery@dhs.gov</p>")
    assert "data-contact" not in bare
    assert not lint_contact_rendering(bare).ok

    # emails inside attributes / existing graded blocks are never double-wrapped
    linked = grade_contact_channels('<a href="mailto:kayani@state.gov">contact</a>')
    assert linked == '<a href="mailto:kayani@state.gov">contact</a>'
    pre = ('<span data-contact="1" data-grade="B" data-source="https://x.gov">'
           'kayani@state.gov</span>')
    assert grade_contact_channels(pre) == pre


def test_target_report_carries_logos_and_graded_pocs():
    from agents.reports.lint import lint_contact_rendering, lint_gtm_logo
    from agents.reports.target_report import render_target_report

    _seed_graph_channel()
    html = render_target_report({
        "client_name": "Testco",
        "strategy_note": "angle",
        "known_pocs": [{"name": "Bilal Kayani", "title": "CO",
                        "org": "State", "email": "kayani@state.gov"}],
        "searches": [], "citations": [],
    })
    assert lint_gtm_logo(html).ok
    logo_block = html.split('<div class="cover-logo">', 1)[1][:600]
    assert "<svg" in logo_block                       # client mark or monogram
    assert 'data-grade="A"' in html                   # POC email graded+sourced
    assert lint_contact_rendering(html).ok


def test_zero_opportunity_brief_is_valid_and_ships():
    """A client with no pursue-grade opportunity still produces a clean,
    shippable report that SELLS THE NEXT LAYER DOWN: the schema ACCEPTS zero
    (so the composer is never forced to invent one), QA does not false-fail on
    'no runway', and the zero branch points INTO the found layers — with the
    clean screen as the credential, never the headline."""
    from agents.reports.lint import (
        lint_brief_identity, lint_counts, lint_whitelabel,
    )
    c = _content(opportunities=[], pipeline=[])
    assert c.opportunities == []                        # schema accepts zero
    assert qa_capture_brief(c) == []                    # ships, not DO-NOT-SEND

    # with found layers: the zero branch points into each one that is present
    hz = {"items": [{"title": "x", "where": "y", "verified": [
        {"text": "t", "source": "https://sam.gov/opp/a/view"}],
        "pattern": "p", "projection": "GTM judges.", "window": "w",
        "watching": "wa", "confidence": "early"}]}
    html = render_capture_brief(
        c, counts={"monitor_notices": 9, "teaming_watch": 6, "pursuits": 0},
        horizon=hz)
    assert "The motion this cycle is one layer down" in html
    assert "9 monitor-grade adjacencies" in html         # resolved, ground truth
    assert "primes named from award evidence" in html    # teaming pointer
    assert "Developing Horizon" in html                  # horizon pointer
    # client-file doctrine (2026-07-10): the clean screen renders as a
    # forward action (a standing watch), never as self-graded credential
    assert "A standing watch holds on these lanes" in html
    assert "credential" not in html
    # the FORWARD motion opens the section; the credential closes it
    assert html.index("The motion this cycle") < html.index("A standing watch holds")
    assert "No forming programs surfaced" in html        # honest empty pipeline
    assert '<div class="plays-grid"></div>' not in html  # no empty grids anywhere
    assert lint_brief_identity(html).ok
    assert lint_counts(html).ok and lint_whitelabel(html).ok

    # with NO found layers: no dangling pointers to sections that don't exist
    bare = render_capture_brief(c)
    assert "Developing Horizon (Section 05)" not in bare
    assert "monitor-grade adjacencies" not in bare
    assert "market read, the watchlist, and the vehicle posture" in bare
    assert lint_counts(bare).ok and lint_whitelabel(bare).ok


def test_count_tokens_resolve_from_caller_counts():
    """The compose prompt tells the model to write {{COUNT:name}} tokens instead
    of literal numbers, so prose can never disagree with ground truth. This
    legacy render must substitute them from the caller's canonical counts().
    Regression: Osprey's zero-opp brief shipped a raw '{{COUNT:pursue_notices}}'
    because render_capture_brief never resolved tokens and run_capture_brief
    never gated on them."""
    from agents.reports.lint import lint_count_tokens
    stats = [
        CBStat(number="{{COUNT:pursue_notices}}",
               context="Pursue-grade notices this cycle — a reasoned all-clear"),
        CBStat(number="{{COUNT:monitor_notices}}",
               context="Monitor-grade adjacencies held on the watchlist"),
    ] + [CBStat(number="5", context="c")] * 3
    c = _content(stats=stats, opportunities=[], pipeline=[])

    # no counts -> token survives raw AND the gate catches it (loud, not silent)
    raw = render_capture_brief(c)
    assert "{{COUNT:pursue_notices}}" in raw
    assert not lint_count_tokens(raw).ok

    # counts supplied -> tokens become the ground-truth numbers, gate is clean
    resolved = render_capture_brief(
        c, counts={"pursue_notices": 0, "monitor_notices": 10})
    assert "{{COUNT" not in resolved
    assert '<span class="stat-number">0' in resolved
    assert '<span class="stat-number">10' in resolved
    assert lint_count_tokens(resolved).ok


# ── resolved-number gates: the report is mechanically prevented from
# contradicting itself (2026-07-09 institutional-grade review) ──

def test_zero_stat_gate_catches_misnamed_count_token():
    """'0 subaward primes' rendered beside GDIT's cited $893.7M because a
    mis-named COUNT token resolved to an unrelated pipeline tally. Any
    marquee stat that RESOLVES to zero fails the build."""
    from agents.reports.capture_brief import zero_stat_problems
    c = _content(stats=[
        CBStat(number="{{COUNT:teaming_plays}}", accent="GDIT",
               context="subaward primes routing work [F11][F12]"),
        CBStat(number="$893.7M", accent="GDIT", context="subaward flow [F12]"),
        CBStat(number="5", accent="", context="c"),
        CBStat(number="5", accent="", context="c"),
        CBStat(number="5", accent="", context="c"),
    ])
    problems = zero_stat_problems(c, {"teaming_plays": 0})
    assert len(problems) == 1
    assert "renders zero" in problems[0] and "F11" in problems[0]
    # same token, populated tally -> clean
    assert not zero_stat_problems(c, {"teaming_plays": 8})


def test_superlative_gate_defers_to_cited_rank():
    """'top/largest/#1' beside a citation whose fact states a lower rank
    fails; stating the true rank in the same sentence clears it."""
    from agents.reports.facts import Fact, FactPack
    from datetime import date
    pack = FactPack(client_name="Testco", as_of=date(2026, 7, 9), facts=[
        Fact(id="F12", kind="competitor", source="https://u.example",
             text="GDIT pushed $893.7M; ranked #2 of 8 by subaward dollars; teaming target."),
    ])
    bad = _content(thesis=["GDIT is the top subaward conduit at $893.7M [F12].",
                           "Two.", "Three."])
    problems = qa_capture_brief(bad, pack=pack)
    assert any("superlative contradicts cited rank" in p for p in problems)
    good = _content(thesis=[
        "GDIT, #2 of 8 by subaward dollars [F12], is the priority door and the largest direct incumbent.",
        "Two.", "Three."])
    assert not [p for p in qa_capture_brief(good, pack=pack)
                if "superlative" in p]


def test_editor_gets_cold_read_context_only():
    """The editor first DETERMINES the report's value (well presented,
    relevant, new to the client, actionable), then revises to maximize it —
    seeing ONLY the document. Hard limits ride in the system prompt;
    acceptance is the caller's re-audit."""
    from agents.reports.capture_brief import EditorResult, edit_capture_brief

    class Eng:
        def deliberate(self, layer, system_prompt, context, schema):
            self.layer, self.sys, self.ctx = layer, system_prompt, context
            assert schema is EditorResult
            return EditorResult(
                value_assessment={"well_presented": "tight",
                                  "relevant": "on-mission",
                                  "new_to_client": "buyer map is new",
                                  "actionable": "dated doors named"},
                revised=_content())

    eng = Eng()
    out = edit_capture_brief(_content(), engine=eng)
    assert eng.layer == "capture-brief-editor"
    assert "assessment" in eng.ctx
    assert "fact_pack" not in eng.ctx and "full_picture_review" not in eng.ctx
    for needle in ("DETERMINE VALUE", "WELL PRESENTED", "RELEVANT",
                   "NEW TO THE CLIENT", "ACTIONABLE",
                   "Never alter or introduce numbers", "never invent"):
        assert needle in eng.sys, needle
    assert out.revised.client_name == "Testco"
    assert out.value_assessment["new_to_client"]

def test_stat_band_seal_single_department_only():
    """Guarded option 3 (client-directed + Cycle 5 review): ONE restrained
    department seal when the stat band is genuinely single-department; a mixed
    or all-scope band renders text-only, never a repeated parent seal or a
    multi-agency report wearing one department's mark."""
    from agents.reports.capture_brief import CBStat, _stat_band_seal
    def band(accents):
        return _stat_band_seal([CBStat(number="$1M", accent=a, context="c")
                                for a in accents])
    # all DHS-lane (components + department + a non-agency qualifier) -> DHS seal
    one = band(["DHS", "CBP", "USCIS", "TSA", "FORMING"])
    assert "stat-band-seal" in one
    assert "Department of Homeland Security" in one
    # a company accent among agencies does not break single-department
    assert "stat-band-seal" in band(["CBP", "USCIS", "PERATON"])
    # mixed departments -> text-only, no seal
    assert band(["CBP", "DISA", "GSA"]) == ""
    # no agency accent at all -> text-only
    assert band(["FORMING", "one window", "pursue-grade"]) == ""
