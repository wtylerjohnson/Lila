"""Three views, one document. A view is a rendering decision.

    render_assessment(doc, view) -> single self-contained HTML string
        view = "client"    the paid deliverable
        view = "sales"     the test drive: locked elements are VISIBLY locked
        view = "internal"  QA: inline provenance, confidence flags, worksheets,
                           pre-ship checklist

Gating happens on the MODEL, not in templates: gate_for_sales(doc) returns a
document whose client-only content is physically replaced by locked
placeholders (list lengths preserved so counts stay truthful — sales counts
recompute from the gated model). A template bug therefore cannot leak paid
content, and the leak test is mechanical.

Count reconciliation Layer 1 lives here too: every count in rendered copy is a
template variable from doc.counts(); composed prose flows through
resolve_count_tokens; structural elements carry data-entity markers that
lint_counts (Layer 2) reconciles against the copy.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from agents.reports.capture_brief import (
    _cover_art, _esc, _gtm_logo_b64, _gtm_logo_mime, client_cover_lockup,
    grade_contact_channels,
)
from agents.reports.document import (
    AssessmentDocument, GradedPursuit, Locked, PartneringPlay,
    agency_group_phrase, int_to_word, normalize_company, resolve_count_tokens,
)

VIEWS = ("client", "sales", "internal")

LOCKED_TITLE = "Locked · included in the full engagement"
_LOCK_NOTE = "Included in the full engagement"

_GRADE_CLASS = {"A": "vg-a", "B": "vg-b", "C": "vg-c", "D": "vg-d", "F": "vg-f"}

# Exact deadline is sales-visible by default: urgency sells, and a deadline
# alone is not actionable. Reviewer-reversible in ONE line: set to "window"
# and every gated deadline renders as "closes within N days" instead.
SALES_DEADLINE_STYLE = "exact"


def mask_notice_id(notice_id: str) -> str:
    """Real prefix, masked tail (70RSAT26R000XXXX): citable proof the notice
    exists, useless as a search key. Length is preserved; longer ids mask a
    longer tail (a quarter of the id, never fewer than 4 characters)."""
    if not notice_id:
        return ""
    n = min(max(4, len(notice_id) // 4), max(1, len(notice_id) - 1))
    return notice_id[:-n] + "X" * n


def _sales_deadline(deadline: Optional[str], as_of: date) -> Optional[str]:
    if not deadline or SALES_DEADLINE_STYLE == "exact":
        return deadline
    try:
        days = (date.fromisoformat(deadline[:10]) - as_of).days
    except ValueError:
        return deadline
    return f"closes within {max(days, 0)} days"


def _candidate_name_pattern(name: str) -> str:
    return rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"


def _contains_candidate_name(value, names: tuple[str, ...]) -> bool:
    """Whether a nested value carries an exact candidate identity."""
    if isinstance(value, str):
        return any(re.search(_candidate_name_pattern(name), value, flags=re.I)
                   for name in names)
    if isinstance(value, dict):
        return any(_contains_candidate_name(item, names)
                   for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_candidate_name(item, names) for item in value)
    return False


def _redact_candidate_names(value, names: tuple[str, ...]):
    """Remove exact candidate identities from every sales-model string.

    A candidate may also appear in a pursuit-grade basis, composed prose, or
    another joined artifact. Clearing only ``play.candidates`` would leave
    that identity physically present in the gated model even when the sales
    template does not currently render the field. Boundary-aware replacement
    avoids turning a shorter candidate name into a substring edit.
    """
    if isinstance(value, str):
        for name in names:
            value = re.sub(
                _candidate_name_pattern(name),
                _LOCK_NOTE,
                value,
                flags=re.I,
            )
        return value
    if isinstance(value, dict):
        return {
            key: (item if key == "client_name"
                  else _redact_candidate_names(item, names))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_candidate_names(item, names) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_candidate_names(item, names) for item in value)
    return value


# ── sales gating (model-level) ───────────────────────────────────────────────

def gate_for_sales(doc: AssessmentDocument) -> AssessmentDocument:
    """The test drive: shape visible, keys withheld. List lengths are preserved
    so counts computed from the gated model stay truthful.

    The verifier principle: locked content must prove it is real without
    becoming actionable. Every locked element carries a typed Locked
    placeholder whose verifiers are REAL fields from the underlying record —
    agency, notice type, deadline, grade, masked notice ID, verification line
    — deliberately kept as sales-tier content. Masking happens HERE: the gated
    copy carries the masked id only, the full id physically leaves the model,
    so the leak test holds mechanically."""
    g = deepcopy(doc)
    partner_names = tuple(sorted({
        candidate.name
        for play in doc.partnering.plays
        for candidate in play.candidates
        if candidate.name
    }, key=lambda name: (-len(name), name.casefold())))
    as_of = doc.as_of.isoformat()
    sam_line = f"Verified against SAM.gov {as_of}"
    usa_line = f"Verified against USAspending {as_of}"

    # market: headline TAM + agency NAMES stay; per-agency dollars + math locked
    for a in g.market.agency_map:
        a.dollars = None
        a.dollars_label = "🔒"
        a.basis = _LOCK_NOTE
        a.source = ""
    for a in (*g.market.award_agency_annual, *g.market.addressable_agency_annual):
        # same rule: names stay, per-agency dollars are client content
        a.dollars = None
        a.dollars_label = None
        a.basis = _LOCK_NOTE
        a.source = ""
    n_market = sum(1 for c in g.market.components if c.kind == "market")
    g.market.components = []  # the math table is a client feature
    g.market.locked = Locked(
        shape_hint="TAM math table + per-agency dollar map",
        upsell_line="Per-agency breakdown and the TAM math ship with the "
                    "full assessment, every dollar traced to its source.",
        verifiers={"market components": str(n_market), "verified": usa_line})
    # Cited third-party rows stay verbatim. If any field carries a paid
    # candidate identity, remove the complete row instead of corrupting its
    # headline or URL with token redaction. Filter before the sales cap so
    # later safe rows backfill the three visible slots.
    g.market.news = [
        item for item in g.market.news
        if not _contains_candidate_name(item, partner_names)
    ][:3]

    # pursuits: rank, grade, agency, notice type and deadline stay — the
    # credibility tokens; identity (title/id/url), dossier and contacts locked.
    # Only #1 keeps its name.
    sid_mask: dict[str, str] = {}
    for p in g.board.pursuits:
        masked = mask_notice_id(p.solicitation or p.source_id)
        sid_mask[p.source_id] = masked
        p.response_deadline = _sales_deadline(
            p.response_deadline, p.grade.as_of)
        verifiers = {"agency": p.agency or "", "notice type": p.notice_type or "",
                     "response deadline": p.response_deadline or "",
                     "grade": p.grade.letter, "notice id": masked,
                     "verified": sam_line}
        p.locked = Locked(
            shape_hint="pursuit dossier" if p.rank == 1 else "pursuit identity + dossier",
            upsell_line="Full notice ID, source link, contacts, vehicle path, "
                        "incumbent, and the recommended play are client content.",
            verifiers={k: v for k, v in verifiers.items() if v})
        p.dossier = None
        p.url = None
        p.source_id = masked  # the gated model never carries the full id
        p.solicitation = None
        p.amendments = []  # posting history carries full notice ids: client-only
        p.set_aside = None  # eligibility shape is client analysis, not a verifier
        p.set_aside_code = None
        p.fit_trace = None  # capability terms/evidence trace are client analysis
        # RATIFIED 2026-07-06: the #1 pursuit's NAME and GRADE stay visible in
        # the sales view (the hook that proves the board is real). Do not
        # re-open without a reviewer decision.
        if p.rank != 1:
            p.title = LOCKED_TITLE
            p.naics = None

    # competitors: count + verification basis + NAICS lanes stay (proof the
    # evidence exists); names, dollars, agency joins and reasoning are the
    # product. No per-competitor vehicle data exists in the pipeline, so the
    # lanes ARE the honest category verifier — nothing invented.
    n_comp = len(g.competitive.competitors)
    for c in g.competitive.competitors:
        c.locked = Locked(
            shape_hint="competitor identity",
            upsell_line="Names and the award evidence behind each are client content.",
            verifiers={"basis": c.basis, "naics lanes": ", ".join(c.naics) or "·",
                       "verified": usa_line})
        c.name = LOCKED_TITLE
        c.dollars = None
        c.dollars_label = None
        c.agencies = []
        c.pursuit_ids = []
        c.awards = []  # award ids/urls identify the competitor: client content
        c.lineage_note = None    # names inside the note identify the door
        c.merged_names = []
    if n_comp:
        g.competitive.locked = Locked(
            shape_hint="competitor map",
            upsell_line="Named competitors with the evidence behind each ship "
                        "with the full assessment.",
            verifiers={"competitors mapped": str(n_comp), "verified": usa_line})
        g.competitive.coverage_note = _LOCK_NOTE
    # buyer-map incumbents are internal workbench data: physically gone from
    # the gated model, so no sales render can leak them
    g.buyer_incumbents = []

    # partnering: that paths EXIST and their shape stay (count, directions,
    # verifier line per locked candidate group); names, blocker analysis and
    # evidence detail are the product. Play count survives -> counts()
    # recomputes truthfully from the gated model.
    from agents.reports.document import _fmt_money as _fm
    for play in g.partnering.plays:
        # the play joins its pursuit by source_id: carry the SAME mask so the
        # gated model never holds the raw notice id and the join still works
        play.source_id = sid_mask.get(play.source_id,
                                      mask_notice_id(play.source_id))
        agg = sum((e.dollars or 0) for c in play.candidates for e in c.evidence)
        n_cand = len(play.candidates)
        play.locked = Locked(
            shape_hint="teaming play",
            upsell_line="Named partner candidates with graded fit and cited "
                        "award evidence ship with the full assessment.",
            verifiers={k: v for k, v in {
                "direction": " / ".join(play.directions),
                "candidates": str(n_cand) if n_cand else "",
                "evidence basis": "verified awards and subawards in this lane "
                                  "(USAspending)" if n_cand else "",
                "aggregate dollars": _fm(agg) or "",
                "verified": usa_line}.items() if v})
        play.blockers = []       # blocker bases carry client-attested analysis
        play.review_flags = []
        play.candidates = []
        play.title = None        # the monitor notice title is client content

    # watchlist: the items are locked; count, source types, motion badges and
    # last-refreshed stay — the feed is provably alive without being readable
    src_types = sorted({e.source for e in g.watchlist.entries})
    for e in g.watchlist.entries:
        e.locked = Locked(
            shape_hint="watchlist item",
            upsell_line="Weekly monitoring is a client feature.",
            verifiers={"source": e.source, "status": e.kind})
        e.id = mask_notice_id(e.id)
        e.title = LOCKED_TITLE
        e.url = None
        e.detail = _LOCK_NOTE
    g.watchlist.locked = Locked(
        shape_hint="watchlist",
        upsell_line="The weekly-refreshed watchlist is a client feature.",
        verifiers={"entries": str(len(g.watchlist.entries)),
                   "source types": ", ".join(src_types) or "·",
                   "last refreshed": g.watchlist.last_refreshed or "·"})
    if partner_names:
        # Round-trip through the unchanged public model. The gated object keeps
        # the exact AssessmentDocument shape while candidate identities leave
        # every joined string, not just the PartneringBoard candidate list.
        g = AssessmentDocument.model_validate(
            _redact_candidate_names(g.model_dump(mode="python"), partner_names))
    return g


# ── shared building blocks ───────────────────────────────────────────────────

def _bar_label(label: str, limit: int = 34) -> str:
    """Word-boundary truncation: no mid-word chops in chart gutters."""
    if len(label) <= limit:
        return label
    cut = label[:limit].rsplit(" ", 1)[0]
    return (cut or label[:limit]) + "…"


def _money_bars(rows: list[tuple[str, Optional[float], Optional[str]]]) -> str:
    """Inline SVG horizontal bars — the money-chart rule: render only when 3+
    citable dollar figures exist. Rows without a dollar never chart.
    House chart standard: navy series on a quiet track, hairline baseline,
    mono value labels, largest first, labels never truncated mid-word."""
    dollared = [(label, d, dl) for label, d, dl in rows if d]
    if len(dollared) < 3:
        return ""
    dollared.sort(key=lambda r: -r[1])
    top = dollared[0][1]
    gutter, track_w, bar_h, row_h = 264, 436, 20, 38
    svg, y = [], 12
    for label, d, dlabel in dollared[:6]:
        w = max(3, int(track_w * d / top))
        svg.append(
            f'<text x="{gutter - 12}" y="{y + 14}" text-anchor="end" '
            f'font-family="var(--sans)" font-size="12.5" fill="var(--ink-dim)">'
            f'{_esc(_bar_label(label))}</text>'
            f'<rect x="{gutter}" y="{y}" width="{track_w}" height="{bar_h}" '
            f'fill="var(--bg-card)"></rect>'
            f'<rect x="{gutter}" y="{y}" width="{w}" height="{bar_h}" '
            f'fill="var(--accent-hi)"></rect>'
            f'<text x="{gutter + w + 8}" y="{y + 14}" font-family="var(--mono)" '
            f'font-size="12" font-weight="700" fill="var(--ink)">'
            f'{_esc(dlabel or "")}</text>')
        y += row_h
    svg.append(f'<line x1="{gutter}" y1="4" x2="{gutter}" y2="{y - 10}" '
               f'stroke="var(--rule-hi)" stroke-width="1"></line>')
    return (f'<div class="chart-block"><svg viewBox="0 0 800 {y}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="Cited dollar figures">{"".join(svg)}</svg></div>')


def _verify_strip(lk: Optional[Locked]) -> str:
    """The credibility tokens on a locked element — real fields only. Each
    token is its own block element: block boundaries keep adjacent fields
    ('…deadline 2026-08-15' | 'notice id …') from fusing into phantom count
    claims under the lint_counts scan."""
    if lk is None:
        return ""
    toks = "".join(
        f'<div class="v-vtok"><span class="v-vk">{_esc(k)}</span>'
        + (_agency_mark(v, "v-vmark") if k == "agency" else "")
        + f'{_esc(v)}</div>'
        for k, v in lk.verifiers.items() if v and k != "verified")
    line = lk.verifiers.get("verified", "")
    return ((f'<div class="v-verify">{toks}</div>' if toks else "")
            + (f'<div class="v-vline">✓ {_esc(line)}</div>' if line else ""))


# one consistent lock glyph everywhere (emoji varies by platform and printer)
_LOCK_GLYPH = ('<svg class="v-lock-ic" width="13" height="13" viewBox="0 0 24 24" '
               'fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">'
               '<rect x="4" y="10" width="16" height="11" rx="1.5" fill="currentColor" '
               'stroke="none"></rect><path d="M8 10V7a4 4 0 0 1 8 0v3"></path></svg>')


def _lock(inner: str, lk: Optional[Locked] = None, cls: str = "") -> str:
    """Locked treatment: premium content behind glass, never a broken gray
    box. Verifier-carrying locks render ghost lines — an EMPTY decorative
    element suggesting the withheld body; it contains no text, so there is
    nothing to invent and nothing for the leak scan to find."""
    ghost = '<div class="v-ghostlines" aria-hidden="true"></div>' if lk else ""
    return (f'<div class="v-locked{" " + cls if cls else ""}" data-locked="1">'
            f'{_LOCK_GLYPH}'
            f'<div class="v-lock-body">{inner}{ghost}{_verify_strip(lk)}</div>'
            f'<span class="v-lock-tag">CLIENT FEATURE</span></div>')


def _grade_chip(p: GradedPursuit) -> str:
    return (f'<span class="v-grade {_GRADE_CLASS.get(p.grade.letter, "vg-c")}" '
            f'title="score {p.grade.score:g}/4 across {p.grade.scored_dimensions} '
            f'scored dimensions">{p.grade.letter}</span>')


_STYLE_ADDENDUM = """
/* --navy was referenced throughout but never defined in the house sheet:
   every var(--navy) silently resolved invalid (transparent bg, black SVG
   fill). Alias it once to the cover's navy family. */
:root{--navy:#16283f}
/* ── shared rhythm ─────────────────────────────────────────────────────── */
.v-section{margin:46px 0}
.view-internal .v-section{margin:28px 0}
.v-h{font-family:var(--serif);font-size:24px;font-weight:900;color:var(--ink);
     border-bottom:2px solid var(--navy);padding-bottom:10px;margin-bottom:18px}
.v-sub{font-family:var(--mono);font-size:10px;letter-spacing:.14em;color:var(--ink-ghost);
       text-transform:uppercase;margin-bottom:10px}
.v-note{font-family:var(--sans);font-size:12.5px;color:var(--ink-ghost);margin:8px 0}
.v-prov{font-family:var(--mono);font-size:10.5px;color:var(--ink-ghost);line-height:1.6;
        border-left:2px solid var(--rule-hi);padding-left:10px;margin:12px 0}
.v-amark{width:15px;height:15px;object-fit:contain;vertical-align:-3px;margin-right:7px}
.v-vmark{width:12px;height:12px;object-fit:contain;vertical-align:-2px;margin-right:4px;
         background:#fff;border-radius:2px;padding:1px}
.v-headline{font-family:var(--serif);font-size:18px;line-height:1.55;margin:4px 0 20px}
.v-headline strong{color:var(--navy)}
.v-section a{color:var(--accent-hi);text-decoration:underline;
             text-decoration-thickness:1px;text-underline-offset:2px}
.v-section a:hover{color:var(--navy)}
/* ── tables ────────────────────────────────────────────────────────────── */
.v-table{border-collapse:collapse;width:100%;font-size:13px}
.v-table th{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-align:left;
            color:var(--ink-ghost);text-transform:uppercase;padding:6px 12px 6px 0;
            border-bottom:1px solid var(--rule-hi)}
.v-table td{padding:9px 12px 9px 0;border-top:1px solid var(--rule);vertical-align:top;
            line-height:1.5}
.v-table tr:first-child td{border-top:none}
.v-table td.num{font-family:var(--mono);font-size:12.5px;font-weight:600;
                white-space:nowrap;padding-right:22px}
.v-table td.v-basis{font-size:11.5px;color:var(--ink-ghost);line-height:1.45}
.v-table td.mono{font-family:var(--mono);font-size:12px;white-space:nowrap}
/* ── grades ────────────────────────────────────────────────────────────── */
.v-grade{display:inline-block;min-width:30px;text-align:center;font-family:var(--mono);
         font-weight:700;font-size:14px;padding:4px 9px;color:#fff;border-radius:2px}
.vg-a{background:#1f7a3d}.vg-b{background:#4a7c2a}.vg-c{background:#b98a2e}
.vg-d{background:#a85b2a}.vg-f{background:#8a2f2f}
/* ── dossiers: chapters, not appendix dumps ────────────────────────────── */
.v-dossier{margin-top:44px;padding-top:26px;border-top:3px solid var(--navy)}
.v-dossier h3{font-family:var(--serif);font-size:20px;font-weight:900;color:var(--ink);
              margin:0 0 12px;display:flex;align-items:center;gap:12px}
.v-dossier p{line-height:1.65;margin:10px 0}
.v-facts{font-size:13px;color:var(--ink-dim);margin:12px 0;padding:9px 0;
         border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.v-wins li::marker{color:var(--navy)}
.v-flags li::marker{color:var(--alert)}
.v-wins li,.v-flags li{margin:6px 0;line-height:1.55}
/* ── watchlist: motion scannable in two seconds ────────────────────────── */
.v-watch{list-style:none;padding:0;margin:12px 0}
.v-watch li{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;
            padding:8px 0 8px 12px;border-top:1px solid var(--rule)}
.v-watch li .v-note{flex-basis:100%;margin:2px 0 0}
.v-w-new{border-left:3px solid #1f7a3d}
.v-w-chg{border-left:3px solid #b98a2e}
.v-w-std{border-left:3px solid transparent}
.v-watch-new{font-family:var(--mono);font-size:9px;letter-spacing:.1em;background:#1f7a3d;
             color:#fff;padding:1px 6px;margin-right:6px}
.v-watch-chg{font-family:var(--mono);font-size:9px;letter-spacing:.1em;background:#b98a2e;
             color:#fff;padding:1px 6px;margin-right:6px}
/* ── locked (base; sales trim refines) ─────────────────────────────────── */
.v-locked{display:flex;align-items:flex-start;gap:14px;background:#f7f4ec;
          border:1px solid var(--rule);border-left:3px solid var(--navy);
          padding:16px 18px;margin:12px 0;color:#5d5850}
.v-locked .v-lock-ic{font-size:15px;line-height:1.2;opacity:.7}
.v-lock-body{flex:1;min-width:0}
.v-lock-tag{font-family:var(--mono);font-size:8.5px;letter-spacing:.16em;
            color:var(--navy);border:1px solid var(--navy);padding:2px 7px;
            white-space:nowrap}
.v-verify{display:flex;flex-wrap:wrap;gap:3px 18px;margin-top:12px;padding-top:10px;
          border-top:1px solid #e2dccb}
.v-vtok{font-family:var(--mono);font-size:11px;color:var(--ink)}
.v-vk{letter-spacing:.12em;text-transform:uppercase;font-size:8.5px;color:#8a6d2e;
      margin-right:6px}
.v-vline{font-family:var(--mono);font-size:10.5px;color:#8a6d2e;margin-top:8px}
.v-close{background:var(--navy);color:#f6f4ef;padding:22px 26px;margin-top:36px}
.v-close .v-h{color:#f6f4ef;border-color:#f6f4ef}
.v-close p{line-height:1.6}
/* ── teaming plays ─────────────────────────────────────────────────────── */
.v-team{margin:18px 0 6px;padding:14px 16px;background:#f6f3ec;
        border:1px solid var(--rule);border-left:3px solid #8a5a2f}
.v-cand{margin:8px 0;line-height:1.55}
.v-dual{font-family:var(--mono);font-size:8.5px;letter-spacing:.12em;
        color:#8a2f2f;border:1px solid #8a2f2f;padding:1px 5px;margin-left:6px;
        vertical-align:2px}
.v-flag2{font-family:var(--mono);font-size:10px;color:#8a5a2f;margin-top:3px}
/* ── internal workbench ────────────────────────────────────────────────── */
.v-qa{background:#f3efe6;border-left:4px solid #8a5a2f;padding:12px 16px;margin:10px 0;
      font-size:12px;line-height:1.55}
.v-flag{font-family:var(--mono);font-size:9px;letter-spacing:.1em;background:#8a5a2f;
        color:#fff;padding:1px 6px;margin-left:6px}
.v-cite{font-family:var(--mono);font-size:10px;color:#8a6d2e;word-break:break-all}
.v-check{font-family:var(--mono);font-size:12px}
.v-check .ok{color:#4a6b52;font-weight:600}.v-check .bad{color:#8a2f2f;font-weight:700}
tr:has(.bad) td{background:#f6e8e6}
/* ── sales trim: premium behind glass ──────────────────────────────────── */
.v-locked svg.v-lock-ic{flex:none;margin-top:3px;color:#9a927e}
.v-ghostlines{height:40px;margin:12px 0 2px;border-radius:2px;
   background:repeating-linear-gradient(180deg,#ddd6c4 0 7px,transparent 7px 16px);
   filter:blur(1.3px);opacity:.55;max-width:88%}
.v-locked-row{padding:9px 14px;margin:0;flex:1;border-left-width:2px}
.v-locked-row .v-lock-tag{display:none}
.v-locked-row .v-lock-body{font-size:12.5px;color:#6d675c}
.v-watch li .v-locked-row{align-self:stretch}
td.v-lockcell{max-width:170px;white-space:nowrap}
td.v-lockcell span{display:inline-block;max-width:138px;overflow:hidden;
   text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom;
   color:var(--ink-ghost);margin-left:6px}
td.v-lockcell svg.v-lock-ic{color:#9a927e;vertical-align:baseline}
.view-sales #market td.num{opacity:.5}
.view-sales .footer{border-top:2px solid var(--navy);margin-top:40px}
.view-sales .footer p{font-family:var(--mono);font-size:10px;letter-spacing:.04em;
   color:var(--ink-dim);line-height:1.7}
/* ── navigation + print control ────────────────────────────────────────── */
.v-nav{position:sticky;top:0;z-index:20;display:flex;flex-wrap:wrap;gap:18px;
       align-items:center;background:var(--bg,#fff);border-bottom:1px solid var(--rule);
       padding:10px 0;margin:0 0 12px}
.v-nav a{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;
         text-transform:uppercase;color:var(--ink-dim);text-decoration:none}
.v-nav a:hover{color:var(--ink)}
.v-print{margin-left:auto;font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;
         background:var(--navy);color:#f6f4ef;border:0;padding:7px 14px;cursor:pointer}
.v-print:hover{background:var(--accent-hi)}
/* ── charts ────────────────────────────────────────────────────────────── */
.chart-block{margin:26px 0}
/* ── print: the send-around artifact ───────────────────────────────────── */
@page{margin:16mm 15mm}
@media print{
  html{font-size:10.5pt}
  .v-nav,.v-print{display:none !important}
  a,a:visited{color:inherit !important;text-decoration:none !important}
  a[href]::after{content:"" !important}
  .v-section{margin:0 0 9mm;break-before:page}
  .v-h,.v-dossier h3{break-after:avoid}
  .v-dossier,.v-locked,.v-qa,.chart-block,.v-watch li{break-inside:avoid}
  .v-table tr{break-inside:avoid}
  thead{display:table-header-group}
  .v-locked{background:#f4f1e9 !important;-webkit-print-color-adjust:exact;
            print-color-adjust:exact}
  .v-ghostlines{filter:none;opacity:.4;-webkit-print-color-adjust:exact;
                print-color-adjust:exact}
  .v-close{break-inside:avoid;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .footer{break-inside:avoid}
}
"""


# ── sections ─────────────────────────────────────────────────────────────────

def _sec_market(doc: AssessmentDocument, view: str) -> str:
    m = doc.market
    # EVIDENCE INVERSION (2026-07-10): client-facing agency dollars are the
    # capability-scoped slice, never lane totals; a $74B "your lanes" figure
    # at one agency is the whole filing cabinet and reads as bullshit to any
    # client who knows their market. Lane totals remain INTERNAL reference.
    cap_map = list(m.addressable_agency_annual or m.award_agency_annual)
    src_map = cap_map
    heads = [a.agency for a in src_map[:3]]
    headline = ""
    if heads:
        where = ", ".join(heads[:-1]) + (" and " if len(heads) > 1 else "") + heads[-1]
        # grounded framing: lane spend is market context stated per year, never
        # "the total out for you" — that read as client-addressable TAM. When
        # the keyword-scoped pull ran, the addressable slice is stated too.
        if m.tam_annual_label and m.addressable_annual_label:
            amount = (f" Federal buyers in these lanes obligate about "
                      f"{m.tam_annual_label} a year; roughly "
                      f"{m.addressable_annual_label} of it matches the client's "
                      f"capability keywords (3-year averages).")
        elif m.tam_annual_label:
            amount = (f" Federal buyers in these lanes obligate about "
                      f"{m.tam_annual_label} a year (3-year average).")
        else:
            amount = ""
        headline = f'<p class="v-headline"><strong>You need to be at {_esc(where)}.</strong>{_esc(amount)}</p>'

    rows = "".join(
        f'<tr data-entity="agency"><td><strong>{_agency_mark(a.agency)}{_esc(a.agency)}</strong></td>'
        f'<td class="num">{_esc(a.dollars_label or "·")}</td>'
        f'<td class="v-basis">{_esc(a.basis)}{f" <span class=v-cite>{_esc(a.source)}</span>" if view == "internal" and a.source else ""}</td></tr>'
        for a in src_map)
    agency_table = (f'<table class="v-table"><thead><tr><th>Agency</th><th>Capability-matched dollars</th>'
                    f'<th>Basis</th></tr></thead><tbody>{rows}</tbody></table>'
                    '<p class="v-basis">Two lenses, cited per claim: keyword-'
                    'matched spend sizes what contract language says; product '
                    'and incumbent obligations (the estate mapped elsewhere in '
                    'this assessment) size what agencies already own. The '
                    'lenses measure different things and are never summed.'
                    '</p>') if src_map else \
        '<p>Capability-scoped agency dollars pending the next market pull.</p>'
    if view == "internal" and m.agency_map:
        lane_rows = "".join(
            f'<tr data-entity="agency_lane"><td>{_esc(a.agency)}</td>'
            f'<td class="num">{_esc(a.dollars_label or "·")}</td>'
            f'<td class="v-basis">{_esc(a.basis)}'
            f'{f" <span class=v-cite>{_esc(a.source)}</span>" if a.source else ""}</td></tr>'
            for a in m.agency_map)
        agency_table += (
            '<div class="v-sub">Lane totals, INTERNAL boundary reference '
            '(filing-cabinet numbers; never client-facing)</div>'
            f'<table class="v-table"><thead><tr><th>Agency</th><th>Lane dollars</th>'
            f'<th>Basis</th></tr></thead><tbody>{lane_rows}</tbody></table>')

    if view == "sales":
        math_block = _lock("Per-agency breakdown and the TAM math ship with the "
                           "full assessment, every dollar traced to its source.",
                           m.locked)
        bars = ""
    else:
        comp_rows = "".join(
            f'<tr><td>{_esc(c.label)}</td><td class="num">{_esc(c.dollars_label or "·")}</td>'
            f'<td class="mono">{_esc(c.kind)}</td><td class="v-basis">{_esc(c.basis)}'
            f'{f" <span class=v-cite>{_esc(c.source)}</span>" if view == "internal" else ""}</td></tr>'
            for c in m.components)
        math_block = (f'<div class="v-sub">The math: what sums to the number</div>'
                      f'<table class="v-table"><thead><tr><th>Component</th><th>Dollars</th>'
                      f'<th>Type</th><th>Basis</th></tr></thead><tbody>{comp_rows}</tbody></table>') if m.components else ""
        bars = _money_bars([(c.label, c.dollars, c.dollars_label)
                            for c in m.components if c.kind == "market"])

    news = "".join(
        f'<li><a data-thirdparty="1" href="{_esc(n.get("url") or "")}">'
        f'{_esc(n.get("title") or "")}</a></li>'
        for n in m.news)
    news_block = f'<div class="v-sub">Most relevant news, current cycle</div><ul>{news}</ul>' if news else ""
    teaser = ('<p class="v-note">Weekly watchlist, a client feature, tracks the full feed.</p>'
              if view == "sales" else "")
    return (f'<section class="v-section" id="market"><h2 class="v-h">High-Level Market Overview</h2>'
            f'{headline}{agency_table}{bars}{math_block}{news_block}{teaser}</section>')


def _escn(s) -> str:
    """Escape + house-style normalize: artifact-borne prose (triage reasons,
    watchlist detail, blocker basis, notice titles) predates the punctuation
    rule and re-renders forever, so the seam normalizes. Third-party news
    titles stay verbatim on plain _esc (the quoted-coverage exemption)."""
    from agents.reports.capture_brief import normalize_house_style
    return _esc(normalize_house_style(s if isinstance(s, str) else ("" if s is None else str(s))))


_DIRECTION_LABELS = {
    "SUB_TO_PRIME": "subcontract to a prime",
    "PRIME_WITH_SUBS": "prime with subs",
    "JV_OR_TEAMING_ARRANGEMENT": "JV / teaming arrangement (structure is a "
                                 "human decision)",
}


def _fit_chip(fit) -> str:
    if fit is None:
        return ""
    return (f'<span class="v-grade {_GRADE_CLASS.get(fit.letter, "vg-c")}" '
            f'title="partner fit {fit.score:g}/4 across {fit.scored_dimensions} '
            f'scored dimensions">{fit.letter}</span>')


def _teaming_block(play, view: str) -> str:
    """The teaming play inside a pursuit's chapter. Client: blocker stated
    plainly, direction, top candidates with cited evidence. Internal: the
    full board — blockers table, candidate worksheets, evidence rows,
    dual-role markers, review flags."""
    directions = " · ".join(_DIRECTION_LABELS.get(d, d) for d in play.directions)
    if view == "sales":
        # Candidate identities and their evidence rows have already been
        # physically removed by gate_for_sales. Render only the opportunity-
        # specific shape and the verifier-bearing lock from that gated model.
        # Monitor sales copy is owned by _sec_teaming_watch; routing it here
        # would silently give that separate surface pursuit-card wording.
        if play.kind != "pursue":
            raise ValueError("sales teaming card renderer is pursuit-only")
        head = f"Teaming path · pursuit #{play.rank}"
        locked = _lock(
            f"<strong>{head}</strong>. <strong>Direction:</strong> "
            f"{_esc(directions)}. Named evidence-graded candidates and their "
            "cited award records are client content.",
            play.locked,
        )
        return (f'<div class="v-team" data-entity="teaming_play">'
                f'{locked}</div>')
    fired = [b for b in play.blockers if b.fired]
    if play.kind == "monitor":
        head = ("Teaming watch · " + _escn(play.title or "adjacent opportunity")
                + (f" · {_esc(play.agency)}" if play.agency else ""))
    else:
        head = f"Teaming play · pursuit #{play.rank}"
    parts = [f'<div class="v-team" data-entity="teaming_play">'
             f'<div class="v-sub">{head}</div>']
    if view == "internal":
        rows = "".join(
            f'<tr><td class="mono">{_esc(b.kind)}</td>'
            f'<td class="v-check">{"<span class=bad>FIRED</span>" if b.fired else "<span class=ok>N/A</span>"}'
            f'{" · inverse" if b.inverse else ""}</td>'
            f'<td class="v-basis">{_escn(b.basis)}'
            f'{"".join(f"<div class=v-flag2>{_esc(f)}</div>" for f in b.review_flags)}</td></tr>'
            for b in play.blockers)
        parts.append(f'<table class="v-table"><thead><tr><th>Blocker</th>'
                     f'<th>State</th><th>Basis</th></tr></thead><tbody>{rows}</tbody></table>')
    else:
        for b in fired:
            parts.append(f'<p>{_escn(b.basis)}.</p>')
    parts.append(f'<p><strong>Direction:</strong> {_esc(directions)}.</p>')

    top = play.candidates[:3] if view == "client" else play.candidates[:8]
    for c in top:
        dollars = sum(e.dollars or 0 for e in c.evidence)
        n_rows = len(c.evidence)
        dual = (f' <span class="v-dual" title="{_esc(c.dual_role)}">DUAL ROLE</span>'
                if c.dual_role else "")
        cite = next((e.source for e in c.evidence if e.source), "")
        parts.append(
            f'<div class="v-cand" data-entity="partner_candidate">'
            f'{_fit_chip(c.fit)} <strong>{_esc(c.name)}</strong>{dual} · '
            f'evidence: {n_rows} cited row(s), ${dollars:,.0f} total '
            f'(USAspending){f" <span class=v-cite>{_esc(cite)}</span>" if cite else ""}</div>')
        if view == "internal" and c.fit is not None:
            work = "".join(
                f'<tr><td>{_esc(d.dimension)}</td>'
                f'<td>{d.score if d.score is not None else "N/A"}</td>'
                f'<td>{d.effective_weight:.0%}</td><td class="v-basis">{_escn(d.basis)}'
                f'{f" <em>({_esc(d.na_reason)})</em>" if d.na_reason else ""}</td></tr>'
                for d in c.fit.dimensions)
            ev = "".join(
                f'<tr><td class="mono">{_esc(e.kind)}</td>'
                f'<td class="mono">{_esc(e.fact_id or "·")}</td>'
                f'<td class="num">{f"${e.dollars:,.0f}" if e.dollars else "·"}</td>'
                f'<td>{_esc(e.agency or "·")}</td><td class="mono">{_esc(e.date or "·")}</td>'
                f'<td class="v-basis">{_escn(e.detail)}</td></tr>'
                for e in c.evidence[:8])
            parts.append(
                f'<div class="v-qa"><strong>Partner fit worksheet · '
                f'{_esc(c.name)} ({c.fit.letter} {c.fit.score:g})</strong>'
                f'<table class="v-table"><tr><th>Dimension</th><th>Score</th>'
                f'<th>Weight</th><th>Basis</th></tr>{work}</table>'
                f'<div class="v-sub" style="margin-top:8px">Evidence rows'
                f'{f" (first 8 of {n_rows})" if n_rows > 8 else ""}</div>'
                f'<table class="v-table"><tr><th>Kind</th><th>Fact id</th>'
                f'<th>$</th><th>Agency</th><th>Date</th><th>Detail</th></tr>'
                f'{ev}</table></div>')
    if not play.candidates:
        parts.append('<p class="v-note">No evidence-backed candidates surfaced '
                     'for this play yet; see gaps.</p>')
    if view == "client" and play.review_flags:
        parts.append("".join(f'<div class="v-prov">{_esc(f)}</div>'
                             for f in play.review_flags))
    parts.append("</div>")
    return "".join(parts)


def _sec_teaming_watch(doc: AssessmentDocument, view: str) -> str:
    """Monitor-grade adjacencies where teaming is the path — the pre-prime
    surface. Renders only when evidence-backed monitor plays exist (no
    padding). Internal/client: full teaming blocks. Sales: locked shape with
    verifier lines; notice titles and candidate names are client content."""
    plays = [p for p in doc.partnering.plays if p.kind == "monitor"]
    if not plays:
        return ""
    n = doc.counts()["teaming_watch"]
    lead = ("Adjacent, monitor-grade notices the client would team on rather "
            "than prime; the primes that win this kind of work in the lane, "
            "named from award evidence.")
    if view == "sales":
        blocks = []
        for p in plays:
            dirs = " · ".join(_DIRECTION_LABELS.get(d, d) for d in p.directions)
            blocks.append(_lock(
                f"<strong>Teaming watch · {_esc(p.agency or 'adjacent opportunity')}"
                f"</strong>. {_esc(dirs)}. The notice and the named partner "
                "candidates with cited award evidence are client content.",
                p.locked))
        body = "".join(blocks)
    else:
        body = "".join(_teaming_block(p, view) for p in plays)
    return (f'<section class="v-section" id="teaming-watch">'
            f'<h2 class="v-h">Teaming Watch</h2>'
            f'<p><span data-counts-verified="1">{n} adjacent opportunit'
            f'{"y" if n == 1 else "ies"}</span> where teaming is the path, not '
            f'priming. {lead}</p>{body}</section>')


def _sec_pursuits(doc: AssessmentDocument, view: str) -> str:
    board = doc.board
    counts = doc.counts()
    vt = doc.verdict_totals or {}
    scanned = sum(value for value in vt.values() if type(value) is int)
    census = doc.sam_census or {}
    raw_total = census.get("active_screened")
    census_total = raw_total if type(raw_total) is int and raw_total >= 0 else 0
    strict_held = any(
        gap.startswith("strict Live SAM cutover invalid")
        or gap.startswith("strict Live SAM coverage is incomplete")
        for gap in doc.gaps
    )

    def _held_copy(*, has_pursuits: bool = False) -> str:
        complete = census.get("complete", True) is True
        mark = (f'data-screen-census="1" data-screened="{scanned}" '
                f'data-census="{census_total}" '
                f'data-complete="{1 if complete else 0}" '
                'data-held-closed="1"')
        reconciliation = (
            f"the evidence set reconciles {scanned:,} of {census_total:,} "
            "census records"
            if census_total else
            f"the evidence set carries {scanned:,} reconciled census records"
        )
        next_step = (
            "The cards above remain evidence-bound, but treat them as a "
            "verified shortlist rather than a complete market screen until "
            "source coverage is reconciled."
            if has_pursuits else
            "Refresh and reconcile source coverage before drawing a "
            "no-opportunity conclusion or making pursuit claims."
        )
        return (f'<span data-counts-verified="1" {mark}>'
                "The live-source evidence gate is held closed: "
                f"{reconciliation}. {next_step}</span>")

    if board.pursuits:
        # the compound per-agency phrase is ASSEMBLED from grouped data (Layer 1)
        # and marked verified — lint_counts hunts freewritten numbers, not these
        landscape = (f'{int_to_word(counts["pursuits"]).capitalize()} graded pursuit'
                     f'{"s" if counts["pursuits"] != 1 else ""} from '
                     f'{counts["pursue_notices"]} pursue-grade solicitation'
                     f'{"s" if counts["pursue_notices"] != 1 else ""}: '
                     f'<span data-counts-verified="1">'
                     f'{_esc(agency_group_phrase(board.pursuits))}</span>.')
        # Opportunity-assessment doctrine: the client deliverable is an
        # opportunity SET, never an audit. The held-coverage reconciliation is
        # audit language, so it rides the internal/sales views only; the client
        # view carries the opportunity set alone.
        if strict_held and view != "client":
            landscape += _held_copy(has_pursuits=True)
    else:
        # zero pursue-grade is a FINDING, not an empty section — and NEVER the
        # headline. The assessment sells the next layer down: LEAD with what
        # the sweep DID find (the monitored adjacencies and teaming surface),
        # then state the screen once as the credential that makes the finding
        # trustworthy. Counts are assembled from verdict_totals (Layer-1
        # ground truth) and marked verified so lint_counts leaves them.
        monitor, off = vt.get("monitor", 0), vt.get("discard", 0)
        # Opportunity-assessment doctrine (client view): the deliverable is an
        # opportunity SET. It leads with forward motion (the layer down worked
        # on the watchlist and teaming surface) and carries NO screen credential,
        # NO "screened out", NO "held closed" reconciliation, and NO QA-appendix
        # homework. The full screen/census audit rides the internal/sales views.
        client_view = view == "client"
        if strict_held and not client_view:
            landscape = _held_copy()
        elif scanned:
            lead = ""
            if monitor:
                lead = (f'The motion this cycle is one layer down: {monitor} '
                        f'notice{"s" if monitor != 1 else ""} tracked as '
                        f'adjacent or teaming '
                        f'opportunit{"y" if monitor == 1 else "ies"}, worked '
                        f'on the watchlist and the teaming surface below. ')
            if client_view:
                landscape = (f'<span data-counts-verified="1">{lead}</span>'
                             if lead else "")
            else:
                cred = (f'{"The" if lead else "This cycle the"} screen behind '
                        f'{"that finding" if lead else "the sweep"}: {scanned} '
                        f'notice{"s" if scanned != 1 else ""} triaged in the '
                        f"client's NAICS lanes, every one accounted for, none "
                        f'meeting the pursue-grade bar'
                        + (f', {off} screened out as off-mission' if off else '')
                        + '.')
                # L17: the census travels with the line (contract-surface
                # template above is untouched; the mark + shortfall sentence are
                # the FEED). A partial screen rendering as a complete one is a
                # lint failure.
                c_total = census.get("active_screened")
                c_complete = census.get("complete", True)
                shortfall = ""
                if census and not c_complete:
                    got = census.get("retrieved", census.get("matched", scanned))
                    shortfall = (f' The sweep screened {got:,} of {c_total:,} '
                                 f'records; the shortfall is flagged in the QA '
                                 f'appendix.')
                mark = (f'data-screen-census="1" data-screened="{scanned}" '
                        f'data-census="{c_total if c_total is not None else scanned}" '
                        f'data-complete="{1 if c_complete else 0}"')
                landscape = (f'<span data-counts-verified="1" {mark}>'
                             f'{lead}{cred}{shortfall}</span>')
        elif client_view:
            landscape = ""
        else:
            landscape = ("No notices were screened in this sweep; the market "
                         "pull returned none to triage. This is a data gap, not "
                         "an all-clear.")

    weights = " · ".join(f"{k} {v:.0%}" for k, v in board.rubric_weights.items())
    rows = []
    for p in board.pursuits:
        title = (f'<a href="{_esc(p.url)}">{_escn(p.title)}</a>' if p.url and view != "sales"
                 else _escn(p.title))
        # rendered blindly from the model: the full id on client/internal, the
        # masked form on sales — the gated model never carries the full id
        notice_id = p.solicitation or p.source_id
        rows.append(f'<tr data-entity="pursuit"><td>{p.rank}</td><td>{title}</td>'
                    f'<td>{_agency_mark(p.agency)}{_esc(p.agency or "·")}</td>'
                    f'<td class="mono">{_esc(notice_id)}</td>'
                    f'<td>{_grade_chip(p)}</td>'
                    f'<td class="mono">{_esc(p.response_deadline or "·")}</td></tr>')
        # SHOW THE WORK (2026-07-10): why this fits, mechanically — matched
        # capability terms, NAICS boundary, mission component, and the screen
        # inference labeled as inference. Client + internal; sales stays gated.
        ft = getattr(p, "fit_trace", None)
        if ft and view != "sales":
            bits = []
            if ft.get("matched_terms"):
                bits.append("matched: " + ", ".join(ft["matched_terms"]))
            if ft.get("naics"):
                bits.append(f'NAICS {ft["naics"]}'
                            + (" (in the client's lanes)"
                               if ft.get("naics_in_boundary") else ""))
            if ft.get("mission_component"):
                bits.append(f'mission component: {ft["mission_component"]}')
            bits.append(f'capability score {ft.get("score", 0)}')
            inference = (f' Analyst inference: {ft["screen_inference"]}'
                         if ft.get("screen_inference") else "")
            rows.append(
                f'<tr class="v-why"><td></td><td colspan="5" class="v-basis">'
                f'Why this fits: {_esc(" · ".join(bits))}.{_esc(inference)}</td></tr>')
    # a zero-pursuit cycle renders the FORWARD WATCH, never an empty grid
    # (client-file doctrine: a negative is a watch stood up, not furniture)
    if rows:
        table = (f'<table class="v-table"><thead><tr><th>#</th><th>Pursuit</th><th>Agency</th>'
                 f'<th>Notice ID</th><th>Grade</th><th>Due</th></tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table>')
    else:
        table = ('<p>A standing watch holds on these lanes; pursue-grade '
                 'notices surface here the moment they post.</p>')

    # The sales gate masks notice ids before rendering. Several solicitations
    # can share the same visible prefix and therefore the same masked id, so
    # rank remains the non-sensitive disambiguator for the already-masked join.
    plays = {(pl.source_id, pl.rank): pl for pl in doc.partnering.plays
             if pl.kind == "pursue"}
    dossier_html = []
    for p in board.pursuits:
        play = plays.get((p.source_id, p.rank))
        if view == "sales":
            # every locked opportunity: lock treatment PLUS credibility tokens
            # (agency, notice type, deadline, grade, masked id, verified line)
            card = _lock(
                f"<strong>#{p.rank} · {_escn(p.title)} · grade {p.grade.letter}"
                "</strong>. The full dossier (contracting office, contacts, "
                "vehicle path, incumbent, runway, recommended play) is a "
                "client feature.", p.locked)
            if play is not None:
                # The locked teaming path belongs to this opportunity card;
                # candidate identities remain absent from the gated model.
                card += _teaming_block(play, view)
            dossier_html.append(
                f'<div class="v-dossier" data-pursuit-rank="{p.rank}">'
                f'{card}</div>')
            continue
        dossier_html.append(_dossier_block(p, view, play=play))

    profile_warn = ""
    if view == "internal" and (doc.partnering.profile_unset
                               or doc.partnering.profile_warnings):
        bits = []
        if doc.partnering.profile_unset:
            bits.append("unset fields: " + ", ".join(doc.partnering.profile_unset))
        bits.extend(doc.partnering.profile_warnings)
        profile_warn = ('<div class="v-qa"><span class="v-flag">PROFILE</span> '
                        'partnering profile (data/review/&lt;slug&gt;.partnering.json); '
                        f'{_esc(" · ".join(bits))}; related blocker checks '
                        'degrade to N/A</div>')

    rubric = (f'<div class="v-sub">Grading rubric · {_esc(weights)} · '
              f'N/A dimensions redistribute weight (shown in worksheets)</div>'
              if rows else "")
    return (f'<section class="v-section" id="pursuits"><h2 class="v-h">Pursuit Strategy</h2>'
            f'{profile_warn}'
            f'<p>{landscape}</p>{rubric}'
            f'{table}{"".join(dossier_html)}</section>')


def _dossier_block(
    p: GradedPursuit,
    view: str,
    *,
    play: Optional[PartneringPlay] = None,
) -> str:
    """One pursuit's dossier chapter for client/internal rendering — used by
    the assessment's pursuit section AND the standalone dossier downloads.
    Returns the amendment-only card when no dossier exists, else the full
    closed block (with the grade worksheet on internal)."""
    # amendment history: stated data only (posting ids, dates, deadline
    # moves the postings themselves state) — one solicitation, one card
    amend = ""
    amend_rule = ""
    if p.amendments:
        hops = " → ".join(
            f'{a["source_id"]} posted {a.get("posted_date") or "?"}'
            + (f' ({a["change"]})' if a.get("change") else "")
            for a in p.amendments)
        amend = (f'<div class="v-prov">Amendment history for this solicitation: '
                 f'{_esc(hops)}. Graded on the latest posting.</div>')
        amend_rule = ('<div>Rule: the grade keys to the latest amendment '
                      f'posting ({_esc(p.amendments[-1].get("posted_date") or "·")}, '
                      f'deadline {_esc(p.response_deadline or "·")}), the live '
                      'state of the solicitation. Earlier postings are listed '
                      'in the amendment history above.</div>')
    teaming = _teaming_block(play, view) if play is not None else ""
    d = p.dossier
    if not d:
        if amend or teaming:
            rule = (f'<div class="v-qa">{amend_rule}</div>'
                    if view == "internal" and amend_rule else "")
            return (f'<div class="v-dossier" data-pursuit-rank="{p.rank}">'
                    f'<h3>#{p.rank} · {_escn(p.title)} {_grade_chip(p)}</h3>'
                    f'{amend}{rule}{teaming}</div>')
        return ""
    wins = "".join(f"<li>{_esc(w)}</li>" for w in d.get("win_themes") or [])
    flags = "".join(f"<li>{_esc(w)}</li>" for w in d.get("red_flags") or [])
    out = (
        f'<div class="v-dossier" data-entity="dossier" '
        f'data-pursuit-rank="{p.rank}"><h3>#{p.rank} · {_escn(p.title)} '
        f'{_grade_chip(p)}</h3>{amend}<p>{_esc(d.get("scope_summary") or "")}</p>'
        f'<p class="v-facts"><strong>Vehicle:</strong> {_esc(d.get("vehicle") or "full and open")} · '
        f'<strong>Next:</strong> {_esc(d.get("next_action") or "")}</p>'
        + (f"<div class='v-sub'>Win themes</div><ul class='v-wins'>{wins}</ul>" if wins else "")
        + (f"<div class='v-sub'>Red flags</div><ul class='v-flags'>{flags}</ul>" if flags else "")
        + teaming)
    if view == "internal":
        work_rows = "".join(
            f'<tr><td>{_esc(dim.dimension)}</td>'
            f'<td>{dim.score if dim.score is not None else "N/A"}</td>'
            f'<td>{dim.effective_weight:.0%}</td><td>{_esc(dim.basis)}'
            f'{f" <em>({_esc(dim.na_reason)})</em>" if dim.na_reason else ""}</td></tr>'
            for dim in p.grade.dimensions)
        out += (f'<div class="v-qa"><strong>Grade worksheet · how '
                f'{p.grade.letter} ({p.grade.score:g}) was computed</strong>'
                f'{amend_rule}'
                f'<table class="v-table"><tr><th>Dimension</th><th>Score</th>'
                f'<th>Weight</th><th>Basis</th></tr>{work_rows}</table></div>')
    return out + "</div>"


def _sec_competitive(doc: AssessmentDocument, view: str) -> str:
    comp = doc.competitive
    # PRODUCT COMPETITORS vs LANE COHABITANTS (2026-07-10): when the
    # capability profile classified the landscape, client and sales views
    # carry product competitors only; the lane furniture renders internal
    if comp.product_tagged:
        product = [c for c in comp.competitors if c.product]
        cohab = [c for c in comp.competitors if not c.product]
    else:
        product, cohab = list(comp.competitors), []
    n = len(product)
    if view == "sales" and not n:
        body = f"<p>{_esc(comp.coverage_note)}</p>"
    elif view == "sales":
        # locked rows keep their structural anchors (the count claim in the
        # upsell copy reconciles against N competitor elements) AND carry the
        # per-competitor verifiers: verification basis + NAICS lanes stay,
        # names and reasoning stay locked
        rows = "".join(
            f'<tr data-entity="competitor" data-locked="1">'
            f'<td class="v-lockcell">{_LOCK_GLYPH}<span>{_esc(c.name)}</span></td>'
            f'<td class="mono">{_esc(", ".join(c.naics) or "·")}</td>'
            f'<td class="v-basis">{_esc(c.basis)}</td></tr>'
            for c in product)
        locked_table = (f'<table class="v-table"><thead><tr><th>Competitor</th>'
                        f'<th>NAICS lanes</th><th>Verification basis</th></tr></thead>'
                        f'<tbody>{rows}</tbody></table>') if n else ""
        body = _lock(f"<strong>{n} competitor{'s' if n != 1 else ''} mapped</strong> in your "
                     "lanes. Names, the awards and vehicles behind each, and why they'll "
                     "show up: it all ships with the full assessment.",
                     comp.locked) + locked_table
    else:
        rows = "".join(
            f'<tr data-entity="competitor"><td><strong>{_company_mark(c.name)}{_esc(c.name)}</strong></td>'
            f'<td class="num">{_esc(c.dollars_label or "·")}</td>'
            f'<td>{_esc(", ".join(c.agencies[:3]) or "·")}</td><td class="v-basis">{_esc(c.basis)}'
            f'{f"<div class=v-note>LINEAGE: {_esc(c.lineage_note)}</div>" if view == "internal" and c.lineage_note else ""}'
            f'{f" <span class=v-cite>{_esc(c.source)}</span>" if view == "internal" else ""}</td></tr>'
            for c in product)
        body = (f'<table class="v-table"><thead><tr><th>Competitor</th><th>Dollars seen</th>'
                f'<th>Agencies</th><th>Why they show up</th></tr></thead><tbody>{rows}</tbody></table>'
                f'<p class="v-note">{_esc(comp.coverage_note)}</p>') if n else \
            ('<p>Product-level competition lives in the incumbent estate: the '
             'displacement windows in this assessment name who holds the '
             'ground today and when each clock runs out.</p>'
             if comp.product_tagged else f"<p>{_esc(comp.coverage_note)}</p>")
        if view == "internal" and cohab:
            crows = "".join(
                f'<tr data-entity="competitor_lane"><td>{_esc(x.name)}</td>'
                f'<td class="num">{_esc(x.dollars_label or "·")}</td>'
                f'<td class="v-basis">{_esc(x.basis)}'
                f'{f"<div class=v-note>LINEAGE: {_esc(x.lineage_note)}</div>" if x.lineage_note else ""}'
                f'</td></tr>' for x in cohab)
            body += (f'<div class="v-sub">Lane cohabitants, INTERNAL reference '
                     f'(repeat lane awardees, not product competitors)</div>'
                     f'<table class="v-table"><thead><tr><th>Awardee</th>'
                     f'<th>Dollars seen</th><th>Basis</th></tr></thead>'
                     f'<tbody>{crows}</tbody></table>')
    return (f'<section class="v-section" id="competitive"><h2 class="v-h">Competitive '
            f'Landscape</h2><p>These are your competitors and why.</p>{body}</section>')


def _sec_watchlist(doc: AssessmentDocument, view: str) -> str:
    w = doc.watchlist
    badge = {"new": '<span class="v-watch-new">NEW</span>',
             "changed": '<span class="v-watch-chg">CHANGED</span>', "standing": ""}
    kind_cls = {"new": "v-w-new", "changed": "v-w-chg", "standing": "v-w-std"}
    items = []
    for e in w.entries:
        cls = kind_cls.get(e.kind, "v-w-std")
        if e.locked:
            # locked item: motion badge + source stay (real, non-actionable);
            # title, link and detail are the client feature
            src = e.locked.verifiers.get("source", "")
            items.append(
                f'<li class="{cls}" data-entity="watchlist_entry">{badge.get(e.kind, "")}'
                + _lock(f"Watchlist item · {_esc(src)} · weekly monitoring "
                        "is a client feature.", cls="v-locked-row")
                + "</li>")
        else:
            link = (f'<a href="{_esc(e.url)}">{_escn(e.title or e.id)}</a>' if e.url
                    else _escn(e.title or e.id))
            items.append(f'<li class="{cls}" data-entity="watchlist_entry">{badge.get(e.kind, "")}'
                         f'{link} <span class="v-note">{_escn(e.detail)}</span></li>')
    refreshed = (f'Last refreshed {_esc(w.last_refreshed or "·")} · {_esc(w.delta_note)}')
    strip = _verify_strip(w.locked)
    return (f'<section class="v-section" id="watchlist"><h2 class="v-h">Watchlist</h2>'
            f'<div class="v-sub">{refreshed}</div>{strip}'
            f'<ul class="v-watch">{"".join(items)}</ul></section>')


def _host_local_datetime(value: datetime) -> datetime:
    """Convert an aware value with the host's rules for that exact instant."""
    return value.astimezone()


def _generated_local_date(value: str) -> Optional[date]:
    """Sweep day on the operator host; naive fallback mtimes are local."""
    text = str(value or "").strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        generated = datetime.fromisoformat(text)
    except ValueError:
        return None
    if generated.tzinfo is not None:
        generated = _host_local_datetime(generated)
    return generated.date()


def _sec_internal_qa(doc: AssessmentDocument, client_html: str) -> str:
    """Pre-ship checklist rendered IN the document: the same hard gates the
    build enforces, shown as a table, plus count reconciliation and gaps."""
    from agents.reports.lint import (
        lint_brief_identity, lint_client_terminology, lint_contact_rendering,
        lint_counts,
    )
    checks = [
        ("Identity assets (logos, cover art)", lint_brief_identity(client_html)),
        ("Contacts graded + sourced", lint_contact_rendering(client_html)),
        ("Client terminology", lint_client_terminology(client_html)),
        ("Counts reconcile with copy", lint_counts(client_html)),
    ]
    rows = "".join(
        f'<tr><td>{_esc(name)}</td>'
        f'<td class="v-check">{"<span class=ok>PASS</span>" if r.ok else "<span class=bad>FAIL</span>"}</td>'
        f'<td>{_esc("; ".join(v.detail for v in r.violations[:3]))}</td></tr>'
        for name, r in checks)
    counts_rows = "".join(f"<tr><td>{_esc(k)}</td><td>{v}</td></tr>"
                          for k, v in doc.counts().items())
    gaps = "".join(f"<li>{_esc(g)}</li>" for g in doc.gaps) or "<li>none recorded</li>"
    stale = ""
    if (doc.generated_at
            and _generated_local_date(doc.generated_at) != doc.as_of):
        stale = ('<div class="v-qa"><span class="v-flag">CONFIDENCE</span> underlying sweep '
                 f'was generated {_esc(str(doc.generated_at))}; not same-day verified '
                 f'against {doc.as_of.isoformat()}.</div>')
    return (f'<section class="v-section" id="preship"><h2 class="v-h">Pre-Ship Review '
            f'(internal)</h2>{stale}'
            f'<table class="v-table"><tr><th>Gate</th><th>State</th><th>Detail</th></tr>{rows}</table>'
            f'<div class="v-sub">Ground-truth counts (single source for all copy numbers)</div>'
            f'<table class="v-table">{counts_rows}</table>'
            f'<div class="v-sub">Known gaps</div><ul>{gaps}</ul></section>')


# ── entry point ──────────────────────────────────────────────────────────────

_SALES_CLOSE = (
    '<section class="v-section v-close"><h2 class="v-h">What the full engagement '
    'includes</h2><p>The per-agency dollar map with the math shown, all pursuit '
    'dossiers with recommended plays, named competitors with the evidence behind '
    'each, and the weekly-refreshed watchlist, every figure traced to a citable '
    'source.</p></section>')


def _footer_note(view: str, as_of: date) -> str:
    note = ("Verified from SAM.gov, USAspending, and primary federal sources. "
            "GTM Group analyst judgment applied throughout.")
    if view == "sales":
        note += (f" All opportunities are verified against primary sources as of "
                 f"{as_of.isoformat()}. Full identifiers, contacts, and pursuit "
                 f"plays are part of the client engagement.")
    return note


def house_css() -> str:
    """The locked house sheet + the views addendum — one source for every
    surface that renders assessment content (standalone docs, dashboard)."""
    from agents.reports.capture_brief import _TPL
    return (_TPL / "capture_brief.css").read_text() + _STYLE_ADDENDUM


def render_sales_teaser_body(doc: AssessmentDocument) -> str:
    """The dashboard's sales-mode body: the gated teaser sections rendered
    from the SAME gated model as the standalone sales document. Composition
    only — gating, verifiers, masking and counts all come from
    gate_for_sales; the rendered fragment physically lacks paid content."""
    g = gate_for_sales(doc)
    body = ("".join([_sec_market(g, "sales"), _sec_pursuits(g, "sales"),
                     _sec_competitive(g, "sales"), _sec_watchlist(g, "sales")])
            + _SALES_CLOSE
            + f'<footer class="footer"><p>{_esc(_footer_note("sales", g.as_of))}</p></footer>')
    return resolve_count_tokens(body, g.counts())


def _dossier_page(doc: AssessmentDocument, pursuits: list[GradedPursuit],
                  subtitle: str) -> str:
    """Standalone self-contained dossier document in the house template —
    extraction of the assessment's internal dossier rendering, not new
    content. Links live; inline assets only (plus the existing fonts)."""
    from agents.reports.capture_brief import _gtm_logo_b64, _gtm_logo_mime
    plays = {(play.source_id, play.rank): play
             for play in doc.partnering.plays if play.kind == "pursue"}
    blocks = []
    for p in pursuits:
        src = (f'<p class="v-facts"><a href="{_esc(p.url)}">SAM.gov notice</a> · '
               f'{_esc(p.solicitation or p.source_id)} · due '
               f'{_esc(p.response_deadline or "·")}</p>' if p.url else "")
        blocks.append(src + _dossier_block(
            p, "internal", play=plays.get((p.source_id, p.rank))))
    gtm = (f'<div class="logo-plate"><img id="gtmLogoImg" '
           f'src="data:{_gtm_logo_mime()};base64,{_gtm_logo_b64()}" '
           f'alt="GTM — Go To Market"></div>')
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(doc.client_name)} · {_esc(subtitle)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{house_css()}</style></head><body><div class="page view-internal">
<div style="display:flex;align-items:center;gap:14px;margin:6px 0 10px">
  <span class="cover-eyebrow" style="flex:1">Pursuit intelligence · Internal working document</span>{gtm}
</div>
<h1 style="font-family:var(--serif);font-size:30px;font-weight:900;margin:0 0 4px">{_esc(doc.client_name)}</h1>
<div class="v-sub">{_esc(subtitle)} · as of {doc.as_of.isoformat()}</div>
<section class="v-section">{"".join(blocks)}</section>
<footer class="footer"><p>{_esc(_footer_note("internal", doc.as_of))}</p></footer>
</div></body></html>"""


def render_dossier_html(doc: AssessmentDocument, rank: int) -> Optional[str]:
    """One ranked dossier as a standalone document; None when that rank has
    no dossier record."""
    p = next((x for x in doc.board.pursuits if x.rank == rank and x.dossier), None)
    if p is None:
        return None
    return _dossier_page(doc, [p], f"Pursuit Dossier #{rank}")


def render_all_dossiers_html(doc: AssessmentDocument) -> Optional[str]:
    """Every built dossier in one standalone document; None when none exist."""
    ps = [p for p in doc.board.pursuits if p.dossier]
    if not ps:
        return None
    return _dossier_page(doc, ps, "Pursuit Dossiers · complete set")


# ── scoreboard banner (dashboard, both views) ───────────────────────────────

# Official seal images can swap in behind this single flag when the business
# decision lands: style "seal" looks for data/reference/seals/<slug>.png and
# falls back to the monogram when no seal file exists. Default: monogram.
# "seal": official agency marks from the local cache render as the badge
# visual and as inline marks throughout; anything uncached falls back to the
# designed monogram. Renderers NEVER fetch — tools/brand_marks.py prefetches.
AGENCY_BADGE_STYLE = "seal"


def _seal_dir() -> Path:
    import os as _os
    return Path(_os.environ.get(
        "LILA_SEALS_DIR",
        Path(__file__).resolve().parents[2] / "data" / "reference" / "seals"))


def _company_dir() -> Path:
    import os as _os
    return Path(_os.environ.get(
        "LILA_COMPANY_MARKS_DIR",
        Path(__file__).resolve().parents[2] / "data" / "reference" / "marks" / "company"))


_MARK_CACHE: dict = {}  # path -> (mtime, data_uri) — embeds are per-render hot


def _mark_data_uri(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _MARK_CACHE.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    import base64
    from tools.brand_marks import MARK_MIMES
    mime = MARK_MIMES.get(path.suffix.lower(), "image/png")
    uri = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
    _MARK_CACHE[str(path)] = (mtime, uri)
    return uri


def _agency_mark(agency: Optional[str], cls: str = "v-amark") -> str:
    """Inline agency seal, keyed by MONOGRAM so every string variant of an
    agency ('DEPT OF DEFENSE / DISA…', 'Department of Defense') shares one
    cached file (any supported extension). Empty when uncached — never a
    broken image."""
    if not agency:
        return ""
    from tools.brand_marks import find_mark
    uri = _mark_data_uri(find_mark(_seal_dir(), _agency_monogram(agency).lower()))
    return f'<img class="{cls}" alt="" src="{uri}">' if uri else ""


def _company_mark(name: Optional[str], cls: str = "v-amark") -> str:
    if not name:
        return ""
    from tools.brand_marks import company_slug, find_mark
    uri = _mark_data_uri(find_mark(_company_dir(), company_slug(name)))
    return f'<img class="{cls}" alt="" src="{uri}">' if uri else ""

_AGENCY_MONOGRAMS = {
    "DEPARTMENT OF DEFENSE": "DoD", "DEPT OF DEFENSE": "DoD",
    "DEPARTMENT OF HOMELAND SECURITY": "DHS",
    "DEPARTMENT OF JUSTICE": "DOJ",
    "DEPARTMENT OF VETERANS AFFAIRS": "VA",
    "DEPARTMENT OF HEALTH AND HUMAN SERVICES": "HHS",
    "DEPARTMENT OF THE TREASURY": "TREAS",
    "DEPARTMENT OF STATE": "DOS",
    "DEPARTMENT OF TRANSPORTATION": "DOT",
    "DEPARTMENT OF THE INTERIOR": "DOI",
    "DEPARTMENT OF AGRICULTURE": "USDA",
    "DEPARTMENT OF COMMERCE": "DOC",
    "DEPARTMENT OF ENERGY": "DOE",
    "DEPARTMENT OF LABOR": "DOL",
    "GENERAL SERVICES ADMINISTRATION": "GSA",
    "SOCIAL SECURITY ADMINISTRATION": "SSA",
    "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION": "NASA",
    "ENVIRONMENTAL PROTECTION AGENCY": "EPA",
    "SMITHSONIAN INSTITUTION": "SI",
    "NATIONAL ARCHIVES AND RECORDS ADMINISTRATION": "NARA",
    "DISTRICT OF COLUMBIA COURTS": "DCC",
    "ADMINISTRATIVE OFFICE OF THE US COURTS": "AOUSC",
}
_MONO_STOPWORDS = {"OF", "THE", "AND", "FOR", "US", "U", "S",
                   "DEPT", "DEPARTMENT", "OFFICE", "ADMINISTRATIVE"}


def _agency_monogram(name: str) -> str:
    """DHS / DoD / DOJ-style abbreviation for the badge tile; initials of the
    significant words when the agency is not in the known map."""
    up = (name or "").upper()
    for key, mono in _AGENCY_MONOGRAMS.items():
        if key in up:
            return mono
    words = [w for w in re.split(r"[^A-Z0-9]+", up)
             if w and w not in _MONO_STOPWORDS]
    return "".join(w[0] for w in words[:4]) or "US"


def _agency_badge(agency: str, dollars_label: Optional[str], view: str) -> str:
    """Designed monogram tile; a seal image swaps in behind AGENCY_BADGE_STYLE.
    The dollar line renders ONLY when the data supports it — a badge without a
    citable figure renders without one, never with an invented number."""
    seal = ""
    if AGENCY_BADGE_STYLE == "seal":
        from tools.brand_marks import find_mark
        uri = _mark_data_uri(find_mark(_seal_dir(), _agency_monogram(agency).lower()))
        if uri:
            seal = f'<img class="vb-seal" alt="" src="{uri}">'
    if view == "sales":
        # per-agency dollars are client content; the badge proves the agency,
        # the lock marks the withheld figure
        dollars = '<span class="vb-bdollar vb-lockmark">locked</span>'
    else:
        dollars = (f'<span class="vb-bdollar">{_esc(dollars_label)}</span>'
                   if dollars_label else "")
    # seal cached -> the seal IS the badge visual (monogram stays as the
    # caption); nothing cached -> the designed monogram tile, exactly as before
    face = (f'{seal}<span class="vb-mono vb-mono-cap">{_esc(_agency_monogram(agency))}</span>'
            if seal else f'<span class="vb-mono">{_esc(_agency_monogram(agency))}</span>')
    return (f'<div class="vb-badge" data-entity="agency_badge" title="{_esc(agency)}">'
            f'{face}{dollars}</div>')


_VB_STYLE = """<style data-vb="1">
.vb-board{background:#16283f;color:#f6f4ef;padding:18px 22px;margin:0 0 20px;
  font-family:'IBM Plex Sans',system-ui,sans-serif}
.vb-row{display:flex;flex-wrap:wrap;gap:14px 26px;align-items:flex-start}
.vb-h{font-family:'IBM Plex Mono',monospace;font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;color:#9db2c9;margin:0 0 8px}
.vb-badges{display:flex;flex-wrap:wrap;gap:8px}
.vb-badge{position:relative;width:74px;min-height:58px;background:#0f1d30;
  border:1px solid #2a4059;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:3px;padding:7px 4px;overflow:hidden}
.vb-seal{position:relative;width:30px;height:30px;object-fit:contain;background:#fff;
  border-radius:3px;padding:2px}
.vb-mono{position:relative;font-family:'Playfair Display',Georgia,serif;font-weight:900;
  font-size:17px;color:#f6f4ef}
.vb-mono-cap{font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:8.5px;
  letter-spacing:.1em;color:#9db2c9}
.vb-cmark{width:14px;height:14px;object-fit:contain;vertical-align:-2px;margin-right:5px;
  background:#fff;border-radius:2px;padding:1px}
.vb-bdollar{position:relative;font-family:'IBM Plex Mono',monospace;font-size:10px;
  font-weight:600;color:#8fd3a8}
.vb-lockmark{color:#7d97b3;font-size:8.5px;letter-spacing:.12em;text-transform:uppercase}
.vb-tam{min-width:200px}
.vb-tam .n{font-family:'Playfair Display',Georgia,serif;font-size:34px;font-weight:900;
  line-height:1.05;color:#fff}
.vb-tam .l{font-size:11px;color:#9db2c9;margin-top:2px}
.vb-strip{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.vb-sol{background:#0f1d30;border:1px solid #2a4059;padding:8px 12px;font-size:11.5px;
  min-width:190px;max-width:300px}
.vb-sol .t{font-weight:700;color:#f6f4ef;margin-bottom:2px}
.vb-sol .m{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#9db2c9}
.vb-sol .vfy{font-family:'IBM Plex Mono',monospace;font-size:9px;color:#c9a86a;margin-top:4px}
.vb-grade{display:inline-block;min-width:20px;text-align:center;font-family:'IBM Plex Mono',monospace;
  font-weight:700;font-size:11px;padding:1px 5px;color:#fff;margin-right:6px}
.vb-comp{font-size:12px;color:#c4d3e2;margin-top:12px}
.vb-comp b{color:#fff}
.vb-ticker{margin-top:14px;border-top:1px solid #2a4059;padding-top:8px;overflow:hidden}
.vb-tape{display:inline-flex;gap:36px;white-space:nowrap;will-change:transform;
  animation:vb-scroll var(--vb-secs,90s) linear infinite;padding-right:36px;
  font-size:11.5px;color:#c4d3e2}
.vb-ticker:hover .vb-tape{animation-play-state:paused}
.vb-tape .k{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.1em;
  color:#c9a86a;margin-right:7px}
@keyframes vb-scroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}
@media (prefers-reduced-motion: reduce){
  .vb-tape{animation:none;white-space:normal;display:block}
  .vb-tape .vb-dup{display:none}
}
@media print{.vb-tape{animation:none;white-space:normal;display:block}
  .vb-tape .vb-dup{display:none}}
</style>"""


def _pop_figure(pop: str, entries: list[tuple[str, str]], label: str,
                empty_note: str) -> str:
    """One scoreboard figure: its count, its named examples, and its
    'and X more' all come from the SAME population (L13). The count IS the
    population length, so cross-population arithmetic cannot happen here."""
    count = len(entries)
    shown = entries[:3]
    more = count - len(shown)
    assert more >= 0, f"population {pop}: examples exceed the population"
    named = ", ".join(
        f'<span data-pop-name="1">{_company_mark(mark, "vb-cmark")}'
        f'{_esc(display)}</span>' for display, mark in shown)
    detail = (named + (f" and {more} more" if more > 0 else "")
              if shown else empty_note)
    return (f'<div class="vb-comp" data-pop="{pop}" data-pop-count="{count}">'
            f'<b>{count} {label}.</b> {detail}</div>')


def _internal_competitor_block(working: AssessmentDocument) -> str:
    """INTERNAL scoreboard competitor figures (L13, 2026-07-10): one label
    per population, each company under exactly one label with precedence
    incumbent-in-buyer-accounts > product competitor > lane awardee."""
    comp_all = working.competitive.competitors
    if not working.competitive.product_tagged:
        # untagged sweep (no capability profile): one population, one line,
        # and the count client copy may state (counts()) IS that population
        n = len(comp_all)
        tops = comp_all[:3]
        more = n - len(tops)
        assert more >= 0, "untagged population: examples exceed the population"
        named = ", ".join(
            f'<span data-pop-name="1">{_company_mark(x.name, "vb-cmark")}'
            f'{_esc(x.name)}</span>' for x in tops)
        detail = (named + (f" and {more} more" if more > 0 else "")
                  if tops else "none surfaced in this sweep")
        return (f'<div class="vb-comp" data-pop="all" data-pop-count="{n}">'
                f'<b><span data-counts-verified="1">{n}</span> '
                f'competitor{"s" if n != 1 else ""} and incumbents identified.'
                f'</b> These are your competitors and why. {detail}</div>')

    incumbents = working.buyer_incumbents
    inc_keys = {normalize_company(x.company) for x in incumbents}
    product = [x for x in comp_all
               if x.product and normalize_company(x.name) not in inc_keys]
    lane = [x for x in comp_all
            if not x.product and normalize_company(x.name) not in inc_keys]

    n_p, n_l, n_i = len(product), len(lane), len(incumbents)
    out = [
        _pop_figure("product", [(x.name, x.name) for x in product],
                    f'product competitor{"s" if n_p != 1 else ""} identified',
                    "none surfaced in this sweep"),
        _pop_figure("lane", [(x.name, x.name) for x in lane],
                    f'repeat lane awardee{"s" if n_l != 1 else ""} in the '
                    f'client\'s NAICS lanes (not product competitors)',
                    "none in this sweep"),
    ]
    if incumbents:
        entries = [(f'{x.company} ({", ".join(x.agencies[:2])}'
                    f'{" +" + str(len(x.agencies) - 2) if len(x.agencies) > 2 else ""})'
                    if x.agencies else x.company, x.company)
                   for x in incumbents]
        out.append(_pop_figure(
            "incumbent", entries,
            f'product incumbent{"s" if n_i != 1 else ""} in buyer accounts',
            "none surfaced in this sweep"))
    return "".join(out)


def render_scoreboard(doc: AssessmentDocument, view: str) -> str:
    """The scoreboard banner for the client dashboard, BOTH views. Computed
    from AssessmentDocument (its gated copy in sales mode) — never assembled
    ad hoc; every count comes from counts(). Empty string when no assessment
    has run: an empty scoreboard never renders."""
    if view not in ("internal", "sales"):
        raise ValueError("scoreboard renders internal or sales")
    working = gate_for_sales(doc) if view == "sales" else doc
    c = working.counts()
    if not (working.board.pursuits or working.market.agency_map
            or working.watchlist.entries):
        return ""

    # ONE basis for the whole block (grounded 2026-07-06): the badges slice the
    # SAME top-award pool the headline sums, both averaged per year — so the
    # agency figures visibly add up to the headline instead of exceeding it,
    # and nothing here claims to be client-addressable TAM.
    m = working.market
    # badge + headline share ONE basis. Preference order: the keyword-matched
    # (addressable) slice when the pull carried it, else the top-award pool,
    # else agency names with no figure (older sweeps).
    if m.addressable_agency_annual:
        pool, pool_dollars = m.addressable_agency_annual, True
    elif m.award_agency_annual:
        pool, pool_dollars = m.award_agency_annual, True
    else:
        pool, pool_dollars = m.agency_map, False
    badges = "".join(
        _agency_badge(a.agency,
                      f"{a.dollars_label}/yr" if (pool_dollars and a.dollars_label) else None,
                      view)
        for a in pool[:6])
    agencies_line = (f'<span data-counts-verified="1">{c["agencies"]} relevant '
                     f'agenc{"ies" if c["agencies"] != 1 else "y"} identified</span>')
    if m.addressable_annual_label:
        # THE NUMBER SHOWS ITS MATH (2026-07-10): a headline dollar without
        # its derivation means nothing and earns no trust. The banner states
        # the formula, the keyword basis, the lanes, the source, and the cap.
        addr_comp = next((x for x in m.components if x.kind == "addressable"), None)
        kws = list(m.addressable_keywords or [])
        lanes = sorted({n for a in m.agency_map for n in (a.naics or [])})
        derivation = (
            (f"{addr_comp.dollars_label} obligated on keyword-matched awards "
             f"over 3 years, divided by 3 = {m.addressable_annual_label}/yr"
             if addr_comp and addr_comp.dollars_label else
             f"keyword-matched obligations averaged over the 3-year window = "
             f"{m.addressable_annual_label}/yr")
            + (f" · matched on {len(kws)} capability keyword"
               f"{'s' if len(kws) != 1 else ''}"
               + (f" ({', '.join(kws[:3])}"
                  + (f" +{len(kws) - 3} more" if len(kws) > 3 else "") + ")"
                  if kws else "") if kws else "")
            + (f" · within NAICS {', '.join(lanes[:5])}" if lanes else "")
            + " · USAspending spending_by_category, summed at the top awarding "
              "agencies: a conservative floor, not a ceiling")
        tam = (f'<div class="vb-tam"><div class="n">{_esc(m.addressable_annual_label)}/yr</div>'
               f'<div class="l">{agencies_line} · keyword-matched federal spend '
               f'per year</div>'
               f'<div class="l" style="opacity:.72">How this number is built: '
               f'{_esc(derivation)}</div>'
               + (f'<div class="l" style="opacity:.6">Context only: total lane '
                  f'obligations run {_esc(m.tam_annual_label)}/yr; the lane figure '
                  f'is where contracts are filed, never the addressable claim</div>'
                  if m.tam_annual_label else "")
               + '</div>')
    else:
        tam_head = (f"{m.tam_annual_label}/yr" if m.tam_annual_label
                    else (m.tam_label or "·"))
        tam = (f'<div class="vb-tam"><div class="n">{_esc(tam_head)}</div>'
               f'<div class="l">{agencies_line} · avg federal '
               f'obligations per year across the client&#39;s NAICS lanes</div>'
               f'<div class="l" style="opacity:.72">How this number is built: '
               f'sum of the top awards by obligation in each NAICS lane over 3 '
               f'years, divided by 3 · USAspending · market context, not a '
               f'client forecast; run the keyword-scoped market pull for the '
               f'addressable figure</div></div>')

    sols = []
    for p in working.board.pursuits[:3]:
        chip = (f'<span class="vb-grade {_GRADE_CLASS.get(p.grade.letter, "vg-c")}">'
                f'{p.grade.letter}</span>')
        if view == "sales":
            v = (p.locked.verifiers if p.locked else {})
            sols.append(
                f'<div class="vb-sol" data-locked="1"><div class="t">{chip}'
                f'{_esc(p.title if p.rank == 1 else "Locked · client content")}</div>'
                f'<div class="m">{_esc(v.get("agency", p.agency or "·"))}</div>'
                f'<div class="m">due {_esc(v.get("response deadline") or "·")} · '
                f'{_esc(v.get("notice id", p.source_id))}</div>'
                f'<div class="vfy">✓ {_esc(v.get("verified", ""))}</div></div>')
        else:
            sols.append(
                f'<div class="vb-sol"><div class="t">{chip}{_escn(p.title)}</div>'
                f'<div class="m">{_esc(p.agency or "·")}</div>'
                f'<div class="m">{_esc(p.solicitation or p.source_id)} · due '
                f'{_esc(p.response_deadline or "·")}</div></div>')
    strip = (f'<div class="vb-h" style="margin-top:12px">Top priority solicitations '
             f'(<span data-counts-verified="1">{c["pursuits"]}</span> graded)</div>'
             f'<div class="vb-strip">{"".join(sols)}</div>') if sols else ""

    if view == "sales":
        lanes = sorted({n for comp in working.competitive.competitors for n in comp.naics})
        basis = next((comp.locked.verifiers.get("basis", "")
                      for comp in working.competitive.competitors if comp.locked), "")
        comp_detail = (f'{_esc(basis)}{" · NAICS " + _esc(", ".join(lanes[:6])) if lanes else ""}'
                       if basis or lanes else "named in the full assessment")
        comp = (f'<div class="vb-comp"><b><span data-counts-verified="1">{c["competitors"]}'
                f'</span> competitor{"s" if c["competitors"] != 1 else ""} and incumbents '
                f'identified.</b> These are your competitors and why. {comp_detail}</div>')
    else:
        comp = _internal_competitor_block(working)
    if c["teaming_plays"]:
        dirs = sorted({_DIRECTION_LABELS.get(d, d)
                       for pl in working.partnering.plays
                       if pl.kind == "pursue" for d in pl.directions})
        tail = (" Directions and candidates are client content."
                if view == "sales" else f' {_esc(" · ".join(dirs))}.')
        comp += (f'<div class="vb-comp"><b><span data-counts-verified="1">'
                 f'{c["teaming_plays"]}</span> teaming play'
                 f'{"s" if c["teaming_plays"] != 1 else ""}</b> identified '
                 f'across the pursuit board.{tail}</div>')
    if c["teaming_watch"]:
        tail = (" Primes and evidence are client content."
                if view == "sales" else " Adjacent notices where teaming, not "
                "priming, is the path; primes named from award evidence.")
        comp += (f'<div class="vb-comp"><b><span data-counts-verified="1">'
                 f'{c["teaming_watch"]}</span> teaming-watch '
                 f'opportunit{"y" if c["teaming_watch"] == 1 else "ies"}</b>'
                 f' on the monitor list.{tail}</div>')

    if view == "sales":
        items = [f'<span><span class="k">NEWS</span>{_esc(n.get("title") or "")}</span>'
                 for n in working.market.news]
        n_locked = c["watchlist_entries"]
        if n_locked:
            items.append(f'<span><span class="k">WATCHLIST</span>'
                         f'<span data-counts-verified="1">plus {n_locked} more '
                         f'item{"s" if n_locked != 1 else ""} in the weekly watchlist'
                         f'</span></span>')
    else:
        shown = working.watchlist.entries[:20]
        items = [f'<span><span class="k">{_esc((e.kind or "item").upper())}</span>'
                 f'{_escn(e.title or e.id)} · {_escn(e.detail)}</span>' for e in shown]
        # Ticker famine (2026-07-12): the strict cutover holds monitor-grade
        # rows to human-reviewed evidence and a scoped sweep can carry zero
        # news, which emptied the operator's tape entirely. The internal feed
        # falls back to what IS verifiably moving: sweep news when present,
        # product incumbents in buyer accounts, and the live-census screening
        # state. Internal view only; sales and client tapes are unchanged.
        if not items:
            items = [f'<span><span class="k">NEWS</span>'
                     f'{_esc(n.get("title") or "")}</span>'
                     for n in working.market.news[:10]]
            for b in working.buyer_incumbents[:8]:
                agencies = ", ".join(list(b.agencies)[:2])
                items.append(f'<span><span class="k">INCUMBENT</span>'
                             f'{_escn(b.company)} · {_escn(agencies)}</span>')
            census = working.sam_census or {}
            matched = census.get("matched") or census.get("retrieved")
            if matched:
                items.append(
                    f'<span><span class="k">CENSUS</span>{matched} live '
                    f'records screened this cycle · requirement review is '
                    f'the gate to pursuit grade</span>')
        hidden = c["watchlist_entries"] - len(shown)
        if hidden > 0:
            items.append(f'<span><span class="k">MORE</span>{hidden} further items '
                         f'on the full watchlist</span>')
    tape = "".join(items)
    # One copy-width scrolls past over `secs`, so px/s = copy_width / secs. Item
    # widths vary wildly (a news headline vs a long watchlist sentence), so
    # per-ITEM scaling gave inconsistent speeds; scale by VISIBLE CHARACTERS,
    # which tracks pixel width (~5.8px/char incl. gaps). 0.19 s/char lands a
    # readable ~30 px/s for any client's data (was ~140 px/s — far too fast).
    visible_len = len(re.sub(r"<[^>]+>", "", tape))
    # floor only guards against a twitchy ultra-short loop; the char-scaling
    # already holds a short tape (e.g. the sales feed) at the same ~30 px/s
    # rather than freezing it.
    secs = max(15, round(visible_len * 0.19))
    ticker = (f'<div class="vb-ticker" aria-label="Relevant news activity">'
              f'<div class="vb-tape" style="--vb-secs:{secs}s">{tape}'
              f'<span class="vb-dup" aria-hidden="true">{tape}</span></div></div>') if items else ""

    return (f'{_VB_STYLE}<div class="vb-board" data-view="{view}">'
            f'<div class="vb-h">Assessment scoreboard · {_esc(working.client_name)}</div>'
            f'<div class="vb-row"><div><div class="vb-h">Relevant agencies</div>'
            f'<div class="vb-badges">{badges}</div></div>{tam}</div>'
            f'{strip}{comp}{ticker}</div>')


def render_assessment(doc: AssessmentDocument, view: str = "client",
                      focus_label: Optional[str] = None) -> str:
    """One document, one renderer, three views. focus_label marks an
    agency-focused pass on the cover (the underlying document is already
    scoped by the caller; this is the visible framing)."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    working = gate_for_sales(doc) if view == "sales" else doc
    counts = working.counts()

    css = house_css()
    gtm = (f'<div class="logo-plate"><img id="gtmLogoImg" '
           f'src="data:{_gtm_logo_mime()};base64,{_gtm_logo_b64()}" '
           f'alt="GTM — Go To Market"></div>')
    client_lockup = client_cover_lockup(working.client_name)
    eyebrow = {"client": "Federal Opportunity Assessment · Confidential",
               "sales": "Federal Opportunity Assessment · Preview",
               "internal": "Federal Opportunity Assessment · INTERNAL REVIEW · DO NOT SEND"}[view]
    if focus_label:
        eyebrow = eyebrow.replace("Federal Opportunity Assessment",
                                  "Federal Opportunity Assessment · Agency Focus")

    content = working.content
    thesis = ""
    if content:
        from agents.reports.capture_brief import normalize_house_style
        thesis_ps = "".join(
            f"<p>{_esc(normalize_house_style(resolve_count_tokens(t, counts)))}</p>"
            for t in content.thesis)
        thesis = (f'<div class="thesis-block"><span class="thesis-label">Thesis</span>'
                  f'<div class="thesis-text">{thesis_ps}</div></div>')

    sections = [_sec_market(working, view), _sec_pursuits(working, view),
                _sec_teaming_watch(working, view),
                _sec_competitive(working, view), _sec_watchlist(working, view)]
    if view == "sales":
        sections.append(_SALES_CLOSE)

    # in-page navigation on every view; Print / Save as PDF on the two views
    # that leave the building. The print stylesheet hides both.
    nav_targets = [("#market", "Market"), ("#pursuits", "Pursuits"),
                   ("#competitive", "Competitive"), ("#watchlist", "Watchlist")]
    if view == "internal":
        nav_targets.append(("#preship", "Pre-Ship"))
    # internal carries NO print control BY DESIGN (2026-07-06): it is a
    # DO-NOT-SEND artifact and must not invite a send-around copy.
    print_ctl = ('<button class="v-print" type="button" onclick="window.print()">'
                 'Print / Save as PDF</button>') if view in ("client", "sales") else ""
    nav = ('<nav class="v-nav" aria-label="Sections">'
           + "".join(f'<a href="{h}">{t}</a>' for h, t in nav_targets)
           + print_ctl + "</nav>")

    footer_note = _footer_note(view, working.as_of)

    page = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(working.client_name)} · Federal Opportunity Assessment</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="page view-{view}">
<header class="cover">
  {_cover_art()}
  <div class="cover-top-row">
    <div class="cover-logo">{client_lockup}</div>
    <div class="pscg-logo">{gtm}</div>
  </div>
  <span class="cover-eyebrow">{_esc(eyebrow)}</span>
  <h1 class="cover-title">{_esc(working.client_name)}</h1>
  <p class="cover-subtitle">{_esc(f"Agency focus: {focus_label} · pursuits, market, and watchlist in one lane"
                                   if focus_label else
                                   "Federal market, pursuits, competitors, and watchlist · one assessment")}</p>
  <div class="cover-meta">
    <div class="cover-meta-item"><span class="cover-meta-label">Prepared by</span><span class="cover-meta-value">Tyler Johnson · GTM Group</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Date</span><span class="cover-meta-value">{working.as_of.strftime("%B %-d, %Y")}</span></div>
    <div class="cover-meta-item"><span class="cover-meta-label">Classification</span><span class="cover-meta-value">Proprietary · Prepared for {_esc(working.client_name)}</span></div>
  </div>
  {thesis}
</header>
{nav}
{"".join(sections)}
<footer class="footer"><p>{_esc(footer_note)}</p></footer>
</div></body></html>"""

    page = resolve_count_tokens(page, counts)
    page = grade_contact_channels(page)
    if view == "internal":
        client_page = render_assessment(doc, "client")
        page = page.replace("<footer", _sec_internal_qa(doc, client_page) + "<footer")
    return page
