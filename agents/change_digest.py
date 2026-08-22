"""Recurring, internal-only intelligence change digest.

The digest compares the current per-client evidence state with the baseline
stored by the prior digest.  It delegates change classification to the
existing SAM/watchlist and forecast snapshot differs.  First execution stores
an explicit baseline and asserts no motion.

Everything here is PROGRAM-tier operating context.  SAM rows are described as
census movement only.  No row in this artifact establishes a live posting or
pursuit-grade opportunity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agents.assess.approval import approval_evidence_diff
from tools.api.forecasts.snapshots import diff_snapshots as diff_forecasts
from tools.snapshots import diff_snapshots as diff_watchlist
from tools.snapshots import sweep_slim_records


SCHEMA_VERSION = 1
VISIBILITY = "internal_only"
TIER = "program"
_DEFAULT_STATE = (
    Path(__file__).resolve().parents[1] / "data" / "state" / "change_digests"
)
_PROGRAM_HORIZON_KINDS = frozenset({
    "budget_line",
    "cisa_kev",
    "forecast_delta",
    "forecast_line",
    "forecast_screen",
    "legislation",
    "oversight",
    "regulatory",
})
_ASSERTED_POSTING = re.compile(
    r"\b(?:solicitation|recompete|opportunity|notice)\b"
    r"[^.!?\n]{0,48}\b(?:posted|released|is\s+live|is\s+open|"
    r"has\s+dropped|went\s+live|hit\s+the\s+street)\b",
    re.I,
)
_CLAIM_BOUNDARY = (
    "PROGRAM signals and census movement only. Pursuit-grade status requires "
    "separate live SAM verification."
)


class ChangeDigestError(ValueError):
    """Current or prior evidence cannot support an honest digest."""


def _slug(value: str) -> str:
    return "".join(
        character if character.isalnum() else "_" for character in value
    ).strip("_").lower()


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_root(value: Optional[str | os.PathLike[str]] = None) -> Path:
    if value is not None:
        return Path(value)
    return Path(os.environ.get("LILA_CHANGE_DIGEST_DIR", str(_DEFAULT_STATE)))


def change_digest_path(
    client_name: str,
    *,
    state_dir: Optional[str | os.PathLike[str]] = None,
) -> Path:
    from agents.assess.ledger import assess_client_storage_key

    return (
        _state_root(state_dir)
        / assess_client_storage_key(client_name)
        / "current.json"
    )


def load_change_digest(
    client_name: str,
    *,
    state_dir: Optional[str | os.PathLike[str]] = None,
) -> Optional[dict[str, Any]]:
    path = change_digest_path(client_name, state_dir=state_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ChangeDigestError(f"prior change digest is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ChangeDigestError("prior change digest root is not an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ChangeDigestError("prior change digest schema is unsupported")
    if payload.get("client_name") != client_name:
        raise ChangeDigestError("prior change digest belongs to another client")
    baselines = payload.get("baselines")
    if not isinstance(baselines, dict):
        raise ChangeDigestError("prior change digest has no valid baseline")
    problems = validate_change_digest(payload)
    if problems:
        raise ChangeDigestError(
            "prior change digest failed validation: " + "; ".join(problems))
    return payload


def digest_for_display(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the internal UI shape without the full comparison baseline."""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"baselines", "source_cursors"}
    }


def _brief(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("source_id"),
        "title": record.get("title"),
        "url": record.get("url"),
    }


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("id") or row.get("source_id") or ""),
            str(row.get("field") or ""),
            str(row.get("title") or ""),
        ),
    )


def _ensure_unique(records: list[dict[str, Any]], lane: str) -> None:
    keys = [
        f"{record.get('source')}::{record.get('source_id')}"
        for record in records
    ]
    if any(key.endswith("::None") or key.endswith("::") for key in keys):
        raise ChangeDigestError(f"{lane} baseline contains a record without identity")
    if len(keys) != len(set(keys)):
        raise ChangeDigestError(f"{lane} baseline contains duplicate identities")


def _unchanged_rows(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    raw_delta: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Reconcile the unchanged population around the existing diff result."""
    def identity(record: dict[str, Any]) -> tuple[str, str]:
        return str(record.get("source")), str(record.get("source_id"))

    previous_ids = {identity(record) for record in previous}
    current_by_id = {identity(record): record for record in current}
    moved_ids = {str(row.get("id")) for row in raw_delta.get("moved") or []}
    return _sort_rows([
        _brief(current_by_id[record_id])
        for record_id in previous_ids & current_by_id.keys()
        if record_id[1] not in moved_ids
    ])


def _lane_delta(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    engine: Callable[..., dict[str, list[dict[str, Any]]]],
    compare: Optional[dict[str, str]] = None,
) -> dict[str, list[dict[str, Any]]]:
    identity_map: dict[str, tuple[str, str]] = {}

    def working(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for record in records:
            source = str(record.get("source"))
            source_id = str(record.get("source_id"))
            synthetic_id = _digest([source, source_id])
            identity_map[synthetic_id] = (source, source_id)
            out.append({**record, "source_id": synthetic_id})
        return out

    previous_working = working(previous)
    current_working = working(current)
    raw = (
        engine(previous_working, current_working, compare=compare)
        if compare is not None
        else engine(previous_working, current_working)
    )

    def restore(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        restored = []
        for row in rows:
            source, source_id = identity_map[str(row.get("id"))]
            restored.append({**row, "id": source_id, "source": source})
        return _sort_rows(restored)

    return {
        "entered": restore(list(raw.get("new") or [])),
        "left": restore(list(raw.get("disappeared") or [])),
        "moved": restore(list(raw.get("moved") or [])),
        "unchanged": restore(_unchanged_rows(
            previous_working, current_working, raw)),
    }


def _lane(
    *,
    label: str,
    framing: str,
    first_run: bool,
    current: Optional[list[dict[str, Any]]],
    previous: list[dict[str, Any]],
    engine: Callable[..., dict[str, list[dict[str, Any]]]],
    compare: Optional[dict[str, str]] = None,
    unavailable: Optional[str] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if current is None:
        return ({
            "label": label,
            "state": "unavailable",
            "tier": TIER,
            "framing": framing,
            "population": len(previous),
            "entered": [],
            "left": [],
            "moved": [],
            "unchanged": [],
            "summary": unavailable or "current source artifact is unavailable",
        }, previous)
    _ensure_unique(current, label)
    if first_run:
        return ({
            "label": label,
            "state": "baseline",
            "tier": TIER,
            "framing": framing,
            "population": len(current),
            "entered": [],
            "left": [],
            "moved": [],
            "unchanged": [],
            "summary": (
                f"Baseline captured for {len(current)} record(s); change "
                "classification begins with the next digest."
            ),
        }, current)
    _ensure_unique(previous, f"prior {label}")
    delta = _lane_delta(
        previous,
        current,
        engine=engine,
        compare=compare,
    )
    changed = len(delta["entered"]) + len(delta["left"]) + len(delta["moved"])
    return ({
        "label": label,
        "state": "current",
        "tier": TIER,
        "framing": framing,
        "population": len(current),
        **delta,
        "summary": (
            f"{len(delta['entered'])} entered · {len(delta['left'])} left · "
            f"{len(delta['moved'])} moved · {len(delta['unchanged'])} unchanged"
            if changed
            else f"No movement; {len(delta['unchanged'])} unchanged."
        ),
    }, current)


def _sam_records(searches: dict[str, Any]) -> list[dict[str, Any]]:
    results = searches.get("results")
    if not isinstance(results, dict):
        raise ChangeDigestError("sweep has no results object")
    rows = results.get("sam.gov")
    if not isinstance(rows, list):
        raise ChangeDigestError("sweep SAM census is not a list")
    safe_rows = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("source_id")
        and row.get("source") in (None, "sam.gov")
    ]
    safe_results = {**results, "sam.gov": safe_rows}
    records = [
        {
            **row,
            "tier": TIER,
            "record_role": "census_observation",
            "verdict_role": "review_metadata_only",
        }
        for row in sweep_slim_records(safe_results)
        if row.get("source") == "sam.gov"
    ]
    return sorted(records, key=lambda row: str(row.get("source_id")))


def _forecast_records(
    searches: dict[str, Any],
) -> tuple[Optional[list[dict[str, Any]]], Optional[str]]:
    results = searches.get("results") or {}
    payload = results.get("forecast_signals")
    if not isinstance(payload, dict):
        return None, "forecast signal payload is absent"
    if payload.get("error") or payload.get("disabled"):
        return None, str(payload.get("error") or payload.get("note")
                         or "forecast collection is disabled")
    matched = payload.get("matched")
    if not isinstance(matched, list):
        return None, "forecast matched-subset snapshot is unavailable"
    fields = (
        "source", "source_id", "title", "url", "agency", "component",
        "anticipated_solicitation", "anticipated_award",
        "estimated_value_range", "forecast_status", "set_aside",
    )
    records: list[dict[str, Any]] = []
    for raw in matched:
        if not isinstance(raw, dict):
            continue
        row = raw.get("record") if isinstance(raw.get("record"), dict) else raw
        if not row.get("source") or not row.get("source_id"):
            continue
        native_id = str(row.get("source_id"))
        records.append({
            **{field: row.get(field) for field in fields},
            "native_source_id": native_id,
            "source_id": f"{row.get('source')}:{native_id}",
            "tier": TIER,
        })
    return sorted(
        records,
        key=lambda row: (str(row.get("source")), str(row.get("source_id"))),
    ), None


def _forecast_events(
    searches: dict[str, Any],
    *,
    relevant_source_ids: set[str],
) -> list[dict[str, Any]]:
    payload = ((searches.get("results") or {}).get("forecast_signals") or {})
    raw_events = payload.get("events") if isinstance(payload, dict) else []
    if not isinstance(raw_events, list):
        return []
    from agents.reports.horizon_discovery import REGISTRY

    adapter = REGISTRY.get("forecast_store_changes")
    events: list[dict[str, Any]] = []
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        if str(event.get("id") or "") not in relevant_source_ids:
            continue
        mapped = adapter.map_payload({"events": [event]})
        if not mapped:
            continue
        program = mapped[0].bank_row()
        event_id = _digest({
            "kind": event.get("kind"),
            "id": event.get("id"),
            "text": program["text"],
            "source": program["source"],
        })
        events.append({
            "event_id": event_id,
            "kind": str(event.get("kind") or "change"),
            "source_id": event.get("id"),
            "title": event.get("title"),
            "url": program["source"],
            "detail": program["text"],
            "agency": event.get("agency"),
            "component": event.get("component"),
            "tier": TIER,
        })
    unique = {row["event_id"]: row for row in events}
    return [unique[key] for key in sorted(unique)]


def _forecast_store_cursors(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Read, filter, and fingerprint the global store without mutating it."""
    from tools.api.forecasts.store import load_store

    identities: dict[str, set[str]] = {}
    for record in records:
        source = str(record.get("source") or "")
        native_id = str(record.get("native_source_id") or "")
        if source and native_id:
            identities.setdefault(source, set()).add(native_id)
    cursors: dict[str, dict[str, Any]] = {}
    for source in sorted(identities):
        store = load_store(source)
        if not isinstance(store, dict):
            store = {}
        selected = {
            source_id: store[source_id]
            for source_id in sorted(identities[source])
            if isinstance(store.get(source_id), dict)
        }
        last_seen = sorted(
            str(row.get("last_seen"))
            for row in selected.values()
            if row.get("last_seen")
        )
        cursors[source] = {
            "matched_source_ids": sorted(identities[source]),
            "records_found": len(selected),
            "selected_records_sha256": _digest(selected),
            "latest_last_seen": last_seen[-1] if last_seen else None,
        }
    return cursors


def _recompete_records(
    client_name: str,
) -> tuple[
    Optional[list[dict[str, Any]]], Optional[str], Optional[dict[str, Any]],
]:
    from tools.api.recompete import _store_dir, load_calendar

    calendar = load_calendar(client_name)
    if calendar is None:
        return None, "recompete calendar has not been built", None
    if calendar.get("client") != client_name:
        return None, "recompete calendar belongs to another client", None
    artifact = _store_dir() / f"{_slug(client_name)}.json"
    try:
        artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    except OSError:
        artifact_sha256 = None
    provenance = {
        "artifact": artifact.name,
        "artifact_sha256": artifact_sha256,
        "generated": calendar.get("generated"),
        "population": calendar.get("population"),
    }
    records: list[dict[str, Any]] = []
    for posture in ("attack", "defend"):
        rows = calendar.get(posture)
        if not isinstance(rows, list):
            return (
                None,
                f"recompete calendar {posture} collection is malformed",
                provenance,
            )
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = row.get("award_id") or row.get("internal_id")
            if not source_id:
                continue
            title = " · ".join(
                value for value in (
                    str(row.get("recipient") or "").strip(),
                    str(row.get("awarding_office")
                        or row.get("awarding_agency") or "").strip(),
                ) if value
            )
            records.append({
                "source": "usaspending_recompete",
                "source_id": str(source_id),
                "title": title or str(source_id),
                "url": row.get("url"),
                "pop_end": row.get("pop_end"),
                "score": row.get("score"),
                "posture": posture,
                "recipient": row.get("recipient"),
                "award_id": row.get("award_id"),
                "tier": TIER,
            })
    return (
        sorted(records, key=lambda row: str(row.get("source_id"))),
        None,
        provenance,
    )


def _horizon_records(
    client_name: str,
    *,
    review_dir: Path,
    searches: dict[str, Any],
    sweep_path: Path,
    profile: Any,
) -> tuple[Optional[list[dict[str, Any]]], Optional[str], Optional[dict]]:
    path = review_dir / f"{_slug(client_name)}.horizon.json"
    if not path.exists():
        return None, "Horizon artifact has not been composed", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Horizon artifact is unreadable: {exc}", None
    if not isinstance(payload, dict):
        return None, "Horizon artifact root is not an object", None
    if payload.get("client") != client_name:
        return None, "Horizon artifact belongs to another client", payload
    try:
        from agents.reports.horizon import (
            HorizonSet,
            current_horizon_binding,
            horizon_binding_problems,
            validate_horizon,
        )

        hset = HorizonSet.model_validate(payload.get("set") or {})
        bank = payload.get("fact_bank")
        if not isinstance(bank, list):
            raise ValueError("fact bank is not a list")
        problems = validate_horizon(
            hset,
            bank,
            schema_version=payload.get("schema_version", 1),
            expected_client_name=client_name,
        )
        current_binding = current_horizon_binding(
            client_name,
            sweep_path=str(sweep_path),
            sweep=searches,
            profile=profile,
        )
        problems.extend(horizon_binding_problems(
            payload, current_binding=current_binding))
        if problems:
            return None, "; ".join(problems[:6]), payload
    except Exception as exc:  # noqa: BLE001 - one optional lane degrades
        return None, f"Horizon validation failed: {exc}", payload

    records: list[dict[str, Any]] = []
    for fact in bank:
        if (not isinstance(fact, dict)
                or fact.get("kind") not in _PROGRAM_HORIZON_KINDS):
            continue
        evidence_id = str(fact.get("id") or "").strip()
        text = str(fact.get("text") or "").strip()
        source = str(fact.get("source") or "").strip()
        kind = str(fact.get("kind") or "").strip()
        if not evidence_id or not text or not source:
            continue
        stable_identity = {
            "evidence_id": evidence_id,
            "source": source,
            "text": text,
            "kind": kind,
        }
        scope = fact.get("scope") if isinstance(fact.get("scope"), dict) else {}
        records.append({
            "source": "horizon_signal",
            "source_id": "program:" + _digest(stable_identity)[:24],
            "title": text,
            "url": source,
            "evidence_id": evidence_id,
            "signal_text": text,
            "signal_source": source,
            "kind": kind,
            "scope": scope,
            "scope_fingerprint": json.dumps(
                scope, sort_keys=True, separators=(",", ":")),
            "retrieved_at": fact.get("retrieved_at"),
            "horizon_status": payload.get("status"),
            "tier": TIER,
        })
    return sorted(records, key=lambda row: str(row.get("source_id"))), None, payload


def _load_profile(client_name: str) -> Any:
    try:
        from tools.capability import load_profile
        return load_profile(client_name)
    except Exception:  # noqa: BLE001 - optional lanes report unavailability
        return None


def _source_artifacts(
    client_name: str,
    *,
    sweep_path: Optional[str | os.PathLike[str]],
    review_dir: Optional[str | os.PathLike[str]],
) -> tuple[dict[str, Any], Path, Path, Any, str]:
    from agents.assess.ledger import _scope_designator, scope_from_sweep
    from agents.review import REVIEW_DIR, gate_designator, sweep_artifact_path

    selected_review = Path(review_dir or REVIEW_DIR)
    selected_sweep = Path(
        sweep_path or sweep_artifact_path(
            client_name, review_dir=str(selected_review)))
    try:
        searches = json.loads(selected_sweep.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChangeDigestError(f"current sweep is unavailable: {exc}") from exc
    if not isinstance(searches, dict):
        raise ChangeDigestError("current sweep root is not an object")
    if searches.get("client") != client_name:
        raise ChangeDigestError("current sweep belongs to another client")
    gate_scope = gate_designator(
        client_name, review_dir=str(selected_review)) or "all"
    try:
        sweep_scope = _scope_designator(scope_from_sweep(searches))
    except ValueError as exc:
        raise ChangeDigestError(
            f"current sweep has no valid explicit search scope: {exc}") from exc
    if sweep_scope != gate_scope:
        raise ChangeDigestError(
            f"current sweep scope {sweep_scope} does not match operator gate "
            f"{gate_scope}")
    return (
        searches,
        selected_sweep,
        selected_review,
        _load_profile(client_name),
        gate_scope,
    )


def build_change_digest(
    client_name: str,
    *,
    sweep_path: Optional[str | os.PathLike[str]] = None,
    review_dir: Optional[str | os.PathLike[str]] = None,
    state_dir: Optional[str | os.PathLike[str]] = None,
    generated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build one deterministic digest against the prior persisted baseline."""
    if not isinstance(client_name, str) or not client_name.strip():
        raise ChangeDigestError("client name is required")
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ChangeDigestError("digest generated_at must be timezone-aware")
    now = now.astimezone(timezone.utc)
    prior = load_change_digest(client_name, state_dir=state_dir)
    first_run = prior is None
    previous_baselines = (prior or {}).get("baselines") or {}
    previous_cursors = (prior or {}).get("source_cursors") or {}
    searches, selected_sweep, selected_review, profile, scope_designator = \
        _source_artifacts(
            client_name, sweep_path=sweep_path, review_dir=review_dir)
    scope_changed = (
        not first_run
        and previous_cursors.get("scope_designator") != scope_designator
    )
    rebaseline = first_run or scope_changed
    previous_observed = previous_cursors.get("lane_observed") or {}

    def prior_rows(lane: str) -> list[dict[str, Any]]:
        if scope_changed:
            return []
        return list(previous_baselines.get(lane) or [])

    def first_observation(lane: str) -> bool:
        return rebaseline or previous_observed.get(lane) is not True

    sam_current = _sam_records(searches)
    sam_lane, sam_baseline = _lane(
        label="SAM census",
        framing=(
            "Census movement only. Eligibility and live status remain outside "
            "this PROGRAM digest."
        ),
        first_run=first_observation("sam"),
        current=sam_current,
        previous=prior_rows("sam"),
        engine=diff_watchlist,
        compare={"response_deadline": "deadline", "verdict": "verdict"},
    )
    approval_context = approval_evidence_diff(
        client_name, review_dir=str(selected_review))
    if scope_changed:
        approval_context = {
            "available": bool(approval_context.get("available")),
            "summary": (
                "Scope changed; approval drift was not mixed into this "
                "new-scope baseline."
            ),
            "entered": [],
            "left": [],
            "changed": [],
            "profile_changed": [],
            "scope": approval_context.get("scope") or {
                "from": previous_cursors.get("scope_designator"),
                "to": scope_designator,
            },
            "unchanged_count": 0,
        }
    sam_lane["approval_context"] = {
        "role": "census_context_only",
        "tier": TIER,
        "framing": (
            "Approval drift explains census and review-state movement only. "
            "Verdict metadata here is not PROGRAM proof or a live-posting claim."
        ),
        **approval_context,
    }

    forecast_current, forecast_problem = _forecast_records(searches)
    forecast_prior = prior_rows("forecast")
    forecast_lane, forecast_baseline = _lane(
        label="Forecast planning records",
        framing=(
            "Agency-stated acquisition planning only. Timing movement is not "
            "a live solicitation claim."
        ),
        first_run=first_observation("forecast"),
        current=forecast_current,
        previous=forecast_prior,
        engine=diff_forecasts,
        unavailable=forecast_problem,
    )
    if forecast_current is None:
        forecast_store_cursors = (
            {} if scope_changed
            else dict(previous_cursors.get("forecast_store") or {}))
        forecast_events = []
    else:
        store_basis = [*forecast_current, *forecast_prior]
        forecast_store_cursors = _forecast_store_cursors(store_basis)
        relevant_forecast_ids = {
            str(row.get("native_source_id") or "")
            for row in store_basis
            if row.get("native_source_id")
        }
        forecast_events = _forecast_events(
            searches, relevant_source_ids=relevant_forecast_ids)
    prior_event_ids = (
        set() if rebaseline
        else set(previous_baselines.get("forecast_event_ids") or []))
    current_event_ids = (
        [row["event_id"] for row in forecast_events]
        if forecast_current is not None
        else sorted(prior_event_ids)
    )
    forecast_lane["events"] = (
        [] if first_observation("forecast") else [
            row for row in forecast_events
            if row["event_id"] not in prior_event_ids
        ]
    )
    forecast_lane["event_count"] = len(forecast_lane["events"])

    recompete_current, recompete_problem, recompete_provenance = \
        _recompete_records(client_name)
    recompete_lane, recompete_baseline = _lane(
        label="Recompete calendar",
        framing=(
            "USAspending period-of-performance movement only. Expiry math "
            "does not establish a procurement notice."
        ),
        first_run=first_observation("recompete"),
        current=recompete_current,
        previous=prior_rows("recompete"),
        engine=diff_watchlist,
        compare={
            "pop_end": "period of performance end",
            "score": "attack value",
            "posture": "capture posture",
        },
        unavailable=recompete_problem,
    )

    horizon_current, horizon_problem, horizon_payload = _horizon_records(
        client_name,
        review_dir=selected_review,
        searches=searches,
        sweep_path=selected_sweep,
        profile=profile,
    )
    horizon_lane, horizon_baseline = _lane(
        label="Horizon signals",
        framing=(
            "Bound, cited PROGRAM signals used in forming-window analysis. "
            "They do not establish a live solicitation."
        ),
        first_run=first_observation("horizon"),
        current=horizon_current,
        previous=prior_rows("horizon"),
        engine=diff_watchlist,
        compare={
            "scope_fingerprint": "buyer scope",
        },
        unavailable=horizon_problem,
    )

    lanes = {
        "sam_census": sam_lane,
        "forecast_store": forecast_lane,
        "recompete_calendar": recompete_lane,
        "horizon_signals": horizon_lane,
    }
    changed = sum(
        len(lane[category])
        for lane in lanes.values()
        for category in ("entered", "left", "moved")
    ) + forecast_lane["event_count"]
    if first_run:
        summary = (
            "First-run baseline captured. No change is asserted until the "
            "next digest."
        )
    elif scope_changed:
        summary = (
            f"Scope changed to {scope_designator}; all lanes were rebaselined "
            "and no cross-scope movement is asserted."
        )
    elif changed:
        summary = f"{changed} PROGRAM or census change item(s) since prior digest."
    else:
        summary = "No PROGRAM or census movement since prior digest."

    payload = {
        "schema_version": SCHEMA_VERSION,
        "client_name": client_name,
        "visibility": VISIBILITY,
        "intelligence_tier": TIER,
        "generated_at": now.isoformat(timespec="seconds"),
        "since": None if first_run else prior.get("generated_at"),
        "baseline": rebaseline,
        "baseline_reason": (
            "first_run" if first_run
            else "scope_changed" if scope_changed
            else None
        ),
        "summary": summary,
        "claim_boundary": _CLAIM_BOUNDARY,
        "lanes": lanes,
        "source_cursors": {
            "scope_designator": scope_designator,
            "sweep_artifact": selected_sweep.name,
            "sweep_sha256": _digest(searches),
            "horizon_sha256": (
                _digest(horizon_payload) if horizon_payload is not None else None),
            "forecast_store": forecast_store_cursors,
            "recompete": (
                recompete_provenance
                if recompete_provenance is not None
                else (None if scope_changed
                      else previous_cursors.get("recompete"))
            ),
            "lane_observed": {
                "sam": True,
                "forecast": (
                    forecast_current is not None
                    or (not scope_changed
                        and previous_observed.get("forecast") is True)),
                "recompete": (
                    recompete_current is not None
                    or (not scope_changed
                        and previous_observed.get("recompete") is True)),
                "horizon": (
                    horizon_current is not None
                    or (not scope_changed
                        and previous_observed.get("horizon") is True)),
            },
        },
        "baselines": {
            "sam": sam_baseline,
            "forecast": forecast_baseline,
            "forecast_event_ids": current_event_ids,
            "recompete": recompete_baseline,
            "horizon": horizon_baseline,
        },
    }
    problems = validate_change_digest(
        payload,
        horizon_payload=(horizon_payload if horizon_current is not None else None),
    )
    if problems:
        raise ChangeDigestError("; ".join(problems))
    return payload


def validate_change_digest(
    payload: dict[str, Any],
    *,
    horizon_payload: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Fail closed on tier, visibility, invention, and posting-claim drift."""
    problems: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("change digest schema version is invalid")
    if payload.get("visibility") != VISIBILITY:
        problems.append("change digest must remain internal-only")
    if payload.get("intelligence_tier") != TIER:
        problems.append("change digest must remain PROGRAM-tier")
    for field in ("summary", "claim_boundary"):
        if _ASSERTED_POSTING.search(str(payload.get(field) or "")):
            problems.append("change digest asserts an unverified posting")
    lanes = payload.get("lanes")
    if not isinstance(lanes, dict):
        problems.append("change digest lanes are missing")
        return problems
    required_lanes = {
        "sam_census", "forecast_store", "recompete_calendar",
        "horizon_signals",
    }
    if set(lanes) != required_lanes:
        problems.append("change digest lane vocabulary is incomplete or unknown")
    for name, lane in lanes.items():
        if not isinstance(lane, dict) or lane.get("tier") != TIER:
            problems.append(f"change digest lane {name} is not PROGRAM-tier")
            continue
        for field in ("summary", "framing"):
            if _ASSERTED_POSTING.search(str(lane.get(field) or "")):
                problems.append(
                    f"change digest lane {name} asserts an unverified posting")
        for category in ("entered", "left", "moved", "unchanged", "events"):
            for row in lane.get(category) or []:
                if not isinstance(row, dict):
                    problems.append(f"change digest lane {name} has malformed rows")
                    continue
                if category == "events" and row.get("tier") != TIER:
                    problems.append("forecast event is not PROGRAM-tier")
                if (category == "events"
                        and _ASSERTED_POSTING.search(
                            str(row.get("detail") or ""))):
                    problems.append(
                        f"change digest lane {name} asserts an unverified posting")

    approval_context = (lanes.get("sam_census") or {}).get(
        "approval_context") or {}
    if (approval_context.get("role") != "census_context_only"
            or approval_context.get("tier") != TIER):
        problems.append(
            "Assess approval drift must remain labeled census context only")

    baseline_reason = payload.get("baseline_reason")
    if payload.get("baseline") is True:
        if baseline_reason not in {"first_run", "scope_changed"}:
            problems.append("baseline digest has no valid baseline reason")
        if (baseline_reason == "first_run"
                and payload.get("since") is not None):
            problems.append("first-run digest cannot name a prior digest")
        if (baseline_reason == "scope_changed"
                and (not isinstance(payload.get("since"), str)
                     or not payload.get("since"))):
            problems.append("scope rebaseline must name the prior digest")
        if any(
            lane.get(category)
            for lane in lanes.values()
            if isinstance(lane, dict)
            for category in ("entered", "left", "moved", "unchanged", "events")
        ):
            problems.append("first-run digest cannot assert change")
    else:
        if baseline_reason is not None:
            problems.append("non-baseline digest cannot carry a baseline reason")
        if not isinstance(payload.get("since"), str) or not payload.get("since"):
            problems.append(
                "recurring digest must name its prior digest timestamp")

    baselines = payload.get("baselines")
    if not isinstance(baselines, dict):
        problems.append("change digest baselines are missing")
        return problems
    required_baselines = {
        "sam", "forecast", "forecast_event_ids", "recompete", "horizon",
    }
    if set(baselines) != required_baselines:
        problems.append(
            "change digest baseline vocabulary is incomplete or unknown")
    for name, rows in baselines.items():
        if name == "forecast_event_ids":
            if (not isinstance(rows, list)
                    or any(not isinstance(value, str) for value in rows)):
                problems.append("forecast event baseline is malformed")
            continue
        if not isinstance(rows, list):
            problems.append(f"change digest baseline {name} is malformed")
            continue
        if any(not isinstance(row, dict) or row.get("tier") != TIER
               for row in rows):
            problems.append(f"change digest baseline {name} is not PROGRAM-tier")

    cursors = payload.get("source_cursors")
    if not isinstance(cursors, dict):
        problems.append("change digest source cursors are missing")
    else:
        scope = cursors.get("scope_designator")
        if not isinstance(scope, str) or not scope:
            problems.append("change digest scope cursor is missing")
        observed = cursors.get("lane_observed")
        if (not isinstance(observed, dict)
                or set(observed) != {"sam", "forecast", "recompete", "horizon"}
                or any(not isinstance(value, bool)
                       for value in observed.values())):
            problems.append("change digest lane-observation cursor is malformed")
        forecast_store = cursors.get("forecast_store")
        if not isinstance(forecast_store, dict):
            problems.append("forecast-store provenance cursor is malformed")
        else:
            for source, cursor in forecast_store.items():
                if (not isinstance(source, str) or not source
                        or not isinstance(cursor, dict)
                        or not isinstance(cursor.get("matched_source_ids"), list)
                        or not isinstance(
                            cursor.get("selected_records_sha256"), str)):
                    problems.append(
                        "forecast-store source provenance is malformed")
                    break
        recompete_cursor = cursors.get("recompete")
        if recompete_cursor is not None and (
                not isinstance(recompete_cursor, dict)
                or not isinstance(recompete_cursor.get("artifact"), str)
                or not isinstance(
                    recompete_cursor.get("artifact_sha256"), str)):
            problems.append("recompete artifact provenance is malformed")
        if (isinstance(observed, dict)
                and observed.get("recompete") is True
                and recompete_cursor is None):
            problems.append(
                "observed recompete lane has no artifact provenance")

    if horizon_payload is not None:
        bank = horizon_payload.get("fact_bank") or []
        allowed = {
            (str(row.get("id")), row.get("text"), row.get("source"), row.get("kind"))
            for row in bank
            if isinstance(row, dict)
            and row.get("kind") in _PROGRAM_HORIZON_KINDS
        }
        for row in baselines.get("horizon") or []:
            claim = (
                row.get("evidence_id"),
                row.get("signal_text"),
                row.get("signal_source"),
                row.get("kind"),
            )
            if claim not in allowed:
                problems.append(
                    f"Horizon digest signal {row.get('source_id')} is not an "
                    "exact PROGRAM fact-bank row")
    return problems


def persist_change_digest(
    payload: dict[str, Any],
    *,
    state_dir: Optional[str | os.PathLike[str]] = None,
) -> Path:
    from tools.artifacts import atomic_write_json

    problems = validate_change_digest(payload)
    if problems:
        raise ChangeDigestError(
            "change digest persistence validation failed: "
            + "; ".join(problems))
    path = change_digest_path(payload["client_name"], state_dir=state_dir)
    return atomic_write_json(path, payload)


def build_and_persist_change_digest(
    client_name: str,
    *,
    sweep_path: Optional[str | os.PathLike[str]] = None,
    review_dir: Optional[str | os.PathLike[str]] = None,
    state_dir: Optional[str | os.PathLike[str]] = None,
    generated_at: Optional[datetime] = None,
) -> tuple[dict[str, Any], Path]:
    payload = build_change_digest(
        client_name,
        sweep_path=sweep_path,
        review_dir=review_dir,
        state_dir=state_dir,
        generated_at=generated_at,
    )
    return payload, persist_change_digest(payload, state_dir=state_dir)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the internal per-client intelligence change digest")
    parser.add_argument("--client", required=True)
    parser.add_argument("--sweep-path")
    parser.add_argument("--review-dir")
    parser.add_argument("--state-dir")
    parser.add_argument("--as-of", help="timezone-aware ISO-8601 timestamp")
    args = parser.parse_args(argv)
    generated_at = None
    if args.as_of:
        generated_at = datetime.fromisoformat(
            args.as_of.strip().replace("Z", "+00:00"))
    try:
        payload, path = build_and_persist_change_digest(
            args.client,
            sweep_path=args.sweep_path,
            review_dir=args.review_dir,
            state_dir=args.state_dir,
            generated_at=generated_at,
        )
    except ChangeDigestError as exc:
        print(f"[change-digest] failed: {exc}")
        return 1
    print(f"[change-digest] {payload['summary']}")
    print(f"[out] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
