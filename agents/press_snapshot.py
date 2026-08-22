"""Deterministic press-state snapshots for recurring client refreshes.

The snapshot is a read-only projection of artifacts the press already owns.
It never mutates a sweep, board-content file, gate, or verification record.
All clocks are injected, every collection is stably ordered, and canonical
JSON serialization makes the same inputs byte-identical on re-run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


SCHEMA_VERSION = 1
_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_VERIFICATION_REASON = (
    "adversarial verify pass unavailable on this baseline"
)


class PressSnapshotError(ValueError):
    """A source artifact cannot support a comparable press snapshot."""


class SourceArtifact(BaseModel):
    path: str
    sha256: str
    generated_at: Optional[str] = None


class SourceArtifacts(BaseModel):
    sweep: SourceArtifact
    content: Optional[SourceArtifact] = None
    qualify: Optional[SourceArtifact] = None
    client_html: Optional[SourceArtifact] = None
    recompete: Optional[SourceArtifact] = None
    gate: Optional[SourceArtifact] = None
    figure_source: Literal[
        "board_content_registry",
        "fact_pack_cited",
        "fact_pack_source_records",
        "none",
    ]
    figure_source_note: Optional[str] = None


class Candidate(BaseModel):
    id: str
    kind: str
    title: str = ""
    score: int
    tier: Literal["core", "adjacent", "none"]


class CandidateScreen(BaseModel):
    id: str
    kind: str
    title: str = ""
    score: int
    tier: Literal["core", "adjacent", "none"]
    relevant: bool
    off_scope: bool = False
    excluded_by_code: bool = False
    response_deadline: Optional[str] = None
    scope_basis: str = "UNSCOPED"


class FigureValue(BaseModel):
    metric: str
    value: float
    display_text: str
    retrieved_at: Optional[datetime] = None
    generated_internal_id: Optional[str] = None
    component_values: list[float] = Field(default_factory=list)
    disputed: bool = False

    @field_validator("retrieved_at")
    @classmethod
    def _aware_retrieval(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("retrieved_at must be timezone-aware")
        return value.astimezone(timezone.utc) if value is not None else None


class FigureGroup(BaseModel):
    key: str
    source_system: str
    source_record_id: Optional[str] = None
    source_url: Optional[str] = None
    comparable: bool = Field(
        description="false for an unkeyed citation; it cannot assert same-record drift"
    )
    values: list[FigureValue]


class Window(BaseModel):
    key: str
    source_system: str
    source_record_id: str
    field: str
    value: str


class RecompeteEvent(BaseModel):
    key: str
    kind: Literal["recompete"] = "recompete"
    date: str
    source_system: Literal["usaspending"] = "usaspending"
    source_record_id: str
    generated_internal_id: Optional[str] = None
    posture: Literal["attack", "defend"]
    score: Optional[int] = None
    tier: Literal["program"] = "program"


class GateState(BaseModel):
    status: Literal["CLEAN", "DO_NOT_SEND"]
    render_date: str
    violations: list[str] = Field(default_factory=list)

    @field_validator("violations")
    @classmethod
    def _stable_violations(cls, value: list[str]) -> list[str]:
        # Gate details are rendered into the INTERNAL refresh summary on a
        # later press.  Normalize the house-banned punctuation at this schema
        # boundary so both fresh sidecars and legacy on-disk snapshots are
        # safe before they reach the delta engine.  Loading an older snapshot
        # runs this validator too, which makes the migration read-only.
        normalized = (
            re.sub(r"\s*—\s*", ", ", str(item)).strip()
            for item in value
            if str(item).strip()
        )
        return sorted(dict.fromkeys(item for item in normalized if item))


class VerificationState(BaseModel):
    status: Literal["COMPLETE", "DEFERRED"] = "DEFERRED"
    reason: Optional[str] = _DEFAULT_VERIFICATION_REASON
    verified: list[str] = Field(default_factory=list)
    disputed: list[str] = Field(default_factory=list)
    unverifiable: list[str] = Field(default_factory=list)

    @field_validator("verified", "disputed", "unverifiable")
    @classmethod
    def _stable_keys(cls, value: list[str]) -> list[str]:
        return sorted(dict.fromkeys(str(item) for item in value if str(item)))


class PressSnapshot(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    client_name: str
    slug: str
    press_timestamp: datetime
    source_artifacts: SourceArtifacts
    candidates: list[Candidate]
    candidate_screen: list[CandidateScreen]
    figures: list[FigureGroup]
    windows: list[Window]
    recompete_events: list[RecompeteEvent]
    gate_state: GateState
    verification: VerificationState

    @field_validator("press_timestamp")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("press_timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _candidate_projection_is_consistent(self) -> "PressSnapshot":
        screened = {
            (row.kind, row.id): row
            for row in self.candidate_screen
        }
        for candidate in self.candidates:
            row = screened.get((candidate.kind, candidate.id))
            if row is None or not row.relevant:
                raise ValueError(
                    f"qualifying candidate {candidate.kind}:{candidate.id} "
                    "is absent from the relevant screen"
                )
        return self


def client_slug(value: str) -> str:
    return "".join(
        character if character.isalnum() else "_" for character in value
    ).strip("_").lower()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _artifact(path: Path, root: Path, generated_at: Any = None) -> SourceArtifact:
    return SourceArtifact(
        path=_repo_path(path, root),
        sha256=_hash_bytes(path.read_bytes()),
        generated_at=(str(generated_at) if generated_at not in (None, "") else None),
    )


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PressSnapshotError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise PressSnapshotError(f"{label} root is not an object")
    return payload


def _record_id(record: dict[str, Any]) -> str:
    for field in (
        "source_id",
        "generated_id",
        "internal_id",
        "award_id",
        "id",
        "url",
        "api_url",
    ):
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return ""


def _calibration_record_ref(record: dict[str, Any]) -> str:
    """The calibration harness's established comparable identity."""
    return str(
        record.get("source_id")
        or record.get("award_id")
        or record.get("url")
        or ""
    )[:80]


def _record_title(record: dict[str, Any]) -> str:
    return str(
        record.get("title")
        or record.get("description")
        or record.get("recipient")
        or ""
    )[:240]


def _verdict_tier(verdict: Any) -> Literal["core", "adjacent", "none"]:
    tiers = {span.tier for span in verdict.spans}
    if "core" in tiers:
        return "core"
    if "adjacent" in tiers:
        return "adjacent"
    return "none"


def _score_candidates(
    client_name: str,
    sweep: dict[str, Any],
) -> tuple[list[Candidate], list[CandidateScreen]]:
    # The calibration harness owns both the scored record population and
    # engagement-scope precedence. Import those owners rather than creating a
    # second sweep walker or scope interpretation in the snapshot layer.
    from tools.relevance.calibrate import iter_records, resolve_sweep_scope
    from tools.relevance.engine import score_record
    from tools.relevance.taxonomy import derived_taxonomy, load_taxonomy

    taxonomy = load_taxonomy(client_name) or derived_taxonomy(client_name)
    if taxonomy is None:
        raise PressSnapshotError(
            f"no capability taxonomy or profile for {client_name!r}; "
            "relevance state cannot be snapshotted"
        )
    engagement_scope, _basis = resolve_sweep_scope(client_name, sweep)
    screen: list[CandidateScreen] = []
    seen: set[str] = set()
    for kind, record in iter_records(sweep):
        identity = _calibration_record_ref(record)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        verdict = score_record(
            record, taxonomy, engagement_scope=engagement_scope
        )
        screen.append(CandidateScreen(
            id=identity,
            kind=kind,
            title=_record_title(record),
            score=verdict.score,
            tier=_verdict_tier(verdict),
            relevant=verdict.relevant,
            off_scope=verdict.off_scope,
            excluded_by_code=verdict.excluded_by_code,
            response_deadline=(
                str(record.get("response_deadline"))
                if record.get("response_deadline") not in (None, "")
                else None
            ),
            scope_basis=verdict.scope_basis,
        ))
    screen.sort(key=lambda row: (row.id, row.kind))
    candidates = [
        Candidate(
            id=row.id,
            kind=row.kind,
            title=row.title,
            score=row.score,
            tier=row.tier,
        )
        for row in screen
        if row.relevant
    ]
    return candidates, screen


def _close_money(left: Any, right: Any) -> bool:
    return (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
        and abs(float(left) - float(right)) < 0.005
    )


def _figure_metric(figure: Any, backing: Optional[dict[str, Any]]) -> str:
    if figure.component_raws:
        return "component_sum"
    if backing is not None:
        for field in ("amount", "potential_ceiling", "value"):
            if _close_money(figure.raw, backing.get(field)):
                return field
    return "reported_value"


def _figure_groups(content: Any, sweep: dict[str, Any]) -> list[FigureGroup]:
    results = sweep.get("results") or {}
    backing_by_id: dict[str, dict[str, Any]] = {}
    for lane in ("award_repulls", "agency_docs"):
        for row in results.get(lane) or []:
            if not isinstance(row, dict):
                continue
            for identity in (
                row.get("generated_id"),
                row.get("award_id"),
                row.get("source_record_id"),
            ):
                if identity:
                    backing_by_id.setdefault(str(identity), row)

    grouped: dict[str, dict[str, Any]] = {}
    for index, figure in enumerate(content.figures):
        if figure.raw is None:
            continue
        system = getattr(figure.source_system, "value", figure.source_system)
        system = str(system or "unknown")
        record_id = figure.source_record_id or figure.generated_internal_id
        comparable = bool(record_id)
        if comparable:
            key = f"{system}:{record_id}"
        else:
            source_digest = hashlib.sha256(
                str(figure.source_url or "").encode("utf-8")
            ).hexdigest()[:12]
            key = f"unkeyed:{system}:{index:04d}:{source_digest}"
        backing = (
            backing_by_id.get(str(figure.generated_internal_id or ""))
            or backing_by_id.get(str(figure.source_record_id or ""))
        )
        explicit_dispute = bool((backing or {}).get("disputed", False))
        slot = grouped.setdefault(key, {
            "source_system": system,
            "source_record_id": str(record_id) if record_id else None,
            "source_urls": set(),
            "comparable": comparable,
            "values": [],
        })
        if figure.source_url:
            slot["source_urls"].add(str(figure.source_url))
        slot["values"].append(FigureValue(
            metric=_figure_metric(figure, backing),
            value=float(figure.raw),
            display_text=figure.text,
            retrieved_at=figure.retrieved_at,
            generated_internal_id=figure.generated_internal_id,
            component_values=[float(value) for value in (figure.component_raws or [])],
            disputed=explicit_dispute,
        ))

    groups: list[FigureGroup] = []
    for key in sorted(grouped):
        row = grouped[key]
        observations = sorted(
            row["values"],
            key=lambda value: (
                value.metric,
                value.value,
                value.display_text,
                value.retrieved_at.isoformat() if value.retrieved_at else "",
                value.generated_internal_id or "",
            ),
        )
        # Exact duplicates add no state and make reordered registry rows noisy.
        unique: list[FigureValue] = []
        seen: set[str] = set()
        for value in observations:
            fingerprint = value.model_dump_json()
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(value)
        groups.append(FigureGroup(
            key=key,
            source_system=row["source_system"],
            source_record_id=row["source_record_id"],
            source_url=(sorted(row["source_urls"])[0]
                        if row["source_urls"] else None),
            comparable=row["comparable"],
            values=unique,
        ))
    return groups


def _fact_pack_figure_groups(
    pack: Any,
    *,
    cited_ids: Optional[set[str]],
) -> list[FigureGroup]:
    """Project only explicit numeric payload fields from FactPack facts.

    A FactPack is the honest fallback for assessment clients without a Signal
    Board registry. Nested aggregates are deliberately not guessed apart: only
    named, top-level numeric fields are figures "where possible". Unkeyed facts
    remain present but non-comparable, so they can never manufacture
    same-record drift.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for index, fact in enumerate(pack.facts):
        if cited_ids is not None and fact.id not in cited_ids:
            continue
        payload = fact.value if isinstance(fact.value, dict) else {}
        numbers = [
            (str(metric), float(value))
            for metric, value in payload.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not numbers:
            continue
        system_value = getattr(fact.source_system, "value", fact.source_system)
        system = str(system_value or "unknown")
        record_id = str(fact.source_record_id) if fact.source_record_id else None
        comparable = record_id is not None
        if record_id:
            key = f"{system}:{record_id}"
        else:
            source_digest = hashlib.sha256(
                str(fact.source or "").encode("utf-8")
            ).hexdigest()[:12]
            key = f"unkeyed:{system}:fact:{index:04d}:{source_digest}"
        slot = grouped.setdefault(key, {
            "source_system": system,
            "source_record_id": record_id,
            "source_urls": set(),
            "comparable": comparable,
            "values": [],
        })
        if fact.source:
            slot["source_urls"].add(str(fact.source))
        generated = payload.get("generated_id") or payload.get("internal_id")
        for metric, value in sorted(numbers):
            slot["values"].append(FigureValue(
                metric=metric,
                value=value,
                display_text=fact.text,
                retrieved_at=fact.retrieved_at,
                generated_internal_id=str(generated) if generated else None,
                disputed=bool(fact.disputed),
            ))

    groups: list[FigureGroup] = []
    for key in sorted(grouped):
        row = grouped[key]
        values = sorted(
            row["values"],
            key=lambda value: (
                value.metric,
                value.value,
                value.display_text,
                value.retrieved_at.isoformat() if value.retrieved_at else "",
            ),
        )
        groups.append(FigureGroup(
            key=key,
            source_system=row["source_system"],
            source_record_id=row["source_record_id"],
            source_url=(sorted(row["source_urls"])[0]
                        if row["source_urls"] else None),
            comparable=row["comparable"],
            values=values,
        ))
    return groups


@contextmanager
def _isolated_entity_telemetry():
    """Fact assembly may resolve companies; snapshots never grow telemetry."""
    previous = os.environ.get("LILA_ENTITIES_DIR")
    with tempfile.TemporaryDirectory(prefix="lila-press-snapshot-") as temp:
        os.environ["LILA_ENTITIES_DIR"] = temp
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("LILA_ENTITIES_DIR", None)
            else:
                os.environ["LILA_ENTITIES_DIR"] = previous


def _fact_pack_fallback(
    client_name: str,
    sweep: dict[str, Any],
    *,
    press_timestamp: datetime,
    qualify: dict[str, Any],
    client_html: Optional[Path],
) -> tuple[list[FigureGroup], str, str]:
    from agents.reports.facts import build_fact_pack

    cited_ids: Optional[set[str]] = None
    mode = "fact_pack_source_records"
    note = (
        "no exact client HTML citation set; included explicit numeric fields "
        "from source-record facts"
    )
    if client_html is not None:
        from agents.reports.verification import cited_fact_ids
        cited_ids = cited_fact_ids(client_html.read_text(encoding="utf-8"))
        mode = "fact_pack_cited"
        note = f"restricted to {len(cited_ids)} fact id(s) cited by client HTML"
    with _isolated_entity_telemetry():
        pack = build_fact_pack(
            client_name,
            searches=sweep,
            qualify_report=qualify,
            as_of=press_timestamp.astimezone(timezone.utc).date(),
        )
    return _fact_pack_figure_groups(pack, cited_ids=cited_ids), mode, note


def _insert_window(rows: dict[str, Window], window: Window) -> None:
    prior = rows.get(window.key)
    if prior is not None and prior != window:
        raise PressSnapshotError(
            f"window identity {window.key!r} has conflicting values"
        )
    rows[window.key] = window


def _windows(
    sweep: dict[str, Any],
    calendar: Optional[dict[str, Any]],
) -> list[Window]:
    results = sweep.get("results") or {}
    found: dict[str, Window] = {}
    for row in results.get("sam.gov") or []:
        if not isinstance(row, dict):
            continue
        identity = _record_id(row)
        value = row.get("response_deadline")
        if identity and value not in (None, ""):
            _insert_window(found, Window(
                key=f"sam:{identity}:response_deadline",
                source_system="sam",
                source_record_id=identity,
                field="response_deadline",
                value=str(value),
            ))
    for row in results.get("award_repulls") or []:
        if not isinstance(row, dict):
            continue
        identity = str(row.get("generated_id") or row.get("award_id") or "")
        if not identity:
            continue
        for field in ("end_date", "potential_end_date"):
            value = row.get(field)
            if value not in (None, ""):
                _insert_window(found, Window(
                    key=f"usaspending:{identity}:{field}",
                    source_system="usaspending",
                    source_record_id=identity,
                    field=field,
                    value=str(value),
                ))
    forecasts = results.get("forecast_signals") or {}
    for raw in forecasts.get("matched") or [] if isinstance(forecasts, dict) else []:
        if not isinstance(raw, dict):
            continue
        row = raw.get("record") if isinstance(raw.get("record"), dict) else raw
        identity = _record_id(row)
        if not identity:
            continue
        for field in ("anticipated_solicitation", "anticipated_award"):
            value = row.get(field)
            if value not in (None, ""):
                _insert_window(found, Window(
                    key=f"forecast:{identity}:{field}",
                    source_system=str(row.get("source") or "forecast"),
                    source_record_id=identity,
                    field=field,
                    value=str(value),
                ))
    for posture in ("attack", "defend"):
        for row in (calendar or {}).get(posture) or []:
            if not isinstance(row, dict):
                continue
            identity = str(row.get("award_id") or row.get("internal_id") or "")
            value = row.get("pop_end")
            if identity and value not in (None, ""):
                _insert_window(found, Window(
                    key=f"recompete:{identity}:pop_end",
                    source_system="usaspending",
                    source_record_id=identity,
                    field="pop_end",
                    value=str(value),
                ))
    return [found[key] for key in sorted(found)]


def _recompete_events(
    calendar: Optional[dict[str, Any]],
) -> list[RecompeteEvent]:
    found: dict[str, RecompeteEvent] = {}
    for posture in ("attack", "defend"):
        for row in (calendar or {}).get(posture) or []:
            if not isinstance(row, dict):
                continue
            identity = str(row.get("award_id") or row.get("internal_id") or "")
            event_date = row.get("pop_end")
            if not identity or event_date in (None, ""):
                continue
            key = f"usaspending:{identity}:recompete"
            event = RecompeteEvent(
                key=key,
                date=str(event_date),
                source_record_id=identity,
                generated_internal_id=(
                    str(row.get("internal_id")) if row.get("internal_id") else None
                ),
                posture=posture,
                score=(int(row["score"])
                       if isinstance(row.get("score"), (int, float)) else None),
            )
            prior = found.get(key)
            if prior is not None and prior != event:
                raise PressSnapshotError(
                    f"recompete identity {key!r} has conflicting rows"
                )
            found[key] = event
    return [found[key] for key in sorted(found)]


_RENDER_DATE_RE = re.compile(r"^_render date (\d{4}-\d{2}-\d{2})\b", re.M)


def parse_signal_board_gate(
    path: str | Path,
    *,
    press_timestamp: datetime,
) -> GateState:
    """Parse the press-owned INTERNAL verdict.

    The board's render date is host-local and a standalone snapshot may
    intentionally capture the latest stored press. The orchestrator owns the
    file-generation freshness token; UTC date equality would reject valid
    stored presses around midnight and during replay.
    """
    if press_timestamp.tzinfo is None or press_timestamp.utcoffset() is None:
        raise PressSnapshotError("press_timestamp must be timezone-aware")
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PressSnapshotError(f"Signal Board INTERNAL sidecar is unreadable: {exc}") from exc
    match = _RENDER_DATE_RE.search(text)
    if match is None:
        raise PressSnapshotError("Signal Board INTERNAL sidecar has no render date")
    render_date = match.group(1)

    verdict_match = re.search(
        r"^## Gate verdict\s*$\n- (CLEAN|DO-NOT-SEND)\b",
        text,
        re.M,
    )
    if verdict_match is None:
        raise PressSnapshotError("Signal Board INTERNAL sidecar has no gate verdict")
    status = "CLEAN" if verdict_match.group(1) == "CLEAN" else "DO_NOT_SEND"

    violations: list[str] = []
    section = re.search(
        r"^## Violations\s*$\n(?P<body>.*?)(?=^## |\Z)",
        text,
        re.M | re.S,
    )
    if section is not None:
        current: Optional[str] = None
        for line in section.group("body").splitlines():
            if line.startswith("- "):
                if current:
                    violations.append(current)
                current = line[2:].strip()
            elif current and line.strip():
                current += " " + line.strip()
        if current:
            violations.append(current)
    if status == "CLEAN" and violations:
        raise PressSnapshotError("CLEAN gate sidecar also lists violations")
    if status == "DO_NOT_SEND" and not violations:
        raise PressSnapshotError("DO-NOT-SEND gate sidecar lists no violations")
    return GateState(
        status=status,
        render_date=render_date,
        violations=violations,
    )


def _assessment_gate(
    qa_path: Path,
    *,
    press_timestamp: datetime,
) -> GateState:
    """Fail-closed compatibility gate for non-Signal-Board assessments."""
    payload = _load_object(qa_path, "assessment QA sidecar")
    state = str(payload.get("state") or "").lower()
    status = "CLEAN" if state == "release" else "DO_NOT_SEND"
    violations = []
    for action in payload.get("actions") or []:
        if (isinstance(action, dict)
                and str(action.get("action") or "").lower() == "flag"):
            reason = str(action.get("reason") or "").strip()
            if reason:
                violations.append(reason)
    if status == "DO_NOT_SEND" and not violations:
        violations.append(
            f"assessment QA state is {state or 'unknown'}, not release"
        )
    return GateState(
        status=status,
        render_date=press_timestamp.astimezone(timezone.utc).date().isoformat(),
        violations=violations,
    )


def build_press_snapshot(
    client_name: str,
    *,
    press_timestamp: datetime,
    root: str | Path = _ROOT,
    sweep_path: Optional[str | Path] = None,
    content_path: Optional[str | Path] = None,
    qualify_path: Optional[str | Path] = None,
    client_html_path: Optional[str | Path] = None,
    recompete_path: Optional[str | Path] = None,
    gate_path: Optional[str | Path] = None,
    assessment_qa_path: Optional[str | Path] = None,
    gate_state: Optional[GateState | dict[str, Any]] = None,
    verification: Optional[VerificationState | dict[str, Any]] = None,
) -> PressSnapshot:
    """Build, but do not persist, one comparable press snapshot."""
    if press_timestamp.tzinfo is None or press_timestamp.utcoffset() is None:
        raise PressSnapshotError("press_timestamp must be timezone-aware")
    root = Path(root)
    slug = client_slug(client_name)
    if sweep_path is None:
        from agents.review import sweep_artifact_path
        sweep_path = sweep_artifact_path(client_name)
    sweep_file = Path(sweep_path)
    content_file = Path(content_path or root / "clients" / slug / "signal_board_content.json")
    qualify_file = Path(
        qualify_path or root / "data" / "review" / f"{slug}.qualify.json"
    )
    recompete_file = Path(recompete_path or root / "data" / "state" / "recompete" / f"{slug}.json")
    gate_file = Path(gate_path or root / "data" / "reports" / f"{slug}.federal_opportunity_signals.internal.md")
    assessment_qa_file = Path(
        assessment_qa_path
        or root / "data" / "reports"
        / f"{slug}.federal_opportunity_assessment.qa.json"
    )

    sweep = _load_object(sweep_file, "stored sweep")
    if sweep.get("client") not in (None, client_name):
        raise PressSnapshotError("stored sweep belongs to another client")

    content = None
    if content_file.exists():
        from agents.reports.board_content import SignalBoardContent
        try:
            content = SignalBoardContent.model_validate(
                _load_object(content_file, "Signal Board content")
            )
        except ValueError as exc:
            raise PressSnapshotError(f"Signal Board content is invalid: {exc}") from exc
        if content.client_name != client_name:
            raise PressSnapshotError("Signal Board content belongs to another client")

    calendar = None
    if recompete_file.exists():
        calendar = _load_object(recompete_file, "recompete calendar")
        if calendar.get("client") not in (None, client_name, client_name.upper()):
            raise PressSnapshotError("recompete calendar belongs to another client")

    gate_artifact_file: Optional[Path] = None
    if gate_state is not None:
        gate = (
            gate_state
            if isinstance(gate_state, GateState)
            else GateState.model_validate(gate_state)
        )
    elif gate_file.exists():
        gate = parse_signal_board_gate(
            gate_file, press_timestamp=press_timestamp
        )
        gate_artifact_file = gate_file
    elif assessment_qa_file.exists():
        gate = _assessment_gate(
            assessment_qa_file, press_timestamp=press_timestamp
        )
        gate_artifact_file = assessment_qa_file
    else:
        gate = GateState(
            status="DO_NOT_SEND",
            render_date=press_timestamp.astimezone(
                timezone.utc).date().isoformat(),
            violations=[
                "no press gate artifact is available; release state is "
                "held closed"
            ],
        )
    candidates, screen = _score_candidates(client_name, sweep)
    qualify: dict[str, Any] = {}
    if qualify_file.exists():
        qualify = _load_object(qualify_file, "qualify report")
    selected_html: Optional[Path] = None
    if client_html_path is not None:
        candidate_html = Path(client_html_path)
        if candidate_html.exists():
            selected_html = candidate_html
    else:
        for candidate_html in (
            root / "data" / "reports"
            / f"{slug}.federal_opportunity_assessment.html",
            root / "data" / "reports" / f"{slug}.assessment.client.html",
        ):
            if candidate_html.exists():
                selected_html = candidate_html
                break
    if content is not None:
        figures = _figure_groups(content, sweep)
        figure_source = "board_content_registry"
        figure_source_note = None
    else:
        figures, figure_source, figure_source_note = _fact_pack_fallback(
            client_name,
            sweep,
            press_timestamp=press_timestamp,
            qualify=qualify,
            client_html=selected_html,
        )
    verification_state = (
        verification
        if isinstance(verification, VerificationState)
        else VerificationState.model_validate(verification or {})
    )

    artifacts = SourceArtifacts(
        sweep=_artifact(sweep_file, root, sweep.get("generated_at")),
        content=(
            _artifact(content_file, root) if content is not None else None
        ),
        qualify=(
            _artifact(qualify_file, root, qualify.get("generated_at"))
            if qualify_file.exists() else None
        ),
        client_html=(
            _artifact(selected_html, root) if selected_html is not None else None
        ),
        recompete=(
            _artifact(recompete_file, root, (calendar or {}).get("generated"))
            if calendar is not None else None
        ),
        gate=(
            _artifact(gate_artifact_file, root, gate.render_date)
            if gate_artifact_file is not None else None
        ),
        figure_source=figure_source,
        figure_source_note=figure_source_note,
    )
    return PressSnapshot(
        client_name=client_name,
        slug=slug,
        press_timestamp=press_timestamp,
        source_artifacts=artifacts,
        candidates=candidates,
        candidate_screen=screen,
        figures=figures,
        windows=_windows(sweep, calendar),
        recompete_events=_recompete_events(calendar),
        gate_state=gate,
        verification=verification_state,
    )


def canonical_snapshot_bytes(snapshot: PressSnapshot) -> bytes:
    payload = snapshot.model_dump(mode="json")
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def snapshot_filename(press_timestamp: datetime) -> str:
    if press_timestamp.tzinfo is None or press_timestamp.utcoffset() is None:
        raise PressSnapshotError("press_timestamp must be timezone-aware")
    moment = press_timestamp.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H%M%SZ.json")


def write_press_snapshot(
    snapshot: PressSnapshot,
    *,
    state_root: Optional[str | Path] = None,
) -> Path:
    """Atomically persist under data/state/deltas/<slug>/<timestamp>.json."""
    from tools.atomic_io import atomic_write_text

    base = Path(state_root or _ROOT / "data" / "state" / "deltas")
    path = base / snapshot.slug / snapshot_filename(snapshot.press_timestamp)
    atomic_write_text(str(path), canonical_snapshot_bytes(snapshot).decode("utf-8"))
    return path
