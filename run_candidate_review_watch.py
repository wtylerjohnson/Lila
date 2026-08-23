#!/usr/bin/env python3
"""Persist one approved Candidate Review event and vehicle watch generation.

The assessment and refresh chains call this boundary after relevance and
after composition plus the scoped award re-pull.  It derives the two research
frames only from the exact approved review packet, populated client profile,
gate-designated sweep, and fresh relevance receipt.  A configured offline
replay adapter may execute supported lanes; without one, every required lane
is persisted as ``NOT_RUN`` rather than being represented as searched.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable, Mapping, Optional


_ROOT = Path(__file__).resolve().parent
_UNAVAILABLE_CONFIG_SHA256 = hashlib.sha256(
    b"candidate-review-v1:no-provider"
).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", required=True, help="canonical client slug")
    parser.add_argument(
        "--live", action="store_true",
        help="run the live SAM/USAspending lanes (quota-guarded); replay "
             "bundles are ignored for this run")
    parser.add_argument(
        "--state-root",
        help="override Candidate Review state root (tests and controlled replay)",
    )
    parser.add_argument(
        "--registry",
        help="override the standing event registry (tests and controlled replay)",
    )
    parser.add_argument(
        "--replay-config",
        help=(
            "run-bound offline replay config; otherwise uses "
            "LILA_CANDIDATE_REVIEW_REPLAY_CONFIG when set"
        ),
    )
    parser.add_argument(
        "--replay-source",
        help=(
            "immutable client replay source bundle; otherwise uses the "
            "client-owned candidate_review_replay_source.json when present"
        ),
    )
    return parser


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _aware_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _candidate_client_id(client_name: str) -> str:
    """Canonical client id; falls back to an EXISTING legacy hyphen
    generation dir (pre-consolidation watch shape) so history stays
    readable. New generations always mint canonical."""
    from tools.slug import client_slug, legacy_hyphen_client_id
    value = client_slug(client_name)
    if not value:
        raise ValueError("client identity cannot produce a Candidate Review id")
    legacy = legacy_hyphen_client_id(client_name)
    if legacy != value:
        from agents.candidate_review_v1.persistence import (
            default_generation_state_root)
        root = default_generation_state_root()
        if (not (root / value / "generations").exists()
                and (root / legacy / "generations").exists()):
            return legacy
    return value


def _ordered_unique(values: Iterable[object], *, maximum: int) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(str(raw or "").split())
        key = value.casefold()
        if not value or key in seen:
            continue
        rows.append(value)
        seen.add(key)
    if len(rows) > maximum:
        raise ValueError(
            f"approved frame contains {len(rows)} values; maximum is {maximum}"
        )
    return tuple(sorted(rows, key=lambda value: (value.casefold(), value)))


def _structured_rows(
    results: Mapping[str, object],
    lane: str,
) -> tuple[Mapping[str, object], ...]:
    """Return an exact structured lane or fail instead of quietly slicing it."""

    raw = results.get(lane)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"results.{lane} must be a list when present")
    rows: list[Mapping[str, object]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"results.{lane}[{index}] must be an object")
        rows.append(value)
    return tuple(rows)


def _exact_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be an exact nonblank string")
    return value


def _optional_structured_text(value: object, *, label: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string when present")
    return " ".join(value.split())


def _canonical_source_identities(
    values: Iterable[object],
    *,
    maximum: int,
    label: str,
) -> tuple[object, ...]:
    """Canonicalize SourceIdentity rows without deriving any identity text."""

    by_key: dict[str, object] = {}
    for value in values:
        key = value.canonical_key
        by_key.setdefault(key, value)
    if len(by_key) > maximum:
        raise ValueError(
            f"{label} contains {len(by_key)} identities; maximum is {maximum}"
        )
    return tuple(by_key[key] for key in sorted(by_key))


def _resolved_focus_agencies(scope) -> tuple[dict, ...]:
    if scope.all:
        return ()
    from tools.agencies import find

    resolved = tuple(find(abbr) for abbr in scope.agencies)
    if any(row is None for row in resolved):
        raise ValueError("approved focus scope contains an unresolved agency")
    return resolved


def _extract_dynamic_inputs(
    results: Mapping[str, object],
    *,
    focus_agencies: tuple[dict, ...],
    profile_competitors: Iterable[str],
):
    """Project only structured sweep fields; triage never removes identities."""

    from agents.candidate_review_v1.contracts import SourceIdentity
    from tools.agency_scope import agency_is_foreign

    candidate_accounts: list[str] = []
    candidate_identities: list[SourceIdentity] = []
    for index, row in enumerate(_structured_rows(results, "sam.gov")):
        source = _exact_identifier(
            row.get("source"),
            label=f"results.sam.gov[{index}].source",
        )
        if source.casefold() != "sam.gov":
            raise ValueError(
                f"results.sam.gov[{index}] has non-SAM source identity"
            )
        record_id = _exact_identifier(
            row.get("source_id"),
            label=f"results.sam.gov[{index}].source_id",
        )
        candidate_identities.append(SourceIdentity(
            source_system="sam.gov",
            record_id=record_id,
        ))
        agency = _optional_structured_text(
            row.get("agency"),
            label=f"results.sam.gov[{index}].agency",
        )
        if agency is not None:
            if focus_agencies and agency_is_foreign(agency, focus_agencies):
                raise ValueError(
                    f"results.sam.gov[{index}] is outside approved focus scope"
                )
            candidate_accounts.append(agency)

    buyer_communities: list[str] = []
    buyer_map = results.get("incumbent_buyer_map")
    if buyer_map is not None:
        if not isinstance(buyer_map, dict):
            raise ValueError("results.incumbent_buyer_map must be an object")
        raw_buyers = buyer_map.get("buyers")
        if raw_buyers is not None and not isinstance(raw_buyers, list):
            raise ValueError(
                "results.incumbent_buyer_map.buyers must be a list"
            )
        for index, row in enumerate(raw_buyers or []):
            if not isinstance(row, dict):
                raise ValueError(
                    "results.incumbent_buyer_map.buyers"
                    f"[{index}] must be an object"
                )
            buyer = _optional_structured_text(
                row.get("buyer"),
                label=(
                    "results.incumbent_buyer_map.buyers"
                    f"[{index}].buyer"
                ),
            )
            agency = _optional_structured_text(
                row.get("agency"),
                label=(
                    "results.incumbent_buyer_map.buyers"
                    f"[{index}].agency"
                ),
            )
            if focus_agencies and agency is not None \
                    and agency_is_foreign(agency, focus_agencies):
                raise ValueError(
                    "results.incumbent_buyer_map.buyers"
                    f"[{index}] is outside approved focus scope"
                )
            if buyer is not None:
                buyer_communities.append(buyer)

    ecosystem: list[object] = list(profile_competitors)
    parent_identities: list[SourceIdentity] = []
    for index, row in enumerate(_structured_rows(results, "award_repulls")):
        kind = row.get("kind")
        if kind is not None and kind != "award_repull":
            raise ValueError(
                f"results.award_repulls[{index}] has an invalid kind"
            )
        recipient = _optional_structured_text(
            row.get("recipient"),
            label=f"results.award_repulls[{index}].recipient",
        )
        if recipient is not None:
            ecosystem.append(recipient)
        parent = row.get("parent_generated_id")
        if parent is not None and parent != "":
            parent_identities.append(SourceIdentity(
                source_system="usaspending.gov",
                record_id=_exact_identifier(
                    parent,
                    label=(
                        f"results.award_repulls[{index}]"
                        ".parent_generated_id"
                    ),
                ),
            ))

    return {
        # 1024 matches the SAM candidate identity bound: these are sanity
        # bounds against malformed sweeps, never relevance filters; an
        # all-federal universe legitimately yields 100s of office-level
        # accounts (operator rule 2026-08-04: a relevant lead is never
        # dropped by an arbitrary cap).
        "candidate_accounts": _ordered_unique(
            candidate_accounts,
            maximum=1024,
        ),
        "buyer_communities": _ordered_unique(
            buyer_communities,
            maximum=1024,
        ),
        "ecosystem": _ordered_unique(ecosystem, maximum=64),
        "candidate_source_identities": _canonical_source_identities(
            candidate_identities,
            maximum=1024,
            label="SAM candidate source frame",
        ),
        "known_parent_idvs": _canonical_source_identities(
            parent_identities,
            maximum=128,
            label="known parent IDV frame",
        ),
    }


def _source_data_as_of(
    results: Mapping[str, object],
    *,
    sweep_generated_at: datetime,
) -> datetime:
    """Advance research as-of to exact post-sweep sanctioned retrievals."""

    timestamps = [sweep_generated_at]
    for lane in ("award_repulls", "agency_docs"):
        for index, row in enumerate(_structured_rows(results, lane)):
            value = row.get("retrieved_at")
            if value in (None, ""):
                continue
            timestamps.append(_aware_datetime(
                value,
                label=f"results.{lane}[{index}].retrieved_at",
            ))
    return max(timestamps)


def _normalized_tag_terms(values: Iterable[str]) -> tuple[str, ...]:
    """Project literal approved words/phrases into controlled tag candidates.

    This is intentionally lexical, not a semantic classifier.  Full phrases,
    individual words, and adjacent two-/three-word phrases are included so an
    approved phrase such as ``National Geospatial-Intelligence Agency`` can
    match the registry's literal ``geospatial-intelligence`` tag without an
    unstated model inference.
    """

    tags: set[str] = set()
    for value in values:
        words = re.findall(r"[a-z0-9]+", value.casefold())
        if not words:
            continue
        for width in range(1, min(3, len(words)) + 1):
            for index in range(len(words) - width + 1):
                tag = "-".join(words[index:index + width])
                if len(tag) >= 3:
                    tags.add(tag)
        tags.add("-".join(words))
    return tuple(sorted(tags))


def _provider_bindings():
    from agents.candidate_review_v1.pipeline import (
        ProviderBinding,
        ProviderMode,
        ResearchProviderBindings,
    )
    unavailable = dict(
        adapter_version="1",
        config_sha256=_UNAVAILABLE_CONFIG_SHA256,
        mode=ProviderMode.UNAVAILABLE,
    )
    return ResearchProviderBindings(
        event=ProviderBinding(
            adapter_name="unavailable-event-provider",
            **unavailable,
        ),
        vehicle=ProviderBinding(
            adapter_name="unavailable-vehicle-provider",
            **unavailable,
        ),
    )


def _prior_event_checks(
    client_id: str,
    *,
    state_root: Path,
    registry_path: Path,
    expected_binding=None,
) -> dict[str, datetime]:
    """Recover the pipeline's validated next-run cadence projection."""

    from agents.candidate_review_v1.pipeline import EventWatchCheckProjection
    from agents.candidate_review_v1.event_watch import (
        load_event_watch_universe,
        watch_universe_sha256,
    )
    from agents.candidate_review_v1.persistence import load_current_generation

    current = load_current_generation(client_id, state_root=state_root)
    if current is None:
        return {}
    prior_registry = current.registries.get("event-watch-universe")
    prior_registry_sha256 = (
        prior_registry.get("registry_sha256")
        if isinstance(prior_registry, dict) else None
    )
    if prior_registry_sha256 != watch_universe_sha256(registry_path):
        # Registry edits can change aliases, targeting, priority, or cadence
        # without changing an ID.  No prior check is reusable across that
        # boundary: every currently applicable target is due again.
        return {}
    if expected_binding is not None and current.receipt.binding == expected_binding:
        # An exact retry must rebuild the originally committed plan, not use
        # the first run's projected next-cadence state and silently become a
        # different generation.  The latter remains the input for a new run.
        selection = current.registries.get("event-watch-selection")
        if not isinstance(selection, dict):
            raise ValueError(
                "current Candidate Review generation lacks watch selection"
            )
        rows = selection.get("last_checked")
        if not isinstance(rows, list):
            raise ValueError(
                "current Candidate Review generation has invalid watch checks"
            )
        from agents.candidate_review_v1.pipeline import EventWatchCheck

        original = tuple(EventWatchCheck.model_validate(row) for row in rows)
        return {row.watch_target_id: row.checked_at for row in original}
    payload = current.artifacts.get("event-watch-checks-next")
    if payload is None:
        raise ValueError(
            "current Candidate Review generation lacks next-run event checks"
        )
    projection = EventWatchCheckProjection.model_validate_json(json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ))
    universe = load_event_watch_universe(registry_path)
    valid_ids = {
        row.organizer_id for row in universe.organizers if row.include_as_target
    } | {row.event_id for row in universe.event_series}
    return {
        target_id: checked_at
        for target_id, checked_at in projection.as_mapping().items()
        if target_id in valid_ids
    }


def _next_committed_at(
    client_id: str,
    *,
    state_root: Path,
    as_of: datetime,
) -> datetime:
    """Return a real, monotonic commit time without changing research as-of."""

    from agents.candidate_review_v1.persistence import load_current_generation

    candidate = max(datetime.now(timezone.utc), as_of)
    current = load_current_generation(client_id, state_root=state_root)
    if current is not None and candidate <= current.receipt.committed_at:
        candidate = current.receipt.committed_at + timedelta(microseconds=1)
    return candidate


def _build_inputs(root: Path, slug: str, *, registry_sha256: str):
    from agents.assessment_chain import (
        canonical_slug,
        relevance_receipt_valid,
    )
    from agents.candidate_review_v1.contracts import ArtifactBinding
    from agents.candidate_review_v1.event_research import EventResearchFrame
    from agents.candidate_review_v1.event_watch import WatchFrameTags
    from agents.candidate_review_v1.vehicle_watch import VehicleWatchFrame
    from agents.review import (
        ReviewStatus,
        gate_designator,
        load_packet_snapshot,
        sweep_artifact_path,
    )
    from agents.workstations import canonical_scope
    from tools.capability import ClientProfile
    from tools.relevance.taxonomy import CapabilityTaxonomy

    canonical = canonical_slug(slug)
    review_dir = root / "data" / "review"
    profile_path = root / "clients" / canonical / "profile.json"
    profile_bytes = profile_path.read_bytes()
    profile = ClientProfile.model_validate_json(profile_bytes)
    if not profile.is_populated():
        raise ValueError(f"profile is not populated: {profile_path}")
    if canonical_slug(profile.client_name) != canonical:
        raise ValueError("profile client identity does not match requested slug")
    if not relevance_receipt_valid(
        canonical,
        root=root,
        allow_board_content_change=True,
        allow_sweep_change=True,
    ):
        raise ValueError(
            "Candidate Review watch requires the current valid relevance receipt"
        )

    packet_snapshot = load_packet_snapshot(
        profile.client_name,
        review_dir=str(review_dir),
    )
    packet = packet_snapshot.packet
    if packet.status is not ReviewStatus.APPROVED or packet.decided_at is None:
        raise ValueError("Candidate Review watch requires an approved analyst packet")

    sweep_path = Path(sweep_artifact_path(
        profile.client_name,
        review_dir=str(review_dir),
    ))
    sweep_bytes = sweep_path.read_bytes()
    sweep = json.loads(sweep_bytes)
    if not isinstance(sweep, dict) or sweep.get("client") != profile.client_name:
        raise ValueError("gate-designated sweep belongs to another client")
    results = sweep.get("results")
    if not isinstance(results, dict):
        raise ValueError("gate-designated sweep requires object-valued results")
    sweep_generated_at = _aware_datetime(
        sweep.get("generated_at"),
        label="sweep generated_at",
    )
    as_of = _source_data_as_of(
        results,
        sweep_generated_at=sweep_generated_at,
    )
    if packet.decided_at > as_of:
        raise ValueError("approved analyst packet postdates the current sweep")

    approved_scope = canonical_scope(packet.search_scope or {"all": True})
    if "search_scope" not in sweep:
        raise ValueError("gate-designated sweep lacks its exact search_scope")
    sweep_scope = canonical_scope(sweep.get("search_scope"))
    if sweep_scope != approved_scope:
        raise ValueError(
            "gate-designated sweep search_scope differs from approved packet"
        )
    focus_agencies = _resolved_focus_agencies(approved_scope)

    taxonomy_path = root / "clients" / canonical / "capability_taxonomy.json"
    taxonomy = None
    taxonomy_sha256 = None
    if taxonomy_path.exists():
        taxonomy_bytes = taxonomy_path.read_bytes()
        taxonomy = CapabilityTaxonomy.model_validate_json(taxonomy_bytes)
        if canonical_slug(taxonomy.client_name) != canonical:
            raise ValueError(
                "capability taxonomy client identity does not match requested slug"
            )
        taxonomy_sha256 = _sha256_bytes(taxonomy_bytes)

    scope_designator = gate_designator(
        profile.client_name,
        review_dir=str(review_dir),
    ) or "all"
    scope_payload = approved_scope.as_dict()
    scope_sha256 = _stable_sha256(scope_payload)
    profile_sha256 = _sha256_bytes(profile_bytes)
    evidence_sha256 = _sha256_bytes(sweep_bytes)

    strategy = packet.strategy
    capability_keywords = [
        row.term for row in strategy.keywords
        if row.category.value in {"capability", "technology", "search_term"}
    ]
    agency_keywords = [
        row.term for row in strategy.keywords if row.category.value == "agency"
    ]
    capabilities = _ordered_unique(
        (
            *profile.capability_terms.core,
            *profile.capability_terms.adjacent,
            *(
                row.term
                for row in ((*taxonomy.core, *taxonomy.adjacent)
                            if taxonomy is not None else ())
            ),
            *capability_keywords,
        ),
        maximum=64,
    )
    agencies = (
        _ordered_unique(
            (row["name"] for row in focus_agencies),
            maximum=64,
        )
        if focus_agencies
        else _ordered_unique(
            (
                *profile.mission_components,
                *strategy.target_agencies,
                *agency_keywords,
            ),
            maximum=64,
        )
    )
    taxonomy_naics = (
        tuple(taxonomy.code_universe.naics)
        if taxonomy is not None and taxonomy.code_universe is not None
        else ()
    )
    taxonomy_psc = (
        tuple(taxonomy.code_universe.psc)
        if taxonomy is not None and taxonomy.code_universe is not None
        else ()
    )
    naics = _ordered_unique(
        (*profile.naics_boundary, *strategy.inferred_naics, *taxonomy_naics),
        maximum=128,
    )
    psc = _ordered_unique(taxonomy_psc, maximum=128)
    dynamic = _extract_dynamic_inputs(
        results,
        focus_agencies=focus_agencies,
        profile_competitors=profile.named_competitors_and_incumbents,
    )
    candidate_accounts = dynamic["candidate_accounts"]
    buyer_communities = dynamic["buyer_communities"]
    ecosystem = dynamic["ecosystem"]
    candidate_source_identities = dynamic["candidate_source_identities"]
    known_parent_idvs = dynamic["known_parent_idvs"]

    structured_dynamic_inputs = {
        "capabilities_missions": capabilities,
        "priority_agencies_components": agencies,
        "naics_codes": naics,
        "psc_codes": psc,
        "candidate_accounts": candidate_accounts,
        "buyer_communities": buyer_communities,
        "vehicle_partner_competitor_ecosystem": ecosystem,
        "candidate_source_identities": [
            row.model_dump(mode="json") for row in candidate_source_identities
        ],
        "known_parent_idvs": [
            row.model_dump(mode="json") for row in known_parent_idvs
        ],
    }
    frame_revision_sha256 = _stable_sha256({
        "review_packet_sha256": packet_snapshot.sha256,
        "profile_sha256": profile_sha256,
        "scope_sha256": scope_sha256,
        "capability_taxonomy_sha256": taxonomy_sha256,
        "structured_dynamic_inputs": structured_dynamic_inputs,
    })
    run_id = (
        f"watch-{evidence_sha256[:12]}-{frame_revision_sha256[:12]}-"
        f"{registry_sha256[:8]}"
    )
    binding = ArtifactBinding(
        client_id=_candidate_client_id(profile.client_name),
        client_name=profile.client_name,
        run_id=run_id,
        scope_designator=scope_designator,
        scope_sha256=scope_sha256,
        profile_sha256=profile_sha256,
        evidence_snapshot_sha256=evidence_sha256,
    )

    event_frame = EventResearchFrame(
        binding=binding,
        revision_sha256=frame_revision_sha256,
        capabilities_missions=capabilities,
        priority_agencies_components=agencies,
        naics_psc=_ordered_unique((*naics, *psc), maximum=64),
        candidate_accounts=candidate_accounts,
        vehicle_partner_competitor_ecosystem=ecosystem,
        buyer_communities=buyer_communities,
    )
    capability_tags = _normalized_tag_terms(capabilities)
    agency_tags = _normalized_tag_terms(agencies)
    buyer_tags = _normalized_tag_terms(buyer_communities)
    watch_tags = WatchFrameTags(
        mission_tags=tuple(sorted(set(capability_tags) | set(agency_tags))),
        buyer_tags=buyer_tags,
        agency_tags=agency_tags,
        capability_tags=capability_tags,
    )
    vehicle_frame = VehicleWatchFrame(
        binding=binding,
        revision_sha256=frame_revision_sha256,
        approved_at=packet.decided_at,
        window_start=as_of.date(),
        client_terms=capabilities,
        agencies=agencies,
        naics_codes=naics,
        psc_codes=psc,
        candidate_source_identities=candidate_source_identities,
        known_parent_idvs=known_parent_idvs,
    )
    return binding, event_frame, watch_tags, vehicle_frame, as_of


def run_watch_generation(
    client: str,
    *,
    root: Path | str = _ROOT,
    state_root: Path | str | None = None,
    registry_path: Path | str | None = None,
    replay_config_path: Path | str | None = None,
    replay_source_path: Path | str | None = None,
    live: bool = False,
    observed_at: datetime | None = None,
) -> Path:
    """Build and persist one exact generation; return its receipt path."""

    from agents.candidate_review_v1.pipeline import (
        generate_candidate_review_generation,
        plan_candidate_review_generation,
    )
    from agents.candidate_review_v1.persistence import (
        default_generation_state_root,
    )

    root_path = Path(root)
    effective_state_root = Path(state_root) if state_root is not None \
        else default_generation_state_root(root_path)
    effective_registry_path = (
        Path(registry_path) if registry_path is not None
        else root_path / "data" / "reference" / "event_watch_universe.json"
    )
    registry_sha256 = _sha256_bytes(effective_registry_path.read_bytes())
    binding, event_frame, watch_tags, vehicle_frame, as_of = _build_inputs(
        root_path,
        client,
        registry_sha256=registry_sha256,
    )
    if observed_at is not None and not live:
        raise ValueError("observed_at is only valid for a live watch run")
    if observed_at is not None and observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    if live:
        # A live run is a NEW observation: its as_of is the collection
        # instant, and the run id carries that instant, so two same-day
        # live pulls (or a live pull after an offline press of the same
        # sweep) mint distinct generations instead of colliding with the
        # snapshot-diff basis guard. Offline replay stays sweep-anchored
        # and byte-deterministic exactly as before.
        as_of = (
            observed_at.astimezone(timezone.utc)
            if observed_at is not None
            else datetime.now(timezone.utc)
        ).replace(microsecond=0)
        binding = binding.model_copy(update={
            "run_id": (f"{binding.run_id}-live-"
                       f"{as_of.strftime('%Y%m%dT%H%M%SZ')}")})
        event_frame = event_frame.model_copy(update={"binding": binding})
        vehicle_frame = vehicle_frame.model_copy(update={"binding": binding})
    event_last_checked_at = _prior_event_checks(
        binding.client_id,
        state_root=effective_state_root,
        registry_path=effective_registry_path,
        expected_binding=binding,
    )
    committed_at = _next_committed_at(
        binding.client_id,
        state_root=effective_state_root,
        as_of=as_of,
    )
    providers = _provider_bindings()
    event_searcher = None
    vehicle_searcher = None
    extra_artifacts = None
    if live:
        # Live wins outright: a configured or auto-detected replay bundle is
        # ignored for this run rather than silently mixing modes.
        from agents.candidate_review_v1.live_providers import (
            load_live_provider_runtime,
        )
        runtime = load_live_provider_runtime(
            binding=binding, as_of=as_of, root=root_path)
        providers = runtime.bindings
        event_searcher = runtime.event_searcher
        vehicle_searcher = runtime.vehicle_searcher
        extra_artifacts = runtime.extra_artifacts
        generation = generate_candidate_review_generation(
            binding,
            event_frame,
            watch_tags,
            vehicle_frame,
            as_of=as_of,
            event_last_checked_at=event_last_checked_at,
            registry_path=effective_registry_path,
            providers=providers,
            event_searcher=event_searcher,
            vehicle_searcher=vehicle_searcher,
            state_root=effective_state_root,
            committed_at=committed_at,
            extra_artifacts=extra_artifacts,
        )
        relative = generation.commit.generation.pointer.receipt_path
        return (effective_state_root / binding.client_id / relative).resolve()
    configured_replay = replay_config_path or os.environ.get(
        "LILA_CANDIDATE_REVIEW_REPLAY_CONFIG"
    )
    configured_source = replay_source_path or os.environ.get(
        "LILA_CANDIDATE_REVIEW_REPLAY_SOURCE"
    )
    default_source = (
        root_path
        / "clients"
        / binding.client_id
        / "candidate_review_replay_source.json"
    )
    if configured_replay is None \
            and configured_source is None \
            and default_source.is_file():
        configured_source = default_source
    if configured_replay and configured_source:
        raise ValueError(
            "select either a run-bound replay config or a replay source "
            "bundle, not both"
        )
    if configured_replay or configured_source:
        preview = plan_candidate_review_generation(
            binding,
            event_frame,
            watch_tags,
            vehicle_frame,
            as_of=as_of,
            event_last_checked_at=event_last_checked_at,
            registry_path=effective_registry_path,
            providers=providers,
        )
        from agents.candidate_review_v1.replay_providers import (
            load_replay_provider_runtime,
            load_replay_source_bundle_runtime,
        )
        from agents.review import sweep_artifact_path

        loader = (
            load_replay_provider_runtime
            if configured_replay
            else load_replay_source_bundle_runtime
        )
        replay = loader(
            configured_replay or configured_source,
            binding=binding,
            event_manifest=preview.event_query_manifest,
            vehicle_manifest=preview.vehicle_query_manifest,
            sweep_path=Path(sweep_artifact_path(
                binding.client_name,
                review_dir=str(root_path / "data" / "review"),
            )),
        )
        providers = replay.bindings
        event_searcher = replay.event_searcher
        vehicle_searcher = replay.vehicle_searcher
    generation = generate_candidate_review_generation(
        binding,
        event_frame,
        watch_tags,
        vehicle_frame,
        as_of=as_of,
        event_last_checked_at=event_last_checked_at,
        registry_path=effective_registry_path,
        providers=providers,
        event_searcher=event_searcher,
        vehicle_searcher=vehicle_searcher,
        state_root=effective_state_root,
        committed_at=committed_at,
    )
    relative = generation.commit.generation.pointer.receipt_path
    return (effective_state_root / binding.client_id / relative).resolve()


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = run_watch_generation(
            args.client,
            state_root=args.state_root,
            registry_path=args.registry,
            replay_config_path=args.replay_config,
            replay_source_path=args.replay_source,
            live=bool(getattr(args, "live", False)),
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary emits one safe gate
        print(json.dumps({
            "stage": "candidate-review-watch",
            "reason": f"{type(exc).__name__}: {str(exc)[:400]}",
            "fix_surface": (
                f"run_candidate_review_watch.py --client {args.client} "
                "(approved profile, analyst packet, sweep, and relevance receipt)"
            ),
        }, sort_keys=True, ensure_ascii=False), file=sys.stderr)
        return 2
    print(f"[out:candidate-review-watch] {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
