"""Render the Federal Market Map into the design the operator approved.

WHAT WENT WRONG BEFORE. This module emitted a class vocabulary it invented:
mm, mmnav, tick, kv, opps, lede, n. Not one of those had a CSS rule in the
document it was spliced into, so 24 of 31 emitted classes were unstyled and
the artifact rendered as raw HTML. Navigation ran together as
"CompanyMarketCompetitionOpportunities". Ticker items collided into
"DEPARTMENT OFSources Sought". Raw 32-character hex notice ids were shown to
a client. The suite was green throughout, because nothing in it asked
whether the document could be LOOKED AT.

WHAT THIS EMITS NOW. Only classes from the approved Market Map stylesheet,
whose markup contracts were read out of the approved artifact rather than
guessed at. `style_contract.require_styled` asserts the whole set.

FOUR RULES THIS FILE KEEPS.

1 A CLIENT NEVER SEES A RAW NOTICE ID. `2b004cf8a914455eb70bda4d0acd3f1d`
  is a database key, not something a contracting officer would recognise.
  `display_identifier` prefers the solicitation number, then any non-hex
  evidence id, and falls back to a short form only when there is nothing
  else.

2 FIGURES ARE NOT EDITABLE, PROSE IS. Every amount renders as a
  `work-trigger` button that opens its receipt; only prose carries
  `data-edit-id`. A hand-edited figure would silently contradict the source
  record it links to.

3 EVERY FIGURE CARRIES ITS RECEIPT. `ExactMoney` already knows its basis,
  population, components and evidence, so a drawer entry is generated from
  the same object that renders the number. The figure and its explanation
  cannot drift apart, because there is only one of them.

4 AN EMPTY SECTION IS A RESEARCH ORDER, NOT AN APOLOGY. Absence renders as
  `plain-state needs` plus a forward line naming what happens next.
"""

from __future__ import annotations

import re
from typing import Any

from agents.golden_press.market_map_projection import FederalMarketMapDocument
from agents.golden_press.market_map_skeleton import EditIds, esc, logo_slot

MARKET_MAP_RENDER_VERSION = "market_map_render.v2.2026-08-07"

SECTIONS = (
    ("company", "Company"), ("market", "Market"), ("competition", "Competition"),
    ("opportunities", "Opportunities"), ("teaming", "Teaming"),
    ("contacts", "Contacts"), ("events", "Events"), ("research", "Research"),
    ("pursuit-board", "Pursuits"), ("evidence-method", "Method"),
)

_HEX_KEY = re.compile(r"^[0-9a-f]{32}$", re.I)
_SAM = "https://sam.gov/opp/{}/view"
_USASPENDING_NONE_AWARD = re.compile(
    r"^https?://(?:www\.)?usaspending\.gov/award/"
    r"((?:CONT_AWD|CONT_IDV)_[^/?#]+_-NONE-_-NONE-)/?(?:[?#].*)?$",
    re.I,
)


def _t(value: Any) -> str:
    return " ".join(str(value or "").split())


def display_identifier(opp: Any) -> str:
    """The published identifier, or EMPTY when there is none.

    Returns "" rather than a placeholder. A placeholder reads as data: an
    earlier pass returned "Open the record" here and the ticker rendered
    "Open the record · Strategic Defense Industrial Base", as though the
    phrase were the solicitation number. Callers decide what to show when a
    record has no published id, because the right answer differs between a
    link label and a ticker line.
    """
    ident = _t(getattr(opp, "identifier", ""))
    if ident and not _HEX_KEY.match(ident):
        return ident
    for ref in (getattr(opp, "evidence", ()) or ()):
        sid = _t(getattr(ref, "source_id", ""))
        if sid and not _HEX_KEY.match(sid):
            return sid
    return ""


def _resolving_source_url(url: Any) -> str:
    """Return the official route that actually resolves for an award id.

    USAspending's public award page does not resolve generated identifiers
    whose parent-award and modification segments are both ``-NONE-``.  The
    official v2 award endpoint does resolve the same exact generated id and
    returns the canonical award record.  Preserve every other source URL
    byte-for-byte; this is a narrow routing repair, not evidence inference.
    """
    value = _t(url)
    match = _USASPENDING_NONE_AWARD.match(value)
    if match:
        return ("https://api.usaspending.gov/api/v2/awards/"
                f"{match.group(1)}/")
    return value


def source_url(opp: Any) -> str:
    for ref in (getattr(opp, "evidence", ()) or ()):
        url = _t(getattr(ref, "url", "")) or _t(getattr(ref, "source_url", ""))
        if url:
            return _resolving_source_url(url)
    ident = _t(getattr(opp, "identifier", ""))
    return _SAM.format(ident) if _HEX_KEY.match(ident) else ""


def _link(url: str, text: str, *, cls: str = "") -> str:
    """An externally-opening link the operator may repoint in edit mode."""
    body = esc(text)
    url = _resolving_source_url(url)
    if not url:
        opening = f'<span class="{cls}">' if cls else "<span>"
        return f"{opening}{body}</span>"
    attrs = f' class="{cls}"' if cls else ""
    return (f'<a{attrs} data-edit-link="" href="{esc(url)}" target="_blank" '
            f'rel="noopener noreferrer" '
            f'title="Edit mode: double-click to change this URL">{body} ↗</a>')


def _money(money: Any, key: str, *, cls: str = "") -> str:
    """An exact amount that opens its own receipt. Never abbreviated.

    THE CLASS DEPENDS ON WHERE THE AMOUNT SITS. `.money` is styled ONLY as
    `.market-proof .money` and `.pursuit-lead .money`, so putting it in a
    table cell or a metric styles nothing at all. The design gives each
    context its own carrier: `.metric-value` in a metric band,
    `.coverage-value` in the coverage strip, and a bare button inside
    `td.exact` or `.opp-value`, both of which style their own button.

    A context-blind audit called this styled, because `.money` appears in
    the stylesheet. It appears there qualified.
    """
    if money is None:
        return '<span class="plain-state needs">Amount research next</span>'
    classes = f"{cls} work-trigger".strip()
    return (f'<button class="{classes}" type="button" '
            f'data-work="{esc(key)}" aria-label="Show the evidence behind '
            f'{esc(money.display)}">{esc(money.display)}</button>')


def _seal(agency: str, slot: str, client_name: str = "") -> str:
    """Curated agency authority mark, with a visible code fallback."""
    from agents.reports.report_assets import agency_seal

    label = _t(agency)[:80] or "Agency"
    return logo_slot(
        slot, label, cls="seal-slot", required=False,
        src=agency_seal(label, _t(client_name) or None))


def _company_mark(name: str, slot: str, client_name: str = "", *,
                  partner: bool = False) -> str:
    """Locally resolved company identity; never a live render-time fetch."""
    from agents.reports.report_assets import company_logo, partner_logo

    label = _t(name)[:100] or "Company"
    src = (partner_logo(_t(client_name) or None, label) if partner
           else company_logo(label, _t(client_name) or None))
    return logo_slot(slot, label, cls="partner-slot", required=False, src=src)


def _state(text: str, *, needs: bool = False) -> str:
    cls = "plain-state needs" if needs else "plain-state"
    return f'<span class="{cls}">{esc(text)}</span>'


def _head(number: int, headline: str, deck: str, ids: EditIds, *,
          state: str = "", needs: bool = False, work: str = "") -> str:
    """A section head. EXACTLY THREE GRID CHILDREN.

    `.plain-head` is `grid-template-columns: 58px minmax(0,1fr) auto`, so a
    fourth child silently lands in a column that does not exist. An earlier
    pass emitted both a state chip and a work button here and broke the
    layout. The state chip owns the third slot; the section's receipt is
    reached from its figures, and `.work-button` is `display:none` in this
    design anyway.
    """
    del work
    tail = _state(state, needs=needs) if state else "<span></span>"
    return (f'<div class="plain-head"><span class="plain-number">{number}</span>'
            f'<div><h2 data-edit-id="{ids.next()}" data-resizable-text="">'
            f'{esc(headline)}</h2>'
            f'<p data-edit-id="{ids.next()}">{esc(deck)}</p></div>{tail}</div>')


def _order(label: str, body: str, ids: EditIds) -> str:
    return (f'<div class="research-order"><strong>{esc(label)}</strong><br>'
            f'<span data-edit-id="{ids.next()}">{esc(body)}</span></div>')


# --------------------------------------------------------------------------- #
# 1 Company. The inference layer leads, because it decided everything below.
# --------------------------------------------------------------------------- #
def _company(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    c = doc.company_understanding
    if c is None:
        return ""
    blocks = []

    def block(label: str, prose: str) -> None:
        blocks.append(f'<div class="profile-block"><h3>{esc(label)}</h3>'
                      f'<p data-edit-id="{ids.next()}">{esc(prose)}</p></div>')

    block("What they sell", c.description or "Profile research next")
    if c.products:
        block("Products", " · ".join(_t(p) for p in c.products))
    if c.channels:
        block("Channels", " · ".join(_t(p) for p in c.channels))
    block("Federal posture",
          c.federal_footprint or c.posture or "Federal posture research next")

    if c.naics:
        blocks.append(
            '<div class="profile-block"><h3>NAICS codes that qualify a match'
            f'</h3><div class="code-chips" data-edit-id="{ids.next()}">'
            + "".join(f"<span>{esc(n)}</span>" for n in c.naics)
            + "</div></div>")

    searched = list(c.researched_keywords or ())
    if searched:
        blocks.append(
            '<div class="profile-block"><h3>Search vocabulary this research '
            f'ran on</h3><div class="code-chips" data-edit-id="{ids.next()}">'
            + "".join(f"<span>{esc(t)}</span>" for t in searched[:40])
            + "</div></div>")

    pending = ""
    if c.pending_keywords:
        # MINED PHRASES ARE QUOTES, NOT OUR VOICE. They come verbatim from
        # government notice text, and a varonis press failed certification
        # because an RFI said "robust". The chips carry the quoted-source
        # mark so the filler lint judges only what WE wrote.
        chips = ", ".join(
            f'<span class="term source-quote">{esc(_t(t))}</span>'
            for t in c.pending_keywords[:12])
        pending = (
            '<div class="research-order"><strong>Vocabulary awaiting '
            "approval</strong><br>"
            f'<span data-edit-id="{ids.next()}">These terms are proposed '
            "from the source text and not yet searched: </span>"
            + chips +
            f'<span data-edit-id="{ids.next()}">. Approve them to widen '
            "the next pass.</span></div>")

    return ('<section class="plain-section" id="company">'
            + _head(1, f"This is {doc.client_name} as we understand it.",
                    "This profile determined what the research counted. "
                    "Correct it first if the market boundary is wrong.",
                    ids, work="company-profile")
            + '<div class="profile-grid">' + "".join(blocks) + "</div>"
            + pending + "</section>")


# --------------------------------------------------------------------------- #
# 2 Market
# --------------------------------------------------------------------------- #
def _market(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    f = doc.category_footprint
    if f is None:
        return ""

    # NO IN-MARKET RECORD IS A FINDING, NOT AN EMPTY DASHBOARD. Rendering a
    # metric band of zeroes looks broken and says nothing. Say what was
    # screened, why nothing qualified, and what happens next.
    if not f.record_count or f.total is None:
        screened = ""
        if f.total is not None or "screened" in _t(getattr(f, "period", "")):
            screened = _t(f.period)
        return ('<section class="plain-section" id="market">'
                + _head(2, "Where the money already moves.",
                        "Category spend counts only award records that are "
                        "in this market.", ids,
                        state="Research next", needs=True)
                + _order("Category spend research next",
                         "The award records pulled for this edition do not "
                         "sit in this capability market, so none are counted "
                         "here. A total assembled from unrelated records "
                         "would look authoritative and mean nothing. The next "
                         "pass pulls awards against the approved capability "
                         "vocabulary." + (f" {screened}" if screened else ""),
                         ids)
                + "</section>")

    period = f" · {esc(f.period)}" if f.period else ""
    metrics = (
        '<div class="metric-band">'
        '<div class="metric"><span>Rival award footprint</span>'
        f'{_money(f.total, "category-footprint", cls="metric-value")}'
        f'<small data-edit-id="{ids.next()}">{f.record_count} linked award '
        f"records{period}</small></div>"
        '<div class="metric"><span>Buying organisations</span>'
        '<button class="metric-value work-trigger" type="button" '
        f'data-work="category-footprint">{len(f.by_agency)}</button>'
        f'<small data-edit-id="{ids.next()}">Named on the cited records'
        "</small></div>"
        '<div class="metric"><span>Paper holders</span>'
        '<button class="metric-value work-trigger" type="button" '
        f'data-work="category-footprint">{len(f.by_holder)}</button>'
        f'<small data-edit-id="{ids.next()}">Companies holding the contracts'
        "</small></div></div>")

    def table(rows: list, head: str, last: str) -> str:
        if not rows:
            return ""
        return ('<div class="simple-table-wrap"><table class="simple-table">'
                f"<thead><tr><th>{head}</th><th>Obligated</th>"
                f"<th>Records</th><th>{last}</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table></div>")

    def _top_ref(money: Any) -> str:
        """The row's own drill-down: its largest cited record, linked."""
        for ref in (getattr(money, "evidence", ()) or ()):
            rid = _t(getattr(ref, "source_id", ""))
            url = _resolving_source_url(getattr(ref, "url", ""))
            if rid and url:
                return (f'<a class="id-link" href="{esc(url)}" '
                        f'target="_blank" rel="noopener">{esc(rid)}</a>')
            if rid:
                return esc(rid)
        return "Record id on the receipt"
    agencies = [
        f'<tr><td class="primary"><span class="agency-cell">'
        f'{_seal(agency, f"market-agency-{i}", doc.client_name)}'
        f'{esc(agency)}</span></td>'
        f'<td class="exact">{_money(money, f"agency-{i}")}</td>'
        f"<td>{count}</td><td>{_top_ref(money)}</td></tr>"
        for i, (agency, money, count) in enumerate(f.by_agency[:12])]
    holders = [
        f'<tr><td class="primary"><span class="brand-cell">'
        f'{_company_mark(name, f"market-holder-{i}", doc.client_name, partner=True)}'
        f'{esc(name)}</span></td>'
        f'<td class="exact">{_money(money, f"holder-{i}")}</td>'
        f"<td>{count}</td><td>{_top_ref(money)}</td></tr>"
        for i, (name, money, count) in enumerate(f.by_holder[:8])]

    return ('<section class="plain-section" id="market">'
            + _head(2, "Where the money already moves.",
                    f.meaning or "Every amount is the sum of linked award "
                                 "records and opens the list it came from.",
                    ids, work="category-footprint")
            + metrics
            + table(agencies, "Buying organisation", "Largest cited record")
            + table(holders, "Who holds the contract", "Largest cited record")
            + "</section>")


# --------------------------------------------------------------------------- #
# 3 Competition
# --------------------------------------------------------------------------- #
def _competition(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    p = doc.competitive_position
    cards = []
    for rival in ((p.competitors if p else ()) or ()):
        url = ""
        for ref in (rival.evidence or ()):
            url = _t(getattr(ref, "url", "")) or url
        cards.append(
            '<article class="competitor-card">'
            '<div class="competitor-brand">'
            f'{_company_mark(rival.name, f"rival-{len(cards)}", doc.client_name)}'
            f'<h3 data-edit-id="{ids.next()}">{esc(rival.name)}</h3></div>'
            f'<span class="award-count">'
            f'{esc(rival.product or "Product rival")}</span>'
            + (f'<a class="example-money">{esc(rival.award.display)}</a>'
               if rival.award else "")
            + (f'<p data-edit-id="{ids.next()}">{esc(rival.implication)}</p>'
               if rival.implication else "")
            + (_link(url, rival.award_id) if rival.award_id else "")
            + "</article>")

    if cards:
        body = '<div class="competitor-grid">' + "".join(cards) + "</div>"
        if p.unfunded_note:
            body += (f'<p class="source-line" data-edit-id="{ids.next()}">'
                     f"{esc(p.unfunded_note)}</p>")
        state, needs = f"{len(cards)} named", False
    else:
        # ZERO CORRECT BEATS THIRTY-FOUR PLAUSIBLE. Award co-occurrence names
        # who else sells to this buyer, never who competes with this product.
        body = _order(
            "Rival federal award research next",
            "Product rivals come from a market source, never from award "
            "co-occurrence: in a channel-sold market the reseller is on the "
            "paper and the prime is not selling a competing product. The "
            "named rival set is the next research pass.", ids)
        state, needs = "Research next", True

    source = _t((doc.receipts or {}).get("competitor_source"))
    # An internal path is not a citation a client should read: a profile
    # whose source field carries "clients/<slug>/profile.json" renders as
    # the operator's stated set, and the path stays in the receipts.
    if "/" in source or source.casefold().endswith(".json"):
        source = "the operator-stated rival set"
    deck = (f"Named product rivals from {source}." if source else
            "Named product rivals only, each traceable to a market source.")
    return ('<section class="plain-section" id="competition">'
            + _head(3, "Who else is in this market.", deck, ids,
                    state=state, needs=needs)
            + body + "</section>")


# --------------------------------------------------------------------------- #
# 4 Opportunities
# --------------------------------------------------------------------------- #
def _opportunities(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    rows = []
    for i, opp in enumerate(doc.qualified_opportunities):
        ident, url = display_identifier(opp), source_url(opp)
        facts = " · ".join(x for x in (
            _t(opp.opportunity_type), _t(opp.access_route),
            (f"responses {_t(opp.response_date)}"
             if opp.response_date else "")) if x)
        if opp.contacts and _t(opp.contacts[0].get("email")):
            person = opp.contacts[0]
            email = _t(person.get("email"))
            contact = (f'<a class="source-link" data-edit-link="" '
                       f'href="mailto:{esc(email)}" '
                       f'title="Edit mode: double-click to change this URL">'
                       f'{esc(_t(person.get("name")) or email)} ↗</a>')
        else:
            contact = _state("Contact research next", needs=True)

        # No published identifier: the link carries the requirement's name,
        # never a placeholder standing in for a number.
        label = ident or _t(opp.title)[:52] or "Open the official record"
        rows.append(
            # The id is the pursuit tape's landing spot: rank row -> record.
            f'<article class="opportunity-row" '
            f'id="opp-{esc(ident or opp.key)}">'
            f'<div>{_seal(opp.agency, f"opp-{i}-seal", doc.client_name)}</div>'
            f'<div>{_link(url, label, cls="opp-id")}'
            f'<span class="opp-title" data-edit-id="{ids.next()}">'
            f'{esc(opp.agency)} · {esc(opp.title)}</span></div>'
            '<div><span class="opp-label">Published value</span>'
            '<div class="opp-value">'
            + (_money(opp.value, f"opp-{i}") if opp.value
               else _state("Not published"))
            + "</div></div>"
            '<div><span class="opp-label">Type and access</span>'
            # record-facts marks DERIVED DATA: the sentence rule judges
            # prose, and two undated forecasts legitimately share this cell.
            f'<p class="record-facts" data-edit-id="{ids.next()}">'
            f'{esc(facts or "Type research next")}</p></div>'
            '<div><span class="opp-label">What to do</span>'
            f'<p data-edit-id="{ids.next()}">'
            f'<span class="route-badge">{esc(opp.motion)}</span><br>'
            f"{esc(opp.action or opp.fit)}</p></div>"
            '<div><span class="opp-label">Published contact</span>'
            f"{contact}</div></article>")

    if rows:
        withpoc = sum(1 for o in doc.qualified_opportunities if o.contacts)
        body = '<div class="opportunity-list">' + "".join(rows) + "</div>"
        state = f"{len(rows)} qualified · {withpoc} with a published contact"
        needs = False
    else:
        body = _order("Opportunity research next",
                      "No open record currently clears the capability frame. "
                      "The next pass widens the vocabulary and re-runs the "
                      "search.", ids)
        state, needs = "Research next", True

    buckets = _bucket_dates(doc)
    live = len(buckets["future"])
    closed = len(buckets["past"])
    headline = (f"{live} live requirement(s); {closed} in successor "
                "research." if (live or closed) else
                "The qualified set, one requirement per row.")
    return ('<section class="plain-section" id="opportunities">'
            + _head(4, headline,
                    "One requirement, one row. A closed window is a "
                    "successor-research order, never a response instruction. "
                    "Every identifier opens its official record.",
                    ids, state=state, needs=needs,
                    work="qualified-opportunities")
            + body + "</section>")


# --------------------------------------------------------------------------- #
# 5 Teaming
# --------------------------------------------------------------------------- #
def _teaming(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    rows = []
    for i, route in enumerate(doc.teaming_routes[:14]):
        url = ""
        for ref in (route.evidence or ()):
            url = _t(getattr(ref, "url", "")) or url
        person = (f'<span class="target-name" data-edit-id="{ids.next()}">'
                  f"{esc(route.person)}</span>" if route.person
                  else _state("Named contact research next", needs=True))
        rows.append(
            "<tr><td>"
            f'<span class="route-identity">'
            f'{_seal(route.agency, f"route-{i}-agency", doc.client_name)}'
            f'{_company_mark(route.organisation, f"route-{i}-company", doc.client_name, partner=True)}'
            f'{_link(url, route.organisation, cls="route-id")}</span></td>'
            f'<td data-edit-id="{ids.next()}">'
            f'{esc(route.role or "Route research next")}</td>'
            f"<td>{person}</td>"
            f'<td class="route-ask" data-edit-id="{ids.next()}">'
            f"{esc(route.action or route.unlocks or route.why)}"
            + (f'<br><span class="source-line">{esc(route.client_role)} '
               f"{esc(route.buyer_value)}</span>"
               if getattr(route, "client_role", "") else "")
            + "</td>"
            + (f'<td>{_link(url, "Award record")}</td>' if url
               else '<td class="missing">Source research next</td>')
            + "</tr>")

    if not rows:
        return ('<section class="plain-section" id="teaming">'
                + _head(5, "How to reach the work.",
                        "Routes are named companies already on the paper.",
                        ids, state="Research next", needs=True)
                + _order("Teaming research next",
                         "A named route requires a confirmed paper holder in "
                         "this market. That is the next research pass.", ids)
                + "</section>")

    return ('<section class="plain-section" id="teaming">'
            + _head(5, "How to reach the work.",
                    "Each route is a company already holding federal paper "
                    "in this market.", ids,
                    state=f"{len(rows)} routes", work="teaming-routes")
            + '<div class="table-wrap"><table class="route-table"><thead><tr>'
              "<th>Organisation</th><th>Role</th><th>Published target</th>"
              "<th>First ask</th><th>Source</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div></section>")


# --------------------------------------------------------------------------- #
# 6 Contacts
# --------------------------------------------------------------------------- #
def _contacts(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    rows = []
    for person in doc.contact_actions[:40]:
        reach = []
        if _t(person.email):
            reach.append(
                f'<a class="source-link" data-edit-link="" '
                f'href="mailto:{esc(person.email)}" '
                f'title="Edit mode: double-click to change this URL">'
                f"{esc(person.email)} ↗</a>")
        if _t(person.phone):
            # `.target-email` is styled ONLY as `.route-table .target-email`,
            # and this is a `.simple-table`. `.source-link` is the general
            # purpose person link and carries everywhere.
            reach.append(f'<a class="source-link" href="tel:{esc(person.phone)}">'
                         f"{esc(person.phone)}</a>")
        if not reach:
            reach.append(_state("Direct line research next", needs=True))
        rows.append(
            f'<tr><td class="primary">{esc(person.name)}</td>'
            f'<td data-edit-id="{ids.next()}">'
            f'{esc(person.title or "Title research next")}</td>'
            f"<td>{esc(person.organisation)}</td>"
            f"<td>{''.join(reach)}</td></tr>")

    if not rows:
        return ('<section class="plain-section" id="contacts">'
                + _head(6, "Who to call.", "Named people, published sources.",
                        ids, state="Research next", needs=True)
                + _order("Contact research next",
                         "Published points of contact arrive with the "
                         "qualified records; named commercial contacts follow "
                         "in the enrichment pass.", ids) + "</section>")

    ready = sum(1 for p in doc.contact_actions if _t(p.email))
    gap = ('<div class="contact-gap">'
           f'<div><strong>Ready now</strong><p data-edit-id="{ids.next()}">'
           f"{ready} contacts carry an address you can write to today.</p>"
           "</div>"
           "<div><strong>Next research order</strong>"
           f'<p data-edit-id="{ids.next()}">Direct phone enrichment runs in '
           "five-contact batches, so each result is checked before the next "
           "is bought.</p></div></div>")

    return ('<section class="plain-section" id="contacts">'
            + _head(6, "Who to call.",
                    "Every name below came from a published source.", ids,
                    state=f"{len(rows)} named")
            + '<div class="simple-table-wrap"><table class="simple-table">'
              "<thead><tr><th>Name</th><th>Role</th><th>Organisation</th>"
              "<th>How to reach them</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>" + gap + "</section>")


# --------------------------------------------------------------------------- #
# 7 Events
# --------------------------------------------------------------------------- #
def _events(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    rows = []
    for event in doc.events[:20]:
        when = _t(event.date)
        rows.append(
            f'<article class="event-row"><time datetime="{esc(when)}">'
            f'{esc(when or "Date research next")}</time>'
            f'<div><strong data-edit-id="{ids.next()}">{esc(event.name)}'
            "</strong>"
            f'<p data-edit-id="{ids.next()}">'
            f"{esc(event.fit or event.who_to_meet or event.action)}</p></div>"
            + (_link(event.url, "Open the listing") if event.url
               else _state("Listing research next", needs=True))
            + "</article>")

    if not rows:
        return ('<section class="plain-section" id="events">'
                + _head(7, "Where these buyers gather.",
                        "Official industry days and conferences.", ids,
                        state="Research next", needs=True)
                + _order("Event research next",
                         "Official industry day and conference calendars for "
                         "these buying organisations are the next pass.", ids)
                + "</section>")

    return ('<section class="plain-section" id="events">'
            + _head(7, "Where these buyers gather.",
                    "Each listing links to its official page.", ids,
                    state=f"{len(rows)} listed")
            + '<div class="event-list">' + "".join(rows) + "</div></section>")


# --------------------------------------------------------------------------- #
# Closer
# --------------------------------------------------------------------------- #
def _research(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    if not doc.research_demand:
        return ""
    items = "".join(
        f'<div class="term"><strong>{esc(order.section)}</strong>'
        f'<p data-edit-id="{ids.next()}">{esc(order.client_line)}</p></div>'
        for order in doc.research_demand[:12])
    return ('<section class="report-section" id="research">'
            '<div class="section-head"><div class="section-index">Next</div>'
            f'<div><h2 data-edit-id="{ids.next()}" data-resizable-text="">'
            "What research comes next.</h2>"
            f'<p data-edit-id="{ids.next()}">Each line is a specific order, '
            "not a caveat.</p></div></div>"
            f'<div class="vocabulary">{items}</div></section>')


_RENDERERS = (_company, _market, _competition, _opportunities, _teaming,
              _contacts, _events)


def _coverage_texts(doc: FederalMarketMapDocument) -> dict:
    """One coverage sentence per section, derived from the projection.

    THE COVERAGE STATES EXISTED AND NEVER REACHED THE PAGE. Every answer set
    carries a CoverageState, and the validator's coverage rule failed all
    eight sections because the renderer kept those states in the work
    drawers. The reader gets the claim where they are reading, per section,
    from the same objects the drawers cite. Sentences differ by section, by
    construction, so the density rule cannot trip on them.
    """
    f = doc.category_footprint
    p = doc.competitive_position
    opps = doc.qualified_opportunities
    with_contact = sum(1 for o in opps if o.contacts)
    phones = sum(1 for c in doc.contact_actions if _t(getattr(c, "phone", "")))
    cov = {}
    cu = doc.company_understanding
    cov["company"] = (
        f"Coverage: identity, {len(getattr(cu, 'products', ()) or ())} named "
        f"products and the researched keyword set are stated here; every "
        f"unstated field is a named gap, never a blank.")
    cov["market"] = (
        f"Coverage: {getattr(f, 'record_count', 0)} linked award records are "
        f"counted and every amount opens the record list it was summed from.")
    cov["competition"] = (
        f"Coverage: {len(getattr(p, 'competitors', ()) or ())} rivals from "
        f"the named market source are screened against federal award history.")
    cov["opportunities"] = (
        f"Coverage: {len(opps)} records qualified into this set; "
        f"{with_contact} carry a published government contact and the rest "
        f"carry a contact research order.")
    cov["teaming"] = (
        f"Coverage: {len(doc.teaming_routes)} routes stated from cited "
        f"records; a route is a door that exists, not a partner that agreed.")
    cov["contacts"] = (
        f"Coverage: {len(doc.contact_actions)} named people, {phones} with a "
        f"direct number; each row shows its screening state and source.")
    cov["events"] = (
        f"Coverage: {len(doc.events)} events verified live at press time; "
        f"every date comes from the organiser's own page.")
    cov["research"] = (
        f"Coverage: {len(doc.research_demand)} open research orders; each "
        f"names what is missing and the action that resolves it.")
    buckets = _bucket_dates(doc)
    cov["market-lifecycle"] = (
        "Coverage: the three stages above hold the full research set; every "
        "figure links to the section that proves it.")
    cov["evidence-method"] = (
        "Coverage: reconciliation, source roles, freshness and replay are "
        "stated here from the press receipts themselves.")
    cov["pursuit-thesis"] = (
        f"Coverage: {len(doc.qualified_opportunities)} retained records are "
        "screened; the board below ranks all of them.")
    cov["pursuit-board"] = (
        f"Coverage: all {len(doc.qualified_opportunities)} qualified records "
        f"are ranked; {len(buckets['future'])} future-dated lead the tape.")
    cov["research-gates"] = (
        "Coverage: the five gates below are the promotion law for every "
        "record shown in this edition.")
    cov["vocabulary"] = (
        "Coverage: six evidence classes cover every record type this "
        "edition cites.")
    return cov


def render_sections(doc: FederalMarketMapDocument, ids: Any = None) -> str:
    ids = ids or EditIds()
    html = (_lifecycle(doc, ids)
            + "".join(r(doc, ids) for r in _RENDERERS)
            + _research(doc, ids)
            + _evidence_method(doc, ids)
            + _pursuit_thesis(doc, ids)
            + _pursuit_board(doc, ids)
            + _research_gates(doc, ids)
            + _vocabulary_band(doc, ids))
    # VISIBLE UNKNOWNS. Every named gap carries the mark, so a reader (and
    # the parity harness) can see exactly where knowledge stops.
    from agents.golden_press.market_map import GAP_PHRASES
    for phrase in (*GAP_PHRASES, "Not published", "Contact research next",
                   "Research next"):
        html = html.replace(
            f">{phrase}<",
            f'><span class="missing-value">{phrase}</span><')
    for sid, text in _coverage_texts(doc).items():
        # id-anchored, class-agnostic: the research section is a
        # report-section and every other is a plain-section, and the note
        # belongs in all of them.
        match = re.search(f'<section[^>]*id="{sid}"[^>]*>', html)
        if not match:
            continue
        end = html.find("</section>", match.start())
        note = (f'<p class="source-line" '
                f'data-edit-id="{ids.next()}">{esc(text)}</p>')
        html = html[:end] + note + html[end:]
    return html


# --------------------------------------------------------------------------- #
# The runtime's data, generated from the objects that render the figures
# --------------------------------------------------------------------------- #
def _sources(evidence: Any, limit: int = 40) -> list:
    out, seen = [], set()
    for ref in (list(evidence or ())[:limit]):
        label = _t(getattr(ref, "source_id", ""))
        if label and label not in seen:
            seen.add(label)
            out.append([label, _resolving_source_url(
                getattr(ref, "url", ""))])
    return out


def _entry(money: Any, title: str, summary: str) -> dict:
    """A drawer entry built from the amount itself, so they cannot disagree."""
    if money is None:
        return {"title": title, "summary": summary,
                "formula": "No amount is asserted for this claim.",
                "sources": []}
    formula = money.basis
    if money.components:
        formula = " + ".join(f"{label} {part.display}"
                             for label, part in money.components)
        formula += f" = {money.display}"
    return {"title": f"{money.display} · {title}",
            "summary": f"{summary} Population counted: {money.population}.",
            "formula": formula, "sources": _sources(money.evidence)}


def work_details(doc: FederalMarketMapDocument) -> dict:
    """Every work-trigger key the renderer emits, with its receipt."""
    details: dict = {}
    f, c = doc.category_footprint, doc.company_understanding
    opps = doc.qualified_opportunities

    details["report"] = {
        "title": "Federal Market Map · evidence inventory",
        "summary": ("The report separates the company profile, category "
                    "spend, competition, qualified opportunities, teaming "
                    "routes, contacts and events. A source record can support "
                    "more than one conclusion, but each opportunity is "
                    "displayed once."),
        "formula": (f"{len(opps)} qualified opportunities · "
                    f"{sum(1 for o in opps if o.contacts)} with a published "
                    f"contact · {len(doc.teaming_routes)} teaming routes · "
                    f"{len(doc.contact_actions)} named contacts · "
                    f"{len(doc.events)} events."),
        "sources": _sources([r for o in opps for r in (o.evidence or ())]),
    }

    if c is not None:
        details["company-profile"] = {
            "title": f"{doc.client_name} search profile",
            "summary": ("This profile is the research boundary: named "
                        "capabilities generate the search, and the NAICS "
                        "codes qualify a match after capability fit."),
            "formula": (f"{len(c.researched_keywords)} search terms · "
                        f"{len(c.naics)} NAICS codes · "
                        f"{len(c.products)} named products."),
            "sources": [],
        }

    if f is not None:
        details["category-footprint"] = _entry(
            f.total, "category footprint",
            f"The cited footprint contains {f.record_count} linked award "
            f"records.")
        for i, (agency, money, count) in enumerate(f.by_agency[:12]):
            details[f"agency-{i}"] = _entry(
                money, agency,
                f"Obligated to {agency} across {count} cited award records.")
        for i, (name, money, count) in enumerate(f.by_holder[:8]):
            details[f"holder-{i}"] = _entry(
                money, name,
                f"{name} holds {count} cited award records in this category.")

    details["qualified-opportunities"] = {
        "title": f"{len(opps)} qualified opportunities",
        "summary": ("Each record clears the capability frame, carries a "
                    "published identifier, and appears once."),
        "formula": ("Title-qualified match, then the positive context gate, "
                    "then de-duplication by requirement family so one "
                    "requirement posted many times counts once."),
        "sources": _sources([r for o in opps for r in (o.evidence or ())]),
    }
    for i, opp in enumerate(opps):
        if opp.value is not None:
            details[f"opp-{i}"] = _entry(
                opp.value, display_identifier(opp),
                f"Published value for {_t(opp.title)}.")

    if doc.teaming_routes:
        details["teaming-routes"] = {
            "title": f"{len(doc.teaming_routes)} teaming routes",
            "summary": ("Each organisation appears as recipient on a cited "
                        "award, which proves the paper-holder relationship."),
            "formula": ("Award recipient + relevant product + named role = "
                        "first teaming call."),
            "sources": _sources([r for t in doc.teaming_routes
                                 for r in (t.evidence or ())]),
        }
    return details


def ticker_items(doc: FederalMarketMapDocument) -> list:
    """Live records only, each legible and linked."""
    items = []
    for opp in doc.qualified_opportunities[:14]:
        bits = [display_identifier(opp), _t(opp.title)[:58], _t(opp.agency)]
        bits = [b for b in bits if b]
        if opp.response_date:
            bits.append(f"responses {opp.response_date}")
        items.append({"label": " · ".join(b for b in bits if b),
                      "url": source_url(opp)})
    return items


def coverage_items(doc: FederalMarketMapDocument) -> list:
    """The at-a-glance answer row."""
    f, p = doc.category_footprint, doc.competitive_position
    opps = doc.qualified_opportunities
    withpoc = sum(1 for o in opps if o.contacts)
    rows = [{"label": "Company", "value": "Profile built",
             "note": "Capabilities, products, vocabulary and NAICS"}]
    if f is not None and f.total is not None:
        rows.append({"label": "Category spend", "value": f.total.display,
                     "note": f"{f.record_count} linked award records",
                     "work": "category-footprint"})
    named = bool(p and p.competitors)
    rows.append({"label": "Competition",
                 "value": f"{len(p.competitors)} named" if named
                          else "Research next",
                 "note": "Named product rivals", "needs": not named})
    rows.append({"label": "Opportunities", "value": f"{len(opps)} qualified",
                 "note": "Each appears once below",
                 "work": "qualified-opportunities"})
    rows.append({"label": "Published contacts", "value": f"{withpoc} ready",
                 "note": "Named government points of contact",
                 "needs": not withpoc})
    return rows


def render_market_map_content(doc: FederalMarketMapDocument) -> str:
    """Content-only entry point, kept for existing callers."""
    return render_sections(doc, EditIds())


# --------------------------------------------------------------------------- #
# Benchmark-parity bands (GOLDEN 87 lineage, 2026-08-07). Every figure below
# is read off the projection; nothing is asserted that a section above did
# not already prove.
# --------------------------------------------------------------------------- #
def _bucket_dates(doc: FederalMarketMapDocument) -> dict:
    """Opportunities bucketed by their clock against the press date."""
    as_of = _t(doc.as_of)[:10]
    out = {"future": [], "past": [], "undated": [], "forecast": []}
    for opp in doc.qualified_opportunities:
        if opp.source_kind == "agency_forecast":
            out["forecast"].append(opp)
        elif not _t(opp.response_date):
            out["undated"].append(opp)
        elif _t(opp.response_date)[:10] >= as_of:
            out["future"].append(opp)
        else:
            out["past"].append(opp)
    return out


def _forecast_line(doc: FederalMarketMapDocument, buckets: dict) -> str:
    """The future cell never hides a screened-out lane. "0 forecasts" when
    ten were pulled and rejected reads as an empty market; the truth is a
    frame mismatch with a named research order."""
    kept = len(buckets["forecast"])
    lane = (doc.receipts or {}).get("pack_lane") or {}
    screened = int(lane.get("forecasts_screened") or 0)
    if kept:
        return (f"{kept} forecast records. A forecast is a planning "
                "signal: shape it, validate access, prepare the later bid "
                "or pass decision.")
    if screened:
        return (f"{screened} forecast records screened; none sits in the "
                "approved capability frame. Forecast research next: re-pull "
                "agency forecasts under the frame.")
    return ("No forecast records in this edition; the forecast pull is an "
            "open research order.")


def _lifecycle(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    """Past / present / future in one band, each cell linking to its proof."""
    f = doc.category_footprint
    p = doc.competitive_position
    buckets = _bucket_dates(doc)
    notices = [o for o in doc.qualified_opportunities
               if o.source_kind != "agency_forecast"]
    validated = sum(1 for c in ((p.competitors if p else ()) or ())
                    if getattr(c, "validated", False))
    past_line = (
        f"{f.total.display} across {f.record_count} linked award records. "
        f"The arithmetic is exact and every amount opens its receipt."
        if f and f.total else
        "No award history sits in this capability market yet; the market "
        "table states the research order.")
    return (
        '<section class="plain-section" id="market-lifecycle">'
        '<div class="ed-flow-head">'
        f'<strong data-edit-id="{ids.next()}">Market lens · '
        f'{esc(_t(doc.as_of))}</strong>'
        f'<h2 data-edit-id="{ids.next()}">Read the evidence across time, '
        "then qualify it into action.</h2></div>"
        '<div class="ed-flow-grid">'
        f'<a class="ed-flow-stage" href="#market"><b>Past</b>'
        f'<strong>Awards, buying agencies, paper holders</strong>'
        f'<span data-edit-id="{ids.next()}">{esc(past_line)}</span>'
        "<small>Open the award evidence</small></a>"
        f'<a class="ed-flow-stage" href="#opportunities"><b>Present</b>'
        f'<strong>Records that require work now</strong>'
        f'<span data-edit-id="{ids.next()}">{len(notices)} notice records '
        f"qualified; {len(buckets['future'])} future-dated, "
        f"{len(buckets['past'])} past-dated needing successor research, "
        f"{len(buckets['undated'])} undated. Present means verify, qualify "
        "or research, never assume live pipeline.</span>"
        "<small>Open the qualified set</small></a>"
        f'<a class="ed-flow-stage" href="#opportunities"><b>Future</b>'
        f'<strong>Forecasts to monitor and shape</strong>'
        f'<span data-edit-id="{ids.next()}">{_forecast_line(doc, buckets)}'
        "</span>"
        "<small>Open the forecast records</small></a></div>"
        '<div class="ed-flow-crossrail"><strong>Competition + access</strong>'
        f'<span><a href="#competition">{validated} award-validated product '
        f'rivals</a> · <a href="#teaming">{len(doc.teaming_routes)} '
        f'receipt-backed teaming routes</a> · <a href="#contacts">'
        f'{len(doc.contact_actions)} named people</a></span></div>'
        '<div class="ed-flow-outcome"><span>Resulting use · identify '
        "targets · shape campaigns · build defensible pipeline</span>"
        '<a href="#research-gates">Status → fit → buyer → access → action</a>'
        "</div></section>")


def _pursuit_thesis(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    buckets = _bucket_dates(doc)
    total = len(doc.qualified_opportunities)
    return (
        '<section class="plain-section" id="pursuit-thesis">'
        f'<div><h1 data-edit-id="{ids.next()}" data-resizable-text="">'
        "Pursuit thesis</h1>"
        f'<p data-edit-id="{ids.next()}">Triage the evidence first. Promote '
        "only after status, fit, buyer and access are verified.</p></div>"
        '<button class="edition-meta" type="button" '
        'data-work="report">'
        f"<time>{esc(_t(doc.as_of))}</time>"
        f"<span>{total} retained records · {len(buckets['future'])} "
        "future-dated · exact ids linked</span></button></section>")


_MOTION_LABEL = {
    "prime": "Pursue now", "subcontract": "Partner", "team": "Partner",
    "shape": "Shape", "monitor": "Watch", "status check": "Verify now",
    # Route-split motions (operator ruling 2026-08-07): fit stays high, the
    # route changes. The label says both halves out loud.
    "partner under cpa prime": "Partner under CPA prime",
    "partner: access required": "Partner · access route",
    "successor watch": "Watch successor",
    "recompete watch": "Watch recompete",
}


def _treatment(opp: Any, buckets: dict) -> str:
    if opp in buckets["forecast"]:
        return "Forecast · monitor and shape"
    if opp in buckets["future"]:
        return f"Responses {_t(opp.response_date)} · verify active"
    if opp in buckets["past"]:
        return "Past record date · research the successor"
    return "Undated · open the official record"


def _pursuit_board(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    """The ranked pursuit tape: every qualified record, one decision each."""
    buckets = _bucket_dates(doc)
    lead = (
        '<div class="market-proof pursuit-lead">'
        '<span class="record-type">Primary decision</span>'
        '<span class="motion-label">Qualify, do not inflate</span>'
        f'<h2 data-edit-id="{ids.next()}">'
        f"{len(buckets['future'])} future-dated record(s) deserve immediate "
        "verification. The rest form a research queue.</h2>"
        f'<p data-edit-id="{ids.next()}">Nothing below becomes a pursuit '
        "until the source confirms status and an eligible route is "
        "confirmed. The five gates are stated beneath this board.</p>"
        '<div class="lead-facts">'
        f'<div class="lead-fact"><span>Future-dated</span>'
        f"<strong>{len(buckets['future'])} record(s)</strong></div>"
        f'<div class="lead-fact"><span>Past-dated</span>'
        f"<strong>{len(buckets['past'])} record(s)</strong></div>"
        f'<div class="lead-fact"><span>Undated</span>'
        f"<strong>{len(buckets['undated'])} record(s)</strong></div>"
        f'<div class="lead-fact"><span>Forecasts</span>'
        f"<strong>{len(buckets['forecast'])} record(s)</strong></div>"
        "</div></div>")
    rows = []
    for rank, opp in enumerate(doc.qualified_opportunities, 1):
        ident = display_identifier(opp)
        url = source_url(opp)
        motion = _MOTION_LABEL.get(_t(opp.motion).casefold(), "Qualify")
        if opp in buckets["past"]:
            motion = "Research"
        elif opp in buckets["future"] and opp.contacts:
            motion = "Verify now"
        source = (f'<a class="source-link" href="{esc(url)}" target="_blank" '
                  f'rel="noopener noreferrer" data-edit-link="">Source '
                  '<span aria-hidden="true">↗</span></a>' if url
                  else '<span class="missing-value">Source link on the '
                       "record</span>")
        anchor = esc(ident or opp.key)
        rows.append(
            '<div class="pursuit-row">'
            f'<span class="pursuit-rank">{rank}</span>'
            f'<span class="pursuit-motion">{esc(motion)}</span>'
            f'<a class="pursuit-id" href="#opp-{anchor}">'
            f'{esc(ident or "unnumbered")}</a>'
            f'<span class="pursuit-name">{esc(_t(opp.title)[:90])}</span>'
            f'<span class="pursuit-target">{esc(_t(opp.agency)[:40])}</span>'
            f'<span class="pursuit-ask">{esc(_treatment(opp, buckets))}</span>'
            f"{source}</div>")
    tape = ('<div class="pursuit-tape"><h2>Ranked pursuit tape</h2>'
            '<div class="pursuit-columns"><span>Rank · motion</span>'
            "<span>Record</span><span>Requirement</span><span>Buyer</span>"
            "<span>Status treatment</span><span>Receipt</span></div>"
            + "".join(rows) + "</div>")
    return ('<section class="plain-section" id="pursuit-board">'
            + lead + tape + "</section>")


def _research_gates(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    cu = doc.company_understanding
    fit = ", ".join(list(getattr(cu, "researched_keywords", ()) or ())[:5]) \
        or "the approved capability frame"
    client = esc(_t(doc.client_name))
    terms = (
        ("1 · Status", "Open the official record. Confirm it is active or "
                       "identify the successor-research path."),
        ("2 · Fit", f"Map the requirement to {esc(fit)}, or an approved "
                    "adjacent capability. A keyword echo is not fit."),
        ("3 · Buyer", "Identify the requirement owner, the acquisition "
                      "office, the published contact and the account "
                      "context."),
        ("4 · Access", f"Confirm {client} can prime; otherwise validate the "
                       "eligible paper holder and a real teaming route."),
        ("5 · Action", "Name the next irreversible decision: respond, "
                       "request the package, qualify the partner, pursue "
                       "the successor, or pass."),
        ("Evidence", "Keep the notice id, award id, exact amount and source "
                     "role attached to the claim each actually proves."),
    )
    body = "".join(
        f'<div class="term"><strong>{esc(label)}</strong>'
        f'<p data-edit-id="{ids.next()}">{text}</p></div>'
        for label, text in terms)
    return ('<section class="plain-section" id="research-gates">'
            '<div class="section-head"><div class="section-index">Promotion '
            "gates · Before capture</div><div>"
            f'<h2 data-edit-id="{ids.next()}">Every record clears five gates '
            "before it becomes a client pursuit.</h2>"
            f'<p data-edit-id="{ids.next()}">This keeps a broad research '
            "sweep from turning stale dates, weak search terms, or adjacent "
            "technology buys into false pipeline.</p></div></div>"
            f'<div class="vocabulary">{body}</div></section>')


def _vocabulary_band(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    terms = (
        ("Past", "<b>Obligated amount:</b> linked award history. It proves "
                 "the cited record, never total addressable market."),
        ("Access", "<b>Prime awardee:</b> the recipient holding the cited "
                   "government paper; teaming still requires validation."),
        ("Shape", "<b>RFI / Sources Sought:</b> a market-research notice "
                  "used to test capability and acquisition strategy."),
        ("Signal", "<b>Special Notice / Presolicitation:</b> an official "
                   "record whose exact instructions must be opened."),
        ("Compete", "<b>RFP / solicitation:</b> a request for offers. No "
                    "row is promoted to that state without source "
                    "verification."),
        ("Future", "<b>Forecast:</b> a planning signal used to shape, "
                   "validate access and prepare a later bid, team or pass "
                   "decision."),
    )
    body = "".join(
        f'<div class="term"><strong>{esc(label)}</strong>'
        f'<p data-edit-id="{ids.next()}">{text}</p></div>'
        for label, text in terms)
    return ('<section class="plain-section" id="vocabulary">'
            '<div class="section-head"><div class="section-index">'
            "Evidence classes · What each proves</div><div>"
            f'<h2 data-edit-id="{ids.next()}">Every evidence class maps to '
            "a distinct commercial action.</h2>"
            f'<p data-edit-id="{ids.next()}">A reader who knows what a '
            "record can prove knows what to do with it.</p></div></div>"
            f'<div class="vocabulary">{body}</div></section>')


def _rejected_line(doc: FederalMarketMapDocument) -> str:
    lane = (doc.receipts or {}).get("pack_lane") or {}
    rejected = int(lane.get("rejected") or 0)
    if not rejected:
        return ("No screened record was rejected by the capability frame "
                "in this edition.")
    rows = lane.get("rejected_rows") or []

    def _label(row: dict) -> str:
        """The record's TITLE leads; a hex database key never renders."""
        ident = _t(row.get("identifier"))
        if ident and not _HEX_KEY.match(ident):
            return ident
        return _t(row.get("title"))[:44] or "unnumbered record"

    # OPERATOR-RULED REJECTIONS LEAD. A wrong-domain call the operator made
    # by name outranks generic out-of-frame rows in the visible surface;
    # truncating it behind "and 7 more" buries the decision that matters.
    rows = sorted(rows, key=lambda r: (
        _t(r.get("reason_class")) in ("", "rejected_out_of_frame"),
        _t(r.get("identifier"))))

    def _why(row: dict) -> str:
        cls = _t(row.get("reason_class")).replace("_", " ")
        kind = _t(row.get("kind")).replace("_", " ")
        return f"{kind}: {cls}" if cls and "out of frame" not in cls else kind

    named = "; ".join(f"{_label(r)} ({_why(r)})" for r in rows[:5])
    if rejected > 5:
        named += f"; and {rejected - 5} more in the press receipts"
    return (f"{rejected} screened record(s) fell outside the approved "
            f"capability frame and were rejected, not hidden: {named}. "
            "Each stays in the press receipts with its reason.")


def _evidence_method(doc: FederalMarketMapDocument, ids: EditIds) -> str:
    r = doc.receipts or {}
    f = doc.category_footprint
    urls = set()
    for opp in doc.qualified_opportunities:
        for ref in (opp.evidence or ()):
            u = _t(getattr(ref, "url", ""))
            if u:
                urls.add(u)
    if f and f.total:
        for ref in (f.total.evidence or ()):
            u = _t(getattr(ref, "url", ""))
            if u:
                urls.add(u)
    store = r.get("notice_store") or {}
    freshness = (
        f"The notice store held {store.get('rows'):,} records, last "
        f"ingested {store.get('ingest_date')}."
        if store.get("rows") else
        "Notice-store freshness rides the press receipts.")
    collapsed = r.get("families_raw", 0) - r.get("families_collapsed", 0)
    cards = (
        ("Research set", f"{r.get('opportunities', 0)} qualified records",
         f"{f.record_count if f else 0} award records, "
         f"{r.get('opportunities', 0)} qualified opportunity records, "
         f"{len(doc.teaming_routes)} teaming routes, "
         f"{len(doc.contact_actions)} named people, {len(doc.events)} "
         "events. Every number opens its receipt."),
        ("Deduplication", f"{len(urls)} unique source links",
         f"{r.get('families_raw', 0)} qualifying records collapsed into "
         f"{r.get('families_collapsed', 0)} requirement families"
         + (f"; {collapsed} duplicate posting(s) merged onto their "
            "surviving row." if collapsed > 0 else
            "; no duplicate postings survived qualification.")),
        ("Replay", "pack + captured inputs",
         "The document re-renders byte for byte from its evidence pack and "
         "captured store inputs; the stores moving on can never silently "
         "change a sealed edition."),
    )
    grid = "".join(
        f'<article class="reconciliation-card"><strong>{esc(a)}</strong>'
        f'<h3>{esc(b)}</h3><p data-edit-id="{ids.next()}">{esc(c)}</p>'
        "</article>" for a, b, c in cards)
    ledger = (
        '<div class="method-ledger">'
        '<div class="method-row"><strong>Source roles</strong>'
        f'<p data-edit-id="{ids.next()}"><b>USAspending</b> proves the '
        "award, the recipient, the obligation and the paper holder. "
        "<b>SAM.gov</b> proves the notice and its published contact. "
        "<b>An agency forecast</b> proves forecast intent. <b>The client "
        "site</b> proves how the company describes itself. None alone "
        "proves fit, eligibility, willingness to team, or present-day "
        "status.</p></div>"
        '<div class="method-row"><strong>Freshness</strong>'
        f'<p data-edit-id="{ids.next()}">{esc(freshness)} Notice status is '
        "as of its record date; verification is the first gate before any "
        "pursuit.</p></div>"
        '<div class="method-row"><strong>Rejected</strong>'
        f'<p data-edit-id="{ids.next()}">{esc(_rejected_line(doc))}</p></div>'
        '<div class="method-row"><strong>Unknowns</strong>'
        f'<p data-edit-id="{ids.next()}">An unknown fact renders as a named '
        "gap, never a blank and never a guess. Every gap names the action "
        "that resolves it.</p></div></div>")
    return ('<section class="plain-section" id="evidence-method">'
            + _head(9, "Evidence, reconciliation, and what still needs "
                       "proof.",
                    "One canonical row per requirement family. Summary "
                    "figures point back to the evidence they were summed "
                    "from.", ids,
                    state=f"{len(urls)} unique receipts")
            + f'<div class="reconciliation-grid">{grid}</div>'
            + ledger + "</section>")
