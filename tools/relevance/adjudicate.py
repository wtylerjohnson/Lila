"""Append-only adjudication seam for relevance calibration decisions.

The calibration harness proposes; a human disposes. Every disposition is
one decider-stamped row in data/state/relevance/<slug>.adjudications.jsonl
(the observations.jsonl pattern: append-only source of truth, one model
row per line, derived state always rebuildable). This is the seam only;
the UI that writes through it comes later.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RelevanceAdjudication(BaseModel):
    record_ref: str = Field(description="stable record reference (id or url)")
    kind: Literal["false_positive", "false_negative"]
    engine_score: int
    matched_spans: list[str] = Field(default_factory=list)
    decision: Literal["confirm", "reject", "defer"]
    decider: str = Field(description="human identity; never a model name")
    taxonomy_version: int
    decided_at: datetime


def adjudication_log_path(client_name: str, *,
                          log_dir: Optional[str] = None) -> str:
    from tools.capability import _slug
    base = log_dir or os.path.join(_ROOT, "data", "state", "relevance")
    return os.path.join(base, f"{_slug(client_name)}.adjudications.jsonl")


def append_adjudication(client_name: str, row: RelevanceAdjudication, *,
                        log_dir: Optional[str] = None) -> str:
    """Append one decision. Rows are never rewritten; a superseding decision
    is a new row and the latest row for a record_ref governs at read."""
    if row.decided_at.tzinfo is None:
        raise ValueError("decided_at must be timezone-aware")
    path = adjudication_log_path(client_name, log_dir=log_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(row.model_dump_json() + "\n")
    return path


def load_adjudications(client_name: str, *,
                       log_dir: Optional[str] = None) -> list[RelevanceAdjudication]:
    import json
    path = adjudication_log_path(client_name, log_dir=log_dir)
    rows: list[RelevanceAdjudication] = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(RelevanceAdjudication(**json.loads(line)))
                except (ValueError, TypeError):
                    continue
    return rows
