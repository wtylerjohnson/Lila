"""Three-view rendering: view selection, sales gating (never leaks), internal
QA additions, and the lint_counts gate with its four required fixtures."""

from __future__ import annotations

import os
import sys
from datetime import date
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reports.document import build_document  # noqa: E402
from agents.reports.lint import lint_counts, structural_counts  # noqa: E402
from agents.reports.views import gate_for_sales, render_assessment  # noqa: E402
from tests.test_assessment_document import _amended_searches, _searches  # noqa: E402

AS_OF = date(2026, 7, 6)


def _doc(n_pursue=3):
    return build_document("Testco", searches=_searches(n_pursue), qualify=None, as_of=AS_OF)


# ---- view selection -----------------------------------------------------------
def test_three_views_from_one_document():
    doc = _doc()
    client = render_assessment(doc, "client")
    sales = render_assessment(doc, "sales")
    internal = render_assessment(doc, "internal")
    assert "Confidential" in client and "Preview" in sales
    assert "INTERNAL REVIEW · DO NOT SEND" in internal
    for html in (client, sales, internal):
        assert 'id="gtmLogoImg"' in html          # identity assets on every view
        assert 'class="cover-art"' in html
    import pytest
    with pytest.raises(ValueError):
        render_assessment(doc, "board_deck")


# ---- sales gating: the shape without the keys ----------------------------------
def test_sales_view_never_leaks_client_content():
    doc = _doc()
    sales = render_assessment(doc, "sales")
    client = render_assessment(doc, "client")

    # locked elements are visibly locked, not absent
    assert sales.count("data-locked=\"1\"") >= 3
    assert "what the full engagement includes" in sales.lower()

    # competitor names, non-#1 pursuit titles, dossier plays, agency dollars,
    # full notice ids, watchlist item titles: gone
    for secret in ("AVI-SPL", "GDIT", "Diversified",          # competitor names
                   "Courtroom AV Refresh",                     # rank-2 pursuit title
                   "$2.0M",                                    # per-agency dollars (capability-scoped)
                   "sam.gov/opp/P2",                           # notice-level links
                   "70RSAT26R0000001", "70RSAT26R0000002",     # full solicitation ids
                   "Watch this"):                              # watchlist item title
        assert secret in client
        assert secret not in sales, f"sales view leaked: {secret}"

    # EVIDENCE INVERSION (2026-07-10): lane totals are filing-cabinet numbers;
    # they render in the INTERNAL view only, never in a client file
    internal = render_assessment(_doc(), "internal")
    assert "$80.0M" not in client
    assert "$80.0M" in internal

    # …but the SHAPE shows: #1 name + grade, ranks, competitor count, agency names
    assert "Video Wall Modernization" in sales                 # #1 pursuit stays
    assert "3 competitors mapped" in sales
    assert "Department of Defense" in sales                    # agency NAMES stay

    # …and the verifier principle: locked content PROVES it is real. Agency,
    # deadline, notice type, masked id and the verification line are sales-tier
    # content BY DESIGN and must appear.
    assert "U.S. COURTS" in sales                              # rank-2 agency visible
    assert "2026-08-15" in sales                               # rank-2 exact deadline visible
    assert "Solicitation" in sales                             # notice type visible
    assert "70RSAT26R000XXXX" in sales                         # masked notice id
    assert f"Verified against SAM.gov {AS_OF.isoformat()}" in sales
    assert "Verified against USAspending" in sales             # competitor/TAM basis
    # sales footer: the as-of statement + the engagement line
    assert "verified against primary sources as of 2026-07-06" in sales
    assert "part of the client engagement" in sales
    # verifier strips exist ONLY where gating produced them (the class name
    # itself lives in every view's stylesheet, so assert on rendered markup)
    assert 'class="v-vline"' in sales and 'class="v-vline"' not in client


def test_sales_counts_come_from_gated_model():
    doc = _doc()
    gated = gate_for_sales(doc)
    full, g = doc.counts(), gated.counts()
    # entity counts the sales pitch states stay truthful (locked slots preserved)
    for key in ("pursuits", "pursue_notices", "monitor_notices", "competitors",
                "watchlist_entries", "agencies", "news_items"):
        assert g[key] == full[key], key
    # content that sales does NOT render reports the gated shape, not the full one
    assert g["dossiers"] == 0 and g["tam_components"] == 0
    sales = render_assessment(doc, "sales")
    assert lint_counts(sales).ok               # and the rendered copy reconciles


def test_masked_notice_id_mechanics():
    from agents.reports.views import mask_notice_id
    # real prefix, masked tail, length preserved
    assert mask_notice_id("70RSAT26R0000001") == "70RSAT26R000XXXX"
    guid = "d431d02173d64e28a10bd05cb5a753ae"
    masked_guid = mask_notice_id(guid)
    assert len(masked_guid) == len(guid) and masked_guid.endswith("X" * 8)
    assert guid.startswith(masked_guid.rstrip("X"))
    assert mask_notice_id("M1") == "MX" and mask_notice_id("") == ""

    # the gated MODEL carries the masked form only — the leak holds mechanically
    doc = _doc()
    gated = gate_for_sales(doc)
    dump = gated.model_dump_json()
    for full_id in ("70RSAT26R0000001", "70RSAT26R0000002", "70RSAT26R0000003",
                    "sam.gov/opp"):
        assert full_id not in dump, f"gated model still carries: {full_id}"
    assert all(p.solicitation is None for p in gated.board.pursuits)
    assert all(p.source_id.endswith("XXXX") for p in gated.board.pursuits)
    # masked form derives from the real id (same prefix, same length)
    for real, g in zip(doc.board.pursuits, gated.board.pursuits):
        sol = real.solicitation or real.source_id
        assert len(g.source_id) == len(sol)
        assert sol.startswith(g.source_id.rstrip("X"))


def test_every_locked_element_carries_verifiers():
    doc = _doc()
    g = gate_for_sales(doc)
    # pursuits: agency, notice type, deadline, grade, masked id, verified line
    for p in g.board.pursuits:
        assert p.locked is not None and p.locked.verifiers
        v = p.locked.verifiers
        assert v["verified"].startswith("Verified against SAM.gov")
        assert v["notice id"].endswith("XXXX")
        assert v["grade"] == p.grade.letter
        assert v["agency"] and v["response deadline"] and v["notice type"]
    # competitors: verification basis + lanes stay; block lock carries the count
    for c in g.competitive.competitors:
        assert c.locked is not None
        assert "USAspending" in c.locked.verifiers["verified"]
        assert c.locked.verifiers["basis"] == c.basis      # basis is REAL, not blanked
    assert g.competitive.locked.verifiers["competitors mapped"] == str(
        len(g.competitive.competitors))
    # watchlist: entry count, source types, last-refreshed on the block;
    # source + motion status per item
    w = g.watchlist
    assert w.locked.verifiers["entries"] == str(len(w.entries))
    assert w.locked.verifiers["source types"]
    assert w.locked.verifiers["last refreshed"] == "2026-07-06T09:00:00"
    assert all(e.locked and e.locked.verifiers.get("source") for e in w.entries)
    # market: the math table lock names its component count
    assert g.market.locked.verifiers["market components"] == str(
        sum(1 for c in doc.market.components if c.kind == "market"))
    # …and the FULL document never carries placeholders
    assert doc.market.locked is None
    assert all(p.locked is None for p in doc.board.pursuits)


class _LockScan(HTMLParser):
    """Walks rendered HTML tracking data-locked nesting; counts anchors."""
    VOID = {"img", "br", "meta", "link", "input", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.inside = 0
        self.anchors_in = 0
        self.anchors_out = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            if self.inside:
                self.anchors_in += 1
            else:
                self.anchors_out += 1
        if tag in self.VOID:
            return
        locked = dict(attrs).get("data-locked") == "1"
        self.stack.append((tag, locked))
        if locked:
            self.inside += 1

    def handle_endtag(self, tag):
        while self.stack:
            t, locked = self.stack.pop()
            if locked:
                self.inside -= 1
            if t == tag:
                break


def test_locked_blocks_never_carry_links():
    """Interactivity gates with the content: unlocked content keeps working
    links, locked blocks carry NO anchor — by construction (the gated model
    has no client-only URLs) and verified mechanically here."""
    doc = _doc()
    sales = render_assessment(doc, "sales")
    scan = _LockScan()
    scan.feed(sales)
    assert scan.anchors_in == 0, "anchor rendered inside a locked block"
    assert scan.anchors_out > 0            # news links etc. still live
    assert 'href="https://n.gov/1"' in sales   # unlocked news link works
    # client view keeps notice-level links live
    client = render_assessment(doc, "client")
    assert 'href="https://sam.gov/opp/P1/view"' in client


def test_sales_deadline_switch(monkeypatch):
    """Default: exact deadline visible (urgency sells, a date alone is not
    actionable). The fuzzed form is one line away — flip the style constant."""
    import agents.reports.views as views_mod
    doc = _doc()
    assert "2026-07-30" in render_assessment(doc, "sales")     # exact by default

    monkeypatch.setattr(views_mod, "SALES_DEADLINE_STYLE", "window")
    gated = gate_for_sales(doc)
    assert all(p.response_deadline.startswith("closes within")
               for p in gated.board.pursuits if p.response_deadline)
    fuzzed = render_assessment(doc, "sales")
    assert "closes within 24 days" in fuzzed                   # P1: Jul 6 → Jul 30
    assert "2026-07-30" not in fuzzed and "2026-08-15" not in fuzzed


def test_nav_and_print_mode():
    doc = _doc()
    client = render_assessment(doc, "client")
    sales = render_assessment(doc, "sales")
    internal = render_assessment(doc, "internal")
    # in-page navigation on every view
    for html in (client, sales, internal):
        assert 'class="v-nav"' in html and 'href="#pursuits"' in html
    assert 'href="#preship"' in internal and 'href="#preship"' not in client
    # Print / Save as PDF only on the views that leave the building
    for html in (client, sales):
        assert "window.print()" in html and "Print / Save as PDF" in html
        # print stylesheet: chrome hidden, links render as plain text, no URLs
        assert "@media print" in html
        assert ".v-nav,.v-print{display:none" in html
        assert 'a[href]::after{content:"" !important}' in html
    assert "window.print()" not in internal


def test_amendment_history_renders_and_gates():
    """One solicitation, one card: the history line is client/internal
    content (full posting ids + dates); the sales model physically drops it."""
    doc = build_document("Testco", searches=_amended_searches(), qualify=None,
                         as_of=AS_OF)
    client = render_assessment(doc, "client")
    assert client.count('data-entity="pursuit"') == 3          # one card per solicitation
    assert "Amendment history for this solicitation" in client
    assert "deadline 2026-09-01 → 2026-09-15" in client        # stated, not inferred
    assert lint_counts(client).ok

    internal = render_assessment(doc, "internal")
    assert "the grade keys to the latest amendment" in internal

    gated = gate_for_sales(doc)
    assert all(p.amendments == [] for p in gated.board.pursuits)
    assert "P3A" not in gated.model_dump_json()
    sales = render_assessment(doc, "sales")
    assert "Amendment history" not in sales
    assert lint_counts(sales).ok


# ---- internal view --------------------------------------------------------------
def test_internal_view_carries_qa_layer():
    doc = _doc()
    internal = render_assessment(doc, "internal")
    assert "Pre-Ship Review" in internal
    assert "Grade worksheet" in internal
    assert "Ground-truth counts" in internal
    assert "MARKET PROXY" in internal          # worksheet basis text
    for gap in doc.gaps:
        assert gap[:40] in internal            # gaps rendered, not hidden
    # inline provenance: citation URLs appear in-section, not just the footer
    assert internal.count("v-cite") >= 3


# ---- lint_counts: the four required fixtures -------------------------------------
def _page(cards: int, copy: str) -> str:
    body = "".join(f'<div class="play-card" data-entity="pursuit">card {i}</div>'
                   for i in range(cards))
    return (f"<html><body><p>{copy}</p>{body}"
            f'<img id="gtmLogoImg" src="data:image/png;base64,{"gpt4" * 40}">'
            f"</body></html>")


def test_counts_regression_thinklogical_four_vs_three():
    """The shipped bug: prose says four, page renders three OPP cards."""
    page = _page(3, "The sweep surfaced four pursue-grade notices this cycle.")
    r = lint_counts(page)
    assert not r.ok
    bad = r.violations[0]
    assert bad.rule == "count_mismatch"
    assert "claims 4" in bad.detail and "renders 3" in bad.detail
    assert "four pursue-grade notices" in bad.excerpt


def test_counts_day_window_coexists_with_cards():
    """'four-day window' is a duration, not an entity count — must pass."""
    page = _page(3, "Respond inside a four-day window; three notices qualify. "
                    "Section 2 of 4 covers 541512 work worth $4M due 2026-07-30.")
    assert lint_counts(page).ok


def test_counts_spelled_out_mismatch():
    page = _page(2, "Seven competitors mapped across your lanes.")
    # no competitor elements at all -> unverified flag; add rows -> mismatch
    r = lint_counts(page)
    assert not r.ok and r.violations[0].rule == "count_claim_unverified"
    page2 = page.replace("</body>", '<tr data-entity="competitor"></tr>' * 4 + "</body>")
    r2 = lint_counts(page2)
    assert not r2.ok and r2.violations[0].rule == "count_mismatch"
    assert "claims 7" in r2.violations[0].detail and "renders 4" in r2.violations[0].detail


def test_counts_zero_claim_verified_by_absence():
    """A ZERO claim is verified by the absence of elements: 'zero pursue-grade
    notices' with no pursuit cards rendered reconciles, it does not flag. (A
    nonzero claim against an empty section still flags — see the mismatch test.)
    Regression: Osprey's live zero-opportunity compose tripped count_claim_
    unverified on every view because a 0-vs-None was treated as unverifiable."""
    page = _page(0, "Zero pursue-grade notices cleared the screen this cycle.")
    assert lint_counts(page).ok


def test_counts_unclassifiable_flags_for_review():
    # 'engagements' is a count noun the classifier doesn't know -> flag, not pass
    page = _page(1, "We ran nine engagements notices this quarter.")  # garbled claim
    r = lint_counts(page)
    assert not r.ok
    assert any(v.rule in ("count_claim_unclassified", "count_mismatch",
                          "count_claim_unverified") for v in r.violations)
    # and an unresolved compose token can never pass silently
    r2 = lint_counts(_page(1, "{{COUNT:mystery_metric}} pursuits identified."))
    assert any(v.rule == "count_token_unresolved" for v in r2.violations)


def test_counts_never_scan_base64():
    """'gpt4gpt4…' inside the logo payload must not trip anything (the
    lint_whitelabel false-positive rule applies here too)."""
    page = _page(2, "Two notices matter.")
    assert lint_counts(page).ok
    assert "gpt4" not in str(structural_counts(page))


# ---- rendered views reconcile end-to-end -----------------------------------------
def test_all_three_views_pass_the_counts_gate():
    doc = _doc(4)
    for view in ("client", "sales", "internal"):
        html = render_assessment(doc, view)
        r = lint_counts(html)
        assert r.ok, (view, [v.detail for v in r.violations][:3])


def test_internal_qa_compares_sweep_timestamp_on_the_host_local_day(
        monkeypatch):
    """Cycle 5: 00:30Z is still the prior operator day in Denver."""
    import agents.reports.views as views_module

    denver = ZoneInfo("America/Denver")
    monkeypatch.setattr(
        views_module, "_host_local_datetime",
        lambda value: value.astimezone(denver))
    doc = _doc()
    doc.as_of = date(2026, 8, 31)

    doc.generated_at = "2026-09-01T00:30:00Z"  # Aug 31, 18:30 MDT
    fresh = render_assessment(doc, "internal")
    assert "not same-day verified" not in fresh

    doc.generated_at = "2026-08-31T00:30:00+00:00"  # Aug 30, 18:30 MDT
    stale = render_assessment(doc, "internal")
    assert "not same-day verified against 2026-08-31" in stale

    # Artifact-mtime fallback stamps are deliberately naive local time.
    doc.generated_at = "2026-08-31T23:30:00"
    assert "not same-day verified" not in render_assessment(doc, "internal")

    doc.generated_at = "malformed"
    assert "not same-day verified" in render_assessment(doc, "internal")


# ---- zero-pursuit: sell the next layer down, screen as credential ---------------
def test_zero_pursuit_leads_with_findings_not_the_zero():
    """No pursue-grade notices is a FINDING but never the HEADLINE: the
    landscape LEADS with what the sweep did find (the monitored adjacencies /
    teaming surface) and states the ground-truth screen once, as the
    credential — auditable, every view, count-gate clean."""
    doc = build_document("Testco", searches=_searches(0), qualify=None, as_of=AS_OF)
    assert doc.counts()["pursuits"] == 0
    # D1 is a triage-only orphan in the synthetic fixture, not a SAM notice.
    # Verdict populations count only decisions tied to canonical SAM rows.
    assert doc.verdict_totals == {"monitor": 1}
    for view in ("internal", "client", "sales"):
        html = render_assessment(doc, view)
        # the found layer LEADS (monitor==1 -> singular pluralization)...
        lead = html.find("motion this cycle is one layer down: 1 notice tracked "
                         "as adjacent or teaming opportunity")
        # ...and the screen follows as the credential in the internal/sales
        # audit; the client view (opportunity-set doctrine) carries the lead
        # alone, with no screen credential.
        cred = html.find("1 notice triaged in the client's NAICS lanes")
        assert lead != -1, view
        if view == "client":
            assert cred == -1, view
        else:
            assert cred != -1 and lead < cred, view
        assert "screened out as off-mission" not in html
        assert "No pursue-grade notices in the current sweep." not in html  # old bare line gone
        assert lint_counts(html).ok, view


def test_zero_pursuit_distinguishes_a_sweep_that_never_ran():
    """A sweep that returned nothing to triage must NOT read as a reasoned
    all-clear — it is flagged as a data gap."""
    empty = {"client": "Testco", "generated_at": "2026-07-06T09:00:00",
             "results": {"sam.gov": [], "triage": {}}}
    doc = build_document("Testco", searches=empty, qualify=None, as_of=AS_OF)
    assert doc.counts()["pursuits"] == 0 and sum(doc.verdict_totals.values()) == 0
    html = render_assessment(doc, "internal")
    assert "data gap, not an all-clear" in html
    assert "The sweep screened" not in html      # never claims a screen that didn't happen
    assert lint_counts(html).ok
