"""AssessmentDocument; the ONE composed model all three views render.

A view is a rendering decision, not a research run: this module deterministically
joins every artifact a client has (notices ⋈ triage ⋈ dossiers ⋈ qualify ⋈ market
evidence ⋈ sweep snapshots, keyed on source_id) into a single document. The LLM
composes prose ONCE (the existing CaptureBriefContent); the three renderers
consume this object and never re-derive data.

Count reconciliation, Layer 1 (hard requirement, 2026-07-06): every entity count
that appears in client-visible copy is a template variable computed HERE, from
the document, at render time. `counts()` is the single source of truth;
`int_to_word()` spells numbers; `agency_group_phrase()` assembles compound
claims ("Two Army National Guard postings and a U.S. Courts solicitation") from
grouped data. Composed prose refers to counts via {{COUNT:name}} /
{{COUNT_WORD:name}} tokens that `resolve_count_tokens()` substitutes at render
time; the LLM never freewrites a number that has a ground-truth source.

Dollar honesty: a TAM component with no citable dollar renders without one, and
the math table shows exactly which components sum to the headline.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from agents.assess.pursuit_grade import WEIGHTS, PursuitGrade, grade
from agents.reports.capture_brief import CaptureBriefContent

_ROOT = Path(__file__).resolve().parents[2]
_AUTO_LIVE_REPORT = object()
_AUTO_CAP_PROFILE = object()

# ── model ────────────────────────────────────────────────────────────────────


class Locked(BaseModel):
    """Typed placeholder for gated content; proof without the keys.

    verifiers are REAL fields from the underlying record, deliberately kept as
    sales-tier content: locked content must prove it exists (agency, notice
    type, deadline, grade, masked notice ID, verification line) without
    becoming actionable. The gated model carries these; templates never reach
    around them."""
    shape_hint: str
    upsell_line: str
    verifiers: dict[str, str] = Field(default_factory=dict)


class TamComponent(BaseModel):
    label: str
    kind: str = Field(description="'market' (sums into TAM) | 'vehicle' (access path, listed not summed) | 'addressable' (keyword-matched subset, listed not summed) | 'agency_outlay' (context)")
    dollars: Optional[float] = None
    dollars_label: Optional[str] = Field(default=None, description="display form; None renders without a figure")
    basis: str = Field(description="what this number IS (window, method); the math shown")
    source: str = Field(description="citation URL")


class AgencyDollars(BaseModel):
    agency: str
    dollars: Optional[float] = None
    dollars_label: Optional[str] = None
    basis: str
    source: str
    naics: list[str] = Field(default_factory=list)


class MarketOverview(BaseModel):
    tam_dollars: Optional[float] = Field(default=None, description="sum of kind='market' components only; None when nothing citable")
    tam_label: Optional[str] = None
    # grounded framing (2026-07-06): the lane aggregates are WHOLE-NAICS spend
    # over a 3-year window; market context, never client-addressable TAM.
    # The annual average is the honest headline unit; the award-pool agency
    # split comes from the SAME top-award lists the components sum, so any
    # surface showing both stays internally coherent (slices sum to the total).
    tam_annual_dollars: Optional[float] = Field(default=None, description="tam_dollars averaged per year over the query window")
    tam_annual_label: Optional[str] = None
    # the ADDRESSABLE slice (keyword-scoped USAspending pulls): obligations on
    # awards matching the client's capability/technology keywords WITHIN the
    # NAICS lanes; the closest citable figure to client-addressable market.
    addressable_annual_dollars: Optional[float] = Field(default=None, description="keyword-matched lane obligations, averaged per year")
    addressable_annual_label: Optional[str] = None
    addressable_agency_annual: list[AgencyDollars] = Field(
        default_factory=list,
        description="per-agency AVG-ANNUAL slices of the keyword-matched "
                    "spend; same basis as the addressable headline; descending")
    addressable_keywords: list[str] = Field(default_factory=list)
    award_agency_annual: list[AgencyDollars] = Field(
        default_factory=list,
        description="per-agency AVG-ANNUAL dollars sliced from the same "
                    "top-award pool that sums to tam_dollars; descending")
    components: list[TamComponent] = Field(default_factory=list)
    agency_map: list[AgencyDollars] = Field(default_factory=list, description="descending dollars; 'where you need to be'")
    news: list[dict] = Field(default_factory=list, description="current-cycle items {title,url,recency,why}")
    locked: Optional[Locked] = None


class GradedPursuit(BaseModel):
    rank: int
    source_id: str
    title: str
    agency: Optional[str] = None
    naics: Optional[str] = None
    url: Optional[str] = None
    response_deadline: Optional[str] = None
    notice_type: Optional[str] = Field(default=None, description="SAM notice type, e.g. 'Combined Synopsis/Solicitation'")
    solicitation: Optional[str] = Field(default=None, description="solicitation number when the notice carries one")
    set_aside: Optional[str] = Field(default=None, description="SAM set-aside label as published, e.g. 'Total Small Business Set-Aside (FAR 19.5)'")
    set_aside_code: Optional[str] = Field(default=None, description="SAM typeOfSetAside code when the payload carries one")
    verdict: Optional[str] = None
    grade: PursuitGrade
    dossier: Optional[dict] = Field(default=None, description="full dossier record when built")
    fit_trace: Optional[dict] = Field(
        default=None,
        description="show-the-work provenance: matched terms, NAICS boundary, mission component, screen inference")
    amendments: list[dict] = Field(
        default_factory=list,
        description="all postings of this solicitation, oldest first, when >1 "
                    "notice shared the solicitation number: {source_id, "
                    "posted_date, response_deadline, change?}. change states "
                    "only what the data states (deadline moves); never inferred")
    locked: Optional[Locked] = None


class PursuitBoard(BaseModel):
    pursuits: list[GradedPursuit] = Field(default_factory=list, description="grade-score descending")
    rubric_weights: dict = Field(default_factory=lambda: dict(WEIGHTS))


class Competitor(BaseModel):
    name: str
    product: bool = Field(
        default=False,
        description="a PRODUCT competitor (matches the capability profile's "
                    "named list), vs a lane cohabitant")
    lineage_note: Optional[str] = Field(
        default=None,
        description="rendered once when raw names merged into this door (L14); "
                    "internal view and drill-down only")
    merged_names: list[str] = Field(
        default_factory=list,
        description="distinct as-of-contract names merged into this row")
    dollars: Optional[float] = None
    dollars_label: Optional[str] = None
    basis: str
    source: str
    agencies: list[str] = Field(default_factory=list)
    naics: list[str] = Field(default_factory=list)
    pursuit_ids: list[str] = Field(default_factory=list)
    awards: list[dict] = Field(
        default_factory=list,
        description="the award rows behind the dollars, largest first, capped: "
                    "{award_id, amount, awarding_agency, start_date, url, naics}"
                    "; every drill-down figure stays citable")
    locked: Optional[Locked] = None


class CompetitiveLandscape(BaseModel):
    competitors: list[Competitor] = Field(default_factory=list)
    product_tagged: bool = Field(
        default=False,
        description="a capability profile classified this landscape: client "
                    "views render product competitors; lane cohabitants stay "
                    "internal")
    coverage_note: str = ""
    locked: Optional[Locked] = None


class WatchlistEntry(BaseModel):
    id: str
    source: str
    kind: str = Field(description="'new' | 'changed' | 'standing'")
    title: Optional[str] = None
    url: Optional[str] = None
    detail: str = ""
    locked: Optional[Locked] = None


class Watchlist(BaseModel):
    last_refreshed: Optional[str] = None
    delta_note: str = ""
    entries: list[WatchlistEntry] = Field(default_factory=list)
    locked: Optional[Locked] = None


class PartneringPlay(BaseModel):
    """A teaming play: the blockers that fired (or degraded), the direction,
    and evidence-graded partner candidates.

    kind='pursue' ; a pursue-grade pursuit the client wants but a blocker
                     stops them priming alone (no blocker, no play).
    kind='monitor'; a monitor-grade adjacency the client is tracking, not
                     priming; the teaming path (sub to a prime) is always live,
                     so the play exists whenever there are evidence-backed
                     candidates. title/agency carry the notice for rendering
                     (monitor plays have no pursuit dossier chapter)."""
    source_id: str
    rank: int
    kind: str = "pursue"
    title: Optional[str] = None
    agency: Optional[str] = None
    blockers: list = Field(default_factory=list, description="partnering Blocker models")
    directions: list[str] = Field(default_factory=list)
    candidates: list = Field(default_factory=list, description="PartnerCandidate models, ranked")
    review_flags: list[str] = Field(default_factory=list)
    locked: Optional[Locked] = None


class PartneringBoard(BaseModel):
    plays: list[PartneringPlay] = Field(default_factory=list)
    profile_unset: list[str] = Field(default_factory=list)
    profile_warnings: list[str] = Field(default_factory=list)


class BuyerIncumbent(BaseModel):
    """A profile-named product found holding seats in buyer-map accounts.
    INTERNAL-ONLY display data: never client copy, never counted by counts(),
    physically removed by gate_for_sales."""
    company: str
    agencies: list[str] = Field(default_factory=list,
                                description="buyer components holding the seat")


class AssessmentDocument(BaseModel):
    client_name: str
    as_of: date
    generated_at: Optional[str] = None
    market: MarketOverview
    board: PursuitBoard
    competitive: CompetitiveLandscape
    partnering: PartneringBoard = Field(default_factory=PartneringBoard)
    buyer_incumbents: list[BuyerIncumbent] = Field(
        default_factory=list,
        description="profile-named products in buyer-map accounts; internal only")
    watchlist: Watchlist
    content: Optional[CaptureBriefContent] = Field(
        default=None, description="the LLM-composed prose layer (one compose, reused by all views)")
    verdict_totals: dict = Field(default_factory=dict, description="triage verdict -> count, from the sweep")
    sam_census: dict = Field(
        default_factory=dict,
        description="L17 SAM screen ground truth: matched/active_screened/"
                    "complete; screened-N claims reconcile against this")
    research_source_coverage: dict = Field(
        default_factory=dict,
        description="public-safe projection of this sweep's results._attempts; "
                    "contains lane identity and coarse status, never raw "
                    "provider errors")
    gaps: list[str] = Field(default_factory=list, description="explicit missing-layer notes; never silent")

    # ---- Layer 1: ground-truth counts -------------------------------------
    def counts(self) -> dict[str, int]:
        """The single source of truth for every count client copy may state.

        pursue_notices is SOLICITATION-level (2026-07-06 amendment dedupe):
        an amendment pair is one solicitation, one card, one count everywhere
        a client reads. verdict_totals keeps the raw notice-level tally for
        internal provenance."""
        return {
            "pursuits": len(self.board.pursuits),
            "dossiers": sum(1 for p in self.board.pursuits if p.dossier),
            "pursue_notices": len(self.board.pursuits),
            "monitor_notices": self.verdict_totals.get("monitor", 0),
            "competitors": (sum(1 for x in self.competitive.competitors
                                if x.product)
                            if self.competitive.product_tagged
                            else len(self.competitive.competitors)),
            "watchlist_entries": len(self.watchlist.entries),
            "agencies": len(self.market.addressable_agency_annual
                            or self.market.award_agency_annual),
            "headline_agencies": min(3, len(self.market.addressable_agency_annual
                                            or self.market.award_agency_annual)),
            "news_items": len(self.market.news),
            "tam_components": sum(1 for c in self.market.components if c.kind == "market"),
            "teaming_plays": sum(1 for p in self.partnering.plays if p.kind == "pursue"),
            "teaming_watch": sum(1 for p in self.partnering.plays if p.kind == "monitor"),
        }


# ── Layer-1 helpers ──────────────────────────────────────────────────────────

_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
          "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
          "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]


def int_to_word(n: int) -> str:
    """Spelled-out form for small counts (house style); digits beyond twenty."""
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def resolve_count_tokens(text: str, counts: dict[str, int]) -> str:
    """Substitute {{COUNT:name}} and {{COUNT_WORD:name}} with ground truth.
    Unknown tokens are left in place; lint_counts flags them, never silent."""
    if not text or "{{COUNT" not in text:
        return text
    out = text
    for name, value in counts.items():
        out = out.replace("{{COUNT:" + name + "}}", str(value))
        out = out.replace("{{COUNT_WORD:" + name + "}}", int_to_word(value))
    return out


# ── company-name matching + entity crosswalk (L13/L14) ──────────────────────
# ONE matcher and ONE resolver for every path that touches a company name
# (product tagger, buyer-map incumbent resolver, competitive merge, lint).
# They live in tools/entity_lineage; re-exported here for the render layer.

from tools.entity_lineage import (  # noqa: E402,F401 — shared, re-exported
    canonical_name as entity_canonical_name,
    company_matches,
    door_key,
    entity_note,
    normalize_company,
    resolve_entity,
)


def agency_group_phrase(pursuits: list[GradedPursuit]) -> str:
    """Compound count claims assembled from grouped data, never freewritten:
    'Two Army National Guard postings and a U.S. Courts solicitation'."""
    groups: dict[str, int] = {}
    for p in pursuits:
        key = p.agency or "unattributed"
        groups[key] = groups.get(key, 0) + 1
    parts = []
    for agency, n in sorted(groups.items(), key=lambda kv: -kv[1]):
        noun = "posting" if n > 1 else "solicitation"
        parts.append(f"{int_to_word(n).capitalize() if not parts else int_to_word(n)} "
                     f"{agency} {noun}{'s' if n > 1 else ''}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# ── deterministic joins ──────────────────────────────────────────────────────

def _fmt_money(x: Optional[float]) -> Optional[str]:
    if not isinstance(x, (int, float)) or x <= 0:
        return None
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    return f"${x:,.0f}"


def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _slug(name: str) -> str:
    from tools.slug import client_slug
    return client_slug(name)


def _sam_notice_map(results: dict) -> dict[str, dict]:
    """Canonical SAM rows plus source-less legacy rows, keyed by source id.

    Some older sweeps mixed explicit web records into the ``sam.gov`` array.
    Container location is not provenance: an explicit non-SAM source never
    becomes a pursuit, SAM count, watchlist row, or monitor partnering play.
    """
    notices: dict[str, dict] = {}
    for row in results.get("sam.gov") or []:
        if not isinstance(row, dict) or not row.get("source_id"):
            continue
        source = str(row.get("source") or "").strip().lower()
        if source not in ("", "sam.gov"):
            continue
        notices[row["source_id"]] = row
    return notices


_LANE_WINDOW_YEARS = 3  # the adapter's years_back default; every lane basis says "3-year window"


def _build_market(results: dict, gaps: list[str]) -> MarketOverview:
    bundles = results.get("usaspending.gov")
    bundles = bundles if isinstance(bundles, list) else []
    components: list[TamComponent] = []
    agency_sums: dict[str, dict] = {}
    pool_agency: dict[str, float] = {}  # per-agency slice of the top-award pool
    addr_total = 0.0
    addr_agency: dict[str, float] = {}
    addr_keywords: list[str] = []
    addr_seen = False

    for b in bundles:
        if not isinstance(b, dict) or not b.get("naics_code"):
            continue
        naics = b["naics_code"]
        # keyword-matched (addressable) slice, when the pull carried it
        addr = b.get("addressable")
        if isinstance(addr, dict) and not addr.get("error"):
            addr_seen = True
            if isinstance(addr.get("total_obligated"), (int, float)):
                addr_total += addr["total_obligated"]
            for row in addr.get("agency_breakdown") or []:
                if isinstance(row, dict) and row.get("agency") \
                        and isinstance(row.get("total_obligated"), (int, float)):
                    addr_agency[row["agency"]] = (
                        addr_agency.get(row["agency"], 0.0) + row["total_obligated"])
            for t in addr.get("keywords_used") or []:
                if t not in addr_keywords:
                    addr_keywords.append(t)
        summary = b.get("summary") or {}
        total = summary.get("total_obligated")
        if isinstance(total, (int, float)) and total > 0:
            components.append(TamComponent(
                label=f"NAICS {naics} contract obligations",
                kind="market", dollars=float(total), dollars_label=_fmt_money(total),
                basis=f"sum of the {summary.get('award_count', '?')} largest awards, "
                      f"3-year window (USAspending spending_by_award)",
                source="https://api.usaspending.gov/api/v2/search/spending_by_award/"))
        # the same top-award lists the component totals sum, sliced by agency ;
        # so per-agency figures shown NEXT TO the lane total always add up to it
        for a in b.get("awards") or []:
            if isinstance(a, dict) and a.get("awarding_agency") \
                    and isinstance(a.get("amount"), (int, float)):
                pool_agency[a["awarding_agency"]] = (
                    pool_agency.get(a["awarding_agency"], 0.0) + a["amount"])
        breakdown = b.get("agency_breakdown")
        for row in breakdown if isinstance(breakdown, list) else []:
            if not isinstance(row, dict) or not row.get("agency") or row.get("error"):
                continue
            rec = agency_sums.setdefault(row["agency"], {"dollars": 0.0, "naics": []})
            if isinstance(row.get("total_obligated"), (int, float)):
                rec["dollars"] += row["total_obligated"]
            rec["naics"].append(naics)

    if bundles and not agency_sums:
        gaps.append("per-agency dollars unavailable: this sweep predates the "
                    "agency_breakdown aggregate; re-run the market pull to populate the agency map")

    agency_map = [
        AgencyDollars(agency=a, dollars=rec["dollars"] or None,
                      dollars_label=_fmt_money(rec["dollars"]),
                      basis="3-year contract obligations across the client's NAICS lanes "
                            "(USAspending spending_by_category: awarding agency)",
                      source="https://api.usaspending.gov/api/v2/search/spending_by_category/awarding_agency/",
                      naics=sorted(set(rec["naics"])))
        for a, rec in sorted(agency_sums.items(), key=lambda kv: -(kv[1]["dollars"] or 0))
    ]

    # verified vehicles with numeric ceilings: access paths, listed NOT summed
    vehicles = _load_json(_ROOT / "data" / "reference" / "vehicles.json") or {}
    for v in vehicles.get("vehicles", []):
        if v.get("verified") and v.get("ceiling_usd"):
            components.append(TamComponent(
                label=f"{v['name']} ({v.get('agency', '')})", kind="vehicle",
                dollars=float(v["ceiling_usd"]), dollars_label=v.get("ceiling"),
                basis=f"vehicle ceiling{' ' + v['ceiling_period'] if v.get('ceiling_period') else ''}"
                      ": access path, not additive market dollars",
                source="data/reference/vehicles.json (verified entry)"))

    if bundles and not addr_seen:
        gaps.append("keyword-matched (addressable) market slice missing: this "
                    "sweep predates the keyword-scoped USAspending pull; run "
                    "run_market_refresh.py to populate it")
    if addr_seen and addr_total:
        # math-table row: listed for context, NEVER summed into the lane TAM
        components.append(TamComponent(
            label="Keyword-matched slice (across lanes)", kind="addressable",
            dollars=addr_total, dollars_label=_fmt_money(addr_total),
            basis="obligations on awards matching the client's capability "
                  "keywords within the NAICS lanes, 3-year window; aggregate "
                  "capped at the top awarding agencies (conservative); the "
                  "addressable subset of the lane totals",
            source="https://api.usaspending.gov/api/v2/search/spending_by_category/awarding_agency/"))

    market_total = sum(c.dollars for c in components if c.kind == "market" and c.dollars)
    annual = market_total / _LANE_WINDOW_YEARS if market_total else None
    addr_annual = addr_total / _LANE_WINDOW_YEARS if addr_total else None
    addressable_agency_annual = [
        AgencyDollars(
            agency=a, dollars=d / _LANE_WINDOW_YEARS,
            dollars_label=_fmt_money(d / _LANE_WINDOW_YEARS),
            basis="avg annual keyword-matched obligations in the client's "
                  "NAICS lanes, 3-year window (USAspending spending_by_category"
                  " + capability keywords)",
            source="https://api.usaspending.gov/api/v2/search/spending_by_category/awarding_agency/")
        for a, d in sorted(addr_agency.items(), key=lambda kv: -kv[1]) if d > 0
    ]
    award_agency_annual = [
        AgencyDollars(
            agency=a, dollars=d / _LANE_WINDOW_YEARS,
            dollars_label=_fmt_money(d / _LANE_WINDOW_YEARS),
            basis="avg annual share of the top-award pool across the client's "
                  "NAICS lanes, 3-year window (USAspending spending_by_award)",
            source="https://api.usaspending.gov/api/v2/search/spending_by_award/")
        for a, d in sorted(pool_agency.items(), key=lambda kv: -kv[1])
    ]
    news_block = results.get("news")
    news_items = (news_block.get("items") if isinstance(news_block, dict) else None) or []
    return MarketOverview(
        tam_dollars=market_total or None,
        tam_label=_fmt_money(market_total),
        tam_annual_dollars=annual,
        tam_annual_label=_fmt_money(annual),
        addressable_annual_dollars=addr_annual,
        addressable_annual_label=_fmt_money(addr_annual),
        addressable_agency_annual=addressable_agency_annual,
        addressable_keywords=addr_keywords,
        award_agency_annual=award_agency_annual,
        components=components,
        agency_map=agency_map,
        news=[{"title": n.get("title"), "url": n.get("url"),
               "published": n.get("published"), "source": n.get("source")}
              for n in news_items[:8] if isinstance(n, dict) and n.get("url")])


def _build_board(results: dict, qualify: Optional[dict], as_of: date,
                 gaps: list[str], cap_profile=None,
                 notices: Optional[dict] = None,
                 live_report=None) -> tuple[PursuitBoard, dict]:
    if live_report is not None:
        from agents.assess.live_report import LiveReportState
        if live_report.state == LiveReportState.CURRENT:
            return _build_ledger_board(
                results, qualify, as_of, gaps, cap_profile, live_report)
        if live_report.state == LiveReportState.INVALID:
            return PursuitBoard(), {}
    notices = _sam_notice_map(results) if notices is None else notices
    triage = results.get("triage") if isinstance(results.get("triage"), dict) else {}
    verdict_totals: dict[str, int] = {}
    for sid, v in triage.items():
        if sid not in notices:
            continue
        verdict = (v or {}).get("verdict")
        if verdict:
            verdict_totals[verdict] = verdict_totals.get(verdict, 0) + 1

    dossiers = results.get("dossiers") if isinstance(results.get("dossiers"), dict) else {}
    dossier_by_id = {r.get("id"): r for r in dossiers.get("records") or []
                     if isinstance(r, dict) and r.get("id")}
    qualify_fit: dict[str, str] = {}
    for c in (qualify or {}).get("candidates") or []:
        opp = (c or {}).get("opportunity") or {}
        fr = (c or {}).get("fit_rationale") or {}
        if opp.get("source_id") and fr.get("verdict"):
            qualify_fit[opp["source_id"]] = fr["verdict"]

    naics_median: dict[str, float] = {}
    incumbents_by_naics: dict[str, list[str]] = {}
    for b in results.get("usaspending.gov") or []:
        if isinstance(b, dict) and b.get("naics_code"):
            s = b.get("summary") or {}
            if isinstance(s.get("median_award"), (int, float)):
                naics_median[b["naics_code"]] = s["median_award"]
            incumbents_by_naics[b["naics_code"]] = s.get("top_incumbents") or []

    vehicles = (_load_json(_ROOT / "data" / "reference" / "vehicles.json") or {}).get("vehicles", [])
    catalog = [{"name": v.get("name"), "verified": v.get("verified"),
                "software_vendor_path": v.get("software_vendor_path")} for v in vehicles]

    # amendment dedupe (2026-07-06): notices sharing a solicitation number are
    # postings of ONE solicitation; it renders as one pursuit card, so every
    # count in the document counts solicitations, not postings. The LATEST
    # posting is the live state (grading keys to its dates); the full posting
    # history rides along for provenance. Notices without a solicitation
    # number never merge.
    groups: dict[str, list[tuple[str, dict]]] = {}
    for sid, t in triage.items():
        if (t or {}).get("verdict") != "pursue" or sid not in notices:
            continue
        n = notices[sid]
        rp = n.get("raw_payload") if isinstance(n.get("raw_payload"), dict) else {}
        key = rp.get("solicitation") or f"sid:{sid}"
        groups.setdefault(key, []).append((sid, n))

    pursuits = []
    for members in groups.values():
        members.sort(key=lambda m: (m[1].get("posted_date") or "",
                                    m[1].get("response_deadline") or "", m[0]))
        sid, n = members[-1]  # latest posting = the live state of the solicitation
        amendments: list[dict] = []
        if len(members) > 1:
            prev_deadline = None
            for m_sid, m_n in members:
                entry = {"source_id": m_sid, "posted_date": m_n.get("posted_date"),
                         "response_deadline": m_n.get("response_deadline")}
                dl = m_n.get("response_deadline")
                if prev_deadline and dl and dl != prev_deadline:
                    # stated by the data (each posting states its deadline);
                    # WHY it moved is never inferred
                    entry["change"] = f"deadline {prev_deadline} → {dl}"
                prev_deadline = dl or prev_deadline
                amendments.append(entry)
        # dossier/qualify may be keyed to an earlier posting of the same
        # solicitation; any member's record is this solicitation's record
        dossier = next((dossier_by_id[m_sid] for m_sid, _m in reversed(members)
                        if m_sid in dossier_by_id), None)
        fit = next((qualify_fit[m_sid] for m_sid, _m in reversed(members)
                    if m_sid in qualify_fit), None)
        g = grade({
            "triage_verdict": "pursue",
            "qualify_fit": fit,
            "dossier_fit": (dossier or {}).get("fit_verdict"),
            "response_deadline": n.get("response_deadline"),
            "vehicle": (dossier or {}).get("vehicle"),
            "vehicle_catalog": catalog,
            "naics_incumbents": incumbents_by_naics.get(n.get("naics_code"), []),
            "naics_median_award": naics_median.get(n.get("naics_code")),
        }, as_of=as_of)
        rp = n.get("raw_payload") if isinstance(n.get("raw_payload"), dict) else {}
        trace = None
        if cap_profile is not None:
            from tools.capability import fit_trace as _ft
            trace = _ft(cap_profile, n, (t or {}).get("reason") or "")
        pursuits.append(GradedPursuit(
            rank=0, source_id=sid, title=n.get("title") or sid,
            fit_trace=trace,
            agency=n.get("agency"), naics=n.get("naics_code"), url=n.get("api_url"),
            response_deadline=n.get("response_deadline"),
            notice_type=rp.get("type"), solicitation=rp.get("solicitation"),
            set_aside=n.get("set_aside"),
            set_aside_code=rp.get("set_aside_code") or None,
            verdict="pursue", grade=g, dossier=dossier, amendments=amendments))

    pursuits.sort(key=lambda p: (-p.grade.score, p.response_deadline or "9999"))
    for i, p in enumerate(pursuits):
        p.rank = i + 1
    if not qualify:
        gaps.append("qualify layer missing: fit dimension falls back to triage verdicts")
    if not dossier_by_id:
        gaps.append("no pursuit dossiers built: dossier depth absent from every pursuit")
    return PursuitBoard(pursuits=pursuits), verdict_totals


def _build_ledger_board(results: dict, qualify: Optional[dict], as_of: date,
                        gaps: list[str], cap_profile,
                        live_report) -> tuple[PursuitBoard, dict]:
    """Adapt strict Live SAM families into the established pursuit model.

    Eligibility, family identity, deadline, recommendation, and source URL
    come from the validated ledger. Raw sweep and review artifacts contribute
    presentation/depth fields only after the family is already BID_NOW.
    """
    dossiers = results.get("dossiers") \
        if isinstance(results.get("dossiers"), dict) else {}
    dossier_by_id = {
        row.get("id"): row for row in dossiers.get("records") or []
        if isinstance(row, dict) and row.get("id")
    }
    qualify_fit: dict[str, str] = {}
    for candidate in (qualify or {}).get("candidates") or []:
        opportunity = (candidate or {}).get("opportunity") or {}
        rationale = (candidate or {}).get("fit_rationale") or {}
        if opportunity.get("source_id") and rationale.get("verdict"):
            qualify_fit[opportunity["source_id"]] = rationale["verdict"]

    naics_median: dict[str, float] = {}
    incumbents_by_naics: dict[str, list[str]] = {}
    for bundle in results.get("usaspending.gov") or []:
        if not isinstance(bundle, dict) or not bundle.get("naics_code"):
            continue
        summary = bundle.get("summary") or {}
        if isinstance(summary.get("median_award"), (int, float)):
            naics_median[bundle["naics_code"]] = summary["median_award"]
        incumbents_by_naics[bundle["naics_code"]] = \
            summary.get("top_incumbents") or []

    vehicles = (_load_json(
        _ROOT / "data" / "reference" / "vehicles.json") or {}).get(
            "vehicles", [])
    catalog = [{
        "name": vehicle.get("name"),
        "verified": vehicle.get("verified"),
        "software_vendor_path": vehicle.get("software_vendor_path"),
    } for vehicle in vehicles]

    pursuits: list[GradedPursuit] = []
    for projected in live_report.actionable:
        record = projected.record
        current = projected.current
        posting_rows = list(projected.postings)
        posting_ids = [str(row.get("source_id") or "")
                       for row in posting_rows]
        dossier = next((
            dossier_by_id[posting_id] for posting_id in reversed(posting_ids)
            if posting_id in dossier_by_id
        ), None)
        fit = next((
            qualify_fit[posting_id] for posting_id in reversed(posting_ids)
            if posting_id in qualify_fit
        ), None)
        raw = current.get("raw_payload") \
            if isinstance(current.get("raw_payload"), dict) else {}
        deadline = (record.response_deadline.isoformat()
                    if record.response_deadline else None)
        grade_row = grade({
            "triage_verdict": "pursue",
            "qualify_fit": fit,
            "dossier_fit": (dossier or {}).get("fit_verdict"),
            "response_deadline": deadline,
            "vehicle": (dossier or {}).get("vehicle"),
            "vehicle_catalog": catalog,
            "naics_incumbents": incumbents_by_naics.get(
                current.get("naics_code"), []),
            "naics_median_award": naics_median.get(current.get("naics_code")),
        }, as_of=as_of)

        trace = None
        if cap_profile is not None:
            from tools.capability import match_capability
            requirement_fit = match_capability(
                cap_profile,
                record.requirement_excerpt or "",
                component=record.component or record.agency,
            )
            trace = {
                "matched_terms": list(requirement_fit.matched),
                "naics": current.get("naics_code"),
                "naics_in_boundary": (
                    current.get("naics_code") in cap_profile.naics_boundary),
                "mission_component": requirement_fit.mission_hit or None,
                "score": requirement_fit.score,
                "chip": requirement_fit.chip,
                "screen_inference": (
                    "the human-reviewed exact SAM requirement span matches "
                    "approved capability terms"),
                "notice_evidence_trace": list(record.fit_trace),
            }

        amendments: list[dict] = []
        if len(posting_rows) > 1:
            previous_deadline = None
            for row in posting_rows:
                row_deadline = row.get("response_deadline")
                entry = {
                    "source_id": row.get("source_id"),
                    "posted_date": row.get("posted_date"),
                    "response_deadline": row_deadline,
                }
                if (previous_deadline and row_deadline
                        and row_deadline != previous_deadline):
                    entry["change"] = (
                        f"deadline {previous_deadline} → {row_deadline}")
                previous_deadline = row_deadline or previous_deadline
                amendments.append(entry)

        current_evidence = next((
            evidence for evidence in reversed(record.authoritative_evidence)
            if record.notice_id.lower() in str(evidence.source_url).lower()
        ), record.authoritative_evidence[-1])
        pursuits.append(GradedPursuit(
            rank=0,
            source_id=record.notice_id,
            title=record.title,
            fit_trace=trace,
            agency=record.agency,
            naics=current.get("naics_code"),
            url=str(current_evidence.source_url),
            response_deadline=deadline,
            notice_type=(current.get("notice_type") or current.get("type")
                         or raw.get("type") or raw.get("baseType")),
            solicitation=record.solicitation_number,
            set_aside=current.get("set_aside"),
            set_aside_code=raw.get("set_aside_code") or None,
            verdict="pursue",
            grade=grade_row,
            dossier=dossier,
            amendments=amendments,
        ))

    pursuits.sort(key=lambda pursuit: (
        -pursuit.grade.score, pursuit.response_deadline or "9999",
        pursuit.source_id))
    for index, pursuit in enumerate(pursuits, start=1):
        pursuit.rank = index
    if not qualify:
        gaps.append(
            "qualify layer missing: grade fit dimension uses strict Live SAM "
            "eligibility")
    if not dossier_by_id:
        gaps.append(
            "no pursuit dossiers built: dossier depth absent from every pursuit")
    if live_report.live_coverage_complete is False:
        gaps.append(
            "strict Live SAM coverage is incomplete; ledger eligibility remains "
            "fail-closed")
    if live_report.can_release is False:
        gaps.append(
            "strict Assess run is not release-ready; its Live SAM ledger still "
            "controls pursuit eligibility")
    return PursuitBoard(pursuits=pursuits), live_report.verdict_counts()


def _build_competitive(results: dict, board: PursuitBoard,
                       gaps: list[str], *,
                       log_unresolved_entities: bool = True) -> CompetitiveLandscape:
    # keyed by door_key (L14): canonical door when the crosswalk resolves the
    # name, normalized name otherwise, so name variants and M&A lineages merge
    # into ONE row with summed dollars and the lineage note rendered once.
    # Award evidence rows keep their as-of-contract names.
    competitors: dict[str, Competitor] = {}
    naics_of_pursuits: dict[str, list[str]] = {}
    for p in board.pursuits:
        if p.naics:
            naics_of_pursuits.setdefault(p.naics, []).append(p.source_id)

    def _door_row(name: str, basis: str) -> Competitor:
        key = door_key(name, log_unresolved=log_unresolved_entities)
        c = competitors.get(key)
        if c is None:
            cid = resolve_entity(
                name, log_unresolved=log_unresolved_entities)
            display = (entity_canonical_name(cid) if cid else None) or name
            c = Competitor(
                name=display, basis=basis,
                source="https://api.usaspending.gov/api/v2/search/spending_by_award/")
            competitors[key] = c
        if name not in c.merged_names:
            c.merged_names.append(name)
            distinct = sorted({normalize_company(n) for n in c.merged_names})
            if len(distinct) > 1 and not c.lineage_note:
                cid = resolve_entity(
                    name, log_unresolved=log_unresolved_entities)
                note = entity_note(cid) if cid else ""
                names = sorted({n for n in c.merged_names},
                               key=lambda n: normalize_company(n))
                joined = " and ".join(names) if len(names) == 2 else \
                    ", ".join(names[:-1]) + " and " + names[-1]
                c.lineage_note = (
                    f"{joined} records are a single corporate door, not "
                    f"{int_to_word(len(names))}"
                    + (f" ({note})" if note else ""))
        return c

    for b in results.get("usaspending.gov") or []:
        if not isinstance(b, dict):
            continue
        naics = b.get("naics_code") or ""
        for name in (b.get("summary") or {}).get("top_incumbents") or []:
            c = _door_row(name, basis="repeat awardee in the client's NAICS lanes "
                          "(top-5 by award count, USAspending 3-year window)")
            if naics and naics not in c.naics:
                c.naics.append(naics)
            for sid in naics_of_pursuits.get(naics, []):
                if sid not in c.pursuit_ids:
                    c.pursuit_ids.append(sid)
        # full award lists (commit 1) let us attach agencies + dollars; and
        # the rows themselves, so a competitor is drillable to its awards
        for a in b.get("awards") or []:
            name = (a or {}).get("recipient")
            if name and door_key(
                    name, log_unresolved=log_unresolved_entities) in competitors:
                c = _door_row(name, basis="")
                if a.get("awarding_agency") and a["awarding_agency"] not in c.agencies:
                    c.agencies.append(a["awarding_agency"])
                if isinstance(a.get("amount"), (int, float)):
                    c.dollars = (c.dollars or 0) + a["amount"]
                c.awards.append({"award_id": a.get("award_id"),
                                 "amount": a.get("amount"),
                                 "awarding_agency": a.get("awarding_agency"),
                                 "start_date": a.get("start_date"),
                                 "url": a.get("url"), "naics": naics,
                                 "recipient_as_of_contract": name})

    subs = results.get("subawards")
    for prime in (subs.get("primes") if isinstance(subs, dict) else None) or []:
        name = (prime or {}).get("name")
        if not name:
            continue
        c = _door_row(name, basis="prime holding subaward flow in the client's "
                      "lanes (USAspending subawards, 24-month window)")
        if isinstance(prime.get("total"), (int, float)):
            c.dollars = max(c.dollars or 0, prime["total"])
            if "teaming target" not in c.basis:
                c.basis += "; teaming target as much as competitor"

    ca = results.get("contract_awards")
    if isinstance(ca, dict) and ca.get("error"):
        gaps.append("incumbent-expiry data unavailable (contract_awards quota): "
                    "competitor expiries and recompete joins absent")

    out = sorted(competitors.values(), key=lambda c: -(c.dollars or 0))
    for c in out:
        c.dollars_label = _fmt_money(c.dollars)
        c.awards.sort(key=lambda r: -(r.get("amount") or 0))
        del c.awards[12:]  # largest twelve carry the story; totals stay complete
    note = ("Named from award and subaward records in the client's NAICS lanes; "
            "per-pursuit incumbents pending recompete data." if out else
            "No competitor evidence in this sweep.")
    return CompetitiveLandscape(competitors=out, coverage_note=note)


def _build_watchlist(results: dict, slug: str, generated_at: Optional[str],
                     as_of: date,
                     notices: Optional[dict] = None,
                     live_report=None) -> Watchlist:
    from tools.snapshots import diff_snapshots, load_latest_snapshot, sweep_slim_records
    if live_report is not None:
        from agents.assess.live_report import report_verdict
        notices = {
            projected.record.notice_id: projected.current
            for projected in live_report.notices
        }
        triage = {
            projected.record.notice_id: {
                "verdict": report_verdict(projected.record),
                "reason": (
                    "strict Live SAM classification: "
                    + projected.record.classification.value),
            }
            for projected in live_report.notices
        }
    else:
        notices = _sam_notice_map(results) if notices is None else notices
        triage = (results.get("triage")
                  if isinstance(results.get("triage"), dict) else {})
    safe_results = {**results, "sam.gov": list(notices.values()),
                    "triage": {sid: v for sid, v in triage.items()
                               if sid in notices}}
    current = sweep_slim_records(safe_results)
    prev = load_latest_snapshot("sweep", slug, before=as_of)
    entries: list[WatchlistEntry] = []
    if prev is None:
        note = "first pull; changes appear from the next refresh"
    else:
        delta = diff_snapshots(prev, current,
                               compare={"response_deadline": "deadline", "verdict": "verdict"})
        for n in delta["new"]:
            entries.append(WatchlistEntry(id=str(n.get("id")), source="sweep", kind="new",
                                          title=n.get("title"), url=n.get("url"),
                                          detail="new since last refresh"))
        for m in delta["moved"]:
            entries.append(WatchlistEntry(id=str(m.get("id")), source="sweep", kind="changed",
                                          title=m.get("title"), url=m.get("url"),
                                          detail=f"{m['label']}: {m['from']} → {m['to']}"))
        note = f"{len(delta['new'])} new · {len(delta['moved'])} changed since last refresh"
    # standing items: monitor-verdict notices keep a weekly eye
    for sid, t in triage.items():
        if (t or {}).get("verdict") == "monitor" and sid in notices:
            n = notices[sid]
            entries.append(WatchlistEntry(id=sid, source="sam.gov", kind="standing",
                                          title=n.get("title"), url=n.get("api_url"),
                                          detail=(t or {}).get("reason") or "monitor-grade"))
    return Watchlist(last_refreshed=generated_at, delta_note=note, entries=entries[:40])


def _build_partnering(results: dict, board: PursuitBoard,
                      competitive: CompetitiveLandscape, client_name: str,
                      slug: str, as_of: date, gaps: list[str],
                      notices: Optional[dict] = None,
                      live_report=None) -> PartneringBoard:
    """Blockers -> direction -> evidence-graded candidates, per pursuit.
    Everything degrades explicitly: unset profile fields, missing subaward
    artifacts and the missing vehicle-holder source all land in gaps."""
    from agents.partnering.blockers import detect_blockers, has_partnering_play
    from agents.partnering.candidates import identify_candidates
    from agents.partnering.profile import ProfileError, load_partnering_profile

    try:
        profile, unset, warnings = load_partnering_profile(slug)
    except ProfileError as e:
        gaps.append(f"partnering profile rejected: {e}; every blocker check N/A")
        return PartneringBoard(profile_unset=["size_status_by_naics",
                                              "certifications", "vehicles_held",
                                              "award_band"],
                               profile_warnings=[str(e)])
    for f in unset:
        gaps.append(f"partnering: profile field '{f}' unattested; related "
                    "blocker checks degrade to N/A")

    naics_median: dict[str, float] = {}
    awards_by_naics: dict[str, list] = {}
    incumbents_by_naics: dict[str, list] = {}
    for b in results.get("usaspending.gov") or []:
        if isinstance(b, dict) and b.get("naics_code"):
            s = b.get("summary") or {}
            if isinstance(s.get("median_award"), (int, float)):
                naics_median[b["naics_code"]] = s["median_award"]
            awards_by_naics[b["naics_code"]] = b.get("awards") or []
            incumbents_by_naics[b["naics_code"]] = s.get("top_incumbents") or []

    subs = results.get("subawards") if isinstance(results.get("subawards"), dict) else {}
    edges_by = subs.get("edges") if isinstance(subs.get("edges"), dict) else None
    demand_by = ((subs.get("demand") or {}).get("rows_by_naics")
                 if isinstance(subs.get("demand"), dict) else None)
    small_by = subs.get("small_awards") if isinstance(subs.get("small_awards"), dict) else None
    if board.pursuits and edges_by is None:
        gaps.append("partnering: subaward edges not on this sweep; run "
                    "run_market_refresh --subawards (teaming-history evidence absent)")

    competitor_names = [c.name for c in competitive.competitors]
    plays: list[PartneringPlay] = []
    play_gaps: set[str] = set()

    def _ctx_for(p) -> dict:
        return {"client_name": client_name, "competitors": competitor_names,
                "incumbents": incumbents_by_naics.get(p.naics or "", []),
                "awards": awards_by_naics.get(p.naics or "", []),
                "edges": (edges_by or {}).get(p.naics or "") or [],
                "demand_rows": (demand_by or {}).get(p.naics or "") or [],
                "small_rows": (small_by or {}).get(p.naics or "")
                              if small_by is not None else None,
                "naics_median_award": naics_median.get(p.naics or "")}

    # pursue-grade pursuits: no blocker, no play (never team where you can prime)
    for p in board.pursuits:
        blockers = detect_blockers(p, profile, unset,
                                   naics_median_award=naics_median.get(p.naics or ""))
        for b in blockers:
            if b.gap:
                play_gaps.add(b.gap)
        if not has_partnering_play(blockers):
            continue
        candidates, cand_gaps = identify_candidates(p, blockers, _ctx_for(p), as_of=as_of)
        play_gaps.update(cand_gaps)
        fired = [b for b in blockers if b.fired]
        plays.append(PartneringPlay(
            source_id=p.source_id, rank=p.rank,
            blockers=blockers,
            directions=sorted({d for b in fired for d in b.directions}),
            candidates=candidates,
            review_flags=sorted({f for b in blockers for f in b.review_flags})))

    # monitor-grade adjacencies: the pre-prime teaming surface. Every monitor
    # notice fires the ADJACENCY blocker (team, don't prime), so a play exists
    # whenever there are evidence-backed candidates; no candidates, no play.
    plays.extend(_build_monitor_watch(
        results, profile, unset, as_of, _ctx_for, naics_median,
        incumbents_by_naics, play_gaps, notices=notices,
        live_report=live_report))
    gaps.extend(sorted(play_gaps))
    return PartneringBoard(plays=plays, profile_unset=unset,
                           profile_warnings=warnings)


# heuristic cap: monitor adjacencies with candidates, strongest first; more
# than this and the tail is held back (logged), never silently dropped.
_MONITOR_WATCH_CAP = 6
# a teaming watch is a curated shortlist of primes, not the whole lane pool
_WATCH_SHORTLIST = 6


def _build_monitor_watch(results, profile, unset, as_of, ctx_for, naics_median,
                         incumbents_by_naics, play_gaps,
                         notices: Optional[dict] = None,
                         live_report=None) -> list[PartneringPlay]:
    from agents.assess.pursuit_grade import grade as _grade
    from agents.partnering.blockers import detect_blockers
    from agents.partnering.candidates import identify_candidates

    monitors = []
    if live_report is not None:
        candidates = [
            (projected.record.notice_id, projected.current,
             projected.record)
            for projected in live_report.notices
            if projected.record.recommendation.value == "monitor"
        ]
    else:
        notices = _sam_notice_map(results) if notices is None else notices
        triage = (results.get("triage")
                  if isinstance(results.get("triage"), dict) else {})
        candidates = [
            (sid, notices[sid], None)
            for sid, triage_row in triage.items()
            if (triage_row or {}).get("verdict") == "monitor"
            and sid in notices
        ]
    for sid, n, strict_record in candidates:
        rp = n.get("raw_payload") if isinstance(n.get("raw_payload"), dict) else {}
        deadline = (
            strict_record.response_deadline.isoformat()
            if strict_record is not None and strict_record.response_deadline
            else n.get("response_deadline"))
        url = (
            str(strict_record.authoritative_evidence[-1].source_url)
            if strict_record is not None else n.get("api_url"))
        g = _grade({"triage_verdict": "monitor",
                    "response_deadline": deadline,
                    "naics_incumbents": incumbents_by_naics.get(n.get("naics_code"), []),
                    "naics_median_award": naics_median.get(n.get("naics_code"))},
                   as_of=as_of)
        monitors.append(GradedPursuit(
            rank=0, source_id=sid,
            title=(strict_record.title if strict_record is not None
                   else n.get("title") or sid),
            agency=(strict_record.agency if strict_record is not None
                    else n.get("agency")),
            naics=n.get("naics_code"), url=url,
            response_deadline=deadline,
            set_aside=n.get("set_aside"), set_aside_code=rp.get("set_aside_code") or None,
            verdict="monitor", grade=g))
    # HEURISTIC ordering: strongest adjacency (grade score) first; a rough
    # proxy for teaming relevance, not a ranking of the notices themselves
    monitors.sort(key=lambda p: -p.grade.score)

    out: list[PartneringPlay] = []
    for p in monitors:
        blockers = detect_blockers(p, profile, unset,
                                   naics_median_award=naics_median.get(p.naics or ""))
        for b in blockers:
            if b.gap:
                play_gaps.add(b.gap)
        candidates, cand_gaps = identify_candidates(p, blockers, ctx_for(p), as_of=as_of)
        play_gaps.update(cand_gaps)
        if not candidates:
            continue  # a teaming watch needs a real prime to team with
        # the raw pool is every lane awardee; a teaming watch is a SHORTLIST ;
        # keep the strongest few by the candidate ranking (fit, evidence, recency)
        candidates = candidates[:_WATCH_SHORTLIST]
        fired = [b for b in blockers if b.fired]
        out.append(PartneringPlay(
            source_id=p.source_id, rank=0, kind="monitor",
            title=p.title, agency=p.agency, blockers=blockers,
            directions=sorted({d for b in fired for d in b.directions}),
            candidates=candidates,
            review_flags=sorted({f for b in blockers for f in b.review_flags})))
    if len(out) > _MONITOR_WATCH_CAP:
        play_gaps.add(f"teaming watch: {len(out) - _MONITOR_WATCH_CAP} adjacency "
                      f"play(s) held back; top {_MONITOR_WATCH_CAP} shown by "
                      "adjacency strength (heuristic)")
        out = out[:_MONITOR_WATCH_CAP]
    for i, play in enumerate(out):
        play.rank = i + 1
    return out


def _build_buyer_incumbents(results: dict, cap_profile) -> list[BuyerIncumbent]:
    """Profile-named products found in buyer-map accounts (L13): the same
    matcher the product tagger uses, so the two paths cannot drift. The
    client's own seats are renewals, not incumbency, and are excluded
    (mirroring the prober's client_name exclusion). Internal-only."""
    if cap_profile is None:
        return []
    named = [n for n in (cap_profile.named_competitors_and_incumbents or []) if n]
    rows = (results.get("incumbent_buyer_map") or {}).get("buyers") or []
    found: dict[str, BuyerIncumbent] = {}
    for b in rows:
        account = b.get("buyer") or b.get("agency") or ""
        for prod in (b.get("products") or []):
            if company_matches(prod, cap_profile.client_name) \
                    or company_matches(cap_profile.client_name, prod):
                continue
            canon = next((nm for nm in named
                          if company_matches(prod, nm)
                          or company_matches(nm, prod)), None)
            if canon is None:
                continue
            slot = found.setdefault(normalize_company(canon),
                                    BuyerIncumbent(company=canon))
            if account and account not in slot.agencies:
                slot.agencies.append(account)
    return sorted(found.values(),
                  key=lambda x: (-len(x.agencies), x.company.lower()))


def build_document(client_name: str, *, searches: Optional[dict] = None,
                   qualify: Optional[dict] = None,
                   content: Optional[CaptureBriefContent] = None,
                   as_of: Optional[date] = None,
                   _live_report: Any = _AUTO_LIVE_REPORT,
                   _cap_profile: Any = _AUTO_CAP_PROFILE,
                   _log_unresolved_entities: bool = True) -> AssessmentDocument:
    """Deterministic assembly. `content` (the one LLM compose) is optional so
    the joins are testable offline; the run entry supplies it."""
    if as_of is None:
        from agents.assess.live_report import utc_today
        report_as_of = date.today()
        deadline_cutoff = utc_today()
    else:
        # An explicit date is the deterministic/offline replay seam: it binds
        # both the presentation stamp and every date-only comparison.
        report_as_of = as_of
        deadline_cutoff = as_of
    slug = _slug(client_name)
    _artifact: Optional[Path] = None
    if searches is None:
        from agents.review import sweep_artifact_path
        try:
            _artifact = Path(sweep_artifact_path(client_name))  # L19
        except FileNotFoundError:
            raise  # scoped gate without its artifact: loud, never wrong universe
        searches = _load_json(_artifact) or {}
    if qualify is None:
        qualify = _load_json(_ROOT / "data" / "review" / f"{slug}.qualify.json")
    # FOCUS projection (2026-07-12): a scoped sweep's whole-market context
    # layers (buyer map, news, expiring awards, forecast) filter to the gate
    # agencies before any join; a DHS deliverable can never render a DISA
    # displacement window as its own forming play again.
    from tools.agency_scope import scope_focus_results
    results = scope_focus_results(searches)
    generated_at = searches.get("generated_at")
    if not generated_at and _artifact is not None:
        try:
            generated_at = datetime.fromtimestamp(
                os.path.getmtime(_artifact)).isoformat(timespec="seconds")
        except OSError:
            generated_at = None  # artifact vanished mid-build; None is valid

    gaps: list[str] = []
    # L19: snapshot lineage follows the artifact's OWN designator — a DHS run
    # diffs DHS history, never the all-market baseline
    from agents.review import scope_designator as _scope_designator19
    _d19 = _scope_designator19(searches.get("search_scope"))
    _snap_key = f"{slug}.{_d19}" if _d19 else slug
    sam_census = results.get("sam_census") or {}
    if sam_census and not sam_census.get("complete", True):
        gaps.append(
            f"SAM screen INCOMPLETE: retrieved "
            f"{sam_census.get('retrieved', sam_census.get('matched'))} of "
            f"{sam_census.get('active_screened')} records "
            f"(budget/API shortfall); screened-N claims reconcile against "
            f"the census, shortfall flagged in the QA appendix")
    if _cap_profile is _AUTO_CAP_PROFILE:
        from tools.capability import load_profile as _load_cap_profile
        try:
            cap_profile = _load_cap_profile(client_name)
        except Exception as e:  # noqa: BLE001 - malformed profile degrades, never blocks
            cap_profile = None
            gaps.append(f"capability profile unreadable; profile-dependent joins "
                        f"were omitted ({type(e).__name__}: {str(e)[:120]})")
    else:
        cap_profile = _cap_profile
    live_report = None
    if _live_report is _AUTO_LIVE_REPORT:
        from agents.assess.live_report import (
            LiveReportState, resolve_current_live_report,
        )
        live_report = resolve_current_live_report(
            searches.get("client") or client_name, searches, cap_profile,
            effective_date=deadline_cutoff)
        if live_report.state == LiveReportState.INVALID:
            gaps.append(
                "strict Live SAM cutover invalid; live lane held closed: "
                + (live_report.problem or "current ledger validation failed"))
        elif live_report.state == LiveReportState.ABSENT:
            live_report = None
    elif _live_report is not False:
        live_report = _live_report
    # ONE notice-map pass per build (review finding #11): board, watchlist,
    # and the monitor-watch all consume the same canonical SAM map
    _notices = _sam_notice_map(results)
    board, verdict_totals = _build_board(results, qualify, deadline_cutoff, gaps,
                                         cap_profile=cap_profile,
                                         notices=_notices,
                                         live_report=live_report)
    # PRODUCT vs LANE-COHABITANT competitors (2026-07-10): the profile's
    # named list marks who actually competes with the product; the rest is
    # lane furniture that renders internal-only
    _named = [n for n in
              (cap_profile.named_competitors_and_incumbents if cap_profile
               else []) if n]
    competitive = _build_competitive(
        results, board, gaps,
        log_unresolved_entities=_log_unresolved_entities)
    if cap_profile is not None:
        competitive.product_tagged = True
        for _cr in competitive.competitors:
            _cr.product = any(company_matches(_cr.name, nm) for nm in _named)
    from agents.reports.source_coverage import coverage_from_sweep

    doc = AssessmentDocument(
        client_name=searches.get("client") or client_name,
        as_of=report_as_of,
        generated_at=generated_at,
        market=_build_market(results, gaps),
        board=board,
        competitive=competitive,
        buyer_incumbents=_build_buyer_incumbents(results, cap_profile),
        partnering=_build_partnering(results, board, competitive,
                                     searches.get("client") or client_name,
                                     slug, deadline_cutoff, gaps,
                                     notices=_notices,
                                     live_report=live_report),
        watchlist=_build_watchlist(
            results, _snap_key, generated_at, report_as_of,
            notices=_notices, live_report=live_report),
        content=content,
        verdict_totals=verdict_totals,
        sam_census=sam_census,
        research_source_coverage=coverage_from_sweep(searches),
        gaps=gaps,
    )
    return doc
