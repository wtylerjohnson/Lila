"""Human decisions for opportunity-specific live requirement spans.

Keyword approval establishes the client's capability vocabulary. This gate is
different: it records that an operator read one exact authoritative SAM span
and agreed that it states work the procurement is actually buying. Only an
approved, current, evidence-bound decision may promote a live record to
``bid_now``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _slug(value: str) -> str:
    from tools.slug import client_slug
    return client_slug(value)


def review_path(client_name: str, *, review_dir: Optional[str] = None) -> Path:
    if review_dir is None:
        from agents.review import REVIEW_DIR
        review_dir = REVIEW_DIR
    return Path(review_dir) / f"{_slug(client_name)}.live_requirements.json"


def load_requirement_reviews(
    client_name: str,
    *,
    review_dir: Optional[str] = None,
) -> Optional[dict]:
    path = review_path(client_name, review_dir=review_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_write(path: Path, payload: dict) -> None:
    from tools.artifacts import atomic_write_json
    atomic_write_json(path, payload)


def record_requirement_review(
    client_name: str,
    *,
    binding: dict,
    notice_id: str,
    evidence_id: str,
    excerpt: str,
    capability_terms: list[str],
    approved: bool,
    attachment_inventory_hash: Optional[str] = None,
    attachment_inventory_count: Optional[int] = None,
    attachments_reviewed: bool = False,
    reviewed_by: str = "operator",
    note: str = "",
    review_dir: Optional[str] = None,
) -> dict:
    """Atomically upsert one exact, current operator decision."""
    if not all(str(value or "").strip() for value in (
            client_name, notice_id, evidence_id, excerpt, reviewed_by)):
        raise ValueError(
            "client, notice, evidence, excerpt, and reviewer are required")
    terms = list(dict.fromkeys(
        str(term).strip() for term in capability_terms if str(term).strip()))
    if not terms:
        raise ValueError("at least one approved capability term is required")
    existing = load_requirement_reviews(
        client_name, review_dir=review_dir) or {}
    if (existing.get("schema_version") != 1
            or existing.get("client") != client_name
            or existing.get("binding") != binding
            or not isinstance(existing.get("reviews"), list)):
        existing = {
            "schema_version": 1,
            "client": client_name,
            "binding": binding,
            "reviews": [],
        }
    decision = {
        "notice_id": notice_id,
        "evidence_id": evidence_id,
        "excerpt": excerpt,
        "capability_terms": terms,
        "decision": "approved" if approved else "rejected",
        "attachment_inventory_hash": attachment_inventory_hash,
        "attachment_inventory_count": attachment_inventory_count,
        "attachments_reviewed": bool(attachments_reviewed),
        "reviewed_by": reviewed_by.strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": str(note or "").strip(),
    }
    rows = [row for row in existing["reviews"]
            if isinstance(row, dict) and row.get("notice_id") != notice_id]
    rows.append(decision)
    existing["reviews"] = sorted(
        rows, key=lambda row: str(row.get("notice_id") or ""))
    _atomic_write(review_path(client_name, review_dir=review_dir), existing)
    return decision
