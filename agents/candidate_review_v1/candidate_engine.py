"""Deterministic Candidate Review v1 candidate and corridor engine.

This module turns a client/run/scope-bound evidence universe plus explicit
candidate seeds into two projections:

* a complete ranked inventory retained for audit and later analyst review; and
* a diversity-aware report projection bounded by the report content maximum.

The engine owns identity, grouping, traceability, and presentation order.  It
does not own pursue/no-bid treatment, pipeline value, win probability, or any
other business disposition.  Code, set-aside, value, incumbent, and fit
concerns therefore arrive only as rank signals or human-readable cautions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import re
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.candidate_review_v1.contracts import (
    CANDIDATE_ANCHOR_KINDS as CANDIDATE_ANCHOR_KINDS,
    CONTENT_BUDGETS,
    ArtifactBinding,
    CandidateKind,
    CandidateMember,
    CandidateUnit,
    ClusterLevel,
    DateKind,
    DateStatus,
    DateValue,
    DecisionBoundary,
    EvidenceAssertion,
    EvidenceKind,
    EvidenceRecord,
    LifecycleKind,
    MoneyBasis,
    MoneyValue,
    ProcurementFamilyIdentity,
    StrategicCorridorIdentity,
    assert_candidate_review_language,
    evidence_supports_date_value,
)


_NOTICE_LIFECYCLES = frozenset({
    LifecycleKind.MARKET_RESEARCH,
    LifecycleKind.PRESOLICITATION,
    LifecycleKind.LIVE_SOLICITATION,
})

_UNSUPPORTED_RECOMPETE_CLAIM = re.compile(
    r"(?:\bconfirm(?:s|ed|ing)?\b[^.\n]{0,120}\brecompete\w*\b|"
    r"\brecompete\w*\b[^.\n]{0,120}\bconfirm(?:s|ed|ing)?\b|"
    r"\bconfirmed\s+recompete\w*\b|"
    r"\bwill\s+(?:be\s+)?recompete(?:d)?\b|"
    r"\brecompete\w*\s+is\s+"
    r"(?:expected|anticipated|planned|upcoming|scheduled)\b|"
    r"\b(?:expected|anticipated|planned|upcoming)\s+recompete\w*\b|"
    r"\bsignals?\b[^.\n]{0,80}\b(?:an?\s+)?(?:upcoming\s+)?"
    r"recompete\w*\b|"
    r"\brecompete\s+date\s+is\b)",
    re.IGNORECASE,
)

_NEGATED_RECOMPETE_CLAIMS = (
    re.compile(
        r"\b(?:not\s+(?:a\s+)?|no\s+evidence\s+of\s+(?:a\s+)?)"
        r"confirmed\s+recompete\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:does|do|did|can|could)\s+not\s+confirm\w*\b"
        r"[^.\n]{0,120}\brecompete\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bno\b[^.\n]{0,80}\bconfirm\w*\b"
        r"[^.\n]{0,120}\brecompete\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bno\s+recompete\s+date\s+is\b|"
        r"\brecompete\s+date\s+is\s+not\b",
        re.IGNORECASE,
    ),
)


class _EngineContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _unique(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{label} must be unique")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


class CandidateRankSignals(_EngineContract):
    """Advisory report-order inputs; none can remove a candidate."""

    source_authority: Decimal = Field(ge=0, le=1)
    decision_specificity: Decimal = Field(ge=0, le=1)
    recency: Decimal = Field(ge=0, le=1)
    timing_strength: Decimal = Field(ge=0, le=1)
    capability_fit: Decimal = Field(ge=0, le=1)

    @property
    def score(self) -> Decimal:
        weighted = (
            self.source_authority * Decimal("0.25")
            + self.decision_specificity * Decimal("0.20")
            + self.recency * Decimal("0.15")
            + self.timing_strength * Decimal("0.20")
            + self.capability_fit * Decimal("0.20")
        )
        return weighted.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class CandidateSeed(_EngineContract):
    """One evidence-record hypothesis before exact/family/corridor grouping."""

    anchor_evidence_id: str = Field(min_length=1)
    boundary: DecisionBoundary
    title: str = Field(min_length=1)
    agency: str = Field(min_length=1)
    component: Optional[str] = None
    office: Optional[str] = None
    corridor_id: Optional[str] = None
    supporting_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    counterevidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    dates: tuple[DateValue, ...] = Field(default_factory=tuple)
    money: tuple[MoneyValue, ...] = Field(default_factory=tuple)
    records_show: str = Field(min_length=1)
    may_suggest: str = Field(min_length=1)
    validate_next: str = Field(min_length=1)
    cluster_reason: str = Field(min_length=1)
    strategic_action: str = Field(min_length=1)
    distinctness_explanation: str = Field(min_length=1)
    inference_chain: str = Field(min_length=1)
    falsifier: str = Field(min_length=1)
    watch_trigger: str = Field(min_length=1)
    cautions: tuple[str, ...] = Field(default_factory=tuple)
    rank_signals: CandidateRankSignals
    diversity_key: Optional[str] = None

    @model_validator(mode="after")
    def _seed_is_an_explicit_non_dispositive_hypothesis(self) -> "CandidateSeed":
        _unique(self.supporting_evidence_ids, "seed supporting evidence IDs")
        _unique(self.counterevidence_ids, "seed counterevidence IDs")
        _unique((item.date_id for item in self.dates), "seed date IDs")
        _unique((item.money_id for item in self.money), "seed money IDs")
        if self.corridor_id is not None and not self.corridor_id.strip():
            raise ValueError("corridor_id cannot be blank")
        assert_candidate_review_language(
            self.title,
            self.records_show,
            self.may_suggest,
            self.validate_next,
            self.cluster_reason,
            self.strategic_action,
            self.distinctness_explanation,
            self.inference_chain,
            self.falsifier,
            self.watch_trigger,
            *self.cautions,
        )
        return self


class EvidenceAlias(_EngineContract):
    alias_evidence_id: str = Field(min_length=1)
    canonical_evidence_id: str = Field(min_length=1)
    source_identity_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def _alias_points_elsewhere(self) -> "EvidenceAlias":
        if self.alias_evidence_id == self.canonical_evidence_id:
            raise ValueError("evidence alias cannot point to itself")
        return self


class RankedCandidate(_EngineContract):
    candidate: CandidateUnit
    rank_score: Decimal = Field(ge=0, le=1)
    rank: int = Field(ge=1)
    selected_for_report: bool
    diversity_key: str = Field(min_length=1)


class CandidateBuildResult(_EngineContract):
    """Complete audit inventory plus the bounded visible report projection."""

    binding: ArtifactBinding
    as_of: datetime
    evidence: tuple[EvidenceRecord, ...]
    ranked_inventory: tuple[RankedCandidate, ...]
    candidates: tuple[CandidateUnit, ...]
    deferred_candidates: tuple[CandidateUnit, ...]
    evidence_aliases: tuple[EvidenceAlias, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _projection_preserves_the_inventory(self) -> "CandidateBuildResult":
        _require_aware(self.as_of, "candidate build as_of")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        _unique(evidence_ids, "candidate build evidence IDs")
        evidence_by_id = {
            item.evidence_id: item for item in self.evidence
        }
        for item in self.evidence:
            if (item.client_id, item.run_id, item.scope_sha256) != (
                self.binding.client_id,
                self.binding.run_id,
                self.binding.scope_sha256,
            ):
                raise ValueError(
                    "candidate build evidence crosses client, run, or scope"
                )
            if item.retrieved_at > self.as_of:
                raise ValueError("candidate build evidence postdates as_of")
            if item.verified_at is not None and item.verified_at > self.as_of:
                raise ValueError("candidate build verification postdates as_of")
        source_keys = tuple(
            item.source_identity.canonical_key for item in self.evidence)
        _unique(source_keys, "candidate build source identities")
        _unique(
            (item.alias_evidence_id for item in self.evidence_aliases),
            "candidate build evidence aliases",
        )
        canonical = set(evidence_ids)
        if any(item.canonical_evidence_id not in canonical
               for item in self.evidence_aliases):
            raise ValueError("evidence alias does not resolve to canonical evidence")
        if any(
            evidence_by_id[item.canonical_evidence_id]
            .source_identity.canonical_key != item.source_identity_key
            for item in self.evidence_aliases
        ):
            raise ValueError("evidence alias source identity does not match")

        inventory = tuple(item.candidate for item in self.ranked_inventory)
        inventory_ids = tuple(item.candidate_id for item in inventory)
        _unique(inventory_ids, "ranked candidate IDs")
        for candidate in inventory:
            if candidate.client_id != self.binding.client_id:
                raise ValueError("candidate build candidate belongs to another client")
            unresolved = set(candidate.all_evidence_ids) - canonical
            if unresolved:
                raise ValueError(
                    "candidate build candidate has unresolved evidence: "
                    + ", ".join(sorted(unresolved))
                )
            for member in candidate.members:
                member_records = tuple(
                    evidence_by_id[evidence_id]
                    for evidence_id in member.member_evidence_ids
                )
                if not any(record.can_anchor_candidate
                           for record in member_records):
                    raise ValueError(
                        "candidate member has no permitted buying anchor"
                    )
                if not any(
                    record.source_identity.canonical_key
                    == member.source_identity.canonical_key
                    for record in member_records
                ):
                    raise ValueError(
                        "candidate member source identity is unresolved"
                    )
        if tuple(item.rank for item in self.ranked_inventory) \
                != tuple(range(1, len(inventory) + 1)):
            raise ValueError("ranked candidate positions are not contiguous")
        visible_ids = tuple(item.candidate_id for item in self.candidates)
        deferred_ids = tuple(
            item.candidate_id for item in self.deferred_candidates)
        _unique((*visible_ids, *deferred_ids), "candidate projection IDs")
        if set((*visible_ids, *deferred_ids)) != set(inventory_ids):
            raise ValueError("candidate projection does not preserve full inventory")
        selected_ids = {
            item.candidate.candidate_id
            for item in self.ranked_inventory
            if item.selected_for_report
        }
        if selected_ids != set(visible_ids):
            raise ValueError("ranked selection differs from visible candidates")
        expected_visible = tuple(
            item.candidate for item in self.ranked_inventory
            if item.selected_for_report
        )
        expected_deferred = tuple(
            item.candidate for item in self.ranked_inventory
            if not item.selected_for_report
        )
        if self.candidates != expected_visible:
            raise ValueError(
                "visible candidates differ from ranked inventory content"
            )
        if self.deferred_candidates != expected_deferred:
            raise ValueError(
                "deferred candidates differ from ranked inventory content"
            )
        return self

    @property
    def evidence_alias_map(self) -> dict[str, str]:
        return {
            item.alias_evidence_id: item.canonical_evidence_id
            for item in self.evidence_aliases
        }


class _PreparedSeed(_EngineContract):
    seed: CandidateSeed
    anchor: EvidenceRecord
    family: Optional[ProcurementFamilyIdentity]
    kind: CandidateKind


def build_candidate_inventory(
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    evidence: Iterable[EvidenceRecord],
    seeds: Iterable[CandidateSeed],
    max_candidates: int = CONTENT_BUDGETS["candidates"],
) -> CandidateBuildResult:
    """Build the complete candidate inventory and bounded report projection.

    ``max_candidates`` affects visibility only.  Every valid candidate remains
    in ``ranked_inventory`` and either ``candidates`` or
    ``deferred_candidates``; no rank or caution is a business disqualification.
    """

    _require_aware(as_of, "candidate build as_of")
    if max_candidates < 0 or max_candidates > CONTENT_BUDGETS["candidates"]:
        raise ValueError(
            f"max_candidates must be between 0 and "
            f"{CONTENT_BUDGETS['candidates']}"
        )

    canonical_evidence, aliases, evidence_id_map = _canonicalize_evidence(
        binding=binding,
        as_of=as_of,
        evidence=tuple(evidence),
    )
    evidence_by_id = {
        item.evidence_id: item for item in canonical_evidence
    }
    raw_seed_rows = tuple(seeds)
    prepared = _prepare_seeds(
        raw_seed_rows,
        evidence_by_id=evidence_by_id,
        evidence_id_map=evidence_id_map,
        as_of=as_of,
    )
    candidates_with_scores = _build_candidates(prepared, evidence_by_id)
    _validate_global_money_ids(candidates_with_scores)
    base_ranked = sorted(
        candidates_with_scores,
        key=lambda item: (
            -item[1],
            0 if item[0].kind == CandidateKind.CURRENT_NOTICE else 1,
            item[0].candidate_id.casefold(),
        ),
    )
    selected = _select_diverse(base_ranked, max_candidates)
    selected_ids = {candidate.candidate_id for candidate in selected}
    deferred = tuple(
        candidate for candidate, _score, _key in base_ranked
        if candidate.candidate_id not in selected_ids
    )
    ranked_inventory = tuple(
        RankedCandidate(
            candidate=candidate,
            rank_score=score,
            rank=index,
            selected_for_report=candidate.candidate_id in selected_ids,
            diversity_key=diversity_key,
        )
        for index, (candidate, score, diversity_key)
        in enumerate(base_ranked, start=1)
    )
    return CandidateBuildResult(
        binding=binding,
        as_of=as_of,
        evidence=canonical_evidence,
        ranked_inventory=ranked_inventory,
        candidates=tuple(selected),
        deferred_candidates=deferred,
        evidence_aliases=aliases,
    )


def _canonicalize_evidence(
    *,
    binding: ArtifactBinding,
    as_of: datetime,
    evidence: tuple[EvidenceRecord, ...],
) -> tuple[
    tuple[EvidenceRecord, ...],
    tuple[EvidenceAlias, ...],
    dict[str, str],
]:
    evidence_ids = tuple(item.evidence_id for item in evidence)
    _unique(evidence_ids, "input evidence IDs")
    for item in evidence:
        if (item.client_id, item.run_id, item.scope_sha256) != (
            binding.client_id,
            binding.run_id,
            binding.scope_sha256,
        ):
            raise ValueError(
                "candidate evidence differs from the client, run, or scope binding"
            )
        if item.retrieved_at > as_of:
            raise ValueError("candidate evidence retrieval postdates as_of")
        if item.verified_at is not None and item.verified_at > as_of:
            raise ValueError("candidate notice verification postdates as_of")

    by_identity: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for item in evidence:
        by_identity[item.source_identity.canonical_key].append(item)

    canonical: list[EvidenceRecord] = []
    aliases: list[EvidenceAlias] = []
    evidence_id_map: dict[str, str] = {}
    for source_key in sorted(by_identity):
        rows = sorted(by_identity[source_key], key=lambda item: item.evidence_id)
        fingerprints = {_evidence_semantic_fingerprint(item) for item in rows}
        if len(fingerprints) != 1:
            raise ValueError(
                f"conflicting observations share exact source identity {source_key}"
            )
        chosen = rows[0]
        supports = tuple(dict.fromkeys(
            support for item in rows for support in item.supports
        ))
        retrieved_at = max(item.retrieved_at for item in rows)
        verified_values = [
            item.verified_at for item in rows if item.verified_at is not None
        ]
        payload = chosen.model_dump(mode="python")
        payload.update({
            "supports": supports,
            "retrieved_at": retrieved_at,
            "verified_at": max(verified_values) if verified_values else None,
        })
        chosen = EvidenceRecord.model_validate(payload)
        canonical.append(chosen)
        for item in rows:
            evidence_id_map[item.evidence_id] = chosen.evidence_id
            if item.evidence_id != chosen.evidence_id:
                aliases.append(EvidenceAlias(
                    alias_evidence_id=item.evidence_id,
                    canonical_evidence_id=chosen.evidence_id,
                    source_identity_key=source_key,
                ))

    canonical.sort(key=lambda item: item.evidence_id)
    aliases.sort(key=lambda item: item.alias_evidence_id)
    return tuple(canonical), tuple(aliases), evidence_id_map


def _evidence_semantic_fingerprint(item: EvidenceRecord) -> str:
    payload = item.model_dump(
        mode="json",
        exclude={"evidence_id", "retrieved_at", "verified_at", "supports"},
    )
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _prepare_seeds(
    seeds: tuple[CandidateSeed, ...],
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    evidence_id_map: dict[str, str],
    as_of: datetime,
) -> tuple[_PreparedSeed, ...]:
    raw_evidence_ids = set(evidence_id_map)
    normalized: list[CandidateSeed] = []
    for seed in seeds:
        refs = (
            seed.anchor_evidence_id,
            *seed.supporting_evidence_ids,
            *seed.counterevidence_ids,
            *(eid for item in seed.dates for eid in item.evidence_ids),
            *(eid for item in seed.money for eid in item.evidence_ids),
        )
        missing = sorted(set(refs) - raw_evidence_ids)
        if missing:
            raise ValueError(
                "candidate seed references unknown evidence: " + ", ".join(missing)
            )
        normalized.append(_remap_seed(seed, evidence_id_map))

    # One exact source record cannot silently carry competing analytical
    # identities.  Repeated keyword hits collapse only after their seed
    # semantics agree.
    by_anchor: dict[str, list[CandidateSeed]] = defaultdict(list)
    for seed in normalized:
        anchor = evidence_by_id[seed.anchor_evidence_id]
        by_anchor[anchor.source_identity.canonical_key].append(seed)

    unique_seeds: list[CandidateSeed] = []
    for source_key in sorted(by_anchor):
        rows = by_anchor[source_key]
        fingerprints = {_seed_fingerprint(item) for item in rows}
        if len(fingerprints) != 1:
            raise ValueError(
                f"conflicting candidate seed or decision boundary for {source_key}"
            )
        unique_seeds.append(sorted(
            rows, key=lambda item: item.anchor_evidence_id)[0])

    prepared: list[_PreparedSeed] = []
    for seed in unique_seeds:
        anchor = evidence_by_id[seed.anchor_evidence_id]
        if not anchor.can_anchor_candidate:
            raise ValueError(
                f"{anchor.source_kind.value} evidence or acquisition role "
                "cannot anchor a candidate opportunity"
            )
        if not anchor.official_source or not anchor.primary_source:
            raise ValueError(
                "candidate anchor requires official primary-source evidence"
            )
        is_current = anchor.confirms_open_notice
        if is_current:
            if seed.boundary.lifecycle not in _NOTICE_LIFECYCLES:
                raise ValueError(
                    "active SAM notice candidate uses a non-notice lifecycle"
                )
            if seed.corridor_id is not None:
                raise ValueError(
                    "active SAM notice must remain a current notice, not a corridor"
                )
            if anchor.verified_at is None \
                    or as_of - anchor.verified_at > timedelta(hours=24):
                raise ValueError(
                    "current notice status must be verified within 24 hours"
                )
            kind = CandidateKind.CURRENT_NOTICE
        else:
            if seed.corridor_id is None:
                raise ValueError(
                    "non-current evidence requires an explicit research corridor"
                )
            if seed.boundary.lifecycle in _NOTICE_LIFECYCLES:
                raise ValueError(
                    "research evidence cannot claim an official notice lifecycle"
                )
            kind = CandidateKind.RESEARCH_CORRIDOR

        family = None
        if kind == CandidateKind.CURRENT_NOTICE \
                and anchor.issuing_office and anchor.solicitation_number:
            family = ProcurementFamilyIdentity(
                issuing_office=anchor.issuing_office,
                solicitation_number=anchor.solicitation_number,
            )
        _validate_seed_evidence_semantics(seed, evidence_by_id)
        prepared.append(_PreparedSeed(
            seed=seed,
            anchor=anchor,
            family=family,
            kind=kind,
        ))
    return _resolve_effective_family_boundaries(tuple(prepared))


def _resolve_effective_family_boundaries(
    prepared: tuple[_PreparedSeed, ...],
) -> tuple[_PreparedSeed, ...]:
    """Apply a provably later amendment deadline to its whole SAM family.

    Historical postings retain their own evidence and dates.  Only the
    effective decision boundary is normalized, and only when one posting has
    a strictly later authoritative effective date.  Equal or missing dates
    leave competing deadlines separate instead of guessing current state.
    """

    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, item in enumerate(prepared):
        if item.kind == CandidateKind.CURRENT_NOTICE and item.family:
            grouped[(
                item.family.canonical_key,
                _boundary_key_without_deadline(item.seed.boundary),
            )].append(index)

    resolved = list(prepared)
    for indexes in grouped.values():
        deadlines = {
            prepared[index].seed.boundary.deadline for index in indexes
        }
        if len(deadlines) <= 1:
            continue
        dated = [
            (prepared[index].anchor.effective_date, index)
            for index in indexes
            if prepared[index].anchor.effective_date is not None
        ]
        if len(dated) != len(indexes):
            continue
        latest_date = max(value for value, _index in dated)
        latest = [index for value, index in dated if value == latest_date]
        if len(latest) != 1:
            continue
        effective_boundary = prepared[latest[0]].seed.boundary
        if effective_boundary.deadline is None:
            # An omitted deadline is not proof that an earlier stated deadline
            # was withdrawn; keep the records separate and surface ambiguity.
            continue
        for index in indexes:
            item = prepared[index]
            normalized_seed = item.seed.model_copy(update={
                "boundary": effective_boundary,
            })
            resolved[index] = item.model_copy(update={"seed": normalized_seed})
    return tuple(resolved)


def _remap_seed(
    seed: CandidateSeed,
    evidence_id_map: dict[str, str],
) -> CandidateSeed:
    def remap_many(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(evidence_id_map[value] for value in values))

    dates = tuple(DateValue.model_validate({
        **item.model_dump(mode="python"),
        "evidence_ids": remap_many(item.evidence_ids),
    }) for item in seed.dates)
    money = tuple(MoneyValue.model_validate({
        **item.model_dump(mode="python"),
        "evidence_ids": remap_many(item.evidence_ids),
    }) for item in seed.money)
    return CandidateSeed.model_validate({
        **seed.model_dump(mode="python"),
        "anchor_evidence_id": evidence_id_map[seed.anchor_evidence_id],
        "supporting_evidence_ids": remap_many(seed.supporting_evidence_ids),
        "counterevidence_ids": remap_many(seed.counterevidence_ids),
        "dates": dates,
        "money": money,
    })


def _seed_fingerprint(seed: CandidateSeed) -> str:
    payload = seed.model_dump(mode="json", exclude={"anchor_evidence_id"})
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _validate_seed_evidence_semantics(
    seed: CandidateSeed,
    evidence_by_id: dict[str, EvidenceRecord],
) -> None:
    seed_reference_ids = tuple(dict.fromkeys((
        seed.anchor_evidence_id,
        *seed.supporting_evidence_ids,
        *seed.counterevidence_ids,
        *(eid for item in seed.dates for eid in item.evidence_ids),
        *(eid for item in seed.money for eid in item.evidence_ids),
    )))
    explicit_recompete = any(
        evidence_by_id[eid].source_kind in {
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_FORECAST,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        }
        and evidence_by_id[eid].official_source
        and evidence_by_id[eid].primary_source
        and evidence_by_id[eid].has_assertion(
            EvidenceAssertion.EXPLICIT_RECOMPETE)
        for eid in seed_reference_ids
    )
    visible_copy = "\n".join((
        seed.title,
        seed.records_show,
        seed.may_suggest,
        seed.validate_next,
        seed.cluster_reason,
        seed.strategic_action,
        seed.distinctness_explanation,
        seed.inference_chain,
        seed.falsifier,
        seed.watch_trigger,
        *seed.cautions,
    ))
    if _contains_unsupported_recompete_claim(visible_copy) \
            and not explicit_recompete:
        raise ValueError(
            "confirmed-recompete language lacks explicit official evidence"
        )

    if seed.boundary.lifecycle == LifecycleKind.CONFIRMED_RECOMPETE:
        confirmed = tuple(
            item for item in seed.dates
            if item.kind == DateKind.CONFIRMED_RECOMPETE
            and item.status == DateStatus.CONFIRMED
        )
        if not confirmed:
            raise ValueError(
                "confirmed recompete requires an explicit confirmed date"
            )
        refs = tuple(
            evidence_by_id[eid]
            for item in confirmed
            for eid in item.evidence_ids
        )
        if not any(item.has_assertion(EvidenceAssertion.EXPLICIT_RECOMPETE)
                   for item in refs) or not explicit_recompete:
            raise ValueError(
                "confirmed recompete is not backed by explicit official evidence"
            )
    for item in seed.dates:
        if item.kind == DateKind.AWARD_END_RESEARCH_CLOCK:
            if not any(
                evidence_by_id[eid].source_kind == EvidenceKind.AWARD
                and evidence_by_id[eid].has_assertion(
                    EvidenceAssertion.AWARD_PERIOD_END)
                for eid in item.evidence_ids
            ):
                raise ValueError(
                    "award-end research clock lacks explicit award evidence"
                )

    allowed_date_sources = {
        DateKind.RESPONSE_DEADLINE: {EvidenceKind.NOTICE},
        DateKind.QA_DEADLINE: {EvidenceKind.NOTICE},
        DateKind.SITE_VISIT: {EvidenceKind.NOTICE},
        DateKind.INDUSTRY_DAY: {
            EvidenceKind.NOTICE,
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
        DateKind.FORECAST_SOLICITATION: {EvidenceKind.AGENCY_FORECAST},
        DateKind.FORECAST_AWARD: {EvidenceKind.AGENCY_FORECAST},
        DateKind.PROGRAM_DEADLINE: {
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
            EvidenceKind.OFFICIAL_EVENT,
        },
        DateKind.CONFIRMED_RECOMPETE: {
            EvidenceKind.NOTICE,
            EvidenceKind.AGENCY_FORECAST,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
        DateKind.AWARD_END_RESEARCH_CLOCK: {EvidenceKind.AWARD},
        DateKind.BUDGET_MILESTONE: {EvidenceKind.BUDGET},
        DateKind.EVENT_START: {
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
        DateKind.EVENT_END: {
            EvidenceKind.OFFICIAL_EVENT,
            EvidenceKind.ORGANIZER_EVENT,
            EvidenceKind.AGENCY_ANNOUNCEMENT,
        },
    }
    for item in seed.dates:
        allowed = allowed_date_sources.get(item.kind)
        if allowed is None:
            continue
        refs = tuple(evidence_by_id[eid] for eid in item.evidence_ids)
        if not any(ref.source_kind in allowed for ref in refs):
            raise ValueError("typed date kind lacks a compatible source")
        if not evidence_supports_date_value(item, refs):
            raise ValueError(
                "typed date value is not visible in its semantic source role"
            )

    allowed_money_sources = {
        MoneyBasis.OBLIGATED_TO_DATE: {
            EvidenceKind.AWARD, EvidenceKind.SUBAWARD},
        MoneyBasis.AWARD_CEILING: {
            EvidenceKind.AWARD, EvidenceKind.NOTICE},
        MoneyBasis.PUBLISHED_EXACT_ESTIMATE: {
            EvidenceKind.NOTICE, EvidenceKind.AGENCY_FORECAST},
        MoneyBasis.PUBLISHED_RANGE: {
            EvidenceKind.NOTICE, EvidenceKind.AGENCY_FORECAST},
        MoneyBasis.BUDGET_REQUEST: {EvidenceKind.BUDGET},
        MoneyBasis.AGGREGATE_HISTORY: {
            EvidenceKind.AWARD, EvidenceKind.SUBAWARD},
    }
    for item in seed.money:
        if item.basis == MoneyBasis.ANALYST_PIPELINE_VALUE:
            raise ValueError(
                "analyst pipeline value cannot be produced by the candidate engine"
            )
        if not any(
            evidence_by_id[eid].source_kind in allowed_money_sources[item.basis]
            for eid in item.evidence_ids
        ):
            raise ValueError("typed money basis lacks a compatible source")


def _build_candidates(
    prepared: tuple[_PreparedSeed, ...],
    evidence_by_id: dict[str, EvidenceRecord],
) -> list[tuple[CandidateUnit, Decimal, str]]:
    groups: dict[tuple[str, ...], list[_PreparedSeed]] = defaultdict(list)
    corridor_boundaries: dict[str, set[str]] = defaultdict(set)
    for item in prepared:
        boundary_key = _boundary_key(item.seed.boundary)
        if item.kind == CandidateKind.CURRENT_NOTICE:
            if item.family:
                key = ("family", item.family.canonical_key, boundary_key)
            else:
                key = (
                    "exact", item.anchor.source_identity.canonical_key,
                )
        else:
            corridor_id = item.seed.corridor_id or ""
            corridor_boundaries[corridor_id].add(boundary_key)
            key = ("corridor", corridor_id.casefold(), boundary_key)
        groups[key].append(item)

    built: list[tuple[CandidateUnit, Decimal, str]] = []
    for key in sorted(groups):
        rows = sorted(
            groups[key], key=lambda item: item.anchor.evidence_id)
        boundary = rows[0].seed.boundary
        if any(item.seed.boundary != boundary for item in rows[1:]):
            raise ValueError("candidate grouping crossed a decision boundary")
        representative = max(
            rows,
            key=lambda item: (
                item.seed.rank_signals.score,
                item.anchor.effective_date or item.anchor.published_date or date.min,
                item.anchor.retrieved_at,
                item.anchor.evidence_id,
            ),
        )
        family = representative.family
        member_rows = tuple(
            CandidateMember(
                source_identity=item.anchor.source_identity,
                member_evidence_ids=(item.anchor.evidence_id,),
                decision_boundary=boundary,
                procurement_family=family,
            )
            for item in rows
        )
        if representative.kind == CandidateKind.CURRENT_NOTICE:
            if family:
                cluster_level = ClusterLevel.PROCUREMENT_FAMILY
                candidate_id = "family:" + _short_digest(
                    f"{family.canonical_key}|{_boundary_key(boundary)}")
            else:
                cluster_level = ClusterLevel.EXACT_RECORD
                candidate_id = (
                    f"notice:{representative.anchor.source_identity.record_id}"
                )
            strategic = None
        else:
            cluster_level = ClusterLevel.STRATEGIC_CORRIDOR
            corridor_id = representative.seed.corridor_id or ""
            strategic = StrategicCorridorIdentity(
                corridor_id=corridor_id,
                decision_boundary=boundary,
            )
            candidate_id = corridor_id
            if len(corridor_boundaries[corridor_id]) > 1:
                candidate_id += ":" + _short_digest(_boundary_key(boundary))
            family = None
            member_rows = tuple(member.model_copy(update={
                "procurement_family": None,
            }) for member in member_rows)

        supporting = _merged_evidence_ids(
            rows, "supporting_evidence_ids",
            exclude={member.member_evidence_ids[0] for member in member_rows},
        )
        counter = _merged_evidence_ids(
            rows, "counterevidence_ids",
            exclude=set(supporting) | {
                member.member_evidence_ids[0] for member in member_rows},
        )
        dates = _merge_typed_rows(rows, "dates", "date_id")
        money = _merge_typed_rows(rows, "money", "money_id")
        cautions = tuple(dict.fromkeys(
            caution for item in rows for caution in item.seed.cautions
        ))
        agencies = {item.seed.agency for item in rows}
        components = {item.seed.component for item in rows}
        offices = {item.seed.office for item in rows}
        candidate = CandidateUnit(
            candidate_id=candidate_id,
            client_id=representative.anchor.client_id,
            kind=representative.kind,
            title=representative.seed.title,
            agency=(next(iter(agencies)) if len(agencies) == 1 else "Multi-agency"),
            component=(next(iter(components)) if len(components) == 1 else None),
            office=(next(iter(offices)) if len(offices) == 1 else None),
            lifecycle=boundary.lifecycle,
            cluster_level=cluster_level,
            members=member_rows,
            procurement_family=family,
            strategic_corridor=strategic,
            supporting_evidence_ids=supporting,
            counterevidence_ids=counter,
            dates=dates,
            money=money,
            records_show=representative.seed.records_show,
            may_suggest=representative.seed.may_suggest,
            validate_next=representative.seed.validate_next,
            cluster_reason=representative.seed.cluster_reason,
            strategic_action=representative.seed.strategic_action,
            distinctness_explanation=(
                representative.seed.distinctness_explanation),
            inference_chain=representative.seed.inference_chain,
            falsifier=representative.seed.falsifier,
            watch_trigger=representative.seed.watch_trigger,
            cautions=cautions,
        )
        # Resolve every reference here so a build result can be placed directly
        # into CandidateReviewDocument without a second repair pass.
        unresolved = set(candidate.all_evidence_ids) - set(evidence_by_id)
        if unresolved:
            raise ValueError(
                f"candidate {candidate_id} contains unresolved evidence: "
                + ", ".join(sorted(unresolved))
            )
        score = (
            sum((item.seed.rank_signals.score for item in rows), Decimal("0"))
            / Decimal(len(rows))
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        diversity_key = representative.seed.diversity_key or (
            f"{candidate.agency.casefold()}|{boundary.program_key.casefold()}"
        )
        built.append((candidate, score, diversity_key))
    return built


def _merged_evidence_ids(
    rows: list[_PreparedSeed],
    field: str,
    *,
    exclude: set[str],
) -> tuple[str, ...]:
    values = (
        evidence_id
        for item in rows
        for evidence_id in getattr(item.seed, field)
        if evidence_id not in exclude
    )
    return tuple(dict.fromkeys(values))


def _merge_typed_rows(
    rows: list[_PreparedSeed],
    field: str,
    identity_field: str,
) -> tuple:
    by_id: dict[str, object] = {}
    for row in rows:
        for item in getattr(row.seed, field):
            identity = getattr(item, identity_field)
            prior = by_id.setdefault(identity, item)
            if prior != item:
                raise ValueError(
                    f"conflicting {field} rows share identity {identity}"
                )
    return tuple(by_id[key] for key in sorted(by_id))


def _select_diverse(
    ranked: list[tuple[CandidateUnit, Decimal, str]],
    maximum: int,
) -> tuple[CandidateUnit, ...]:
    if maximum == 0:
        return ()
    if len(ranked) <= maximum:
        return tuple(item[0] for item in ranked)

    selected: list[CandidateUnit] = []
    selected_ids: set[str] = set()
    seen_diversity: set[str] = set()
    for candidate, _score, diversity_key in ranked:
        normalized = diversity_key.casefold()
        if normalized in seen_diversity:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.candidate_id)
        seen_diversity.add(normalized)
        if len(selected) == maximum:
            return tuple(selected)
    for candidate, _score, _diversity_key in ranked:
        if candidate.candidate_id in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.candidate_id)
        if len(selected) == maximum:
            break
    # The backfill appends below lower-ranked diverse picks; the visible
    # projection must follow ranked-inventory order exactly, so the chosen
    # set is returned in ranked order.
    return tuple(
        candidate
        for candidate, _score, _diversity_key in ranked
        if candidate.candidate_id in selected_ids
    )


def _validate_global_money_ids(
    candidates: list[tuple[CandidateUnit, Decimal, str]],
) -> None:
    owners: dict[str, str] = {}
    for candidate, _score, _diversity_key in candidates:
        for money in candidate.money:
            prior = owners.setdefault(money.money_id, candidate.candidate_id)
            if prior != candidate.candidate_id:
                raise ValueError(
                    f"money ID {money.money_id} is shared by candidates "
                    f"{prior} and {candidate.candidate_id}"
                )


def _boundary_key(boundary: DecisionBoundary) -> str:
    encoded = json.dumps(
        boundary.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _contains_unsupported_recompete_claim(value: str) -> bool:
    sanitized = value
    for pattern in _NEGATED_RECOMPETE_CLAIMS:
        sanitized = pattern.sub("", sanitized)
    return _UNSUPPORTED_RECOMPETE_CLAIM.search(sanitized) is not None


def _boundary_key_without_deadline(boundary: DecisionBoundary) -> str:
    payload = boundary.model_dump(mode="json")
    payload.pop("deadline", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _short_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]
