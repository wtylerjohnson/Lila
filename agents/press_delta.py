"""Deterministic press-to-press delta classification.

The snapshot builder owns extraction from press artifacts.  This module owns
only comparison, canonical rendering, and the small INTERNAL feed seam.  A
figure drift is reported here, but dispute authorship stays with the verifier:
an injected observer may report whether that path ran, and this module never
arbitrates or appends a resolution.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any, Optional


DELTA_SCHEMA_VERSION = 1
DELTA_CLASS_ORDER = (
    "NEW",
    "DROPPED",
    "FIGURE_DRIFT",
    "WINDOW_MOVED",
    "RECOMPETE_APPEARED",
    "GATE_STATE_CHANGED",
)
_CLASS_RANK = {name: index for index, name in enumerate(DELTA_CLASS_ORDER)}
_DEFERRED_REASON = "adversarial verify pass unavailable on this baseline"

_IDENTITY_FIELDS = (
    "id",
    "key",
    "candidate_id",
    "record_key",
    "event_id",
    "award_id",
    "source_id",
    "internal_id",
)
_WINDOW_FIELDS = frozenset({
    "start",
    "end",
    "date",
    "deadline",
    "window_start",
    "window_end",
    "start_date",
    "end_date",
    "due_date",
    "posted_date",
    "response_deadline",
    "anticipated_solicitation",
    "anticipated_award",
    "pop_start",
    "pop_end",
})

DisputeObserver = Callable[[dict[str, Any]], Mapping[str, Any]]


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _compact(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def _canonical_value(value: Any) -> Any:
    """Return JSON data with deterministic mapping-key insertion order."""
    return json.loads(_compact(value))


def canonical_json(value: Any) -> str:
    """Canonical, human-readable JSON bytes represented as text."""
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"


def deferred_dispute_observer(_drift: dict[str, Any]) -> dict[str, Any]:
    """Explicit status used until an independent verifier is available."""
    return {"status": "DEFERRED", "reason": _DEFERRED_REASON}


def _snapshot_dict(snapshot: Any) -> dict[str, Any]:
    if hasattr(snapshot, "model_dump"):
        snapshot = snapshot.model_dump(mode="json")
    if not isinstance(snapshot, Mapping):
        raise TypeError("press snapshot must be a mapping or Pydantic model")
    result = _canonical_value(dict(snapshot))
    required = ("client_name", "slug", "press_timestamp")
    missing = [field for field in required if not result.get(field)]
    if missing:
        raise ValueError("press snapshot missing " + ", ".join(missing))
    return result


def _identity(row: Mapping[str, Any]) -> Optional[str]:
    for field in _IDENTITY_FIELDS:
        value = row.get(field)
        if value is not None and str(value):
            return str(value)
    system = row.get("source_system")
    record = row.get("source_record_id")
    if system and record:
        return f"{system}:{record}"
    if record:
        return str(record)
    return None


def _indexed_rows(value: Any, *, lane: str) -> dict[str, dict[str, Any]]:
    """Normalize a list of identified rows or an identity-keyed mapping."""
    if value is None:
        return {}
    rows: list[tuple[Optional[str], Any]]
    if isinstance(value, Mapping):
        rows = [(str(key), row) for key, row in value.items()]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = [(None, row) for row in value]
    else:
        raise TypeError(f"snapshot {lane} must be a list or mapping")

    indexed: dict[str, dict[str, Any]] = {}
    for supplied_key, raw in rows:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(mode="json")
        if not isinstance(raw, Mapping):
            raise TypeError(f"snapshot {lane} row must be a mapping")
        row = _canonical_value(dict(raw))
        key = supplied_key or _identity(row)
        if not key:
            raise ValueError(f"snapshot {lane} row has no stable identity")
        if key in indexed:
            raise ValueError(f"snapshot {lane} has duplicate identity {key!r}")
        indexed[key] = row
    return indexed


def _candidate_rows(value: Any, *, lane: str) -> dict[str, dict[str, Any]]:
    return _indexed_rows(value, lane=lane)


def _parse_moment(value: Any, *, end_of_day: bool = False) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.max if end_of_day else time.min)
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        if len(raw) == 10:
            try:
                parsed_date = date.fromisoformat(raw)
            except ValueError:
                return None
            moment = datetime.combine(
                parsed_date, time.max if end_of_day else time.min)
        else:
            try:
                moment = datetime.fromisoformat(raw)
            except ValueError:
                try:
                    parsed_date = date.fromisoformat(raw[:10])
                except ValueError:
                    return None
                moment = datetime.combine(
                    parsed_date, time.max if end_of_day else time.min)
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _reason_text(row: Optional[Mapping[str, Any]]) -> str:
    if not row:
        return ""
    values = [row.get("reason"), row.get("status"), row.get("verdict")]
    return " ".join(str(value).lower() for value in values if value)


def _expiration_values(row: Optional[Mapping[str, Any]]) -> list[Any]:
    if not row:
        return []
    values = []
    for field in (
        "end",
        "window_end",
        "end_date",
        "deadline",
        "due_date",
        "response_deadline",
        "pop_end",
    ):
        if row.get(field) not in (None, ""):
            values.append(row[field])
    for nested_field in ("window", "dates"):
        nested = row.get(nested_field)
        if isinstance(nested, Mapping):
            values.extend(_expiration_values(nested))
    if (row.get("field") in {
            "end", "window_end", "end_date", "deadline", "due_date",
            "response_deadline", "pop_end"}
            and row.get("value") not in (None, "")):
        values.append(row["value"])
    return values


def _explicitly_expired(row: Optional[Mapping[str, Any]]) -> bool:
    if not row:
        return False
    return bool(row.get("expired")) or "expired" in _reason_text(row)


def _is_expired(
    rows: Sequence[Optional[Mapping[str, Any]]],
    *,
    at: datetime,
) -> bool:
    if any(_explicitly_expired(row) for row in rows):
        return True
    for row in rows:
        for value in _expiration_values(row):
            end = _parse_moment(value, end_of_day=True)
            if end is not None and end < at:
                return True
    return False


def _is_out_of_scope(row: Optional[Mapping[str, Any]]) -> bool:
    if not row:
        return False
    if row.get("out_of_scope") is True or row.get("off_scope") is True:
        return True
    if row.get("in_scope") is False:
        return True
    text = " ".join((_reason_text(row), str(row.get("scope_basis") or "").lower()))
    return "out_of_scope" in text or "out-of-scope" in text or "off-scope" in text


def _is_below_threshold(row: Optional[Mapping[str, Any]]) -> bool:
    if not row:
        return False
    if row.get("below_threshold") is True:
        return True
    text = _reason_text(row)
    if "below_threshold" in text or "below threshold" in text:
        return True
    score, threshold = row.get("score"), row.get("threshold")
    if threshold is None and row.get("relevant") is False:
        from tools.relevance.engine import RELEVANCE_THRESHOLD
        threshold = RELEVANCE_THRESHOLD
    try:
        return score is not None and threshold is not None and float(score) < float(threshold)
    except (TypeError, ValueError):
        return False


def _drop_reason(
    candidate_id: str,
    *,
    previous_candidate: Mapping[str, Any],
    current_screen: dict[str, dict[str, Any]],
    previous_windows: dict[str, dict[str, Any]],
    current_windows: dict[str, dict[str, Any]],
    current_press: datetime,
) -> str:
    screen = current_screen.get(candidate_id)
    related_windows = [
        row for collection in (previous_windows, current_windows)
        for row in collection.values()
        if str(row.get("source_record_id") or "") == candidate_id
    ]
    rows = [
        screen,
        previous_candidate,
        *related_windows,
    ]
    if _is_expired(rows, at=current_press):
        return "expired_window"
    if _is_out_of_scope(screen):
        return "out_of_scope"
    if _is_below_threshold(screen):
        return "fell_below_threshold"
    return "not_present" if screen is None else "unknown"


def _figure_groups(value: Any) -> dict[str, dict[str, Any]]:
    indexed = _indexed_rows(value, lane="figures")
    groups: dict[str, dict[str, Any]] = {}
    for record_key, row in indexed.items():
        raw_observations = row.get("observations")
        if raw_observations is None:
            raw_observations = row.get("values")
        if raw_observations is None and "value" in row:
            raw_observations = [row]
        if raw_observations is None:
            raw_observations = []
        if not isinstance(raw_observations, Sequence) or isinstance(
                raw_observations, (str, bytes)):
            raise TypeError("figure observations must be a list")

        observations: list[dict[str, Any]] = []
        values: list[Any] = []
        for raw in raw_observations:
            if isinstance(raw, Mapping):
                observation = _canonical_value(dict(raw))
                value_item = observation.get("value", observation.get("raw"))
                metric = str(observation.get("metric") or "reported_value")
            else:
                observation = {"value": _canonical_value(raw)}
                value_item = observation["value"]
                metric = "reported_value"
            observations.append(observation)
            values.append(_canonical_value({
                "metric": metric,
                "value": value_item,
            }))
        observations.sort(key=_compact)
        values.sort(key=_compact)
        groups[record_key] = {
            "comparable": bool(row.get("comparable", True)),
            "source_system": row.get("source_system"),
            "source_record_id": row.get("source_record_id"),
            "source_url": row.get("source_url"),
            "values": values,
            "observations": observations,
        }
    return groups


def _window_dates(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("field") not in (None, "") and "value" in row:
        return _canonical_value({str(row["field"]): row.get("value")})
    dates: dict[str, Any] = {}
    for field, value in row.items():
        if field in _WINDOW_FIELDS or field.endswith("_date"):
            dates[field] = value
        elif field in ("window", "dates") and isinstance(value, Mapping):
            for nested_field, nested_value in value.items():
                if nested_field in _WINDOW_FIELDS or nested_field.endswith("_date"):
                    dates[f"{field}.{nested_field}"] = nested_value
    return _canonical_value(dates)


def _gate_comparable(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return _canonical_value(value)
    return _canonical_value({
        key: item for key, item in value.items()
        if key not in {"render_date", "retrieved_at", "observed_at"}
    })


def _item_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _CLASS_RANK[str(item["kind"])],
        str(item.get("id") or ""),
        str(item.get("field") or ""),
        _compact(item.get("before")),
        _compact(item.get("after")),
    )


def _observer_result(
    observer: DisputeObserver,
    drift: dict[str, Any],
) -> dict[str, Any]:
    result = observer(_canonical_value(drift))
    if not isinstance(result, Mapping):
        raise TypeError("dispute observer must return a status mapping")
    normalized = _canonical_value(dict(result))
    status = str(normalized.get("status") or "").upper()
    if status not in ("COMPLETE", "DEFERRED"):
        raise ValueError("dispute observer status must be COMPLETE or DEFERRED")
    normalized["status"] = status
    normalized.setdefault("reason", None)
    return _canonical_value(normalized)


def compute_delta(
    previous: Any,
    current: Any,
    *,
    dispute_observer: Optional[DisputeObserver] = None,
) -> dict[str, Any]:
    """Compare two press snapshots and return a stable delta object.

    Figure values are compared as multisets within each record group.  Their
    retrieval timestamps remain available in the observations but never cause
    drift by themselves.
    """
    before = _snapshot_dict(previous)
    after = _snapshot_dict(current)
    if before["client_name"] != after["client_name"]:
        raise ValueError("press snapshots belong to different clients")
    if before["slug"] != after["slug"]:
        raise ValueError("press snapshots use different client slugs")

    current_press = _parse_moment(after["press_timestamp"])
    if current_press is None:
        raise ValueError("current press_timestamp is not ISO-8601")

    previous_candidates = _candidate_rows(
        before.get("candidates") or [], lane="candidates")
    current_candidates = _candidate_rows(
        after.get("candidates") or [], lane="candidates")
    current_screen = _candidate_rows(
        after.get("candidate_screen") or [], lane="candidate_screen")
    previous_windows = _indexed_rows(
        before.get("windows") or [], lane="windows")
    current_windows = _indexed_rows(
        after.get("windows") or [], lane="windows")

    by_kind: dict[str, list[dict[str, Any]]] = {
        kind: [] for kind in DELTA_CLASS_ORDER
    }

    for candidate_id in sorted(current_candidates.keys() - previous_candidates.keys()):
        by_kind["NEW"].append({
            "kind": "NEW",
            "id": candidate_id,
            "before": None,
            "after": current_candidates[candidate_id],
        })

    for candidate_id in sorted(previous_candidates.keys() - current_candidates.keys()):
        by_kind["DROPPED"].append({
            "kind": "DROPPED",
            "id": candidate_id,
            "reason": _drop_reason(
                candidate_id,
                previous_candidate=previous_candidates[candidate_id],
                current_screen=current_screen,
                previous_windows=previous_windows,
                current_windows=current_windows,
                current_press=current_press,
            ),
            "before": previous_candidates[candidate_id],
            "after": current_screen.get(candidate_id),
        })

    previous_figures = _figure_groups(before.get("figures") or [])
    current_figures = _figure_groups(after.get("figures") or [])
    drift_items = []
    for record_key in sorted(previous_figures.keys() & current_figures.keys()):
        old_group = previous_figures[record_key]
        new_group = current_figures[record_key]
        if not old_group["comparable"] or not new_group["comparable"]:
            continue
        if _compact(old_group["values"]) == _compact(new_group["values"]):
            continue
        drift_items.append({
            "kind": "FIGURE_DRIFT",
            "id": record_key,
            "source_system": new_group.get("source_system"),
            "source_record_id": new_group.get("source_record_id"),
            "source_url": new_group.get("source_url"),
            "before": old_group,
            "after": new_group,
        })
    drift_items.sort(key=_item_sort_key)
    observer = dispute_observer or deferred_dispute_observer
    for item in drift_items:
        item["verification"] = _observer_result(observer, item)
    by_kind["FIGURE_DRIFT"] = drift_items

    for window_id in sorted(previous_windows.keys() & current_windows.keys()):
        old_dates = _window_dates(previous_windows[window_id])
        new_dates = _window_dates(current_windows[window_id])
        if _compact(old_dates) != _compact(new_dates):
            by_kind["WINDOW_MOVED"].append({
                "kind": "WINDOW_MOVED",
                "id": window_id,
                "before": old_dates,
                "after": new_dates,
            })

    previous_recompetes = _indexed_rows(
        before.get("recompete_events") or [], lane="recompete_events")
    current_recompetes = _indexed_rows(
        after.get("recompete_events") or [], lane="recompete_events")
    for event_id in sorted(current_recompetes.keys() - previous_recompetes.keys()):
        by_kind["RECOMPETE_APPEARED"].append({
            "kind": "RECOMPETE_APPEARED",
            "id": event_id,
            "before": None,
            "after": current_recompetes[event_id],
        })

    old_gate = _gate_comparable(before.get("gate_state"))
    new_gate = _gate_comparable(after.get("gate_state"))
    if _compact(old_gate) != _compact(new_gate):
        by_kind["GATE_STATE_CHANGED"].append({
            "kind": "GATE_STATE_CHANGED",
            "id": "gate_state",
            "before": old_gate,
            "after": new_gate,
        })

    items: list[dict[str, Any]] = []
    for kind in DELTA_CLASS_ORDER:
        items.extend(sorted(by_kind[kind], key=_item_sort_key))
    counts = {kind: len(by_kind[kind]) for kind in DELTA_CLASS_ORDER}
    return _canonical_value({
        "schema_version": DELTA_SCHEMA_VERSION,
        "client_name": after["client_name"],
        "slug": after["slug"],
        "baseline_created": False,
        "before_timestamp": before["press_timestamp"],
        "after_timestamp": after["press_timestamp"],
        "counts": counts,
        "verification": after.get("verification") or {
            "status": "DEFERRED",
            "reason": _DEFERRED_REASON,
            "verified": [],
            "disputed": [],
            "unverifiable": [],
        },
        "items": items,
    })


def baseline_delta(current: Any) -> dict[str, Any]:
    """First-press artifact: establish state without inventing NEW events."""
    snapshot = _snapshot_dict(current)
    return _canonical_value({
        "schema_version": DELTA_SCHEMA_VERSION,
        "client_name": snapshot["client_name"],
        "slug": snapshot["slug"],
        "baseline_created": True,
        "before_timestamp": None,
        "after_timestamp": snapshot["press_timestamp"],
        "counts": {kind: 0 for kind in DELTA_CLASS_ORDER},
        "verification": snapshot.get("verification") or {
            "status": "DEFERRED",
            "reason": _DEFERRED_REASON,
            "verified": [],
            "disputed": [],
            "unverifiable": [],
        },
        "items": [],
    })


def _display(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, str):
        return value
    return _compact(value)


def _item_text(item: Mapping[str, Any]) -> str:
    kind = str(item["kind"])
    identity = str(item.get("id") or "unknown")
    if kind == "NEW":
        return f"{identity} is a new qualifying candidate."
    if kind == "DROPPED":
        return f"{identity} dropped from the qualifying set: {item.get('reason')}."
    if kind == "FIGURE_DRIFT":
        old_values = (item.get("before") or {}).get("values")
        new_values = (item.get("after") or {}).get("values")
        status = (item.get("verification") or {}).get("status", "DEFERRED")
        return (
            f"{identity} figure values changed from {_display(old_values)} to "
            f"{_display(new_values)}. Verification {status}."
        )
    if kind == "WINDOW_MOVED":
        return (
            f"{identity} window moved from {_display(item.get('before'))} to "
            f"{_display(item.get('after'))}."
        )
    if kind == "RECOMPETE_APPEARED":
        return f"{identity} appeared in the recompete set."
    return (
        f"Gate state changed from {_display(item.get('before'))} to "
        f"{_display(item.get('after'))}."
    )


def render_internal_markdown(delta: Mapping[str, Any]) -> str:
    """Render the INTERNAL refresh summary. No client-facing copy is made."""
    payload = _canonical_value(dict(delta))
    lines = [
        f"# INTERNAL · {payload['client_name']} · Refresh delta",
        (
            f"_from {payload['before_timestamp'] or 'baseline'} · to "
            f"{payload['after_timestamp']} · never leaves the shop_"
        ),
        "",
        "## Summary",
    ]
    total = len(payload.get("items") or [])
    lines.append(f"- {total} classified change{'s' if total != 1 else ''}")
    verification = payload.get("verification") or {}
    reason = verification.get("reason")
    verify_line = f"- Verification {verification.get('status', 'DEFERRED')}"
    if reason:
        verify_line += f": {reason}"
    lines.extend([verify_line, ""])

    items = payload.get("items") or []
    for kind in DELTA_CLASS_ORDER:
        section = [item for item in items if item.get("kind") == kind]
        if not section:
            continue
        lines.append(f"## {kind.replace('_', ' ').title()}")
        for item in section:
            lines.append(f"- {_item_text(item)}")
            if kind == "FIGURE_DRIFT":
                observation = item.get("verification") or {}
                if observation.get("reason"):
                    lines.append(
                        f"  - Dispute observation: {observation['reason']}")
        lines.append("")
    if not items:
        lines.extend(["## Changes", "- None", ""])
    rendered = "\n".join(lines)
    if "—" in rendered:
        raise ValueError("INTERNAL delta summary contains an em dash")
    return rendered


def _feed_id(delta: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    material = "\0".join((
        str(delta["slug"]),
        str(delta["after_timestamp"]),
        str(item["kind"]),
        str(item.get("id") or ""),
    ))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"press-delta:{digest}"


def delta_feed_items(delta: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ticker-ready items in the same stable order as the delta artifact."""
    payload = _canonical_value(dict(delta))
    after = _parse_moment(payload.get("after_timestamp"))
    if after is None:
        raise ValueError("delta after_timestamp is not ISO-8601")
    when = after.date().isoformat()
    return [
        {
            "id": _feed_id(payload, item),
            "kind": item["kind"],
            "slug": payload["slug"],
            "client_name": payload["client_name"],
            "text": _item_text(item),
            "when": when,
        }
        for item in payload.get("items") or []
    ]


def delta_feed(delta: Mapping[str, Any]) -> dict[str, Any]:
    """Session B consumer envelope; no filtering occurs in this seam."""
    items = delta_feed_items(delta)
    return {"items": items, "total": len(items), "suppressed": 0}


__all__ = [
    "DELTA_CLASS_ORDER",
    "DELTA_SCHEMA_VERSION",
    "baseline_delta",
    "canonical_json",
    "compute_delta",
    "deferred_dispute_observer",
    "delta_feed",
    "delta_feed_items",
    "render_internal_markdown",
]
