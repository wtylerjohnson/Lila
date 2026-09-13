"""Stored, evidence-bound research enters the ordinary immutable Assess ledger.

This supplements discovery without changing engagement scope, source census
coverage, requirement approval, or qualification. Revisions are journaled.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

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


class ReviewedSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    research: LeadResearch
    targets: tuple[LeadTarget, ...] = ()


class ReviewedCases(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["reviewed_cases.v1", "reviewed_cases.v2"] = "reviewed_cases.v1"
    client_name: str = Field(min_length=1)
    scope_designator: str = "all"
    cases: tuple[ReviewedCase, ...] = ()
    subjects: tuple[ReviewedSubject, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def versioned_subjects(cls, value):
        if isinstance(value, dict) and value.get("schema_version", "reviewed_cases.v1") == "reviewed_cases.v1" and "subjects" in value:
            raise ValueError("typed subjects require reviewed_cases.v2")
        return value

    @model_serializer(mode="wrap")
    def legacy_serialization(self, handler):
        value = handler(self)
        if self.schema_version == "reviewed_cases.v1":
            value.pop("subjects", None)
        return value


    @model_validator(mode="after")
    def unique_notices(self):
        if self.subjects and self.schema_version != "reviewed_cases.v2":
            raise ValueError("typed subjects require reviewed_cases.v2")
        subject_ids = [c.subject_id for c in self.subjects]
        if len(subject_ids) != len(set(subject_ids)):
            raise ValueError("duplicate reviewed subject IDs")
        ids = [c.record.notice_id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate reviewed notice IDs")
        return self


def load_cases(client_name: str, review_dir: Path, *,
               expected_sha256: str | None = None) -> ReviewedCases:
    path = review_dir / f"{client_slug(client_name)}.reviewed_cases.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if expected_sha256 not in (None, "missing"):
            raise ValueError("bound reviewed cases are missing")
        return ReviewedCases(client_name=client_name)
    if (expected_sha256 is not None
            and hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise ValueError("reviewed cases changed from the bound projection input")
    book = ReviewedCases.model_validate_json(raw)
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


def current_case_record(record: LiveSolicitation) -> LiveSolicitation:
    """Deterministic v2 projection; preserve the immutable research input."""
    from agents.assess.source_clock import acquired_at, acquisition_clock
    row = record.model_dump(mode="json")
    for evidence in row["authoritative_evidence"]:
        if evidence.get("source_acquisition") is None:
            binding = evidence.get("record_hash") or hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            evidence["source_acquisition"] = acquisition_clock(
                evidence.get("retrieved_at"), basis="none", component="notice_payload",
                field="legacy_reviewed_case.retrieved_at", binding=binding).model_dump(mode="json")
            evidence["record_hash"] = binding
            evidence["retrieved_at"] = None
    if any(acquired_at(e) is None for e in row["authoritative_evidence"]):
        names = ("requirement_review_evidence_id", "requirement_reviewed_by",
                 "requirement_reviewed_at", "attachment_reviewed_by", "attachment_reviewed_at")
        prior = {name: row.get(name) for name in names if row.get(name) is not None}
        if prior:
            row["fit_trace"].append(
                "Retained historical review; acquisition chronology is not established: "
                + json.dumps(prior, sort_keys=True))
            for name in names:
                row[name] = None
            if row["attachments"]:
                row["attachments_checked"] = False
            row["attachment_gap"] = "; ".join(filter(None, (row.get("attachment_gap"),
                "Source acquisition chronology is not established")))
    return LiveSolicitation.model_validate(row)


def supplement_live(live, book: ReviewedCases, scope_designator: str):
    if not book.cases:
        return live
    if book.scope_designator != scope_designator:
        raise ValueError("reviewed cases do not match the current engagement scope")
    records = {r.notice_id: r for r in live.records}
    for case in book.cases:
        # Discovery owns existing records; research never overwrites its status.
        records.setdefault(case.record.notice_id,
            current_case_record(case.record) if live.run_id.startswith(("assess:v2:", "assess:v3:")) else case.record)
    return type(live).model_validate({
        **live.model_dump(mode="python"), "records": tuple(records.values())})
