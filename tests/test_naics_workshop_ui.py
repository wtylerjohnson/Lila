"""NAICS Boundary Workshop — BROWSER BEHAVIORAL tests (Cycle-5 review, Tranche B).

Real state transitions, keyboard actions, focus, undo, and rendered 320/390px
overflow — driven through the system Chrome (the same engine the PDF smoke
uses), not source-string assertions. Skips cleanly when no browser is present,
exactly like the Chrome/PDF smoke test.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _chrome():
    from tools.export_pdf import find_chrome
    try:
        return find_chrome()
    except Exception:
        return None


@pytest.fixture(scope="module")
def page_factory():
    chrome = _chrome()
    if not chrome:
        pytest.skip("no system Chrome available for browser behavioral tests")
    html = open(os.path.join(_ROOT, "ui", "index.html"), encoding="utf-8").read()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, executable_path=chrome)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chrome could not launch for behavioral tests: {exc}")

        def make(viewport=(1200, 900)):
            page = browser.new_page(
                viewport={"width": viewport[0], "height": viewport[1]})
            page.set_content(html, wait_until="domcontentloaded")
            return page
        yield make
        browser.close()


# render the NAICS workshop into a fresh panel with a controlled GATE
_SETUP = """
(state) => {
  // GATE/SEL/DETAIL are top-level `let` bindings (global lexical, not on
  // window); assign to the bare name so the page functions see them.
  SEL = 'testco';
  WSEL = null;
  DETAIL = { client_name: 'Testco' };
  GATE = {
    client: 'Testco', locked: false, pursuit_strategy: '',
    keywords: state.keywords || [],
    kept_out: [], inferred_naics: (state.naics||[]).map(e=>e.code),
    naics: state.naics || [],
    kept_out_naics: state.kept_out_naics || [],
    target_agencies: [], set_aside_angles: [],
    near_misses: state.near_misses || [],
    selected: null, undo: null, naicsSelected: null, naicsUndo: null,
  };
  let panel = document.getElementById('tpanel');
  if (panel) panel.remove();
  panel = document.createElement('div');
  panel.id = 'tpanel'; panel.className = 'gate-edit';
  document.body.appendChild(panel);
  window._tpanel = panel;
  drawEditor(panel);
  return true;
}
"""


def _render(page, **state):
    page.evaluate(_SETUP, state)


_SETUP_STRATEGY = """
(strategy) => {
  SEL = 'testco';
  WSEL = null;
  DETAIL = { client_name: 'Testco' };
  let panel = document.getElementById('tpanel');
  if (panel) panel.remove();
  panel = document.createElement('div');
  panel.id = 'tpanel'; panel.className = 'gate-edit';
  document.body.appendChild(panel);
  window._tpanel = panel;
  renderStrategyEditor(panel, strategy, {status: 'pending'});
  return true;
}
"""


def _render_strategy(page, strategy):
    page.evaluate(_SETUP_STRATEGY, strategy)


def _codes(page):
    return page.eval_on_selector_all(
        "#tpanel .naics-ws .naics-card:not(.kept) .nc-code", "els => els.map(e=>e.textContent)")


def _boundary(page):
    return page.evaluate("() => GATE.naics.map(e=>e.code)")


def test_inspect_first_does_not_add_to_boundary(page_factory):
    page = page_factory()
    _render(page, naics=[{"code": "541512", "origin": "system"}],
            near_misses=[{"value": "541513", "kind": "naics",
                          "reason": "flagged", "confidence": 0.5}])
    assert _boundary(page) == ["541512"]
    page.click("#tpanel .naics-ws .kw-judge button:has-text('Inspect first')")
    # boundary UNCHANGED; inspector shows the read-only candidate view
    assert _boundary(page) == ["541512"]
    inspector = page.inner_text("#tpanel .naics-ws .kws-inspector")
    assert "not in the boundary" in inspector.lower()
    # only "Use in boundary" promotes
    page.click("#tpanel .naics-ws .kws-inspector button:has-text('Use in boundary')")
    assert "541513" in _boundary(page)


def test_inferred_cards_surface_grounded_legacy_explanations(page_factory):
    page = page_factory()
    _render_strategy(page, {
        "pursuit_strategy": "p",
        "inferred_naics": ["541512", "334290", "541519"],
        "keywords": [
            {"term": "541512", "category": "naics",
             "rationale": "Exact legacy keyword explanation."},
            {"term": "541519", "category": "naics",
             "rationale": "Fallback that explicit metadata must beat."},
        ],
        "naics_meta": [{
            "code": "334290", "origin": "system",
            "rationale": "Original note questioned this equipment lane."}, {
            "code": "541519", "origin": "system",
            "rationale": "Explicit metadata wins."}],
        "near_misses": [{
            "value": "334290", "kind": "naics",
            "reason": "Original note questioned this equipment lane.",
            "confidence": 0.4}, {
            "value": "541519", "kind": "naics",
            "reason": "This broader lane was also considered and cut.",
            "confidence": 0.4}],
    })
    assert _boundary(page) == ["541512", "334290", "541519"]
    cards = page.locator("#tpanel .naics-ws .naics-card:not(.kept)")
    assert cards.count() == 3
    explanations = cards.locator(".nc-expl .txt").all_inner_texts()
    assert explanations[0] == "Exact legacy keyword explanation."
    assert explanations[1] == "Original note questioned this equipment lane."
    assert explanations[2] == "Explicit metadata wins."
    assert cards.nth(1).locator(".nc-expl .lab").inner_text() == (
        "ORIGINAL REVIEW CONTEXT")
    context = cards.nth(2).locator(".nc-context").inner_text()
    assert "ORIGINAL REVIEW CONTEXT" in context
    assert "This broader lane was also considered and cut." in context
    # An active/near-miss collision remains active only; exclusionary context
    # is visible but is never repackaged as an affirmative rationale.
    assert page.locator("#tpanel .naics-ws .kw-judge:has-text('334290')").count() == 0
    state = page.evaluate("() => GATE.naics")
    assert state[0]["rationale"] == "Exact legacy keyword explanation."
    assert state[1]["rationale"] == ""
    assert state[2]["rationale"] == "Explicit metadata wins."


def test_keyboard_move_out_removes_the_card(page_factory):
    page = page_factory()
    _render(page, naics=[{"code": "541512", "origin": "system"},
                         {"code": "541519", "origin": "system"}])
    assert _codes(page) == ["541512", "541519"]
    # focus the first card's Move-out button and press Enter (real keyboard)
    page.focus("#tpanel .naics-ws .naics-card:not(.kept) .nc-btn.rm")
    page.keyboard.press("Enter")
    assert _boundary(page) == ["541519"]                 # exactly one moved out
    assert page.evaluate("() => GATE.kept_out_naics.map(e=>e.code)") == ["541512"]


def test_space_key_inspects_without_mutating_boundary(page_factory):
    page = page_factory()
    _render(page, naics=[{"code": "541512", "origin": "system",
                          "rationale": "Systems-design lane."}])
    before = _boundary(page)
    page.focus("#tpanel .naics-ws .naics-card:not(.kept) .nc-btn:has-text('Inspect')")
    page.keyboard.press("Space")
    assert _boundary(page) == before
    assert page.input_value(
        "#tpanel .naics-ws .kws-inspector textarea[aria-label='why this NAICS code belongs']"
    ) == "Systems-design lane."


def test_restore_preserves_original_provenance(page_factory):
    page = page_factory()
    _render(page, naics=[], kept_out_naics=[{
        "code": "541512", "title": "Systems Design", "role": "core",
        "origin": "system", "rationale": "primary", "note": "moved out"}])
    page.click("#tpanel .naics-ws details.kws-out summary")   # open the kept-out drawer
    page.click("#tpanel .naics-ws .naics-card.kept .nc-btn")
    restored = page.evaluate("() => GATE.naics[0]")
    assert restored["origin"] == "system"               # NOT overwritten to 'restored'
    assert restored["title"] == "Systems Design"
    assert restored["role"] == "core" and restored["rationale"] == "primary"
    assert page.evaluate("() => GATE.kept_out_naics.length") == 0


def test_manual_readd_of_kept_code_reuses_the_record(page_factory):
    page = page_factory()
    _render(page, naics=[], kept_out_naics=[{
        "code": "541512", "title": "Kept Title", "role": "core",
        "origin": "consultant", "rationale": "kept reason", "note": "kept"}])
    page.fill("#naics-add-code", "541512")
    page.click("#tpanel .naics-ws .kws-add button:has-text('Add to boundary')")
    e = page.evaluate("() => GATE.naics.find(x=>x.code==='541512')")
    assert e["title"] == "Kept Title"                    # preserved, not generic
    assert e["role"] == "core" and e["rationale"] == "kept reason"
    assert e["origin"] == "consultant"
    assert page.evaluate("() => GATE.kept_out_naics.length") == 0


def test_inspector_title_edit_marks_edited_and_keeps_focus(page_factory):
    page = page_factory()
    _render(page, naics=[{"code": "541512", "origin": "system", "title": ""}])
    page.click("#tpanel .naics-ws .naics-card:not(.kept) .nc-btn:has-text('Inspect')")
    page.focus("#tpanel .naics-ws .kws-inspector input")
    page.type("#tpanel .naics-ws .kws-inspector input", "New Label")
    # provenance marked edited; focus never stolen by a redraw mid-type
    assert page.evaluate("() => GATE.naics[0].origin") == "edited"
    assert page.evaluate(
        "() => document.activeElement === document.querySelector('#tpanel .naics-ws .kws-inspector input')")


def test_consultant_rationale_edit_marks_edited_and_updates_card_in_place(page_factory):
    page = page_factory()
    _render(page, naics=[{
        "code": "541513", "origin": "consultant",
        "rationale": "Promoted candidate reason."}])
    page.click("#tpanel .naics-ws .naics-card:not(.kept) .nc-btn:has-text('Inspect')")
    page.wait_for_timeout(20)  # let the inspector's intentional initial focus settle
    rationale = page.locator(
        "#tpanel .naics-ws .kws-inspector textarea[aria-label='why this NAICS code belongs']")
    rationale.fill("Refined operator explanation.")
    assert page.evaluate("() => GATE.naics[0].origin") == "edited"
    # The in-place re-render restores focus through a zero-delay helper, so
    # the assertion must WAIT for that state, never race it: an instant
    # activeElement check failed under external machine load on pristine
    # files (2026-08-03). Condition-waits, not fixed sleeps, are the rule.
    from playwright.sync_api import expect
    expect(rationale).to_be_focused()
    assert page.locator("#tpanel .naics-ws .nc-expl .txt").inner_text() == (
        "Refined operator explanation.")


def test_stale_undo_click_after_expiry_never_throws(page_factory):
    page = page_factory()
    _render(page, naics=[{"code": "541512", "origin": "system"}])
    page.focus("#tpanel .naics-ws .naics-card:not(.kept) .nc-btn.rm")
    page.keyboard.press("Enter")                          # move out -> Undo appears
    assert page.is_visible("#tpanel .naics-ws .kws-undo button")
    # simulate expiry WITHOUT waiting 8s: clear the undo record, then click
    page.evaluate("() => { GATE.naicsUndo = null; }")
    err = page.evaluate("""() => {
        try { document.querySelector('#tpanel .naics-ws .kws-undo button').click(); return null; }
        catch (e) { return String(e); } }""")
    assert err is None                                    # guarded: no null deref


def test_actual_undo_expiry_removes_banner_without_stealing_focus(page_factory):
    page = page_factory()
    # Compress only the eight-second undo timer; zero-delay focus helpers retain
    # their real browser path.
    page.evaluate("""() => {
      const realSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (fn, ms, ...args) =>
        realSetTimeout(fn, ms === 8000 ? 35 : ms, ...args);
    }""")
    _render(page, naics=[
        {"code": "541512", "origin": "system", "rationale": "First lane."},
        {"code": "541519", "origin": "system", "rationale": "Second lane."},
    ])
    page.click("#tpanel .naics-ws .naics-card:first-of-type .nc-btn.rm")
    page.click("#tpanel .naics-ws .naics-card:not(.kept) .nc-btn:has-text('Inspect')")
    page.wait_for_timeout(20)  # initial inspector focus settles before our focus assertion
    rationale = page.locator(
        "#tpanel .naics-ws .kws-inspector textarea[aria-label='why this NAICS code belongs']")
    rationale.focus()
    # The compressed expiry races real click/focus round-trips, so the
    # assertions must WAIT for the settled state (banner gone, focus
    # preserved) rather than sample it after a fixed sleep: the instant
    # version failed under external machine load on pristine files
    # (2026-08-03) while the settled behavior was verified correct.
    from playwright.sync_api import expect
    expect(page.locator("#tpanel .naics-ws .kws-undo")).to_have_count(0)
    expect(rationale).to_be_focused()


def test_candidate_keep_out_clears_stale_inspector_and_lanes_stay_disjoint(page_factory):
    page = page_factory()
    candidate = {"value": "541513", "kind": "naics",
                 "reason": "Managed-service question.", "confidence": 0.5}
    _render(page, naics=[{"code": "541512", "origin": "system",
                          "rationale": "Core lane."}], near_misses=[candidate])
    page.click("#tpanel .naics-ws .kw-judge button:has-text('Inspect first')")
    page.click("#tpanel .naics-ws .kw-judge button:has-text('Keep out')")
    assert page.locator(
        "#tpanel .naics-ws .kws-inspector button:has-text('Use in boundary')").count() == 0
    # Defense in depth: a stale programmatic promotion restores the kept record
    # instead of allowing the same code in both arrays.
    page.evaluate("""(candidate) => {
      promoteNaicsCandidate(candidate, document.getElementById('tpanel'));
    }""", candidate)
    lanes = page.evaluate("() => ({active:GATE.naics.map(e=>e.code), kept:GATE.kept_out_naics.map(e=>e.code)})")
    assert "541513" in lanes["active"]
    assert "541513" not in lanes["kept"]
    assert page.evaluate("() => GATE.naics.find(e=>e.code==='541513').rationale") == ""
    assert page.locator(
        "#tpanel .naics-card:not(.kept):has-text('541513') .nc-expl .lab"
    ).inner_text() == "ORIGINAL REVIEW CONTEXT"


def test_promoted_candidate_context_is_never_recast_as_why_it_belongs(page_factory):
    page = page_factory()
    reason = ("This lane is close, but the client sells products rather than "
              "managed operations, so the code was cut.")
    _render(page, naics=[], near_misses=[{
        "value": "541513", "kind": "naics", "reason": reason,
        "confidence": 0.5,
    }])
    page.click("#tpanel .naics-ws .kw-judge button:has-text('Use in boundary')")
    card = page.locator("#tpanel .naics-card:not(.kept):has-text('541513')")
    assert card.locator(".nc-expl .lab").inner_text() == "ORIGINAL REVIEW CONTEXT"
    assert card.locator(".nc-expl .txt").inner_text() == reason
    assert page.evaluate("() => GATE.naics[0].rationale") == ""


def test_old_kept_candidate_rationale_is_demoted_on_restore(page_factory):
    page = page_factory()
    reason = ("This lane is close, but products are sold instead of managed "
              "operations, so this code was cut.")
    _render(page, naics=[], kept_out_naics=[{
        "code": "541513", "origin": "system", "rationale": reason,
        "note": "kept out at review",
    }], near_misses=[{
        "value": "541513", "kind": "naics", "reason": reason,
        "confidence": 0.5,
    }])
    page.click("#tpanel details.kws-out summary")
    page.click("#tpanel .naics-card.kept button:has-text('Restore')")
    card = page.locator("#tpanel .naics-card:not(.kept):has-text('541513')")
    assert card.locator(".nc-expl .lab").inner_text() == "ORIGINAL REVIEW CONTEXT"
    assert page.evaluate("() => GATE.naics[0].rationale") == ""


@pytest.mark.parametrize("width", [320, 390])
def test_naics_workshop_does_not_overflow_at_phone_widths(page_factory, width):
    page = page_factory(viewport=(width, 780))
    long_reason = ("This is a deliberately long grounded explanation with hostile "
                   "unbroken-content-abcdefghijklmnopqrstuvwxyz0123456789 " * 3)
    _render(page, naics=[{"code": "541512", "origin": "system", "title": "A",
                          "rationale": long_reason},
                         {"code": "541519", "origin": "system", "title": "B",
                          "rationale": "Second explanation."},
                         {"code": "334118", "origin": "system", "title": "C",
                          "rationale": "Third explanation."}])
    overflow = page.evaluate("""(vw) => {
        const ws = document.querySelector('.naics-ws');
        const cards = ws.querySelector('.naics-cards');
        const over = [...ws.querySelectorAll('*')].filter(
            el => el.getBoundingClientRect().right > vw + 0.5).length;
        const cols = getComputedStyle(cards).gridTemplateColumns.split(' ').length;
        return { over, cols, wsOverflow: ws.scrollWidth > ws.clientWidth + 1,
                 docOverflow: document.documentElement.scrollWidth > vw + 1 };
    }""", width)
    assert overflow["over"] == 0                          # nothing extends past the viewport
    assert overflow["cols"] == 1                          # one-column phone layout
    assert overflow["wsOverflow"] is False
    assert overflow["docOverflow"] is False


def test_vocabulary_save_rerenders_then_focuses_fresh_naics_region(page_factory):
    page = page_factory()
    strategy = {
        "pursuit_strategy": "p", "keywords": [], "kept_out": [],
        "inferred_naics": ["541512"],
        "naics_meta": [{"code": "541512", "origin": "system",
                        "rationale": "Server-authoritative explanation."}],
        "kept_out_naics": [], "target_agencies": [], "set_aside_angles": [],
        "near_misses": [],
    }
    _render_strategy(page, strategy)
    page.evaluate("""(strategy) => {
      window._oldNaicsAnchor = document.getElementById('naics-workshop-anchor');
      window.fetch = async () => ({
        ok: true,
        json: async () => ({status:'pending', revision_count:7, strategy})
      });
    }""", strategy)
    page.click("#tpanel button:has-text('Approve search vocabulary & review NAICS')")
    page.wait_for_function("""() => {
      const fresh = document.getElementById('naics-workshop-anchor');
      return fresh && fresh !== window._oldNaicsAnchor && document.activeElement === fresh;
    }""")
    assert "revision 7" in page.inner_text("#tpanel .gate-msg")
    assert page.evaluate("() => GATE.naics[0].rationale") == (
        "Server-authoritative explanation.")


def test_active_origin_requires_exact_id_or_both_legacy_ids_absent(page_factory):
    page = page_factory()
    result = page.evaluate("""() => {
      SEL = 'testco'; WSEL = null;
      const legacySame = isActiveOrigin({slug:'testco', workstationId:null});
      const wrongClient = isActiveOrigin({slug:'other', workstationId:null});
      WSEL = 'agency_dhs';
      const missingAgainstExact = isActiveOrigin(
        {slug:'testco', workstationId:null});
      const exactSame = isActiveOrigin(
        {slug:'testco', workstationId:'agency_dhs'});
      const exactDifferent = isActiveOrigin(
        {slug:'testco', workstationId:'agency_dod'});
      WSEL = null;
      const exactAgainstMissing = isActiveOrigin(
        {slug:'testco', workstationId:'agency_dhs'});
      return {legacySame, wrongClient, missingAgainstExact, exactSame,
              exactDifferent, exactAgainstMissing};
    }""")
    assert result == {
        "legacySame": True,
        "wrongClient": False,
        "missingAgainstExact": False,
        "exactSame": True,
        "exactDifferent": False,
        "exactAgainstMissing": False,
    }


def test_normal_save_refuses_an_unexplained_boundary_code(page_factory):
    page = page_factory()
    _render_strategy(page, {
        "pursuit_strategy": "p", "keywords": [], "kept_out": [],
        "inferred_naics": ["541512"], "naics_meta": [],
        "kept_out_naics": [], "target_agencies": [], "set_aside_angles": [],
        "near_misses": [],
    })
    page.evaluate("() => { window._fetchCount = 0; window.fetch = async () => { window._fetchCount += 1; }; }")
    page.click("#tpanel button:has-text('Save Analyst Layer draft')")
    assert page.evaluate("() => window._fetchCount") == 0
    assert "explain or move out NAICS 541512" in page.inner_text("#tpanel .gate-msg")
    assert page.locator(
        "#tpanel .naics-card[data-naics-idx='0'] .nc-btn:has-text('Inspect')"
    ).evaluate("e => document.activeElement === e") is True


def test_vocabulary_navigation_preserves_unexplained_metadata_edits(page_factory):
    page = page_factory()
    strategy = {
        "pursuit_strategy": "p", "keywords": [], "kept_out": [],
        "inferred_naics": ["541512"], "naics_meta": [],
        "kept_out_naics": [], "target_agencies": [], "set_aside_angles": [],
        "near_misses": [],
    }
    _render_strategy(page, strategy)
    page.click("#tpanel .naics-card .nc-btn:has-text('Inspect')")
    page.locator("#tpanel .kws-inspector input").fill("Refined systems-design title")
    page.locator("#tpanel .kws-inspector select").select_option("core")
    page.evaluate("""(strategy) => {
      window._savedBody = null;
      window.fetch = async (_url, opts) => {
        const body = JSON.parse(opts.body); window._savedBody = body;
        return {ok:true, json:async()=>({status:'pending', revision_count:8,
          strategy:{...strategy, ...body.updates}})};
      };
    }""", strategy)
    page.click("#tpanel button:has-text('Approve search vocabulary & review NAICS')")
    page.wait_for_function("() => window._savedBody !== null")
    sent = page.evaluate("() => window._savedBody.updates.naics_meta[0]")
    assert sent["title"] == "Refined systems-design title"
    assert sent["role"] == "core"
    assert sent["rationale"] == ""
    assert page.evaluate("() => GATE.naics[0].title") == "Refined systems-design title"


def test_old_undo_timer_cannot_remove_a_newer_rerender_banner(page_factory):
    page = page_factory()
    page.evaluate("""() => {
      const realSetTimeout = window.setTimeout.bind(window); let undoCount = 0;
      window.setTimeout = (fn, ms, ...args) => {
        if (ms !== 8000) return realSetTimeout(fn, ms, ...args);
        undoCount += 1;
        return realSetTimeout(fn, undoCount === 1 ? 35 : 140, ...args);
      };
    }""")
    _render(page, naics=[
        {"code": "541512", "origin": "system", "rationale": "First lane."},
        {"code": "541519", "origin": "system", "rationale": "Second lane."},
    ])
    page.click("#tpanel .naics-card:first-of-type .nc-btn.rm")
    _render(page, naics=[
        {"code": "334118", "origin": "system", "rationale": "New first lane."},
        {"code": "334290", "origin": "system", "rationale": "New second lane."},
    ])
    page.click("#tpanel .naics-card:first-of-type .nc-btn.rm")
    page.wait_for_timeout(70)
    assert page.locator("#tpanel .kws-undo").count() == 1
    assert page.evaluate("() => GATE.naicsUndo !== null") is True


def test_downstream_copy_states_web_execution_receives_naics(page_factory):
    page = page_factory()
    _render(page, naics=[{"code": "541512", "origin": "system"}])
    page.click("#tpanel .naics-ws .naics-card:not(.kept) .nc-btn:has-text('Inspect')")
    copy = page.inner_text("#tpanel .naics-ws .kws-inspector")
    assert "web research prompt still receives" in copy   # accurate, not "untouched"
    assert "never asserts capability fit" in copy


def test_naics_cards_show_official_titles_and_never_invent(page_factory):
    # Inferred codes get their official NAICS title (public reference data);
    # an explicit title wins; an unknown code stays blank, never fabricated.
    page = page_factory()
    _render(page, naics=[
        {"code": "541512", "origin": "system"},
        {"code": "334290", "origin": "system"},
        {"code": "541519", "origin": "system", "title": "Custom operator label"},
        {"code": "999999", "origin": "system"},
    ])
    titles = page.eval_on_selector_all(
        "#tpanel .naics-ws .naics-card:not(.kept) .nc-title",
        "els => els.map(e => e.textContent)")
    assert titles[0] == "Computer Systems Design Services"      # official lookup
    assert titles[1] == "Other Communications Equipment Manufacturing"
    assert titles[2] == "Custom operator label"                # explicit wins
    assert titles[3] == "label unavailable"                    # unknown, not invented
