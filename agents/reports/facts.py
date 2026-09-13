"""FactPack; the numbered, sourced fact base every report is built from.

Drift control starts here, not in a prompt: the composer layer is only allowed to
make claims it can pin to a fact id ([F3]), every fact carries its source URL, and
the specificity critic + a deterministic check verify the citations. A number that
is not in the FactPack does not go in a report.

Assembled deterministically (no LLM) from the artifacts earlier steps already wrote:
  data/cleaned/searches_<client>.json   (SAM notices, USAspending market evidence, web leads)
  data/review/<client>.qualify.json     (verified candidates + fit rationales)
"""

from __future__ import annotations

from agents.assess.source_clock import acquired_at

import json
import os
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CLEANED = os.path.join(_ROOT, "data", "cleaned")
_REVIEW = os.path.join(_ROOT, "data", "review")


def _slug(name: str) -> str:
    from tools.slug import client_slug
    return client_slug(name)


class SourceSystem(str, Enum):
    """Where a figure was pulled from. USASPENDING and SAM are PRIMARY federal
    records for arbitration (docs/VERIFICATION_POLICY.md); the rest can inform
    but never override a primary record."""

    USASPENDING = "usaspending"
    SAM = "sam"
    APFS = "apfs"
    AGENCY_DOC = "agency_doc"
    ANALYST = "analyst"


#: Stored-fact freshness marker (2026-07-16): a fact whose retrieval time
#: could not be recovered from run metadata carries this instead of a guessed
#: timestamp. The release gate hard-fails it exactly like a stale figure.
UNKNOWN_FRESHNESS = "UNKNOWN_FRESHNESS"


class CompetingValue(BaseModel):
    """One side of a figure dispute (extract pass vs adversarial verify pass).

    The verify pass (separate Codex-owned session) writes these; this model is
    the agreed structure it writes INTO. `observed_by` is the pass identity
    ('extract', 'verify', a model name, or a human id); the resolution log
    refuses a decider who appears here (no model resolves a dispute over a
    figure it authored)."""

    value: Any = Field(description="the figure as this source states it")
    source_system: Optional[SourceSystem] = None
    source_record_id: Optional[str] = None
    source_url: Optional[str] = None
    observed_by: str = Field(description="'extract' | 'verify' | model or human id")
    observed_at: Optional[datetime] = None


class Fact(BaseModel):
    """One citable statement. `value` is the payload; `source` is where it came from.

    Per-figure provenance (2026-07-16): every figure in a rendered client
    deliverable carries its own source system, source record id, and retrieval
    timestamp. Freshness is a property of the FIGURE, not the document; the
    release gate (agents/reports/verification.py) fails a render whose cited
    figures are stale, unknown-freshness, or disputed-and-unresolved.
    `tier` IS the verification tier (values unchanged: 'program' | 'notice');
    `verification_tier` is a read alias, never a second field."""

    id: str = Field(description="e.g. 'F1'; what report text cites")
    kind: str = Field(description="'market' | 'competitor' | 'opportunity' | 'context'")
    text: str = Field(description="the fact, stated plainly, numbers included")
    value: Optional[dict] = None
    source: str = Field(description="URL or artifact path backing this fact")
    tier: Optional[str] = Field(
        default=None,
        description="evidence tier (L15): 'program' = stable agency/USASpending"
                    "-sourced records and projections, renders only in forming/"
                    "signals sections; 'notice' = live SAM notice claims, "
                    "requires the re-verification gate before pursue-grade")
    source_system: Optional[SourceSystem] = Field(
        default=None, description="which system the figure was pulled from")
    source_record_id: Optional[str] = Field(
        default=None,
        description="the source record behind the figure (award id, notice id)")
    retrieved_at: Optional[datetime] = Field(
        default=None,
        description="UTC moment the backing record was pulled from its source; "
                    "inherited from sweep metadata only for non-strict facts when "
                    "the record carries no closer stamp; strict Assess facts require "
                    "supported source acquisition")
    acquisition_required: bool = False
    freshness: Optional[Literal["UNKNOWN_FRESHNESS"]] = Field(
        default=None,
        description="set to UNKNOWN_FRESHNESS when no retrieval time is "
                    "recoverable from run metadata; hard-fails the release gate")
    disputed: bool = Field(
        default=False,
        description="the extract and verify passes disagree on this figure; "
                    "blocks release until resolved per VERIFICATION_POLICY.md")
    competing_values: list[CompetingValue] = Field(
        default_factory=list,
        description="the disputed figure as each pass/source states it")

    @property
    def verification_tier(self) -> Optional[str]:
        """Read alias: the existing `tier` field IS the verification tier."""
        return self.tier


class FactPack(BaseModel):
    client_name: str
    as_of: date
    facts: list[Fact] = Field(default_factory=list)
    #: convenience views (ids into `facts`)
    market_fact_ids: list[str] = Field(default_factory=list)
    competitor_fact_ids: list[str] = Field(default_factory=list)
    opportunity_fact_ids: list[str] = Field(default_factory=list)
    monitor_fact_ids: list[str] = Field(
        default_factory=list,
        description="screened monitor-grade notices: citable adjacencies, never pursuits")
    incumbent_product_fact_ids: list[str] = Field(
        default_factory=list,
        description="buyer -> incumbent product -> contracting office signals (capability layer)")
    warnings: list[str] = Field(default_factory=list, description="gaps the report must not paper over")

    def ids(self) -> set[str]:
        return {f.id for f in self.facts}

    def get(self, fact_id: str) -> Optional[Fact]:
        return next((f for f in self.facts if f.id == fact_id), None)

    #: provenance is gate machinery, never composer vocabulary: the composer
    #: payload keeps its pre-provenance shape so prompt bytes stay stable and
    #: retrieval language can never leak into client copy
    _COMPOSER_EXCLUDED_FIELDS = frozenset({
        "source_system", "source_record_id", "retrieved_at", "freshness", "acquisition_required",
        "disputed", "competing_values",
    })

    def context(self) -> dict:
        """Shape handed to the composer layer."""
        return {
            "client_name": self.client_name,
            "as_of": self.as_of.isoformat(),
            "facts": [f.model_dump(mode="json",
                                   exclude=set(self._COMPOSER_EXCLUDED_FIELDS))
                      for f in self.facts],
            "warnings": self.warnings,
        }


def _fmt_money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"F{self.n}"


def _parse_retrieved(value: Any) -> Optional[datetime]:
    """ISO string or datetime -> aware UTC datetime; unparseable -> None.

    Naive timestamps read as UTC, matching the evidence-layer convention
    (agents/assess/ledger.py). At the 14-day freshness granularity a timezone
    of error is immaterial; consistency with the ledger owner wins."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed = datetime(*map(int, raw[:10].split("-")))
            except (ValueError, TypeError):
                return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


#: source-URL host fragments -> the system a figure was pulled from. The
#: fact's own `source` field is the derivation input, so a figure can never
#: claim a system its citation does not back.
_SOURCE_SYSTEM_HINTS: tuple[tuple[str, SourceSystem], ...] = (
    ("usaspending.gov", SourceSystem.USASPENDING),
    ("sam.gov", SourceSystem.SAM),
    ("apfs", SourceSystem.APFS),
    ("federalregister.gov", SourceSystem.AGENCY_DOC),
    ("regulations.gov", SourceSystem.AGENCY_DOC),
    ("govinfo.gov", SourceSystem.AGENCY_DOC),
    ("cisa.gov", SourceSystem.AGENCY_DOC),
)

#: value-payload keys that carry the backing record's identity, tried in
#: order. Aggregate facts (lane totals, leaderboards) have no single record
#: and honestly carry None.
_RECORD_ID_KEYS = ("award_id", "notice_id", "live_record_id", "piid")


def _infer_source_system(source: str) -> Optional[SourceSystem]:
    low = (source or "").lower()
    for fragment, system in _SOURCE_SYSTEM_HINTS:
        if fragment in low:
            return system
    return None


def _infer_record_id(fact: Fact) -> Optional[str]:
    v = fact.value or {}
    for key in _RECORD_ID_KEYS:
        if v.get(key):
            return str(v[key])
    opp = v.get("opportunity")
    if isinstance(opp, dict) and opp.get("source_id"):
        return str(opp["source_id"])
    if v.get("source_id"):
        return str(v["source_id"])
    # buyer-map facts quote their largest matched record; that record backs
    # the headline figure and carries the award id
    records = v.get("records")
    if isinstance(records, list) and records and isinstance(records[0], dict):
        rec_id = records[0].get("award_id")
        if rec_id:
            return str(rec_id)
    return None


def _stamp_provenance(facts: list[Fact],
                      default_retrieved: Optional[datetime]) -> None:
    """Per-figure provenance (2026-07-16), one owner for every builder.

    A fact keeps any provenance its builder stamped (the strict ledger knows
    per-record evidence times). Otherwise: source system from the fact's own
    source URL, record id from its value payload, retrieval time from the
    closest stamp the artifact already carries (a bucket-level retrieved_at
    riding in `value`, e.g. the incumbent buyer map) with the sweep artifact's
    `generated_at` as the run-metadata fallback. No recoverable time means
    UNKNOWN_FRESHNESS, never a guess; the release gate fails it closed."""
    for f in facts:
        if f.source_system is None:
            f.source_system = _infer_source_system(f.source)
        if f.source_record_id is None:
            f.source_record_id = _infer_record_id(f)
        if f.retrieved_at is None and not f.acquisition_required:
            own = _parse_retrieved((f.value or {}).get("retrieved_at"))
            f.retrieved_at = own or default_retrieved
        if f.retrieved_at is None:
            f.freshness = UNKNOWN_FRESHNESS


def _market_facts(evidence: list[dict], c: _Counter) -> tuple[list[Fact], list[str]]:
    """One market-size fact per NAICS with USAspending evidence, plus a window note."""
    facts: list[Fact] = []
    warnings: list[str] = []
    for ev in evidence or []:
        if not isinstance(ev, dict) or not ev.get("naics_code"):
            continue
        s = ev.get("summary") or {}
        naics = ev["naics_code"]
        scope = ev.get("agency_filter_used")
        scope_txt = f" at {scope}" if scope else " government-wide"
        if not s.get("award_count"):
            warnings.append(f"no USAspending awards found for NAICS {naics}{scope_txt}")
            continue
        # the query returns the N LARGEST awards (sort=Award Amount desc,
        # limit 50) — its median describes that top slice, NOT the lane.
        # 2026-07-09 review caught "$361M median" wearing a government-wide
        # label; the population travels with the number from here on.
        facts.append(Fact(
            id=c.next(), kind="market",
            text=(
                f"NAICS {naics}{scope_txt}: the {s['award_count']} largest awards by "
                f"obligation in the queried window total "
                f"{_fmt_money(s.get('total_obligated'))} obligated; within that "
                f"top-{s['award_count']} slice (NOT the whole lane) the median is "
                f"{_fmt_money(s.get('median_award'))} and the largest is "
                f"{_fmt_money(s.get('max_award'))}. BOUNDARY CONTEXT ONLY: a "
                f"NAICS lane marks where contracts are filed, not what the "
                f"client sells; at most one context sentence in copy, never a "
                f"stat card, addressable claim, or thesis support."
            ),
            value={"naics": naics, "population": f"top {s['award_count']} awards by obligation", **s},
            source="https://api.usaspending.gov/api/v2/search/spending_by_award/",
        ))
    return facts, warnings


def _competitor_facts(evidence: list[dict], c: _Counter) -> list[Fact]:
    facts: list[Fact] = []
    for ev in evidence or []:
        if not isinstance(ev, dict):
            continue
        s = ev.get("summary") or {}
        incumbents = s.get("top_incumbents") or []
        if not incumbents:
            continue
        sample = ev.get("sample_awards") or []
        src = next((a.get("url") for a in sample if a.get("url")), None)
        facts.append(Fact(
            id=c.next(), kind="competitor",
            text=(
                f"Top incumbents for NAICS {ev.get('naics_code')} (by award count in the "
                f"queried window): {', '.join(incumbents)}."
            ),
            value={"naics": ev.get("naics_code"), "incumbents": incumbents},
            source=src or "https://api.usaspending.gov/api/v2/search/spending_by_award/",
        ))
    return facts


def _opportunity_facts(candidates: list[dict], c: _Counter) -> list[Fact]:
    """Verified SAM candidates, strongest first.

    The explicit source check is a compatibility guard for stale qualify
    artifacts created when reachable web leads were treated as candidates.
    """
    order = {"strong_fit": 0, "partial_fit": 1, None: 2, "weak_fit": 3, "no_fit": 4}

    def _key(rec: dict):
        fr = rec.get("fit_rationale") or {}
        return (order.get(fr.get("verdict"), 2), -(rec.get("opportunity", {}).get("estimated_value") or 0))

    facts = []
    eligible = [
        r for r in candidates
        if r.get("verified")
        and isinstance(r.get("opportunity"), dict)
        and r["opportunity"].get("source") == "sam.gov"
    ]
    for rec in sorted(eligible, key=_key):
        opp = rec.get("opportunity", {}) or {}
        fr = rec.get("fit_rationale") or {}
        bits = [f"{opp.get('title')}", f"({opp.get('source')} {opp.get('source_id')})"]
        if opp.get("agency"):
            bits.append(f"issued by {opp['agency']}")
        if opp.get("naics_code"):
            bits.append(f"NAICS {opp['naics_code']}")
        if opp.get("set_aside"):
            bits.append(f"set-aside: {opp['set_aside']}")
        if opp.get("estimated_value"):
            bits.append(f"est. {_fmt_money(opp['estimated_value'])}")
        if opp.get("response_deadline"):
            bits.append(f"responses due {opp['response_deadline']}")
        if fr.get("verdict"):
            bits.append(f"fit: {fr['verdict']}")
        facts.append(Fact(
            id=c.next(), kind="opportunity",
            text="; ".join(str(b) for b in bits if b) + ".",
            value={"opportunity": {k: opp.get(k) for k in (
                "source", "source_id", "title", "agency", "naics_code", "set_aside",
                "estimated_value", "response_deadline", "api_url")},
                "fit": fr},
            source=str(opp.get("api_url") or "data/review (qualify report)"),
        ))
    return facts


def _ledger_opportunity_facts(live_report, c: _Counter) -> list[Fact]:
    """BID_NOW ledger families, with raw rows used only for display fields."""
    facts: list[Fact] = []
    for projected in sorted(
            live_report.actionable,
            key=lambda row: (
                row.record.response_deadline or date.max,
                row.record.record_id)):
        record = projected.record
        raw_row = projected.current
        raw = raw_row.get("raw_payload") \
            if isinstance(raw_row.get("raw_payload"), dict) else {}
        source = str(record.authoritative_evidence[-1].source_url)
        opportunity = {
            "source": "sam.gov",
            "source_id": record.notice_id,
            "title": record.title,
            "agency": record.agency,
            "naics_code": raw_row.get("naics_code"),
            "set_aside": raw_row.get("set_aside"),
            "estimated_value": raw_row.get("estimated_value"),
            "response_deadline": (
                record.response_deadline.isoformat()
                if record.response_deadline else None),
            "api_url": source,
            "solicitation": record.solicitation_number,
            "notice_type": (raw_row.get("notice_type") or raw_row.get("type")
                            or raw.get("type") or raw.get("baseType")),
        }
        fit = {
            "verdict": "human_reviewed_requirement_fit",
            "basis": "human-reviewed exact SAM requirement span",
            "trace": list(record.fit_trace),
            "requirement_excerpt": record.requirement_excerpt,
            "requirement_reviewed_by": record.requirement_reviewed_by,
        }
        bits = [
            f"{record.title}",
            f"(sam.gov {record.notice_id})",
            f"issued by {record.agency}",
        ]
        if opportunity["naics_code"]:
            bits.append(f"NAICS {opportunity['naics_code']}")
        if opportunity["set_aside"]:
            bits.append(f"set-aside: {opportunity['set_aside']}")
        if opportunity["estimated_value"]:
            bits.append(f"est. {_fmt_money(opportunity['estimated_value'])}")
        if opportunity["response_deadline"]:
            bits.append(f"responses due {opportunity['response_deadline']}")
        bits.append("fit: exact SAM requirement span approved by a human reviewer")
        facts.append(Fact(
            id=c.next(),
            kind="opportunity",
            text="; ".join(bits) + ".",
            value={"opportunity": opportunity, "fit": fit,
                   "live_record_id": record.record_id},
            source=source,
            tier="notice",
            # the strict ledger owns per-record evidence times; its facts
            # never inherit the sweep-level default
            source_system=SourceSystem.SAM,
            source_record_id=record.notice_id,
            retrieved_at=acquired_at(record.authoritative_evidence[-1]),
            acquisition_required=True,
        ))
    return facts


def _ledger_monitor_facts(live_report, c: _Counter) -> list[Fact]:
    """Strict monitor recommendations remain forming context, never pursuits."""
    facts: list[Fact] = []
    for projected in live_report.notices:
        record = projected.record
        if record.recommendation.value != "monitor":
            continue
        source = str(record.authoritative_evidence[-1].source_url)
        raw_row = projected.current
        bits = [
            f"{record.title} (sam.gov {record.notice_id})",
            f"issued by {record.agency}",
            f"strict live classification: {record.classification.value}",
            "verdict: MONITOR, not a pursue-grade solicitation",
        ]
        if raw_row.get("naics_code"):
            bits.insert(2, f"NAICS {raw_row['naics_code']}")
        facts.append(Fact(
            id=c.next(),
            kind="context",
            text="; ".join(bits) + ".",
            value={
                "source_id": record.notice_id,
                "title": record.title,
                "agency": record.agency,
                "deadline": (record.response_deadline.isoformat()
                             if record.response_deadline else None),
                "url": source,
                "verdict": "monitor",
                "classification": record.classification.value,
                "live_record_id": record.record_id,
            },
            source=source,
            tier="notice",
            source_system=SourceSystem.SAM,
            source_record_id=record.notice_id,
            retrieved_at=acquired_at(record.authoritative_evidence[-1]),
            acquisition_required=True,
        ))
    return facts


def _monitor_facts(results: dict, c: _Counter) -> list[Fact]:
    """Screened MONITOR-grade notices -> citable facts. The full-picture
    review reads the whole sweep and directs emphasis at these (2026-07-09
    Osprey: Project Pioneer, the OASIS+ on-ramps, the DHS flight-data
    sole-source cluster) — when the pack carries no fact for them, the
    composer can only invent uncited claims or defy the directive. Every
    fact names its verdict so a monitor item can never pass as a pursuit."""
    triage = results.get("triage") or {}
    by_id = {
        n.get("source_id"): n for n in (results.get("sam.gov") or [])
        if isinstance(n, dict)
        and n.get("source") in (None, "", "sam.gov")
        and n.get("source_id")
    }
    facts: list[Fact] = []
    for nid, entry in triage.items():
        if (entry or {}).get("verdict") != "monitor":
            continue
        n = by_id.get(nid)
        if not n:
            continue
        rp = n.get("raw_payload") or {}
        title = n.get("title") or rp.get("title") or nid
        bits = [f"{title} (sam.gov {nid})"]
        agency = rp.get("agency") or rp.get("department")
        if agency:
            bits.append(f"issued by {agency}")
        naics = rp.get("naics_code") or rp.get("naics")
        if naics:
            bits.append(f"NAICS {naics}")
        deadline = rp.get("response_deadline") or rp.get("response_date")
        if deadline:
            bits.append(f"responses due {deadline}")
        bits.append("screened MONITOR-grade (adjacency/forming window, "
                    "NOT a live pursuit)")
        if entry.get("reason"):
            bits.append(f"screen rationale: {entry['reason']}")
        facts.append(Fact(
            id=c.next(), kind="opportunity",
            text="; ".join(bits) + ".",
            value={"notice_id": nid, "verdict": "monitor", "title": title,
                   "agency": agency, "reason": entry.get("reason")},
            source=str(n.get("api_url") or rp.get("url") or "sam.gov"),
        ))
    return facts


def _signal_facts(results: dict, c: _Counter) -> list[Fact]:
    """Federal Register docs + news items -> context facts (each with its URL)."""
    facts: list[Fact] = []
    fr = results.get("federal_register") or {}
    if isinstance(fr, dict):
        for kw, docs in fr.items():
            if not isinstance(docs, list):
                continue
            for d in docs[:3]:
                if not d.get("url"):
                    continue
                facts.append(Fact(
                    id=c.next(), kind="context",
                    text=f"Federal Register {d.get('type', 'document')} "
                         f"({d.get('publication_date')}): {d.get('title')}",
                    value={"keyword": kw, "agencies": d.get("agencies")},
                    source=d["url"],
                ))
    regs = results.get("regulations_gov") or {}
    if isinstance(regs, dict) and not regs.get("error"):
        for kw, docs in regs.items():
            if not isinstance(docs, list):
                continue
            for d in docs[:3]:
                if not d.get("url"):
                    continue
                open_note = (
                    f" Comment period OPEN until {d.get('comment_ends') or 'TBD'}; "
                    "an engagement window with the program office."
                    if d.get("comment_open") else ""
                )
                facts.append(Fact(
                    id=c.next(), kind="context",
                    text=f"Regulations.gov {d.get('type', 'document')} "
                         f"(docket {d.get('docket')}, posted {d.get('posted')}): "
                         f"{d.get('title')}.{open_note}",
                    value={"keyword": kw, "comment_open": d.get("comment_open")},
                    source=d["url"],
                ))
    news = results.get("news") or {}
    for item in (news.get("items") or [])[:20] if isinstance(news, dict) else []:
        if not item.get("url"):
            continue
        facts.append(Fact(
            id=c.next(), kind="context",
            text=f"News ({item.get('source')}, {item.get('published')}): {item.get('title')}",
            source=item["url"],
        ))
    return facts


LANE_ONLY_LABEL = "lane-level evidence only, not capability-verified"


def _capability_prime_ranking(results: dict, profile) -> list[dict]:
    """Aggregate subaward EDGES by prime DOOR (L14), capability-matched rows
    only. The PRIMARY teaming evidence (2026-07-09 inversion): what the money
    was FOR, per the award descriptions, never which NAICS lane filed it.
    Name variants and M&A lineages roll up to one canonical door; the door's
    display name is the crosswalk canonical when resolved."""
    from tools.capability import match_capability
    from tools.entity_lineage import canonical_name, door_key, resolve_entity
    agg: dict[str, dict] = {}
    edges = results.get("subawards", {}).get("edges") or {}
    for rows in edges.values():
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict) or not r.get("prime"):
                continue
            m = match_capability(
                profile, r.get("description") or "",
                component=f"{r.get('awarding_sub_agency') or ''} "
                          f"{r.get('awarding_agency') or ''}")
            if m.score < 1:
                continue
            raw = str(r["prime"])
            cid = resolve_entity(raw)
            a = agg.setdefault(door_key(raw), {
                "name": (canonical_name(cid) if cid else None) or raw,
                "matched_count": 0, "matched_total": 0.0,
                "terms": set(), "score": 0, "snippets": []})
            a["matched_count"] += 1
            a["matched_total"] += r.get("amount") or 0
            a["terms"].update(m.matched)
            a["score"] = max(a["score"], m.score)
            if len(a["snippets"]) < 2 and r.get("description"):
                a["snippets"].append(str(r["description"])[:110])
    out = sorted(agg.values(), key=lambda x: -x["matched_total"])
    for x in out:
        x["terms"] = sorted(x["terms"])
    return out


def _teaming_facts(results: dict, c: _Counter, profile=None) -> list[Fact]:
    """Subaward prime rankings -> competitor/teaming facts.

    EVIDENCE INVERSION (2026-07-09, permanent): a prime may be named a
    teaming target ONLY from subaward records whose DESCRIPTIONS match the
    client's capability terms, ranked by capability-matched dollars. Zero
    capability-verified primes is a FINDING (the Stated Intent pattern);
    lane evidence then survives only under the mandatory, un-suppressible
    label. Superlative and lineage discipline unchanged."""
    from tools.entity_lineage import lineage_of
    subs = results.get("subawards") or {}
    facts: list[Fact] = []
    if not isinstance(subs, dict):
        return facts

    if profile is not None:
        ranked = _capability_prime_ranking(results, profile)
        n_edges = sum(len(v) for v in (subs.get("edges") or {}).values()
                      if isinstance(v, list))
        if ranked:
            m = len(ranked)
            for i, a in enumerate(ranked[:8]):
                from tools.capability import TermMatch
                chip = TermMatch(score=a["score"], tier="core").chip
                snip = "; ".join(a["snippets"])
                facts.append(Fact(
                    id=c.next(), kind="competitor",
                    text=(f"CAPABILITY-VERIFIED teaming door: {a['name']} holds "
                          f"{a['matched_count']} capability-matched subaward(s) "
                          f"totaling {_fmt_money(a['matched_total'])}, described "
                          f"as: '{snip}' (matched terms: "
                          f"{', '.join(a['terms'][:5])}; capability score "
                          f"{a['score']}, {chip}); ranked #{i + 1} of {m} by "
                          f"capability-matched dollars."),
                    value={**a, "capability_score": a["score"], "chip": chip},
                    source="https://api.usaspending.gov/api/v2/search/spending_by_award/ (subawards=true, description-matched)",
                ))
            return facts
        facts.append(Fact(
            id=c.next(), kind="competitor",
            text=(f"FINDING: zero of {n_edges} subaward records in the boundary "
                  f"lanes match the client's capability vocabulary; no prime "
                  f"clears capability verification this sweep. Teaming interest "
                  f"in these lanes is unproven at the description level; any "
                  f"lane-ranked prime below is {LANE_ONLY_LABEL}."),
            value={"capability_matched_primes": 0, "edges_screened": n_edges},
            source="https://api.usaspending.gov/api/v2/search/spending_by_award/ (subawards=true, description-matched)",
        ))
        # fall through: lane evidence survives ONLY under the mandatory label

    primes = sorted((subs.get("primes") or [])[:8],
                    key=lambda p: p.get("total") or 0, reverse=True)
    listed_names = [str(p.get("name") or "") for p in primes]
    n = len(primes)
    for i, p in enumerate(primes):
        rank = ("the LARGEST subaward conduit in the client's lanes"
                if i == 0 else f"ranked #{i + 1} of {n} by subaward dollars")
        note = ""
        lin = lineage_of(str(p.get("name") or ""))
        if lin and any(lin[0].lower() in other.lower()
                       for other in listed_names if other != p.get("name")):
            note = (f" LINEAGE NOTE: {lin[1]}; never present these records as "
                    f"a teaming door separate from {lin[0]}.")
        lane_label = f" {LANE_ONLY_LABEL.upper()}." if profile is not None else ""
        facts.append(Fact(
            id=c.next(), kind="competitor",
            text=f"{p.get('name')} pushed {_fmt_money(p.get('total'))} across "
                 f"{p.get('subaward_count')} subawards in the client's NAICS lanes "
                 f"(trailing 24 months); {rank}; teaming target.{note}{lane_label}",
            value=p,
            source="https://api.usaspending.gov/api/v2/search/spending_by_award/ (subawards=true)",
        ))
    # post-merge leaderboard: when listed primes share one corporate door,
    # publish the combined figure and the resulting order ONCE, citable.
    # Without it, '#2 of 8' begs the question of the unnamed #1 — and when
    # #1 merges with another entry, the per-entity order misleads (2026-07-09
    # review: Perspecta $938.7M + Peraton is one door, ~$1.5B, altering ranks).
    if primes:
        from tools.entity_lineage import canonical_name, resolve_entity

        def _door(nm: str) -> str:
            cid = resolve_entity(nm)
            return (canonical_name(cid) or nm) if cid else nm

        doors: dict[str, list[dict]] = {}
        for p in primes:
            doors.setdefault(_door(str(p.get("name") or "")), []).append(p)
        if any(len(v) > 1 for v in doors.values()):
            board = sorted(doors.items(),
                           key=lambda kv: -sum(x.get("total") or 0 for x in kv[1]))
            lines = "; ".join(
                f"#{i + 1} {door} at {_fmt_money(sum(x.get('total') or 0 for x in v))}"
                + (f" (one corporate door: {' + '.join(str(x.get('name')) for x in v)})"
                   if len(v) > 1 else "")
                for i, (door, v) in enumerate(board[:4]))
            board_label = (f" {LANE_ONLY_LABEL.upper()}."
                           if profile is not None else "")
            facts.append(Fact(
                id=c.next(), kind="competitor",
                text=("Subaward-door leaderboard with corporate lineage merged "
                      f"(trailing 24 months): {lines}. Cite THIS fact when ranking "
                      f"teaming doors; the per-entity ranks above are pre-merge."
                      f"{board_label}"),
                value={"doors": [{"door": d, "total": sum(x.get("total") or 0 for x in v),
                                  "entities": [x.get("name") for x in v]}
                                 for d, v in board]},
                source="https://api.usaspending.gov/api/v2/search/spending_by_award/ (subawards=true)",
            ))
    return facts


def _recompete_facts(results: dict, c: _Counter, profile=None,
                     focus=None) -> list[Fact]:
    """Expiring contracts -> award-period and completion signals.

    EVIDENCE INVERSION (2026-07-09): with a capability profile, an expiring
    award enters the calendar ONLY if its description matches capability
    terms OR its awarding component is a mission component. Everything else
    is CUT, not demoted. Rows without description evidence survive only on a
    mission-component match, and say so. Current SAM Contract Awards records
    carry the official coreData title when that section is returned."""
    facts: list[Fact] = []
    src = "https://api.sam.gov/contract-awards/v1/search (SAM Contract Awards API)"
    awards = results.get("contract_awards") or {}
    if not isinstance(awards, dict):
        awards = {}

    def record_source(row: dict) -> str:
        if row.get("url"):
            return str(row["url"])
        if row.get("record_source") == "usaspending.gov":
            return "https://api.usaspending.gov/api/v2/search/spending_by_award/"
        return src

    awards_source = str(
        (awards.get("provenance") or {}).get("endpoint") or src
    )

    if profile is not None:
        from tools.capability import match_capability

        # Standing award-period calendar (L16): the productized expiry schedule
        # feeds the same beat the ad-hoc pulls fed. Top attack rows only,
        # score decomposition citable, dollars labeled obligated. PROGRAM
        # tier: expiry math never claims a notice exists.
        from tools.api.recompete import load_calendar
        cal = load_calendar(profile.client_name)
        # FOCUS boundary (2026-07-12): the standing calendar is client-keyed
        # and government-wide by design; a scoped build keeps only rows whose
        # awarding identity matches the gate (the DISA/Army/Navy-in-DHS leak
        # rode this disk read past every results projection).
        attack_rows = ((cal or {}).get("attack") or [])
        if focus:
            from tools.agency_scope import value_focus_violation
            attack_rows = [r for r in attack_rows
                           if not value_focus_violation(r, focus)]
        for r in attack_rows[:5]:
            if not r.get("score"):
                continue
            why = "; ".join(d["evidence"] for d in
                            (r.get("score_decomposition") or [])[:3])
            facts.append(Fact(
                id=c.next(), kind="opportunity", tier="program",
                text=(f"AWARD-PERIOD CALENDAR SIGNAL: "
                      f"{r.get('recipient')}'s award "
                      f"{r.get('award_id')} at "
                      f"{r.get('awarding_office') or r.get('awarding_agency')} "
                      f"ends {r.get('pop_end')} "
                      f"({_fmt_money(r.get('obligated'))} obligated to date); "
                      "this period end is not a confirmed recompete; "
                      f"account relevance {r.get('score')}/100 ({why})."),
                value={k: r.get(k) for k in
                       ("award_id", "recipient", "recipient_door", "pop_end",
                        "obligated", "potential_ceiling", "score",
                        "score_decomposition", "naics", "psc", "award_type")},
                source=str(r.get("url") or
                           "https://api.usaspending.gov/api/v2/search/spending_by_award/"),
            ))

        rows = (results.get("expiring_awards") or {}).get("rows") or []
        kept = []
        for r in rows:
            comp = f"{r.get('awarding_sub_agency') or ''} {r.get('awarding_agency') or ''}"
            m = match_capability(profile, r.get("description") or "",
                                 component=comp)
            comp_hit = m.mission_hit or next(
                (mc for mc in profile.mission_components
                 if mc.lower() in comp.lower()), "")
            if m.score >= 1 or comp_hit:
                kept.append((r, m, comp_hit))
        for r, m, comp_hit in kept[:10]:
            why = (f"matched terms: {', '.join(m.matched[:4])}" if m.matched
                   else f"mission component: {comp_hit}")
            facts.append(Fact(
                id=c.next(), kind="opportunity",
                text=(f"CAPABILITY-VERIFIED award-period signal: "
                      f"{r.get('recipient')}'s "
                      f"award {r.get('award_id')} at "
                      f"{r.get('awarding_sub_agency') or r.get('awarding_agency')} "
                      f"ends {r.get('end_date')} "
                      f"({_fmt_money(r.get('amount'))}); described as "
                      f"'{str(r.get('description') or '')[:110]}' ({why}; "
                      f"capability score {m.score}). The period end is not "
                      "a confirmed recompete."),
                value={**r, "capability_score": m.score,
                       "matched_terms": m.matched, "mission_hit": comp_hit},
                source=str(r.get("url") or
                           "https://api.usaspending.gov/api/v2/search/spending_by_award/"),
            ))
        for r in (awards.get("recompetes") or []):
            ag = r.get("agency") or ""
            description = r.get("description") or ""
            match = match_capability(profile, description, component=ag)
            hit = next((mc for mc in profile.mission_components
                        if mc.lower() in ag.lower()), "")
            if not (r.get("completion") and (match.score >= 1 or hit)):
                continue
            piid = f" (PIID {r['piid']})" if r.get("piid") else ""
            if match.score >= 1:
                basis = (
                    f"official award title/description matched "
                    f"{', '.join(match.matched[:4])} "
                    f"(capability score {match.score})"
                )
            else:
                basis = (
                    f"kept on mission component '{hit}' (this record carries "
                    "no capability-matching description evidence)"
                )
            facts.append(Fact(
                id=c.next(), kind="opportunity",
                text=(f"Capability-verified contract-completion signal: "
                      f"{r.get('awardee')}'s "
                      f"contract{piid} at {ag} completes {r['completion']}; "
                      "this completion date is not a confirmed recompete; "
                      f"{basis}."),
                value={**r, "mission_hit": hit,
                       "capability_score": match.score,
                       "matched_terms": match.matched},
                source=record_source(r),
            ))
        if not facts:
            facts.append(Fact(
                id=c.next(), kind="opportunity",
                text=("FINDING: zero expiring awards in the boundary lanes "
                      "clear capability or mission-component verification "
                      "this window; the award-period and contract-completion "
                      "calendar is empty at the "
                      "evidence level, not merely quiet."),
                value={"capability_matched_expirations": 0},
                source=awards_source,
            ))
        return facts

    # legacy path (no profile): lane-level calendar, unlabeled
    for r in (awards.get("recompetes") or [])[:10]:
        if not r.get("completion"):
            continue
        agency = f" at {r['agency']}" if r.get("agency") else ""
        piid = f" (PIID {r['piid']})" if r.get("piid") else ""
        facts.append(Fact(
            id=c.next(), kind="opportunity",
            text=(f"Contract-completion signal: {r.get('awardee')}'s "
                  f"contract{piid}{agency} in the client's NAICS lanes "
                  f"completes {r['completion']}; this completion date is not "
                  "a confirmed recompete."),
            value=r, source=record_source(r),
        ))
    for inc in (awards.get("incumbents") or [])[:6]:
        facts.append(Fact(
            id=c.next(), kind="competitor",
            text=f"Incumbent: {inc.get('name')} holds {inc.get('contracts')} contract(s) "
                 f"in the client's NAICS lanes; earliest expiry {inc.get('next_expiry')}.",
            value=inc, source=awards_source,
        ))
    return facts


def _funded_demand_facts(results: dict, c: _Counter) -> list[Fact]:
    """Funded-demand scan (govinfo budget/appropriations text) -> citable
    context facts. Document-level citations; the dollar-line extraction
    limitation is recorded, never papered over."""
    fd = results.get("funded_demand") or {}
    facts: list[Fact] = []
    for term, pkgs in (fd.get("hits") or {}).items():
        p0 = pkgs[0]
        facts.append(Fact(
            id=c.next(), kind="context",
            text=(f"FUNDED-DEMAND SIGNAL: '{term}' appears in "
                  f"{len(pkgs)} budget/appropriations document(s), most "
                  f"recently '{(p0.get('title') or '')[:90]}' "
                  f"({p0.get('collection')}, {p0.get('date')}). Document-"
                  f"level citation; dollar-line extraction is a recorded "
                  f"limitation."),
            value={"term": term, "documents": pkgs},
            source=str(p0.get("url") or "https://www.govinfo.gov"),
        ))
    return facts


def _buyer_map_facts(results: dict, profile, c: _Counter) -> list[Fact]:
    """Incumbent buyer map -> citable facts (2026-07-09, the centerpiece
    layer). One fact per significant buyer row; every displacement window
    (PoP end inside 18 months) is its own fact; sole-source notice language
    rides with its buyer so the report can quote what would break the sole
    source."""
    bm = results.get("incumbent_buyer_map") or {}
    facts: list[Fact] = []
    if not isinstance(bm, dict) or not bm.get("buyers"):
        return facts
    label = bm.get("population_label") or "description-matched records"
    for b in (bm.get("buyers") or [])[:12]:
        if not b.get("products") and not b.get("sole_source_notices"):
            continue
        prods = ", ".join(b.get("products") or b.get("terms")[:3])
        pop = (f"; next period-of-performance end {b['next_pop_end']}"
               if b.get("next_pop_end") else "")
        win = (" DISPLACEMENT WINDOW: that end date is inside 18 months."
               if b.get("displacement_window") else "")
        ss = ""
        if b.get("sole_source_notices"):
            n0 = b["sole_source_notices"][0]
            ss = (f" Sole-source trail: {len(b['sole_source_notices'])} "
                  f"notice(s), e.g. '{(n0.get('title') or '')[:70]}' "
                  f"({n0.get('type')}, sam.gov {n0.get('notice_id')}); "
                  f"language: '{(n0.get('quote') or '')[:130]}'.")
        rec0 = (b.get("records") or [{}])[0]
        facts.append(Fact(
            id=c.next(), kind="incumbent_product",
            text=(f"INCUMBENT BUYER MAP: {b['buyer']} shows "
                  f"{_fmt_money(b['total'])} in {label} for {prods}"
                  f"{pop}.{win}{ss} Largest matched record: "
                  f"{rec0.get('recipient')} {_fmt_money(rec0.get('amount'))} "
                  f"'{(rec0.get('description') or '')[:80]}'."),
            value={**{k: b.get(k) for k in
                      ("buyer", "agency", "products", "terms", "total",
                       "next_pop_end", "displacement_window", "sole_source")},
                   "records": b.get("records"),
                   "sole_source_notices": b.get("sole_source_notices"),
                   "retrieved_at": bm.get("retrieved_at")},
            source=str((b.get("records") or [{}])[0].get("url")
                       or bm.get("source") or "https://api.usaspending.gov"),
        ))
    return facts


def _incumbent_product_facts(results: dict, profile, c: _Counter) -> list[Fact]:
    """Buyer -> incumbent product -> contracting office. The standing
    collector version of the layer that found the CBP/AMO cluster
    (2026-07-09): named competitor/incumbent products are searched across
    SAM notice titles/descriptions and award descriptions, and every hit is
    a citable fact with its own id, never a side effect of notice
    screening."""
    from tools.capability import incumbent_hits, match_capability
    facts: list[Fact] = []
    clusters: dict[tuple, dict] = {}
    for n in results.get("sam.gov") or []:
        rp = n.get("raw_payload") or {}
        text = f"{n.get('title') or ''} {rp.get('description_snippet') or ''}"
        hits = incumbent_hits(profile, text)
        if not hits:
            continue
        buyer = rp.get("subtier") or rp.get("agency") or "unknown buyer"
        office = rp.get("office") or ""
        key = (buyer, office)
        cl = clusters.setdefault(key, {"products": set(), "notices": []})
        cl["products"].update(hits)
        cl["notices"].append({
            "notice_id": n.get("source_id"), "title": n.get("title"),
            "type": rp.get("type"), "url": n.get("api_url") or rp.get("url")})
    for (buyer, office), cl in clusters.items():
        m = match_capability(profile, " ".join(
            x["title"] or "" for x in cl["notices"]), component=buyer)
        titles = "; ".join((x["title"] or "")[:60] for x in cl["notices"][:3])
        ids = ", ".join(str(x["notice_id"]) for x in cl["notices"][:3])
        facts.append(Fact(
            id=c.next(), kind="incumbent_product",
            text=(f"INCUMBENT-PRODUCT SIGNAL: {buyer}"
                  f"{' (' + office + ')' if office else ''} buys "
                  f"{', '.join(sorted(cl['products']))}: "
                  f"{len(cl['notices'])} notice(s) [{titles}] (sam.gov {ids}). "
                  f"Buyer -> incumbent product -> contracting office; "
                  f"capability score {m.score}, {m.chip}."),
            value={"buyer": buyer, "office": office,
                   "products": sorted(cl["products"]),
                   "notices": cl["notices"],
                   "capability_score": m.score, "chip": m.chip},
            source=str(cl["notices"][0].get("url") or "https://sam.gov"),
        ))
    return facts


def _threat_context_facts(results: dict, c: _Counter) -> list[Fact]:
    """CISA KEV velocity -> the government's own case for live threat intel."""
    kev = results.get("cisa_kev") or {}
    facts: list[Fact] = []
    if not isinstance(kev, dict) or kev.get("error") or not kev.get("recent_count"):
        return facts
    facts.append(Fact(
        id=c.next(), kind="context",
        text=f"CISA added {kev['recent_count']} actively exploited vulnerabilities to the "
             f"KEV catalog in the last {kev.get('window_days')} days ({kev.get('total')} "
             f"total under federal remediation mandate, BOD 22-01).",
        value={"recent_sample": kev.get("recent_sample")},
        source=kev.get("catalog_url") or "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
    ))
    for v in (kev.get("matched") or [])[:4]:
        facts.append(Fact(
            id=c.next(), kind="context",
            text=f"KEV entry in the client's domain: {v.get('cve')} "
                 f"({v.get('vendor')} {v.get('product')}), added {v.get('added')}"
                 + (", known ransomware campaign use" if str(v.get('ransomware', '')).lower().startswith('known') else "") + ".",
            value=v,
            source=kev.get("catalog_url") or "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
        ))
    return facts


def client_footprint(results: dict) -> Optional[dict]:
    """The client's OWN federal awards (results['client_awards']) as INTERNAL
    context; deliberately NOT FactPack facts. FactPack facts become client
    copy, and the client is never told what they already know; the footprint
    feeds the review layer's judgment (which buyers are proven, what the
    record supports) and stays in our own logic."""
    ca = results.get("client_awards") or {}
    rows = ca.get("rows") or []
    if not rows:
        return None
    return {
        "note": ("INTERNAL reasoning only; sharpens judgment and emphasis; "
                 "the composer must never restate the client's own contracts "
                 "or record back to them"),
        "total_amount": ca.get("total_amount"),
        "awards": [{k: r.get(k) for k in
                    ("award_id", "amount", "awarding_agency",
                     "awarding_sub_agency", "start_date", "end_date")}
                   for r in rows[:10]],
    }


def client_invariance_check(results: dict, profile) -> list[str]:
    """REGRESSION PROOF (2026-07-09, runs on every report build): capability-
    verified findings must be client-SPECIFIC. The same evidence layers are
    rebuilt under a synthetic dummy profile (different capability vocabulary,
    same NAICS boundary); any capability-verified teaming target, verified
    expiration, or incumbent-product signal shared with identical evidence
    came from the lane layer, which means it is not client intelligence."""
    from tools.capability import CapabilityTerms, ClientProfile
    dummy = ClientProfile(
        client_name="__invariance_dummy__",
        capability_terms=CapabilityTerms(
            core=["quantum lattice cryptography widget"],
            adjacent=["hyperspectral blockchain telemetry"],
            excluded=[]),
        named_competitors_and_incumbents=["NoSuchProductXyz"],
        mission_components=[],
        naics_boundary=list(profile.naics_boundary),
    )

    def _verified(p) -> dict[tuple, str]:
        """EVIDENCE keys, not rendered text: the same prime with the same
        matched records is the same finding no matter which vocabulary hit."""
        out: dict[tuple, str] = {}
        for f in _teaming_facts(results, _Counter(), p):
            if f.text.startswith("CAPABILITY-VERIFIED"):
                v = f.value or {}
                out[("teaming", v.get("name"), v.get("matched_count"),
                     round(v.get("matched_total") or 0))] = f.text
        for f in _recompete_facts(results, _Counter(), p):
            if f.text.startswith("CAPABILITY-VERIFIED"):
                out[("recompete", (f.value or {}).get("award_id"))] = f.text
        for f in _incumbent_product_facts(results, p, _Counter()):
            v = f.value or {}
            out[("incumbent", v.get("buyer"), v.get("office"),
                 tuple(sorted(n.get("notice_id") or ""
                              for n in v.get("notices") or [])))] = f.text
        return out

    a, b = _verified(profile), _verified(dummy)
    return [f"client-invariant finding (lane-layer leak): {a[k][:140]}"
            for k in sorted(set(a) & set(b), key=str)]


def build_fact_pack(
    client_name: str,
    searches: Optional[dict] = None,
    qualify_report: Optional[dict] = None,
    as_of: Optional[date] = None,
    profile=None,
) -> FactPack:
    """Assemble the FactPack from artifacts (pass dicts directly, or load from disk).

    With a capability profile (loaded automatically from
    clients/<slug>/profile.json), the EVIDENCE INVERSION applies: teaming,
    recompete, and incumbent-product facts are description-verified; NAICS
    lanes are boundary context only. Without a profile the legacy lane
    builders run and the pack carries a loud warning."""
    if searches is None:
        from agents.review import sweep_artifact_path
        path = sweep_artifact_path(client_name)  # L19: loud when a scoped gate has no artifact
        searches = json.load(open(path)) if os.path.exists(path) else {}
    if qualify_report is None:
        path = os.path.join(_REVIEW, f"{_slug(client_name)}.qualify.json")
        qualify_report = json.load(open(path)) if os.path.exists(path) else {}
    if profile is None:
        from tools.capability import load_profile
        profile = load_profile(client_name)
    if as_of is None:
        from agents.assess.live_report import utc_today
        fact_as_of = date.today()
        deadline_cutoff = utc_today()
    else:
        fact_as_of = as_of
        deadline_cutoff = as_of

    c = _Counter()
    # FOCUS projection (2026-07-12): same boundary as build_document; facts
    # from whole-market context layers must never mint a foreign agency's
    # row into a scoped deliverable (the DISA-in-DHS leak).
    from tools.agency_scope import scope_focus_results
    results = scope_focus_results(searches)
    evidence = results.get("usaspending.gov") or []
    market, warnings = _market_facts(evidence, c)
    competitors = _competitor_facts(evidence, c) + _teaming_facts(results, c, profile)
    from tools.agency_scope import focus_agencies, value_focus_violation
    focus = focus_agencies(searches)
    recompetes = _recompete_facts(results, c, profile, focus=focus)
    from agents.assess.live_report import (
        LiveReportState, resolve_current_live_report,
    )
    live_report = resolve_current_live_report(
        client_name, searches, profile, effective_date=deadline_cutoff)
    if live_report.state == LiveReportState.CURRENT:
        live_opps = _ledger_opportunity_facts(live_report, c)
        strict_monitors = True
    elif live_report.state == LiveReportState.INVALID:
        live_opps = []
        strict_monitors = True
        warnings.append(
            "strict Live SAM cutover invalid; live facts held closed: "
            + (live_report.problem or "current ledger validation failed"))
    else:
        live_opps = _opportunity_facts(
            qualify_report.get("candidates") or [], c)
        strict_monitors = False
    opps = live_opps + recompetes
    incumbents = ((_incumbent_product_facts(results, profile, c)
                   + _buyer_map_facts(results, profile, c))
                  if profile is not None else [])
    monitors = (_ledger_monitor_facts(live_report, c)
                if strict_monitors else _monitor_facts(results, c))
    signals = (_signal_facts(results, c) + _threat_context_facts(results, c)
               + _funded_demand_facts(results, c))

    # evidence tiering (L15): expiry math, buyer maps, and forecast-derived
    # material are PROGRAM-tier (stable agency/USASpending records, forming
    # sections only); notice-derived opportunity facts are NOTICE-tier (live,
    # re-verification-gated). A tier travels with the fact so report language
    # can be lint-checked against it.
    # FAIL-CLOSED focus gate (2026-07-12): no affirmative opportunity-shaped
    # fact may carry an out-of-gate agency, whatever path minted it. Rejected
    # facts surface as loud warnings, never silently.
    if focus:
        def _guard(facts, surface):
            kept = []
            for fact in facts:
                bad = value_focus_violation(fact.value, focus)
                if bad:
                    warnings.append(
                        f"focus boundary rejected an out-of-gate {surface} "
                        f"fact ({bad})")
                    continue
                kept.append(fact)
            return kept
        # every affirmative surface the operator can act on: opportunities
        # (incl. recompetes), buyer/incumbent, and budget/demand signals
        opps = _guard(opps, "opportunity")
        incumbents = _guard(incumbents, "buyer")
        signals = _guard(signals, "signal")
    for f in recompetes + incumbents + signals:
        f.tier = f.tier or "program"
    for f in opps:
        f.tier = f.tier or "notice"

    # Per-figure freshness (2026-07-16): every fact carries where and WHEN its
    # backing record was pulled. The sweep artifact's `generated_at` is the
    # run-metadata recovery path for records without a closer stamp; a legacy
    # artifact without it marks its facts UNKNOWN_FRESHNESS, which the release
    # gate fails closed rather than letting a June figure ride a July date.
    default_retrieved = _parse_retrieved((searches or {}).get("generated_at"))
    all_facts = market + competitors + opps + incumbents + monitors + signals
    _stamp_provenance(all_facts, default_retrieved)

    if not opps:
        if live_report.state == LiveReportState.CURRENT:
            warnings.append(
                "no BID_NOW records cleared the strict Live SAM ledger")
        elif live_report.state == LiveReportState.ABSENT:
            warnings.append("no verified opportunities in the qualify report")
    if profile is None:
        warnings.append(
            "NO CAPABILITY PROFILE (clients/<slug>/profile.json): teaming and "
            "recompete evidence is lane-level only and client-invariant")

    pack = FactPack(
        client_name=client_name,
        as_of=fact_as_of,
        facts=all_facts,
        market_fact_ids=[f.id for f in market],
        competitor_fact_ids=[f.id for f in competitors],
        opportunity_fact_ids=[f.id for f in opps],
        monitor_fact_ids=[f.id for f in monitors],
        incumbent_product_fact_ids=[f.id for f in incumbents],
        warnings=warnings,
    )
    return pack
