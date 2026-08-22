"""The Federal Market Map: seven sections, no padding, every gap stated.

THE CONTRACT IS docs/MARKET_MAP_CONTRACT.md. This module renders it and
nothing else. Three laws drive every decision here:

  L-M1  say what we have AND what we do not. A column that could be empty
        states the gap in words. A blank cell is a defect; a stated gap is
        a work order.
  L-M2  never repeat to fill space. A fact appears once, in the section
        that owns it. A person serving several motions is stated ONCE with
        its motions named, never duplicated per motion.
  L-M3  no prose a number could replace. Figures, ids, links, names, dates,
        actions. The operator does the talking.

WHY IT EXISTS. The assessment family drifted: 38 model-prose slots against
a standing no-prose rule, one contact rendered twenty times because twenty
specs named the same agency, and a banned invented word shipped an artifact
uncertified. The bands enforced structure and nothing enforced density.
This family is deterministic end to end: there is no model call in this
module, so there is no door for prose to enter.
"""

from __future__ import annotations

from html import escape as _esc
from typing import Any

MARKET_MAP_VERSION = "market_map.v1.2026-08-06"

SECTIONS = ("company", "market", "competition", "opportunities",
            "teaming", "contacts", "events")

# L-M1: the only sanctioned ways to say "we do not have this". A cell must
# carry a value or one of these; anything else is a blank and is a defect.
GAP_PHRASES = (
    "Not in current research",
    "Enrichment needed",
    "Named alliance lead needed",
    "No published contact on this record",
    "Not established on this evidence",
    "None carried in this edition",
)

_USASPENDING = "https://www.usaspending.gov/award/"
_SAM = "https://sam.gov/opp/"


def _clean(v: Any) -> str:
    return " ".join(str(v or "").split())


def esc(v: Any) -> str:
    return _esc(_clean(v), quote=True)


def _money(v: Any) -> str:
    try:
        return "${:,.2f}".format(float(v or 0))
    except (TypeError, ValueError):
        return "$0.00"


def _link(url: Any, text: Any) -> str:
    url = _clean(url)
    if not url:
        return esc(text)
    return (f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">'
            f'{esc(text)} &#8599;</a>')


def _record_url(record: Any) -> str:
    url = _clean(getattr(record, "url", ""))
    if url:
        return url
    rid = _clean(getattr(record, "record_id", ""))
    if len(rid) == 32:
        return f"{_SAM}{rid}/view"
    return f"{_USASPENDING}CONT_AWD_{rid}" if rid else ""


def _cell(value: Any, gap: str) -> str:
    """L-M1 in one function: a value, or a NAMED gap. Never empty."""
    text = _clean(value)
    return esc(text) if text else f'<span class="gap">{esc(gap)}</span>'


# --------------------------------------------------------------------------- #
# 1 COMPANY
# --------------------------------------------------------------------------- #
def section_company(pack: Any, profile: dict, identity: dict) -> str:
    client = _clean(getattr(pack, "client_name", "")) or "this client"
    terms = (profile.get("capability_terms") or {})
    core = list(terms.get("core") or [])
    ents = identity.get("entities") or []
    products = [e["name"] for e in ents if e["kind"] == "product"]
    channels = list(profile.get("channel_names") or [])
    if not channels:
        from agents.golden_press.rollups import resolve_relationship
        seen = []
        for record in (getattr(pack, "records", []) or []):
            name = _clean(getattr(record, "recipient", ""))
            if not name or name in seen:
                continue
            if resolve_relationship(name, pack)["relationship"] == \
                    "named_reseller":
                seen.append(name)
        channels = seen
    footprint = _footprint_sentence(pack, client)
    inference = _inference_layer(profile, core)
    rows = [
        # INTERNAL STATE NEVER REACHES CLIENT COPY. The stored summary can
        # carry drafting markers ("PROVISIONAL, pending model assessment");
        # they are cut here rather than shipped to a client.
        ("Company description", _strip_internal(
            profile.get("capability_summary"))
         or "Not established on this evidence"),
        ("Products tracked", " · ".join(products)
         or "Not established on this evidence"),
        ("Channel names tracked", " · ".join(channels)
         or "Not in current research"),
        ("Known federal footprint", footprint),
    ]
    body = "".join(
        f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>' for k, v in rows)
    return _wrap(
        "company", 1, f"This is {client} as we understand it.",
        "The search inputs below determined what the research counted. "
        "Correct them first: everything in this document follows from them.",
        # THE REPORT LEADS WITH THE INFERENCE LAYER (operator, 2026-08-06).
        # The reader's first question is not what we found, it is what we
        # looked for. Showing the approved keywords, the NAICS boundary, the
        # widened match forms and the terms awaiting approval makes the
        # search inspectable, so a wrong boundary is corrected at the top
        # rather than argued about at the bottom.
        inference
        + f'<table class="kv">{body}</table>'
        f'<p class="confirm">Confirm before the next press: the keywords, '
        f'the NAICS boundary, and the candidate terms above. A change to any '
        f'of them reruns every section below.</p>')


def _inference_layer(profile: dict, core: Any) -> str:
    """What we searched for, and what we did not, before what we found."""
    naics = list(profile.get("naics_boundary") or [])
    approved = [t for t in (profile.get("discovered_terms_approved") or [])
                if _clean(t)]
    rejected = profile.get("discovered_terms_rejected") or {}
    try:
        from agents.golden_press.term_expansion import expand_vocabulary
        widened = expand_vocabulary(list(core) + approved)["terms"]
    except Exception:                                     # noqa: BLE001
        widened = list(core)
    candidates = _pending_candidates(profile)

    def _chips(values, kind="") -> str:
        if not values:
            return f'<span class="gap">{esc(GAP_PHRASES[0])}</span>'
        css = f" {kind}" if kind else ""
        return "".join(f'<span class="chip{css}">{esc(v)}</span>'
                       for v in values)

    blocks = [
        ("Keywords you approved", _chips(core),
         f"{len(list(core))} term(s). These are the engagement boundary and "
         f"only you change them."),
        ("NAICS boundary", _chips(naics),
         f"{len(naics)} code(s). Used to bound the search structurally, "
         f"before any word is matched."),
        ("Search terms we ran", _chips(widened[:40], "muted"),
         f"{len(widened)} match form(s) derived from your terms: plurals, "
         f"dropped prefixes, and the federal wording for the same thing. "
         f"Your list is never edited, only how it is matched."
         + (" Showing the first 40." if len(widened) > 40 else "")),
    ]
    if approved:
        blocks.append((
            "Federal terms you approved from our research", _chips(approved),
            "Mined from the notice corpus and approved by you, now searched."))
    if candidates:
        blocks.append((
            "Candidate terms awaiting your decision",
            _chips([c["term"] for c in candidates[:12]], "pending"),
            f"{len(candidates)} term(s) the corpus suggests and we have NOT "
            f"searched. Approve any that fit and the next press finds what "
            f"they reach."))
    if rejected:
        blocks.append((
            "Terms we ruled out", _chips(sorted(rejected)[:10], "muted"),
            "Considered and rejected, with the reason recorded, so the same "
            "wrong turn is not retaken."))

    html = ['<div class="infer">']
    for label, chips, note in blocks:
        html.append(f'<div class="ib"><div class="il">{esc(label)}</div>'
                    f'<div class="ic">{chips}</div>'
                    f'<div class="inote">{esc(note)}</div></div>')
    html.append("</div>")
    return "".join(html)


def _pending_candidates(profile: dict) -> list:
    """Discovered terms neither approved nor rejected, if the review ran."""
    import json as _json
    from pathlib import Path

    client = _clean(profile.get("client_name"))
    if not client:
        return []
    try:
        from agents.assessment_chain import canonical_slug
        root = Path(__file__).resolve().parents[2]
        path = (root / "data" / "review"
                / f"{canonical_slug(client)}.term_discovery.json")
        proposal = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return []
    approved = {_clean(t).casefold()
                for t in (profile.get("discovered_terms_approved") or [])}
    rejected = {_clean(t).casefold()
                for t in (profile.get("discovered_terms_rejected") or {})}
    return [c for c in (proposal.get("candidates") or [])
            if _clean(c.get("term")).casefold() not in approved
            and _clean(c.get("term")).casefold() not in rejected]


_INTERNAL_MARKERS = ("PROVISIONAL", "pending model assessment", "TODO",
                     "DRAFT:", "placeholder")


def _strip_internal(value: Any) -> str:
    """Cut a drafting preamble, not just its keyword.

    The stored summary reads "PROVISIONAL, pending model assessment of the
    finished report: supplier risk and payment integrity software...". Cutting
    only the marker words leaves "of the finished report:" dangling, which is
    worse than leaving it alone. When a marker sits in the preamble before the
    first colon, the whole preamble goes.
    """
    text = _clean(value)
    head, sep, tail = text.partition(":")
    if sep and any(m.casefold() in head.casefold()
                   for m in _INTERNAL_MARKERS) and len(head) < 160:
        text = _clean(tail)
    for marker in _INTERNAL_MARKERS:
        idx = text.casefold().find(marker.casefold())
        if idx == -1:
            continue
        text = _clean(text[:idx] + text[idx + len(marker):].lstrip(" ,.:;-"))
    return text


def _footprint_sentence(pack: Any, client: str) -> str:
    from agents.golden_press.prime_posture import derive_posture
    posture = derive_posture(pack)
    return posture["why"]


# --------------------------------------------------------------------------- #
# 2 MARKET
# --------------------------------------------------------------------------- #
def section_market(pack: Any) -> str:
    from agents.golden_press.rollups import category_spend
    spend = category_spend(pack)
    if not spend.get("available"):
        return _wrap("market", 2,
                     "This is the money already spent in the category.",
                     "", _zero("No categorised spend was computed for this "
                              "pack, so the category total is not stated."))
    # RECONCILE TO THE CITED SET, NOT THE SCREENED SET. category_spend's
    # rows are pre-cap: they carry every record the screen touched (54
    # rival, 87 forecast) while this pack cites 25. Summing those and
    # calling the result "cited" produced a $106bn total that no record in
    # the document supports. The contract requires the splits to reconcile
    # to the combined total exactly, so both come from the same 25 records.
    segments = _cited_segments(pack)
    total = sum(d for _, d, _ in segments)
    by_segment = "".join(
        f'<tr><td>{esc(label)}</td>'
        f'<td class="num">{_money(dollars)}</td>'
        f'<td class="num">{count}</td>'
        f'<td>obligated on cited records</td></tr>'
        for label, dollars, count in segments)
    agencies = _agency_rows(pack)
    by_agency = "".join(
        f'<tr><td>{esc(a)}</td><td class="num">{_money(d)}</td>'
        f'<td class="num">{n}</td></tr>' for a, d, n in agencies)
    cov = spend.get("coverage") or {}
    return _wrap(
        "market", 2,
        "This is the money already spent in the category.",
        "Past obligations show where the category has been funded and which "
        "agencies buy it. This is history on cited records, not market size "
        "and not pipeline.",
        f'<p class="total">Combined cited category spend '
        f'<strong>{_money(total)}</strong> across '
        f'{int(cov.get("records_summed") or 0)} linked records.</p>'
        f'<table><thead><tr><th>Segment</th><th>Obligated</th>'
        f'<th>Records</th><th>Basis</th></tr></thead><tbody>{by_segment}'
        f'</tbody></table>'
        f'<h3>Buying agencies on the cited records</h3>'
        f'<table><thead><tr><th>Buying agency</th><th>Obligated</th>'
        f'<th>Records</th></tr></thead><tbody>{by_agency}</tbody></table>')


def _cited_segments(pack: Any) -> list:
    """(label, dollars, records) over the PACK'S OWN records only."""
    from agents.golden_press.rollups import resolve_relationship

    buckets: dict = {}
    for record in (getattr(pack, "records", []) or []):
        lane = _clean(getattr(record, "lane", ""))
        recipient = _clean(getattr(record, "recipient", ""))
        if lane == "L4_forecast":
            label = "Published forecasts"
        elif recipient:
            rel = resolve_relationship(recipient, pack)["relationship"]
            label = {"client_entity": "Client and its products",
                     "competitor": "Named rivals",
                     "named_reseller": "Channel partners"}.get(
                rel, "Other cited holders")
        else:
            label = "Posted solicitations"
        d, n = buckets.get(label, (0.0, 0))
        try:
            d += float(getattr(record, "obligated_dollars", 0) or 0)
        except (TypeError, ValueError):
            pass
        buckets[label] = (d, n + 1)
    return sorted(((k, v[0], v[1]) for k, v in buckets.items()),
                  key=lambda r: -r[1])


def _segment_label(segment: Any) -> str:
    return {"L2_core": "Client and its products",
            "L2_competitor": "Named rivals",
            "L2_channel": "Channel partners",
            "L4_forecast": "Published forecasts"}.get(
        _clean(segment), _clean(segment) or "Other")


def _agency_rows(pack: Any) -> list:
    agg: dict = {}
    for record in (getattr(pack, "records", []) or []):
        agency = _clean(getattr(record, "agency", ""))
        if not agency:
            continue
        d, n = agg.get(agency, (0.0, 0))
        try:
            d += float(getattr(record, "obligated_dollars", 0) or 0)
        except (TypeError, ValueError):
            pass
        agg[agency] = (d, n + 1)
    return sorted(((a, d, n) for a, (d, n) in agg.items()),
                  key=lambda r: -r[1])[:10]


# --------------------------------------------------------------------------- #
# 3 COMPETITION
# --------------------------------------------------------------------------- #
def section_competition(pack: Any) -> str:
    from agents.golden_press.rollups import resolve_relationship
    families: dict = {}
    for record in (getattr(pack, "records", []) or []):
        name = _clean(getattr(record, "recipient", ""))
        if not name:
            continue
        if resolve_relationship(name, pack)["relationship"] != "competitor":
            continue
        entry = families.setdefault(name, {"n": 0, "d": 0.0, "rep": record})
        entry["n"] += 1
        try:
            entry["d"] += float(getattr(record, "obligated_dollars", 0) or 0)
        except (TypeError, ValueError):
            pass
        if float(getattr(record, "obligated_dollars", 0) or 0) > float(
                getattr(entry["rep"], "obligated_dollars", 0) or 0):
            entry["rep"] = record
    if not families:
        return _wrap("competition", 3,
                     "These competitors are winning money in the same market.",
                     "",
                     _zero("No cited award names a rival as recipient in this "
                           "pack, so no rival total is stated. This is a "
                           "screened result, not an absence of competition."))
    total = sum(e["d"] for e in families.values())
    cards = ""
    for name, e in sorted(families.items(), key=lambda kv: -kv[1]["d"])[:8]:
        rep = e["rep"]
        cards += (
            f'<article class="card"><h4>{esc(name)}</h4>'
            f'<p class="num">{_money(e["d"])} across {e["n"]} linked '
            f'award(s)</p>'
            f'<p class="receipt">Representative award '
            f'{_link(_record_url(rep), getattr(rep, "record_id", ""))} at '
            f'{esc(getattr(rep, "agency", "") or "an unnamed buyer")}.</p>'
            f'</article>')
    return _wrap(
        "competition", 3,
        "These competitors are winning money in the same market.",
        f"The rival total comes from {sum(e['n'] for e in families.values())} "
        f"linked awards. Each card shows one representative award at source "
        f"precision.",
        f'<p class="total">Named-rival awards <strong>{_money(total)}</strong>'
        f' across {len(families)} competitor famil(ies).</p>'
        f'<div class="cards">{cards}</div>'
        f'<p class="coverage">Coverage: every cited award whose recipient '
        f'resolves to a named rival is counted here. Rivals with no cited '
        f'award in this pack are not shown and are not claimed absent.</p>')


# --------------------------------------------------------------------------- #
# 4 OPPORTUNITIES
# --------------------------------------------------------------------------- #
def section_opportunities(pack: Any, posture: dict) -> str:
    from agents.golden_press.prime_posture import route_for
    rows = [r for r in (getattr(pack, "records", []) or [])
            if _clean(getattr(r, "lane", "")) in ("L4_forecast", "L1_notice")]
    if not rows:
        return _wrap("opportunities", 4,
                     "These are the opportunities worth qualifying now.", "",
                     _zero("No forecast or posted solicitation in this pack "
                           "met the inclusion rule."))
    seen: set = set()
    body = ""
    kept = 0
    for record in rows:
        rid = _clean(getattr(record, "record_id", ""))
        # L-M2: an opportunity appears once. Repeated ids are the padding
        # this contract exists to prevent.
        if not rid or rid in seen:
            continue
        seen.add(rid)
        kept += 1
        route = route_for(record, posture)
        body += (
            f'<tr><td>{_link(_record_url(record), rid)}</td>'
            f'<td>{esc(getattr(record, "title", "") or "Untitled record")}</td>'
            f'<td>{esc(getattr(record, "agency", "") or "")}</td>'
            f'<td>{_cell(_published_value(record), "Value not published")}</td>'
            f'<td class="route route-{esc(route["route"])}">'
            f'{esc(_route_label(route["route"]))}</td>'
            f'<td>{esc(route["why"])}</td></tr>')
        if kept >= 12:
            break
    note = (f"{kept} of {len(rows)} cited forecast and notice records are "
            f"shown; the cap is stated here, never silent."
            if len(rows) > kept else
            f"All {kept} cited forecast and notice records are shown.")
    return _wrap(
        "opportunities", 4,
        f"These are the {kept} opportunities worth qualifying now.",
        "Values render as published and are never summed. The prime or team "
        "decision is derived from award history, not assumed.",
        f'<table><thead><tr><th>Record</th><th>What it is</th>'
        f'<th>Buyer</th><th>Published value</th><th>Route</th>'
        f'<th>Why that route</th></tr></thead><tbody>{body}</tbody></table>'
        f'<p class="coverage">{esc(note)}</p>')


def _published_value(record: Any) -> str:
    for attr in ("value_display", "published_value"):
        got = _clean(getattr(record, attr, ""))
        if got:
            return got
    for attr in ("ceiling_dollars", "obligated_dollars"):
        try:
            v = float(getattr(record, attr, 0) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v:
            return _money(v)
    return ""


def _route_label(route: str) -> str:
    return {"prime": "Prime", "sub": "Team or sub",
            "either": "Prime or team",
            "unestablished": "Route unestablished"}.get(route, route)


# --------------------------------------------------------------------------- #
# 5 TEAMING
# --------------------------------------------------------------------------- #
def section_teaming(pack: Any, posture: dict) -> str:
    from agents.golden_press.rollups import resolve_relationship
    holders: dict = {}
    for record in (getattr(pack, "records", []) or []):
        name = _clean(getattr(record, "recipient", ""))
        if not name:
            continue
        rel = resolve_relationship(name, pack)["relationship"]
        if rel not in ("named_reseller", "independent"):
            continue
        entry = holders.setdefault(name, {"n": 0, "d": 0.0, "rep": record,
                                          "rel": rel})
        entry["n"] += 1
        try:
            entry["d"] += float(getattr(record, "obligated_dollars", 0) or 0)
        except (TypeError, ValueError):
            pass
    if not holders:
        return _wrap("teaming", 5,
                     "These are the teaming routes to work first.", "",
                     _zero("No cited award names a paper holder to team "
                           "through in this pack."))
    body = ""
    for name, e in sorted(holders.items(), key=lambda kv: -kv[1]["d"])[:8]:
        rep = e["rep"]
        why = (f'Holds {e["n"]} cited record(s) worth {_money(e["d"])} at '
               f'{_clean(getattr(rep, "agency", "")) or "a federal buyer"}.')
        if e["rel"] == "named_reseller":
            why += " Named channel partner for this client."
        body += (
            f'<tr><td>{esc(name)}</td><td>{esc(why)}</td>'
            f'<td>{_cell("", "Named alliance lead needed")}</td>'
            f'<td>{_cell("", "Enrichment needed")}</td>'
            f'<td>{_link(_record_url(rep), getattr(rep, "record_id", ""))}'
            f'</td></tr>')
    return _wrap(
        "teaming", 5,
        "These are the teaming routes to work first.",
        "The award receipt proves the company already holds relevant federal "
        "paper. The named person is the commercial role most likely to route "
        "the conversation.",
        f'<table><thead><tr><th>Paper holder</th><th>Why call</th>'
        f'<th>Who to start with</th><th>Phone</th><th>Receipt</th></tr>'
        f'</thead><tbody>{body}</tbody></table>'
        f'<p class="coverage">Coverage: {len(holders)} holder(s) carry a '
        f'cited award in this pack. Named alliance leads and phones are the '
        f'next research order; none is invented here.</p>')


# --------------------------------------------------------------------------- #
# 6 CONTACTS
# --------------------------------------------------------------------------- #
def section_contacts(pack: Any, contacts: Any) -> str:
    rows = list(contacts or [])
    if not rows:
        return _wrap(
            "contacts", 6,
            "These are the people to contact, and the contact data still "
            "needed.", "",
            _zero("No government-published contact bound to a cited record "
                  "in this pack. The official-contact lane ran and matched "
                  "nothing; commercial enrichment is the next order."))
    # L-M2: one person, one row, motions named. Never one row per motion.
    people: dict = {}
    for row in rows:
        key = _clean(row.get("email")).casefold() or _clean(row.get("name"))
        person = people.setdefault(key, {**row, "motions": [], "records": []})
        for binding in (row.get("bindings") or []):
            m = _clean(binding.get("strength"))
            if m and m not in person["motions"]:
                person["motions"].append(m)
        for rid in (row.get("join_record_ids") or []):
            if rid not in person["records"]:
                person["records"].append(str(rid))
    with_phone = sum(1 for p in people.values() if p.get("phones"))
    body = ""
    for p in list(people.values())[:20]:
        phone = ""
        for entry in (p.get("phones") or []):
            phone = _clean(entry.get("number"))
            if phone:
                break
        body += (
            f'<tr><td>{esc(" · ".join(p["motions"]) or "bound to a cited record")}</td>'
            f'<td>{esc(p.get("organization"))}</td>'
            f'<td>{esc(p.get("name"))}</td>'
            f'<td>{_cell(p.get("email"), "No published contact on this record")}</td>'
            f'<td>{_cell(phone, "Not in current research")}</td></tr>')
    return _wrap(
        "contacts", 6,
        "These are the people to contact, and the contact data still needed.",
        "Government-published routes are separated from partner-side "
        "contacts so the next action is obvious.",
        f'<p class="total">Ready now: <strong>{len(people)}</strong> '
        f'government-published contact records, '
        f'<strong>{with_phone}</strong> with a published phone. '
        f'Next research order: <strong>{len(people) - with_phone}</strong> '
        f'needing phone enrichment.</p>'
        f'<table><thead><tr><th>Motion</th><th>Organisation</th>'
        f'<th>Person</th><th>Published route</th><th>Phone</th></tr>'
        f'</thead><tbody>{body}</tbody></table>')


# --------------------------------------------------------------------------- #
# 7 EVENTS
# --------------------------------------------------------------------------- #
def section_events(pack: Any) -> str:
    events = list(getattr(pack, "events", []) or [])
    if not events:
        return _wrap("events", 7,
                     "Register for the rooms where buyers and partners will "
                     "be.", "",
                     _zero("No event in this pack passed live URL "
                           "verification for this edition."))
    body = ""
    seen: set = set()
    for event in events[:10]:
        get = (event.get if isinstance(event, dict)
               else lambda k, d=None: getattr(event, k, d))
        url = _clean(get("url"))
        title = _clean(get("name") or get("title"))
        host = _clean(get("host"))
        when = _clean(get("event_start")) or "TBD"
        if not title or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        room = f"{title} · {host}" if host else title
        body += (f'<tr><td>{esc(when)}</td><td>{esc(room)}</td>'
                 f'<td>{_link(url, "Register") if url else _cell("", "Not in current research")}'
                 f'</td></tr>')
    return _wrap(
        "events", 7,
        "Register for the rooms where buyers and partners will be.",
        "Each link opens the official event or registration page.",
        f'<table><thead><tr><th>When</th><th>Room</th><th>Link</th></tr>'
        f'</thead><tbody>{body}</tbody></table>'
        f'<p class="coverage">Coverage: {len(seen)} room(s) carried in this '
        f'edition, each with a live-verified official link. A date reading '
        f'TBD is not published on the source record.</p>')


# --------------------------------------------------------------------------- #
def _zero(sentence: str) -> str:
    return f'<p class="zero">{esc(sentence)}</p>'


def _wrap(sid: str, number: int, heading: str, lede: str, body: str) -> str:
    lede_html = f'<p class="lede">{esc(lede)}</p>' if lede else ""
    return (f'<section class="mm" id="{sid}">'
            f'<div class="n">{number}</div>'
            f'<h2>{esc(heading)}</h2>{lede_html}{body}</section>')


def render_market_map(pack: Any, *, profile: dict, contacts: Any = None,
                      as_of: str = "") -> str:
    """The seven sections, in order. Deterministic: no model call anywhere."""
    from agents.golden_press.client_identity import audit_client_identity
    from agents.golden_press.prime_posture import derive_posture

    identity = audit_client_identity(
        dict(profile, client_name=profile.get("client_name")
             or getattr(pack, "client_name", "")))
    posture = derive_posture(pack)
    parts = [
        section_company(pack, profile, identity),
        section_market(pack),
        section_competition(pack),
        section_opportunities(pack, posture),
        section_teaming(pack, posture),
        section_contacts(pack, contacts),
        section_events(pack),
    ]
    return "".join(parts)
