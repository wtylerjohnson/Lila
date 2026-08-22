"""Target report — the TARGET-stage client deliverable (GTM locked format).

Rendered deterministically from the ContactPlan artifact (run_target.py output):
the outreach angle, verified POCs from source notices, and the Apollo search
plan. No LLM at render time — the plan already went through its decision layer
and review gate; this just puts it in front of the client in the approved skin.
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Optional

_TPL = Path(__file__).resolve().parent / "templates"


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def _poc_rows(pocs: list[dict]) -> str:
    if not pocs:
        return '<p style="color:var(--ink-ghost)">No named POCs appeared in the source notices — the Apollo plan below is the path in.</p>'
    rows = "".join(
        f'<tr><td class="label-col">{_esc(p.get("name"))}</td>'
        f'<td>{_esc(p.get("role") or p.get("title"))}</td>'
        f'<td>{_esc(p.get("org"))}</td>'
        f'<td>{_esc(p.get("email") or "—")}</td></tr>'
        for p in pocs
    )
    return ('<div class="data-table-wrap"><table class="data-table"><thead><tr>'
            "<th>Name</th><th>Role</th><th>Org</th><th>Email</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


_VET_CHIP = {
    "vetted": ("fit-direct", "SAM VETTED"),
    "EXCLUDED": ("fit-adjacent", "EXCLUDED — DO NOT ENGAGE"),
    "not_registered": ("fit-adjacent", "NOT IN SAM"),
    "unverified": ("fit-strong", "VETTING PENDING"),
}


def _search_cards(searches: list[dict], vetting: Optional[dict] = None) -> str:
    orgs = (vetting or {}).get("orgs") or {}
    cards = []
    for i, s in enumerate(searches):
        titles = ", ".join(s.get("person_titles") or [])
        tags = "".join(
            f'<span class="play-tag">{_esc(t)}</span>'
            for t in (s.get("seniorities") or [])[:4]
        )
        v = orgs.get(s.get("org_name") or "") or {}
        cls, label = _VET_CHIP.get(v.get("verdict", ""), (None, None))
        chip = f'<span class="fit-chip {cls}">{label}</span>' if label else ""
        extra = ""
        if v.get("verdict") == "vetted":
            ids = " · ".join(x for x in [f"UEI {v.get('uei')}" if v.get("uei") else None,
                                          f"CAGE {v.get('cage')}" if v.get("cage") else None] if x)
            if ids:
                tags += f'<span class="play-tag">{_esc(ids)}</span>'
        cards.append(
            f'<div class="play-card"><div class="play-header">'
            f'<span class="play-code">TGT-{i+1:02d}</span>'
            f'<span class="play-headline">{_esc(s.get("org_name"))} — {_esc(titles)}</span>{chip}</div>'
            f'<p class="play-body">{_esc(s.get("rationale"))}</p>{extra}'
            f'<div class="play-channel"><span class="play-channel-label">Apollo</span>{tags}</div></div>'
        )
    return "\n".join(cards)


def _persona_cards(titles: Optional[dict]) -> str:
    if not titles or not titles.get("personas"):
        return ""
    cards = "".join(
        f'<div class="play-card"><div class="play-header">'
        f'<span class="play-code">WHO-{i+1:02d}</span>'
        f'<span class="play-headline">{_esc(p.get("title"))}</span>'
        f'<span class="fit-chip fit-strong">{_esc((p.get("seniority") or "").upper())}</span></div>'
        f'<p class="play-body">{_esc(p.get("why"))}</p>'
        f'<div class="play-channel"><span class="play-channel-label">{_esc(p.get("function"))}</span>'
        + "".join(f'<span class="play-tag">{_esc(o)}</span>' for o in (p.get("org_types") or [])[:3])
        + "</div></div>"
        for i, p in enumerate(titles["personas"])
    )
    note = _esc(titles.get("buying_committee_note"))
    return (
        '<section><div class="section-opener section-divider"><span class="ghost-num">00</span>'
        '<span class="section-opener-code">Section 00 · Buying Committee</span>'
        '<h2 class="section-name">Who<br>Buys</h2>'
        f'<p class="section-thesis">{note}</p></div>'
        f'<div class="plays-grid">{cards}</div></section>'
    )


def render_target_report(plan: dict, as_of: Optional[date] = None, titles: Optional[dict] = None,
                         vetting: Optional[dict] = None) -> str:
    # identity assets are template constants on EVERY client document — same
    # loaders as the assessment (they raise on missing assets, never omit)
    from agents.reports.capture_brief import (
        _gtm_logo_b64, _gtm_logo_mime, client_cover_lockup,
        grade_contact_channels,
    )
    css = (_TPL / "capture_brief.css").read_text()
    as_of = as_of or date.today()
    date_label = as_of.strftime("%B %-d, %Y")
    client = plan.get("client_name", "Client")
    pocs = plan.get("known_pocs") or []
    searches = plan.get("searches") or []
    citations = plan.get("citations") or []
    cites = " · ".join(
        f'<a href="{html.escape(u, quote=True)}">{_esc(u[:60])}</a>' for u in citations[:12]
    )
    gtm = ('<div class="logo-plate"><img id="gtmLogoImg" '
           f'src="data:{_gtm_logo_mime()};base64,{_gtm_logo_b64()}" '
           'alt="GTM — Go To Market"></div>')
    client_lockup = client_cover_lockup(client)

    page = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(client)} · Target Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400;1,700&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="page">

<header class="cover">
  <div class="cover-top-row">
    <div class="cover-logo">{client_lockup}</div>
    <div class="pscg-logo">{gtm}</div>
  </div>
  <span class="cover-eyebrow">Target Report · Confidential</span>
  <h1 class="cover-title">{_esc(client)}</h1>
  <p class="cover-subtitle">Who to reach, and why — mapped from the qualified opportunities</p>
  <div class="cover-meta">
    <div class="cover-meta-item"><span class="cover-meta-label">Prepared by</span><span class="cover-meta-value">Tyler Johnson · GTM Group</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Stage</span><span class="cover-meta-value">TARGET — follows the Federal Opportunity Assessment</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Date</span><span class="cover-meta-value">{date_label}</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Classification</span><span class="cover-meta-value">Proprietary — Prepared for {_esc(client)}</span></div>
  </div>
  <div class="thesis-block"><span class="thesis-label">Outreach angle</span>
    <div class="thesis-text"><p>{_esc(plan.get("strategy_note"))}</p></div></div>
</header>

{_persona_cards(titles)}

<section>
  <div class="section-opener"><span class="ghost-num">01</span>
    <span class="section-opener-code">Section 01 · Verified Points of Contact</span>
    <h2 class="section-name">Named<br>In-Source</h2>
    <p class="section-thesis">Every contact below appears in the verified source notices — no scraped or guessed identities.</p>
  </div>
  {_poc_rows(pocs)}
</section>

<section>
  <div class="section-opener section-divider"><span class="ghost-num">02</span>
    <span class="section-opener-code">Section 02 · Contact Sourcing Plan (Apollo)</span>
    <h2 class="section-name">The Search<br>Plan</h2>
    <p class="section-thesis">{len(searches)} targeted people-searches, each tied to a qualified opportunity. Reviewed before any search runs.</p>
  </div>
  <div class="plays-grid">{_search_cards(searches, vetting)}</div>
</section>

<footer class="footer">
  <span class="footer-label">Verification &amp; Sources</span>
  <p class="footer-sources">POCs echo verified source notices only; search specs are recommendations pending human review. Org vetting via SAM Entity Management and Exclusions APIs; VETTED = active registration and no exclusion record at report time. {cites}</p>
</footer>

</div></body></html>"""
    return grade_contact_channels(page)


def qa_target_report(plan: dict) -> list[str]:
    problems = []
    if not (plan.get("known_pocs") or plan.get("searches")):
        problems.append("plan has neither verified POCs nor search specs — nothing to deliver")
    if not plan.get("strategy_note"):
        problems.append("plan is missing the outreach angle (strategy_note)")
    return problems
