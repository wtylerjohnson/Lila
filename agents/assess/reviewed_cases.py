"""Stored, evidence-bound research enters the ordinary immutable Assess ledger.

This supplements discovery without changing engagement scope, source census
coverage, requirement approval, or qualification. Revisions are journaled.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.assess.contracts import LiveClassification, LiveSolicitation
from agents.leadgen.targets import LeadResearch, LeadTarget
from tools.slug import client_slug


class ReviewedCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record: LiveSolicitation
    research: LeadResearch
    targets: tuple[LeadTarget, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def cannot_certify_a_bid(self):
        if self.record.classification == LiveClassification.BID_NOW:
            raise ValueError("reviewed research cannot approve a bid_now record")
        return self


class ReviewedCases(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["reviewed_cases.v1"] = "reviewed_cases.v1"
    client_name: str = Field(min_length=1)
    scope_designator: str = "all"
    cases: tuple[ReviewedCase, ...] = ()

    @model_validator(mode="after")
    def unique_notices(self):
        ids = [c.record.notice_id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate reviewed notice IDs")
        return self


def load_cases(client_name: str, review_dir: Path) -> ReviewedCases:
    path = review_dir / f"{client_slug(client_name)}.reviewed_cases.json"
    if not path.exists():
        return ReviewedCases(client_name=client_name)
    book = ReviewedCases.model_validate_json(path.read_bytes())
    if book.client_name.casefold() != client_name.casefold():
        raise ValueError("reviewed cases belong to a different client")
    return book


def save_cases(book: ReviewedCases, review_dir: Path) -> Path:
    """Append immutable revision before replacing current research input."""
    from tools.artifacts import atomic_write_json
    review_dir.mkdir(parents=True, exist_ok=True)
    name = client_slug(book.client_name)
    current = review_dir / f"{name}.reviewed_cases.json"
    history = review_dir / "research_history" / name
    history.mkdir(parents=True, exist_ok=True)
    payload = book.model_dump(mode="json")
    # Preserve the previous selection and rejection reasons, including imports.
    versions = [json.loads(current.read_text())] if current.exists() else []
    versions.append(payload)
    for version in versions:
        raw = json.dumps(version, sort_keys=True, ensure_ascii=False).encode()
        archive = history / f"{hashlib.sha256(raw).hexdigest()}.json"
        if not archive.exists():
            atomic_write_json(archive, version)
    atomic_write_json(current, payload)
    return current


def supplement_live(live, book: ReviewedCases, scope_designator: str):
    if not book.cases:
        return live
    if book.scope_designator != scope_designator:
        raise ValueError("reviewed cases do not match the current engagement scope")
    records = {r.notice_id: r for r in live.records}
    for case in book.cases:
        # Discovery owns existing records; research never overwrites its status.
        records.setdefault(case.record.notice_id, case.record)
    return type(live).model_validate({
        **live.model_dump(mode="python"), "records": tuple(records.values())})
