"""Evidence-pack contracts for the golden press (GOLDEN_BUILD Phase 2).

One pack carries every retrieved record with full fields, the research-stage
entity output that drove retrieval, the exact per-lane API queries, and the
sufficiency verdict. The composer consumes the pack verbatim; the validator
re-derives every stated figure from it.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

Lane = Literal["L1_notice", "L2_entity_award", "L4_forecast"]


class GoldenRecord(BaseModel):
    """One deduplicated evidence record, in its best-fit lane."""

    record_id: str = Field(description="contract number / notice id / APFS number")
    lane: Lane
    research_clock: bool = Field(
        default=False,
        description="L3 flag: award period of performance ends within 18 months")
    title: str = ""
    agency: Optional[str] = None
    sub_agency: Optional[str] = None
    recipient: Optional[str] = None
    obligated_dollars: Optional[float] = None
    ceiling_dollars: Optional[float] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    potential_end_date: Optional[str] = None
    vehicle: Optional[str] = Field(
        default=None, description="parent IDV vehicle type or award type")
    parent_award_id: Optional[str] = None
    parent_award_agency: Optional[str] = Field(
        default=None,
        description="raw parent-agency segment parsed from the USAspending "
                    "CONT_AWD generated id (decision_rules, 2026-08-03); "
                    "None on packs written before vehicle derivation and on "
                    "records with no parseable award URL")
    vehicle_class: Optional[str] = Field(
        default=None,
        description="prefix-only vehicle classification from the parsed "
                    "parent id: SEWP V | GSA | HHS | DHS | definitive | "
                    "IDV (unclassified). No lookups, no web calls; "
                    "decision_rules.classify_vehicle is the one owner.")
    description: Optional[str] = None
    naics: Optional[str] = None
    psc: Optional[str] = None
    set_aside: Optional[str] = None
    competition: Optional[str] = Field(
        default=None,
        description="extent-competed description from the award detail "
                    "(audit 2026-07-30: fetched by award_repull and dropped "
                    "before the pack; sole-source/8(a) direct-award "
                    "intelligence lives in this field)")
    competition_code: Optional[str] = None
    office: Optional[str] = None
    notice_type: Optional[str] = Field(
        default=None,
        description="sam.gov notice type (Sources Sought, Special Notice, "
                    "Award Notice...). THE NOTICE STORE IS THE ONLY WRITER. "
                    "None on any pack written before 2026-07-28 and on any "
                    "notice the store never saw; such records stay unranked "
                    "and render exactly as they did before.")
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_secondary_email: Optional[str] = None
    contact_quality: Optional[str] = Field(
        default=None,
        description="named_individual | surname_only | shared_mailbox. "
                    "Derived from the address against the name the notice "
                    "already carries. surname_only is a REAL PERSON addressed "
                    "by surname and is never scored as shared.")
    contact_use: Optional[str] = Field(
        default=None,
        description="direct | formal_channel_only. Rank 1 and 2 are pre-"
                    "solicitation, where talking to the contracting officer "
                    "is the whole point. Rank 3 and below carry the contact "
                    "as ownership information ONLY: once a solicitation is "
                    "open, vendor communication belongs in the official Q&A "
                    "process, and handing a client a CO to cold-call "
                    "mid-procurement is advice that damages the client.")
    notice_leverage_rank: Optional[int] = Field(
        default=None,
        description="Derived from notice_type, never stored independently. "
                    "1 RFI/Sources Sought (requirement still being written), "
                    "2 Special Notice/Presolicitation, 3 Solicitation, "
                    "4 Award Notice and justifications, which are history and "
                    "NEVER eligible for a solicitation band. None means the "
                    "type is unknown; no rank is invented.")
    url: Optional[str] = None
    generated_internal_id: Optional[str] = None
    posted_date: Optional[str] = None
    response_deadline: Optional[str] = None
    estimated_value_range: Optional[str] = None
    anticipated_solicitation: Optional[str] = None
    anticipated_solicitation_close: Optional[str] = None
    anticipated_award: Optional[str] = None
    fiscal_year: Optional[str] = None
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    contact_publication_date: Optional[str] = None
    forecast_source: Optional[str] = None
    small_business_poc: Optional[str] = None
    source_fields: dict[str, Any] = Field(default_factory=dict)
    entity_hits: list[str] = Field(
        default_factory=list,
        description="which entity / capability search terms surfaced this record")
    relevance_method: Optional[str] = Field(
        default=None,
        description="tech_naics | tech_psc | term_cooccurrence | "
                    "sweep_capability_search | forecast_profile_match")
    relevance_matched: list[str] = Field(default_factory=list)
    retrieved_at: Optional[str] = None
    detail_enriched: bool = False
    forecast_score: Optional[float] = Field(
        default=None,
        description="L4 keyword/NAICS relevance score (score_forecast)")
    forecast_value: Optional[float] = Field(
        default=None,
        description="L4 rough dollar magnitude parsed from the range display")
    forecast_rarity: Optional[float] = Field(
        default=None,
        description="normalized-IDF of the rarest capability unigram the "
                    "record hits; the niche-slot rank (rare defining "
                    "vocabulary beats ubiquitous IT words)")


class LaneQuery(BaseModel):
    """The exact query one lane executed, kept verbatim for the checkpoint."""

    lane: Lane
    method: str
    endpoint: str
    body: dict[str, Any] = Field(default_factory=dict)
    executed_at: str = ""
    result_count: int = 0
    kept_after_screen: int = 0
    note: str = ""


class LaneStatus(BaseModel):
    lane: str
    status: Literal["live", "degraded", "skipped"]
    detail: str = ""


class Sufficiency(BaseModel):
    unique_records: int
    lanes_represented: list[str]
    met: bool
    escalated: bool = False


class EvidencePack(BaseModel):
    """The single JSON the composer reads and the validator re-derives from."""

    schema_version: int = SCHEMA_VERSION
    client_name: str
    generated_at: str
    lanes: list[LaneStatus] = Field(default_factory=list)
    queries: list[LaneQuery] = Field(default_factory=list)
    records: list[GoldenRecord] = Field(default_factory=list)
    research: dict[str, Any] = Field(
        default_factory=dict,
        description="research-stage output that drove retrieval: entities by "
                    "kind, capability terms, NAICS boundary")
    sufficiency: Optional[Sufficiency] = None
    scarcity_note: Optional[dict[str, Any]] = Field(
        default=None,
        description="machine-readable note for the composer when the gate is "
                    "unmet after escalation; never padding")
    selection: Optional[dict[str, Any]] = Field(
        default=None,
        description="no-silent-caps disclosure: how many relevance-screened "
                    "records each lane returned and what the pack kept, with "
                    "the deterministic selection rule named")
    events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="EVENTS_LANE (2026-07-27): verified events and industry "
                    "days for this client. Additive and legacy-safe (packs "
                    "written before the lane load empty). Events are a "
                    "SEPARATE surface: they never count toward the evidence "
                    "sufficiency gate and never enter the evidence dock.")
    events_screen: Optional[dict[str, Any]] = Field(
        default=None,
        description="events lane disclosure: candidates scanned, screened, "
                    "kept, deduped, and every URL skipped for failing live "
                    "verification (never approximated)")
    sam_lanes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="BRAND-NAME SAM LANES (2026-07-27): notices that name a "
                    "client or competitor product by name, from the free "
                    "daily Contract Opportunities extract plus the durable "
                    "ledger of earlier scans. Additive and legacy-safe. Each "
                    "hit carries strength=subject|mentioned: subject means "
                    "the product sits beside the brand-name or sole-source "
                    "language and IS the buy.")
    sam_lanes_receipt: Optional[dict[str, Any]] = Field(
        default=None,
        description="brand-name lane disclosure: every lane and every "
                    "searched product name with its count INCLUDING ZEROS, "
                    "so a client can tell a lane that found nothing from a "
                    "lane that never ran, plus the ledger carry-forward that "
                    "keeps notices sam.gov has already dropped")
    client_entity_aliases: list[str] = Field(
        default_factory=list,
        description="OPERATOR-EDITABLE. Other corporate names the client "
                    "trades under, used to resolve an awardee against the "
                    "client itself. Award data never states this: on the Red "
                    "Hat pack, DLT Solutions is TD SYNNEX Public Sector and "
                    "carries 44% of obligated dollars, which reads as a "
                    "third-party win until someone says otherwise here. "
                    "Empty means the client name alone.")
    notice_store: Optional[dict[str, Any]] = Field(
        default=None,
        description="Freshness of the durable notice store at press time: "
                    "last ingest date, rows held, and whether that ingest was "
                    "flagged suspect. A STALE STORE MUST BE VISIBLE IN THE "
                    "ARTIFACT rather than silent, because a lane reading a "
                    "week-old store looks exactly like a lane reading a "
                    "current one. None means the store was unreadable, which "
                    "is itself the disclosure.")
    pre_cap_aggregates: Optional[dict[str, Any]] = Field(
        default=None,
        description="THE ONLY HONEST CATEGORY FIGURES IN THE PACK. Computed "
                    "inside select_pack over the full CATEGORIZED segments "
                    "(deduplicated, in-scope, as they entered the selection "
                    "rule) before any display cap cuts them. Counted from "
                    "search-row fields, not detail-enriched. Distinct from "
                    "the SCREENED tally, which counts per query before "
                    "deduplication and double-counts. Packs written before "
                    "2026-07-28 carry None: their qualifying set is gone and "
                    "no offline computation can recover it.")
    rollups: Optional[dict[str, Any]] = Field(
        default=None,
        description="derived offline from this pack: category_spend, "
                    "prime_rollup, competitor_landscape. Every rollup carries "
                    "a coverage block naming what it summed; where the pack "
                    "holds only the capped display set these are totals over "
                    "CITED EVIDENCE and is_market_total is False.")
    decisions: Optional[dict[str, Any]] = Field(
        default=None,
        description="deterministic account-decision rule output "
                    "(decision_rules.build_decisions, 2026-08-03): R1 "
                    "head-to-head agency decisions, R2 displacement alerts "
                    "with verbatim sentence receipts, R3 vehicle-expiry "
                    "context, R4 qualify-only forecast adjacency, plus the "
                    "undated-record receipt. Computed at press time over "
                    "this pack and the durable notice store, stored here so "
                    "replay re-renders the same band from the same evidence "
                    "and the validator can derive its dates and ids. Packs "
                    "written before the decision band carry None; the band "
                    "then states that rules were not computed, which is "
                    "different from rules that ran and found nothing.")
    targeting: Optional[dict[str, Any]] = Field(
        default=None,
        description="deterministic targeting-rule output plus the stored "
                    "enrichment (targeting_rules.build_targeting, contract "
                    "amendment v1.4): one spec per R1 card, R2 alert and R4 "
                    "qualify row (buying component, persona tiers sought, "
                    "rationale, joined record ids), the persona ladder, the "
                    "rule receipt, and the contacts read FROM THE DURABLE "
                    "STORE at press time. Contacts are ENRICHMENT, never "
                    "federal records: nothing here enters the fact registry, "
                    "the evidence dock, or the primary link tally. Carried on "
                    "the pack so a saved-inputs replay re-renders Band 09 "
                    "from the same bytes and the press makes zero live "
                    "calls. Packs written before the targeting band carry "
                    "None; the band then states that the specs were not "
                    "computed, which is not a zero.")
