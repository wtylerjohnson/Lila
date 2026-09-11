"""Internal capability-screen evidence. Never Assess or release authority."""
from __future__ import annotations

import hashlib
import json

from tools.relevance.engine import record_text_fields


def screening_evidence(notice: dict, verdict, attachment: dict | None = None) -> dict:
    raw = notice.get("raw_payload")
    raw = raw if isinstance(raw, dict) else {}
    fields = record_text_fields(notice)
    narrative = [f for f, _ in fields if f.split(".")[-1] not in {"title", "label"}]
    attachment = attachment or raw.get("attachment_lookup") or {}
    if not isinstance(attachment, dict):
        attachment = {}
    attachment_status = attachment.get("status", "NOT_RECORDED_UNKNOWN")
    if attachment_status == "NOT_RECORDED_UNKNOWN" and raw.get("attachment_evidence"):
        attachment_status = "DISCOVERY_TEXT_CAPTURED_INVENTORY_NOT_RECONFIRMED"
    text_coverage = "SUPPLIED_TEXT_COMPLETENESS_UNVERIFIED" if narrative else "TITLE_OR_LABEL_ONLY"
    if any(d.get("description_truncated") is True for d in (notice, raw)):
        text_coverage = "EXPLICITLY_TRUNCATED"
    return {
        "source_id": str(notice.get("source_id") or notice.get("id") or ""),
        "record_sha256": hashlib.sha256(json.dumps(
            notice, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest(),
        "notice_type": str(notice.get("notice_type") or notice.get("type") or raw.get("type")
                           or raw.get("base_type") or "").strip(),
        "text_coverage": text_coverage,
        "text_fields": [{"field": f, "characters": len(t)} for f, t in fields],
        "attachment_status": attachment_status,
        "attachment_errors": list(attachment.get("errors") or [])[:10],
        "attachment_discovery_evidence": attachment.get("discovery_evidence"),
        "relevance": verdict.model_dump(mode="json"),
        "screen_state": "NOT_SCREENED",
        "stage": "deterministic_screen",
        "qualification": "NOT_ESTABLISHED_BY_CAPABILITY_SCREEN",
    }


def no_match_state(evidence: dict) -> tuple[str, str]:
    if evidence["attachment_status"] in {"INVENTORY_UNAVAILABLE", "TEXT_UNAVAILABLE", "FAILED", "STALE_INVENTORY"}:
        return "ATTACHMENT_UNAVAILABLE", "No supported capability in supplied text; attachment evidence unavailable. Fit remains unresolved."
    if evidence["text_coverage"] in {"TITLE_OR_LABEL_ONLY", "EXPLICITLY_TRUNCATED"}:
        return "TEXT_INCOMPLETE", "No supported capability in available text; requirement text is missing or truncated. Fit remains unresolved."
    return "WEAK_MATCH_NO_SUPPORTED_FIT", "No supported capability in supplied text; retrieval or NAICS coverage alone does not establish fit."
