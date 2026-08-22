"""Historical backfill — build the contact graph from what's already on disk.

Walks, in order of POC richness (see docs/API_SETUP.md, verified 2026-07-05):
  1. data/cleaned/searches_<client>.json  → results["sam.gov"][].raw_payload
     — the primary source; the original notice (with pointOfContact) is here.
  2. data/cache/sam/*.json                → payload.opportunitiesData[]
     — the raw search cache (today: empty probe responses, walked defensively).
  3. data/cache/sam_notice/*.json         → the permanent notice/detail cache
     — holds description/attachments only, no POCs (each is a coverage gap).

Two hard constraints:
  - ZERO live API calls. Backfill only reads disk. A notice with no POC data on
    disk is logged as a coverage gap and skipped — never re-fetched.
  - Idempotent. Every write goes through the store's dedupe (notice + person +
    channel), so an interrupted run can be re-run safely and a notice cached in
    two places is stored once.

The notice's original posted date is preserved as observed_at (the grade basis);
the harvest date is preserved separately as harvested_at.
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from tools.contact_graph.extract import observations_from_notice
from tools.contact_graph.resolve import rebuild
from tools.contact_graph.store import ContactGraphStore

DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"


def _notice_id(notice: dict) -> Optional[str]:
    payload = notice.get("raw_payload") if isinstance(notice.get("raw_payload"), dict) else {}
    return (
        (payload or {}).get("noticeId")
        or notice.get("source_id")
        or notice.get("noticeId")
        or notice.get("id")
    )


def iter_cleaned(data: Path) -> Iterator[tuple[dict, str]]:
    for f in sorted(glob.glob(str(data / "cleaned" / "searches_*.json"))):
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sam = (doc.get("results") or {}).get("sam.gov")
        if isinstance(sam, list):
            for rec in sam:
                if isinstance(rec, dict):
                    yield rec, os.path.basename(f)


def iter_raw_cache(data: Path) -> Iterator[tuple[dict, str]]:
    for f in sorted(glob.glob(str(data / "cache" / "sam" / "*.json"))):
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        payload = doc.get("payload") if isinstance(doc, dict) else None
        opps = (payload or {}).get("opportunitiesData") if isinstance(payload, dict) else None
        for o in opps or []:
            if isinstance(o, dict):
                yield o, os.path.basename(f)


def iter_detail_cache(data: Path) -> Iterator[tuple[dict, str]]:
    for f in sorted(glob.glob(str(data / "cache" / "sam_notice" / "*.json"))):
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(doc, dict):
            yield doc, os.path.basename(f)


@dataclass
class SourceReport:
    label: str
    files: int = 0
    notices: int = 0
    with_pocs: int = 0
    coverage_gaps: int = 0
    written: int = 0
    skipped: int = 0


@dataclass
class BackfillReport:
    sources: list[SourceReport] = field(default_factory=list)
    coverage_gap_ids: list[str] = field(default_factory=list)
    profiles: int = 0
    review: int = 0

    @property
    def total_written(self) -> int:
        return sum(s.written for s in self.sources)

    @property
    def total_skipped(self) -> int:
        return sum(s.skipped for s in self.sources)

    @property
    def total_gaps(self) -> int:
        return sum(s.coverage_gaps for s in self.sources)

    def summary(self) -> str:
        lines = ["Contact-graph backfill (zero live API calls):"]
        for s in self.sources:
            lines.append(
                f"  {s.label}: {s.files} files, {s.notices} notices, {s.with_pocs} with POCs, "
                f"{s.coverage_gaps} coverage gaps → {s.written} written, {s.skipped} already stored"
            )
        lines.append(
            f"  TOTAL: {self.total_written} observations written, "
            f"{self.total_skipped} already stored (idempotent), {self.total_gaps} coverage gaps"
        )
        lines.append(f"  index: {self.profiles} profiles, {self.review} ambiguous candidates for review")
        return "\n".join(lines)


def backfill(
    *,
    store: Optional[ContactGraphStore] = None,
    workspace: Optional[Path] = None,
    harvested_at: Optional[datetime] = None,
    rebuild_index: bool = True,
    log: Callable[[str], Any] = print,
) -> BackfillReport:
    """Harvest observations from all on-disk artifacts. Idempotent, offline.

    Not gated by LILA_ENABLE_CONTACT_GRAPH: running this entry point *is* the
    intent. The toggle governs the passive inline side effect, not an explicit
    operator command.
    """
    store = store or ContactGraphStore()
    data = Path(workspace) if workspace else DEFAULT_DATA
    harvested_at = harvested_at or datetime.now()
    report = BackfillReport()

    passes = [
        ("cleaned artifacts", iter_cleaned(data)),
        ("raw search cache", iter_raw_cache(data)),
        ("permanent notice cache", iter_detail_cache(data)),
    ]
    for label, iterator in passes:
        sr = SourceReport(label=label)
        files: set[str] = set()
        batch = []
        for notice, origin in iterator:
            sr.notices += 1
            files.add(origin)
            obs = observations_from_notice(notice, harvested_at=harvested_at)
            if obs:
                sr.with_pocs += 1
                batch.extend(obs)
            else:
                sr.coverage_gaps += 1
                gid = _notice_id(notice)
                if gid:
                    report.coverage_gap_ids.append(gid)
        sr.files = len(files)
        result = store.append(batch)  # dedupe is shared across passes via `store`
        sr.written = result.written
        sr.skipped = result.skipped
        report.sources.append(sr)
        log(
            f"[backfill] {label}: {sr.files} files, {sr.notices} notices, "
            f"{sr.with_pocs} with POCs, {sr.coverage_gaps} gaps → "
            f"{sr.written} written, {sr.skipped} already stored"
        )

    if rebuild_index:
        profiles, review = rebuild(store, now=harvested_at.date())
        report.profiles = len(profiles)
        report.review = len(review)
        log(f"[backfill] index rebuilt: {report.profiles} profiles, {report.review} ambiguous candidates")

    return report
