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
    return {item.subject_id: item for item in overlays}


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
    quota_keys = tuple(key for key in QUOTA_KEYS if key in data)
    return ScoreInput(
        parents=parents,
        leads=leads,
        traces=traces,
        overlays=tuple(_dedupe_overlays(overlays)),
        quota_keys=quota_keys,
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


def _dedupe_overlays(items: list[EvalOverlay]) -> list[EvalOverlay]:
    """Last overlay for a subject_id wins. No silent drop of subjects."""

    by_id: dict[str, EvalOverlay] = {}
    for item in items:
        by_id[item.subject_id] = item
    return list(by_id.values())
