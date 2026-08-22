"""Seed-author lane for Candidate Review v1 (Chunk 6, Pass D).

Turns verified evidence + precomputed anchor offers into ``CandidateSeed``
values.  The DEFAULT path is DETERMINISTIC and needs no model: every
anchor-eligible evidence record becomes a candidate seed whose lifecycle,
boundary, dates, and rank signals are derived from the record itself, and whose
narrative is a safe, non-dispositive template.  This is what keeps the report
from being sparse without any LLM spend.

An optional Claude Max adapter (next pass) may replace the template narrative
with client-specific, evidence-grounded prose after a deterministic grounding
battery re-checks it; it never decides lifecycle, dates, money, or ids.  Passing
an adapter raises until that pass lands, so the lane never emits unauthored or
ungrounded prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Protocol

from agents.candidate_review_v1.candidate_engine import CandidateRankSignals, CandidateSeed
from agents.candidate_review_v1.contracts import (
    CONTENT_BUDGETS,
    ArtifactBinding,
    DateKind,
    DatePrecision,
    DateStatus,
    DateValue,
    DecisionBoundary,
    EvidenceAssertion,
    EvidenceBoundClaim,
    EvidenceKind,
    EvidenceRecord,
    ExecutionFramework,
    LifecycleKind,
    MoneyBasis,
    MoneyValue,
    SearchConcepts,
)
from agents.candidate_review_v1.verification import AnchorOffer

AUTHORING_SCHEMA_VERSION = "candidate_review_v1.authoring.v1"

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_INDEX = {name: index + 1 for index, name in enumerate(_MONTHS)}
_DATE_RE = re.compile(
    r"(" + "|".join(m.capitalize() for m in _MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})")
_DEADLINE_RE = re.compile(r"\b(due|response|responses due|closes|closing)\b", re.I)
_MONEY_RE = re.compile(r"\$\s?([0-9]+(?:\.[0-9]+)?)\s?([bmk])\b", re.I)
_RESPONSE_DEADLINE_RE = re.compile(
    r"[^.]*\b(?:responses?|proposals?|offers?|submissions?|bids?|quotes?)\b"
    r"[^.]*\b(?:due|deadline|closing|closes?|close date|cutoff)\b[^.]*\d{4}",
    re.I)
_MONEY_SCALE = {"b": Decimal("1000000000"), "m": Decimal("1000000"), "k": Decimal("1000")}
_NOTICE_LIFECYCLES = {
    LifecycleKind.MARKET_RESEARCH,
    LifecycleKind.PRESOLICITATION,
    LifecycleKind.LIVE_SOLICITATION,
}


@dataclass(frozen=True)
class AuthoringDrop:
    anchor_evidence_id: str
    stage: str
    reason: str


@dataclass(frozen=True)
class AuthoringResult:
    seeds: tuple[CandidateSeed, ...] = ()
    pattern_claims: tuple[EvidenceBoundClaim, ...] = ()
    market_signals: tuple[EvidenceBoundClaim, ...] = ()
    past_awards: tuple[EvidenceBoundClaim, ...] = ()
    search_concepts: Optional[SearchConcepts] = None
    execution_framework: Optional[ExecutionFramework] = None
    drops: tuple[AuthoringDrop, ...] = ()
    cache_hit: bool = False
    provider_calls: int = 0


class SeedAuthorAdapterLike(Protocol):
    model: str

    def call(self, request: object) -> object:
        ...


class SeedAuthorLaneNotImplemented(NotImplementedError):
    """An adapter was supplied but the Max-plan authoring path is not built yet."""


def author_candidate_review(
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    evidence: tuple[EvidenceRecord, ...],
    anchor_offers: tuple[AnchorOffer, ...],
    adapter: Optional[SeedAuthorAdapterLike] = None,
    cache: object = None,
    max_candidates: int = CONTENT_BUDGETS["candidates"],
) -> AuthoringResult:
    """Author candidate seeds from verified evidence.

    Default (``adapter=None``): deterministic, offline, no model calls.
    """

    if adapter is not None:
        raise SeedAuthorLaneNotImplemented(
            "author_candidate_review Max-plan enrichment path is not implemented "
            "yet; run the deterministic default (adapter=None)")

    evidence_by_id = {record.evidence_id: record for record in evidence}
    seeds: list[CandidateSeed] = []
    drops: list[AuthoringDrop] = []

    # Deterministic order: anchor offers by evidence_id.
    for offer in sorted(anchor_offers, key=lambda o: o.evidence_id):
        record = evidence_by_id.get(offer.evidence_id)
        if record is None:
            drops.append(AuthoringDrop(offer.evidence_id, "resolve", "anchor evidence absent"))
            continue
        if not offer.can_anchor or not record.official_source or not record.primary_source:
            drops.append(AuthoringDrop(offer.evidence_id, "admissible", "not an eligible anchor"))
            continue
        seed = _seed_from_anchor(record, offer, as_of)
        if seed is None:
            drops.append(AuthoringDrop(offer.evidence_id, "compose", "could not derive a valid seed"))
            continue
        seeds.append(seed)
        if len(seeds) >= max_candidates:
            break

    return AuthoringResult(seeds=tuple(seeds), drops=tuple(drops))


# --------------------------------------------------------------------------- #

def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "record"


def _agency(record: EvidenceRecord) -> str:
    if record.issuing_office:
        return record.issuing_office
    head = record.title.split(" - ")[0].split(":")[0].strip()
    return head or record.source_name


def _short(value: str, limit: int = 90) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit].rstrip() + "..."


def _first_date(text: str) -> Optional[tuple[str, date]]:
    for match in _DATE_RE.finditer(text):
        month = _MONTH_INDEX[match.group(1).casefold()]
        try:
            return match.group(0), date(int(match.group(3)), month, int(match.group(2)))
        except ValueError:
            continue
    return None


def _assertion_quote(record: EvidenceRecord, assertion: EvidenceAssertion) -> Optional[str]:
    for span in record.assertion_spans:
        if span.assertion == assertion:
            return span.quote
    return None


def _parse_money(excerpt: str) -> Optional[Decimal]:
    match = _MONEY_RE.search(excerpt)
    if match is None:
        return None
    return (Decimal(match.group(1)) * _MONEY_SCALE[match.group(2).casefold()]).quantize(
        Decimal("1"))


def _fragment(excerpt: str, span_text: str, keyword_re: re.Pattern) -> Optional[str]:
    """A short excerpt slice holding both a role keyword and the date (Path B)."""

    idx = excerpt.find(span_text)
    if idx < 0:
        return None
    window = excerpt[max(0, idx - 120):idx + len(span_text) + 4]
    return window if keyword_re.search(window) else None


def _date_value(record: EvidenceRecord, kind: DateKind, status: DateStatus,
                source_text: str, day: date) -> Optional[DateValue]:
    try:
        return DateValue(
            date_id=f"{record.evidence_id}:{kind.value}",
            label=kind.value.replace("_", " ").title(),
            kind=kind, status=status, precision=DatePrecision.DAY,
            source_text=source_text, sort_date=day, start=day,
            evidence_ids=(record.evidence_id,))
    except ValueError:
        return None


def _money(record: EvidenceRecord, amount: Decimal) -> Optional[MoneyValue]:
    try:
        return MoneyValue(
            money_id=f"{record.evidence_id}:obligated_to_date",
            basis=MoneyBasis.OBLIGATED_TO_DATE, amount=amount,
            as_of=record.effective_date or record.retrieved_at.date(),
            source_field="obligations_to_date", evidence_ids=(record.evidence_id,))
    except ValueError:
        return None


def _rank_signals(record: EvidenceRecord, as_of: datetime) -> CandidateRankSignals:
    authority = Decimal("0.9") if record.source_tier.value == "notice" else Decimal("0.7")
    reference = record.effective_date or record.published_date
    if reference is not None:
        age_days = (as_of.date() - reference).days
        recency = Decimal("0.9") if age_days <= 180 else (
            Decimal("0.6") if age_days <= 540 else Decimal("0.3"))
    else:
        recency = Decimal("0.5")
    return CandidateRankSignals(
        source_authority=authority,
        decision_specificity=Decimal("0.6"),
        recency=recency,
        timing_strength=Decimal("0.6"),
        capability_fit=Decimal("0.6"))


def _seed_from_anchor(record: EvidenceRecord, offer: AnchorOffer,
                      as_of: datetime) -> Optional[CandidateSeed]:
    # An anchor that confirms an open notice MUST become a current notice with a
    # fresh (<=24h) verification, or the engine refuses it -- it cannot demote to
    # a corridor.  A stale active notice is therefore dropped here; it needs
    # re-verification during the watch run, not a downgraded card.
    if offer.confirms_open_notice and not offer.notice_window_open:
        return None
    agency = _agency(record)
    current = offer.confirms_open_notice and offer.notice_window_open
    if current:
        lifecycle = LifecycleKind.LIVE_SOLICITATION
        corridor_id = None
        kind_word = "current federal notice"
    else:
        lifecycle = (LifecycleKind.RECOMPETE_RESEARCH
                     if record.source_kind == EvidenceKind.AWARD
                     else LifecycleKind.ACQUISITION_PLANNING)
        corridor_id = f"corridor:{_slug(agency)}-{_slug(_short(record.title, 40))}"
        kind_word = "evidence-backed research corridor"

    # Derive the typed facts the evidence proves.  Award-end clocks read the
    # AWARD_PERIOD_END span (Path A: the date must sit inside the span quote);
    # obligation money reads a dollar figure from the excerpt.
    dates: tuple[DateValue, ...] = ()
    money: tuple[MoneyValue, ...] = ()
    if record.source_kind == EvidenceKind.AWARD:
        quote = _assertion_quote(record, EvidenceAssertion.AWARD_PERIOD_END)
        if quote is not None:
            found = _first_date(quote)
            if found is not None:
                dv = _date_value(record, DateKind.AWARD_END_RESEARCH_CLOCK,
                                 DateStatus.RESEARCH_CLOCK, found[0], found[1])
                if dv is not None:
                    dates = (dv,)
        amount = _parse_money(record.excerpt)
        if amount is not None:
            mv = _money(record, amount)
            if mv is not None:
                money = (mv,)
    elif record.source_kind == EvidenceKind.NOTICE:
        # The published response deadline is calendar truth (2026-07-24):
        # read it from the deadline sentence the record itself prints, so
        # the Section 6 calendar and the decision boundary carry it.
        deadline_match = _RESPONSE_DEADLINE_RE.search(record.excerpt)
        if deadline_match is not None:
            found = _first_date(deadline_match.group(0))
            if found is not None:
                dv = _date_value(record, DateKind.RESPONSE_DEADLINE,
                                 DateStatus.CONFIRMED, found[0], found[1])
                if dv is not None:
                    dates = (dv,)

    boundary = DecisionBoundary(
        buyer_key=f"agency:{_slug(agency)}",
        deadline=dates[0].start if dates else None,
        eligibility_key="validate-from-source",
        lifecycle=lifecycle,
        program_key=f"program:{_slug(_short(record.title, 40))}",
        access_route_key="sam-notice" if current else "research",
        analyst_action_key="validate-requirement-and-route")

    try:
        return CandidateSeed(
            anchor_evidence_id=record.evidence_id,
            boundary=boundary,
            title=_short(record.title, 110),
            agency=_short(agency, 110),
            corridor_id=corridor_id,
            dates=dates,
            money=money,
            records_show=(
                f"An official {kind_word} from {_short(agency, 60)} documents "
                "this requirement and its current lifecycle."),
            may_suggest=(
                "The pattern may align with the reviewed capability set and the "
                "buying office; treat it as a lead, not a decision."),
            validate_next=(
                "Validate the complete requirement, timing, eligibility, and "
                "acquisition route against the primary record."),
            cluster_reason="This card represents one exact official record.",
            strategic_action=(
                "Review the requirement and confirm the acquisition access route."),
            distinctness_explanation=(
                "The buyer, timing, and access route form one distinct review action."),
            inference_chain=(
                "Official record to lifecycle assessment to analyst validation."),
            falsifier=(
                "The complete package shows no relevant requirement for this client."),
            watch_trigger=(
                "An official amendment or a new notice changes the scope or timing."),
            rank_signals=_rank_signals(record, as_of))
    except ValueError:
        return None


__all__ = (
    "AUTHORING_SCHEMA_VERSION",
    "AuthoringDrop",
    "AuthoringResult",
    "SeedAuthorAdapterLike",
    "SeedAuthorLaneNotImplemented",
    "author_candidate_review",
)
