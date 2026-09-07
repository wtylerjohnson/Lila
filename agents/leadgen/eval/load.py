"""Load press / draft / eval-pack JSON into ScoreInput.

Accepts PressLeadGenReceipt, AssessLeadDrafts, or an eval.v0 wrapper.
Does not import Step 1 intake. Does not invent LeadRows.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents.leadgen.contracts import LeadRow, OpportunityAssessment
from agents.leadgen.traces import DecisionTrace

from .models import EmailStatus, EvalOverlay, ScoreInput

QUOTA_KEYS = ("quota", "target_count", "fill_to", "min_t1", "min_t2",
              "lead_quota")


class ScoreLoadError(ValueError):
    """Fail-closed pack load. Not a lead-tier or Assess error."""


def overlay_for(
    overlays: tuple[EvalOverlay, ...] | Mapping[str, EvalOverlay],
    subject_id: str,
) -> EvalOverlay:
    """Return the overlay for a lead or parent, or an empty default."""

    if isinstance(overlays, Mapping):
        found = overlays.get(subject_id)
        if found is not None:
            return found
        return EvalOverlay(subject_id=subject_id)
    for item in overlays:
        if item.subject_id == subject_id:
            return item
    return EvalOverlay(subject_id=subject_id)


def overlay_index(
    overlays: tuple[EvalOverlay, ...],
) -> dict[str, EvalOverlay]:
    return {item.subject_id: item for item in _dedupe_overlays(list(overlays))}


def load_score_input(
    payload: Mapping[str, Any] | str | Path,
    overlays_payload: Mapping[str, Any] | str | Path | None = None,
    *,
    source_label: str | None = None,
) -> ScoreInput:
    """Parse a press receipt, draft batch, or eval pack."""

    data, label = _read_mapping(payload, source_label or "pack")
    if _looks_like_notice_only(data):
        raise ScoreLoadError(
            "notice-only input refused: scorer needs LeadRow JSON plus "
            "parent assessments, not a notice list")
    try:
        parents = tuple(
            OpportunityAssessment.model_validate(item)
            for item in _as_list(data.get("parents"))
        )
        leads = tuple(
            LeadRow.model_validate(item)
            for item in _as_list(data.get("leads"))
        )
        traces = tuple(
            DecisionTrace.model_validate(item)
            for item in _as_list(data.get("traces"))
        )
    except (TypeError, ValidationError, ValueError) as exc:
        raise ScoreLoadError(
            f"pack is not press/draft LeadRow JSON: {exc}") from exc
    if not parents and not leads:
        raise ScoreLoadError(
            "pack has no parents and no leads; nothing to score")
    overlay_source: Any = data.get("overlays")
    if overlay_source is None:
        overlay_source = data.get("eval_overlays")
    overlays = list(_parse_overlays(overlay_source))
    if overlays_payload is not None:
        extra, _ = _read_mapping(overlays_payload, "overlays")
        overlays.extend(_parse_overlays(extra.get("overlays", extra)))
    declared_ids, reject_ids = _declared_inventory(data)
    quota_keys = tuple(key for key in QUOTA_KEYS if key in data)
    return ScoreInput(
        parents=parents,
        leads=leads,
        traces=traces,
        overlays=tuple(_dedupe_overlays(overlays)),
        quota_keys=quota_keys,
        declared_lead_ids=declared_ids,
        declared_reject_ids=reject_ids,
        source_label=label,
    )


def _read_mapping(
    payload: Mapping[str, Any] | str | Path,
    label: str,
) -> tuple[dict[str, Any], str]:
    if isinstance(payload, Mapping):
        return dict(payload), label
    path = Path(payload)
    if not path.is_file():
        raise ScoreLoadError(f"{label} path is not readable ({path})")
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScoreLoadError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise ScoreLoadError(
            f"{label} must be a JSON object (press receipt, draft "
            "batch, or eval pack)")
    return dict(loaded), str(path)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ScoreLoadError("parents/leads/traces must be JSON arrays")


def _looks_like_notice_only(payload: Mapping[str, Any]) -> bool:
    if "parents" in payload or "leads" in payload:
        return False
    keys = set(payload)
    return bool(keys & {
        "notice_id", "NoticeId", "solicitation_number", "notices",
        "opportunities", "results",
    })


def _parse_overlays(raw: Any) -> list[EvalOverlay]:
    if raw is None or raw == {}:
        return []
    if isinstance(raw, Mapping):
        if "subject_id" in raw or "lead_id" in raw or "assessment_id" in raw:
            return [_overlay_from_mapping(raw)]
        out: list[EvalOverlay] = []
        for key, value in raw.items():
            if isinstance(value, Mapping):
                body = dict(value)
                body.setdefault("subject_id", key)
                out.append(_overlay_from_mapping(body))
            else:
                raise ScoreLoadError(
                    "overlay values must be objects keyed by lead_id "
                    "or assessment_id")
        return out
    if isinstance(raw, list):
        return [_overlay_from_mapping(item) for item in raw]
    raise ScoreLoadError("overlays must be an object or an array")


def _overlay_from_mapping(raw: Mapping[str, Any]) -> EvalOverlay:
    data = dict(raw)
    subject = (
        data.pop("subject_id", None)
        or data.pop("lead_id", None)
        or data.pop("assessment_id", None)
    )
    if not subject:
        raise ScoreLoadError(
            "overlay requires subject_id, lead_id, or assessment_id")
    status = data.get("email_status")
    if isinstance(status, str):
        data["email_status"] = status.strip().upper()
        try:
            EmailStatus(data["email_status"])
        except ValueError as exc:
            raise ScoreLoadError(
                f"email_status must be VERIFIED, UNVERIFIED, or ABSENT "
                f"(got {status!r})") from exc
    try:
        return EvalOverlay(subject_id=str(subject), **data)
    except (TypeError, ValidationError, ValueError) as exc:
        raise ScoreLoadError(f"overlay is not usable: {exc}") from exc


def _declared_inventory(data: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Retain independent press inventories without manufacturing LeadRows."""

    def ids(raw: Any) -> set[str]:
        found: set[str] = set()
        for item in _as_list(raw):
            value = item.get("lead_id") if isinstance(item, Mapping) else item
            if not isinstance(value, str) or not value.strip():
                raise ScoreLoadError("declared inventory requires nonblank lead ids")
            found.add(value.strip())
        return found

    declared = ids(data.get("declared_lead_ids"))
    rejected = ids(data.get("declared_reject_ids"))
    for key in ("reject_receipts", "hold_receipts", "watch_receipts",
                "active_lead_t1", "active_lead_t2"):
        found = ids(data.get(key))
        declared.update(found)
        if key == "reject_receipts":
            rejected.update(found)
    for bucket in _as_list(data.get("by_lead_tier")):
        if not isinstance(bucket, Mapping):
            raise ScoreLoadError("by_lead_tier requires objects with lead_ids")
        found = ids(bucket.get("lead_ids"))
        declared.update(found)
        if bucket.get("lead_tier") == "REJECT":
            rejected.update(found)
    declared.update(rejected)
    return tuple(sorted(declared)), tuple(sorted(rejected))


def _dedupe_overlays(items: list[EvalOverlay]) -> list[EvalOverlay]:
    """Merge supplied fields; adverse facts cannot be cleared by supplements."""

    by_id: dict[str, EvalOverlay] = {}
    for item in items:
        prior = by_id.get(item.subject_id)
        if prior is None:
            by_id[item.subject_id] = item
            continue
        merged = prior.model_dump()
        for field in item.model_fields_set:
            value = getattr(item, field)
            if field.endswith("_cites"):
                merged[field] = tuple(dict.fromkeys((*merged[field], *value)))
            elif field in {"auth_only", "solicitation_only"}:
                merged[field] = True if merged[field] is True else value
            elif field == "email_status":
                # Strictest status wins, even if an address was omitted.
                rank = {EmailStatus.ABSENT: 0, EmailStatus.VERIFIED: 1,
                        EmailStatus.UNVERIFIED: 2}
                merged[field] = max((prior.email_status, value), key=rank.get)
            elif field == "receipt":
                merged[field] = " · ".join(dict.fromkeys(
                    text for text in (prior.receipt, value) if text)) or None
            elif field == "email":
                if prior.email and value and prior.email != value:
                    # A receipt for one address cannot verify a different one.
                    merged["email_status"] = EmailStatus.UNVERIFIED
                merged[field] = prior.email or value
            else:
                merged[field] = value
        # Iterate order of model_fields_set must never change adverse precedence.
        if (prior.email_status is EmailStatus.UNVERIFIED
                or item.email_status is EmailStatus.UNVERIFIED
                or (prior.email and item.email and prior.email != item.email)):
            merged["email_status"] = EmailStatus.UNVERIFIED
        by_id[item.subject_id] = EvalOverlay.model_validate(merged)
    return list(by_id.values())
