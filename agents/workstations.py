"""Persistent scope-workstation identity, creation, and discovery.

Listing or selecting a workstation is observational.  Tranche 3 adds one
explicit, journaled creation door and workstation-native strategy packets,
but creation is not migration: an alternate native scope must not silently
retire the exact legacy-current workstation.  Pointer, approval, report, and
Target cutover remain later, separately receipted operator actions.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import fcntl
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


class WorkstationError(ValueError):
    """A workstation identity or registry is malformed."""


class WorkstationScope(BaseModel):
    """Deeply immutable canonical scope.

    A plain dict inside a frozen Pydantic model is still mutable.  Workstation
    identity cannot tolerate that: appending an agency after validation would
    leave the id bound to different semantics.  The tuple-backed model closes
    that window while serializing to the established wire shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    all: bool = False
    agencies: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _one_scope_shape(self):
        if self.all == bool(self.agencies):
            raise ValueError("scope must be exactly all or one-or-more agencies")
        return self

    @model_serializer
    def _wire_shape(self) -> dict:
        if self.all:
            return {"all": True}
        return {"agencies": list(self.agencies)}

    def as_dict(self) -> dict:
        return {"all": True} if self.all else {"agencies": list(self.agencies)}


class WorkstationRef(BaseModel):
    """Frozen, canonical identity for one client capture workstation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    scope: WorkstationScope
    label: str
    created_at: Optional[datetime] = None
    created_from: str = "derived"
    archived_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_identity(cls, raw: Any):
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        if "scope" not in data:
            raise ValueError("workstation identity requires a scope")
        if not isinstance(data.get("id"), str) or not data["id"].strip():
            raise ValueError("workstation identity requires a nonblank id")
        scope = canonical_scope(data["scope"])
        data["scope"] = scope
        expected_label = workstation_label(scope)
        supplied_label = data.get("label")
        if supplied_label is not None and supplied_label != expected_label:
            raise ValueError(
                f"workstation label {supplied_label!r} does not match scope "
                f"({expected_label!r})")
        data["label"] = expected_label
        return data

    @model_validator(mode="after")
    def _identity_matches_scope(self):
        expected = workstation_id(self.scope)
        if self.id != expected:
            raise ValueError(
                f"workstation id {self.id!r} does not match scope ({expected!r})")
        return self


class WorkstationSummary(BaseModel):
    """Read-model row for the top scope switcher.

    A valid native packet owns only its exact scope.  The legacy-current packet
    remains independently operable during compatibility migration. Historical
    artifacts are reported separately and never imply progress or approval.
    """

    model_config = ConfigDict(frozen=True)

    ref: WorkstationRef
    phase: Literal["dormant", "configure", "search", "assess", "produce", "target"]
    boundary_status: Literal["absent", "pending", "approved", "rejected"]
    is_legacy_current: bool = False
    is_native: bool = False
    sweep_exists: bool = False
    historical_sweep_exists: bool = False
    sweep_status: Literal[
        "absent", "current", "historical", "stale", "invalid"
    ] = "absent"
    historical_artifacts: int = Field(default=0, ge=0)
    diagnostics: tuple[str, ...] = ()


class WorkstationCatalog(BaseModel):
    """Complete read-only workstation catalog for one exact client."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1"
    client_name: str
    workstations: tuple[WorkstationSummary, ...]


class WorkstationCreation(BaseModel):
    """Result of the explicit, idempotent creation transaction."""

    model_config = ConfigDict(frozen=True)

    ref: WorkstationRef
    created: bool
    cloned_baseline: bool


class WorkstationOwnership(BaseModel):
    """Read-only proof that one exact registered workstation was created.

    The hashes are optimistic-concurrency tokens for callers that must bind a
    later packet read or child launch to the same registry/receipt generation.
    Deliberately, this snapshot does not read or validate the mutable packet.
    """

    model_config = ConfigDict(frozen=True)

    ref: WorkstationRef
    clone_baseline: bool
    registry_sha256: str
    receipt_sha256: str


_CREATION_LOCK = threading.RLock()


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (name or "")).strip("_").lower()


def _exact_agency(raw: Any) -> dict:
    """Resolve only an exact abbreviation or canonical name.

    Workstation identity is not autocomplete.  Fuzzy inputs such as ``D``
    must never silently mint DHS.
    """
    from tools.agencies import AGENCIES

    if isinstance(raw, dict):
        abbr = raw.get("abbr")
        name = raw.get("name")
        if not isinstance(abbr, str) or not abbr.strip():
            raise WorkstationError("agency scope object requires a nonblank abbr")
        match = next(
            (row for row in AGENCIES if row["abbr"].lower() == abbr.strip().lower()),
            None,
        )
        if match is None:
            raise WorkstationError(f"unknown agency in workstation scope: {abbr!r}")
        if name is not None and (
                not isinstance(name, str)
                or name.strip().lower() != match["name"].lower()):
            raise WorkstationError("agency scope abbreviation/name mismatch")
        return match
    if not isinstance(raw, str) or not raw.strip():
        raise WorkstationError("agency scope entry must be a nonblank string")
    value = raw.strip().lower()
    match = next(
        (row for row in AGENCIES
         if value in {row["abbr"].lower(), row["name"].lower()}),
        None,
    )
    if match is None:
        raise WorkstationError(f"unknown agency in workstation scope: {raw!r}")
    return match


def canonical_scope(scope: Optional[dict | WorkstationScope]) -> WorkstationScope:
    """Validated canonical scope: ``{'all': True}`` or sorted agency abbrs.

    New workstation identity is order-insensitive.  Existing artifact ids are
    already composed of resolved abbreviations, so common single-agency scopes
    remain byte-identical (``agency_dhs``).
    """
    if isinstance(scope, WorkstationScope):
        scope = scope.as_dict()
    if scope is None:
        return WorkstationScope(all=True)
    if not isinstance(scope, dict):
        raise WorkstationError("workstation scope is not an object")
    unknown = set(scope) - {"all", "agencies", "mode"}
    if unknown:
        raise WorkstationError(
            "workstation scope has unknown field(s): "
            + ", ".join(sorted(str(key) for key in unknown)))
    if not scope:
        raise WorkstationError(
            "workstation scope must explicitly declare all or agencies")
    raw_all = scope.get("all") if "all" in scope else None
    if raw_all is not None and not isinstance(raw_all, bool):
        raise WorkstationError("workstation scope 'all' must be boolean")
    raw_agencies = scope.get("agencies") if "agencies" in scope else None
    if raw_agencies is not None and not isinstance(raw_agencies, (list, tuple)):
        raise WorkstationError("workstation scope agencies must be a list")
    raw_mode = scope.get("mode") if "mode" in scope else None
    if raw_mode is not None and raw_mode != "focus":
        raise WorkstationError("workstation scope mode must be 'focus'")
    raw_agencies = list(raw_agencies or [])
    if raw_all is False:
        raise WorkstationError("workstation scope 'all' may only be true")
    if raw_all is True and "agencies" in scope:
        raise WorkstationError("workstation scope cannot combine all and agencies")
    if raw_all is True and raw_mode is not None:
        raise WorkstationError("all-federal scope cannot declare focus mode")
    if raw_all is True:
        return WorkstationScope(all=True)
    if not raw_agencies:
        raise WorkstationError(
            "workstation scope must explicitly declare all or nonempty agencies")

    from tools.agencies import AGENCIES
    rank = {row["abbr"].lower(): index for index, row in enumerate(AGENCIES)}
    resolved = {_exact_agency(raw)["abbr"] for raw in raw_agencies}
    ordered = tuple(sorted(resolved, key=lambda value: rank[value.lower()]))
    return WorkstationScope(agencies=ordered)


def workstation_id(scope: Optional[dict | WorkstationScope]) -> str:
    """Canonical designator, explicit ``all`` for the unqualified scope."""
    from agents.review import scope_designator

    canonical = canonical_scope(scope)
    return scope_designator(canonical.as_dict()) or "all"


def workstation_label(scope: Optional[dict | WorkstationScope]) -> str:
    canonical = canonical_scope(scope)
    agencies = canonical.agencies
    return "All Federal" if not agencies else " + ".join(agencies)


def registry_path(client_name: str, *, review_dir: os.PathLike | str) -> Path:
    return Path(review_dir) / f"{_slug(client_name)}.workstations.json"


def native_packet_path(client_name: str, workstation_id: str, *,
                       review_dir: os.PathLike | str) -> Path:
    return Path(review_dir) / (
        f"{_slug(client_name)}.{workstation_id}.review.json")


def creation_receipt_path(client_name: str, workstation_id: str, *,
                          review_dir: os.PathLike | str) -> Path:
    return Path(review_dir) / (
        f"{_slug(client_name)}.{workstation_id}.workstation_receipt.json")


def _ref_from_payload(raw: dict) -> WorkstationRef:
    if not isinstance(raw, dict):
        raise WorkstationError("workstation registry entry is not an object")
    unknown = set(raw) - {
        "id", "scope", "label", "created_at", "created_from", "archived_at"}
    if unknown:
        raise WorkstationError(
            "workstation registry entry has unknown field(s): "
            + ", ".join(sorted(str(key) for key in unknown)))
    if not isinstance(raw.get("id"), str) or not raw["id"].strip():
        raise WorkstationError("workstation registry entry requires an id")
    if not isinstance(raw.get("scope"), dict):
        raise WorkstationError("workstation registry entry requires a scope object")
    if "created_from" in raw and (
            not isinstance(raw["created_from"], str)
            or not raw["created_from"].strip()):
        raise WorkstationError("workstation registry created_from must be a string")
    scope = canonical_scope(raw["scope"])
    # Registry identity is persisted truth, not a repair opportunity.  Require
    # its stored scope to already be canonical.
    if raw["scope"] != scope.as_dict():
        raise WorkstationError("workstation registry scope is not canonical")
    try:
        return WorkstationRef(
            id=raw["id"],
            scope=scope,
            label=raw.get("label", workstation_label(scope)),
            created_at=raw.get("created_at"),
            created_from=raw.get("created_from") or "registry",
            archived_at=raw.get("archived_at"),
        )
    except Exception as exc:  # pydantic validation -> one domain error
        raise WorkstationError(f"invalid workstation registry entry: {exc}") from exc


def _parse_registry_payload(client_name: str, payload: Any) \
        -> tuple[WorkstationRef, ...]:
    if not isinstance(payload, dict):
        raise WorkstationError("workstation registry root is not an object")
    unknown = set(payload) - {"schema_version", "client_name", "workstations"}
    if unknown:
        raise WorkstationError(
            "workstation registry has unknown field(s): "
            + ", ".join(sorted(str(key) for key in unknown)))
    exact = payload.get("client_name")
    if (not isinstance(exact, str) or exact != exact.strip()
            or exact != client_name):
        raise WorkstationError("workstation registry client identity mismatch")
    if payload.get("schema_version") != "1":
        raise WorkstationError("unsupported workstation registry schema")
    raw_refs = payload.get("workstations")
    if not isinstance(raw_refs, list):
        raise WorkstationError("workstation registry workstations must be a list")
    refs = tuple(_ref_from_payload(x) for x in raw_refs)
    ids = [x.id for x in refs]
    if len(ids) != len(set(ids)):
        raise WorkstationError("duplicate workstation id in registry")
    return refs


def _load_registry_snapshot(
    client_name: str, *, review_dir: os.PathLike | str,
) -> tuple[tuple[WorkstationRef, ...], Optional[str]]:
    """Parse and fingerprint the registry from one exact file read."""
    path = registry_path(client_name, review_dir=review_dir)
    try:
        exact_bytes = path.read_bytes()
        payload = json.loads(exact_bytes)
    except FileNotFoundError:
        return (), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkstationError(f"workstation registry is unreadable: {exc}") from exc
    return _parse_registry_payload(client_name, payload), _sha256(exact_bytes)


def load_registry(client_name: str, *, review_dir: os.PathLike | str) \
        -> tuple[WorkstationRef, ...]:
    """Read the optional registry. Missing means no explicit workstations.

    This function never creates or repairs the file.  Malformed/cross-client
    registries fail loudly so navigation cannot silently select another truth.
    """
    refs, _digest = _load_registry_snapshot(
        client_name, review_dir=review_dir)
    return refs


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _read_json_snapshot(path: Path, *, surface: str) \
        -> tuple[Optional[dict], Optional[str]]:
    try:
        exact_bytes = path.read_bytes()
        payload = json.loads(exact_bytes)
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkstationError(f"{surface} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkstationError(f"{surface} root is not an object")
    return payload, _sha256(exact_bytes)


def _validate_creation_receipt(
    client_name: str, ref: WorkstationRef, payload: dict,
) -> dict:
    expected_keys = {
        "schema_version", "client_name", "workstation_id", "scope",
        "clone_baseline", "created_at", "created_from",
        "source_packet_sha256", "native_packet_sha256",
    }
    if set(payload) != expected_keys:
        raise WorkstationError(
            "workstation creation receipt has an invalid field set")
    created_at = payload.get("created_at")
    created_from = payload.get("created_from")
    clone_baseline = payload.get("clone_baseline")
    if (payload.get("schema_version") != "1"
            or payload.get("client_name") != client_name
            or payload.get("workstation_id") != ref.id
            or payload.get("scope") != ref.scope.as_dict()
            or not isinstance(clone_baseline, bool)
            or not isinstance(created_at, str) or not created_at.strip()
            or not isinstance(created_from, str)
            or not created_from.strip() or created_from != created_from.strip()
            or not _is_sha256(payload.get("source_packet_sha256"))):
        raise WorkstationError("workstation creation receipt binding is invalid")
    if ((clone_baseline and not _is_sha256(
            payload.get("native_packet_sha256")))
            or (not clone_baseline
                and payload.get("native_packet_sha256") is not None)):
        raise WorkstationError("workstation creation receipt clone binding is invalid")
    try:
        receipt_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkstationError(
            "workstation creation receipt timestamp is invalid") from exc
    if receipt_time.tzinfo is None:
        raise WorkstationError(
            "workstation creation receipt timestamp must be timezone-aware")
    if ref.created_at is None or ref.created_at.tzinfo is None:
        raise WorkstationError(
            "workstation registry row has no creation timestamp")
    if (receipt_time != ref.created_at
            or created_from != ref.created_from):
        raise WorkstationError(
            "workstation creation receipt does not match registry metadata")
    return payload


def _load_creation_receipt_snapshot(
    client_name: str, ref: WorkstationRef, review_dir: Path,
) -> tuple[Optional[dict], Optional[str]]:
    path = creation_receipt_path(
        client_name, ref.id, review_dir=review_dir)
    payload, digest = _read_json_snapshot(
        path, surface="workstation creation receipt")
    if payload is None:
        return None, None
    return _validate_creation_receipt(client_name, ref, payload), digest


def _creation_receipt(client_name: str, ref: WorkstationRef,
                      review_dir: Path) -> Optional[dict]:
    payload, _digest = _load_creation_receipt_snapshot(
        client_name, ref, review_dir)
    return payload


def native_workstation_ownership(
    client_name: str,
    workstation_id: str,
    *,
    review_dir: os.PathLike | str,
) -> WorkstationOwnership:
    """Prove one exact, active registry row and immutable creation receipt.

    Registry and receipt are each read exactly once.  The mutable strategy
    packet is intentionally outside this proof so a caller can read it once
    itself and then compare these returned tokens immediately before acting.
    """
    if (not isinstance(client_name, str) or not client_name.strip()
            or client_name != client_name.strip()):
        raise WorkstationError("client identity must be an exact nonblank string")
    if (not isinstance(workstation_id, str) or not workstation_id.strip()
            or workstation_id != workstation_id.strip()):
        raise WorkstationError("workstation id must be an exact nonblank string")
    refs, registry_digest = _load_registry_snapshot(
        client_name, review_dir=review_dir)
    if registry_digest is None:
        raise WorkstationError("workstation registry does not exist")
    ref = next((row for row in refs if row.id == workstation_id), None)
    if ref is None:
        raise WorkstationError("workstation is not registered")
    if ref.archived_at is not None:
        raise WorkstationError("workstation is archived")
    receipt, receipt_digest = _load_creation_receipt_snapshot(
        client_name, ref, Path(review_dir))
    if receipt is None or receipt_digest is None:
        raise WorkstationError("workstation has no creation receipt")
    return WorkstationOwnership(
        ref=ref,
        clone_baseline=receipt["clone_baseline"],
        registry_sha256=registry_digest,
        receipt_sha256=receipt_digest,
    )


def _load_native_packet(
    client_name: str, ref: WorkstationRef, review_dir: Path,
) -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """Validate one registered workstation-native strategy packet.

    The receipt is the cutover marker. An orphan packet cannot silently turn
    the legacy packet into baseline-only state, and a malformed native packet
    can never fall back to a different scope.
    """
    try:
        receipt = _creation_receipt(client_name, ref, review_dir)
    except WorkstationError as exc:
        return None, None, str(exc)
    path = native_packet_path(client_name, ref.id, review_dir=review_dir)
    if not path.exists():
        if receipt is not None and receipt["clone_baseline"]:
            return None, None, "native review packet is missing"
        return None, None, None
    if not path.is_file():
        return None, None, "native review packet is not a regular file"
    try:
        exact_bytes = path.read_bytes()
        raw_payload = json.loads(exact_bytes)
        if not isinstance(raw_payload, dict):
            raise ValueError("root is not an object")
        from agents.review import ReviewPacket
        packet = ReviewPacket.model_validate_json(exact_bytes)
        payload = packet.model_dump(mode="json")
    except Exception as exc:
        return None, None, f"native review packet is unreadable: {exc}"
    if (raw_payload.get("client_name") != client_name
            or packet.client_name != client_name):
        return None, None, "native review packet client identity mismatch"
    raw_strategy = raw_payload.get("strategy")
    if (not isinstance(raw_strategy, dict)
            or raw_strategy.get("client_name") != client_name
            or packet.strategy.client_name != client_name):
        return None, None, "native review packet strategy identity mismatch"
    if "search_scope" not in raw_payload:
        return None, None, "native review packet has no exact search_scope binding"
    try:
        packet_scope = canonical_scope(raw_payload["search_scope"])
    except WorkstationError as exc:
        return None, None, f"native review packet scope is invalid: {exc}"
    if (packet_scope != ref.scope
            or raw_payload["search_scope"] != ref.scope.as_dict()):
        return None, None, "native review packet scope does not match workstation"
    if raw_payload.get("status") not in {"pending", "approved", "rejected"}:
        return None, None, "native review packet status is invalid"
    revision = raw_payload.get("revision_count")
    if (not isinstance(revision, int) or isinstance(revision, bool)
            or revision < 0):
        return None, None, "native review packet revision is invalid"
    if receipt is None:
        return None, None, "native review packet has no creation receipt"
    if not receipt["clone_baseline"]:
        return None, None, (
            "native review packet conflicts with no-clone creation receipt")
    return payload, _sha256(exact_bytes), None


def _registry_payload(client_name: str,
                      refs: tuple[WorkstationRef, ...]) -> dict:
    rows = []
    for ref in refs:
        row = ref.model_dump(mode="json")
        row.pop("label", None)  # display-only and always derived from scope
        rows.append(row)
    return {
        "schema_version": "1",
        "client_name": client_name,
        "workstations": rows,
    }


def _journal_path(packet_target: Path) -> Path:
    return packet_target.with_name(
        packet_target.name.replace(".review.json", ".journal.jsonl"))


def _creation_receipt_payload(
    client_name: str,
    ref: WorkstationRef,
    *,
    clone_baseline: bool,
    source_sha256: str,
    native_sha256: Optional[str],
) -> dict:
    return {
        "schema_version": "1",
        "client_name": client_name,
        "workstation_id": ref.id,
        "scope": ref.scope.as_dict(),
        "clone_baseline": clone_baseline,
        "created_at": ref.model_dump(mode="json")["created_at"],
        "created_from": ref.created_from,
        "source_packet_sha256": source_sha256,
        "native_packet_sha256": native_sha256,
    }


def _creation_event(receipt: dict) -> dict:
    return {
        "at": receipt["created_at"],
        "event": "workstation_created",
        "workstation_id": receipt["workstation_id"],
        "scope": receipt["scope"],
        "clone_baseline": receipt["clone_baseline"],
        "source_packet_sha256": receipt["source_packet_sha256"],
        "native_packet_sha256": receipt["native_packet_sha256"],
    }


def _creation_sidecar_problem(packet_target: Path, receipt: dict) -> Optional[str]:
    """Validate the immutable creation prefix without rewriting later edits.

    The native packet and receipt are not the whole journaled-creation
    contract.  A clone also owns its review markdown, and every creation owns
    a journal whose first row is the exact creation event.  Later mutation
    rows may append freely; replay only proves that the creation prefix still
    exists and remains bound to the receipt.
    """
    journal_target = _journal_path(packet_target)
    if not journal_target.exists():
        return "native creation journal is missing"
    if not journal_target.is_file():
        return "native creation journal is not a regular file"
    try:
        rows = journal_target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return f"native creation journal is unreadable: {exc}"
    if not rows:
        return "native creation journal is empty"
    try:
        first = json.loads(rows[0])
    except json.JSONDecodeError as exc:
        return f"native creation journal is unreadable: {exc}"
    if first != _creation_event(receipt):
        return "native creation journal does not match creation receipt"

    markdown_target = packet_target.with_suffix(".md")
    if receipt["clone_baseline"]:
        if not markdown_target.exists():
            return "native review markdown is missing"
        if not markdown_target.is_file():
            return "native review markdown is not a regular file"
        try:
            markdown = markdown_target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"native review markdown is unreadable: {exc}"
        if not markdown.strip():
            return "native review markdown is empty"
    elif markdown_target.exists():
        return "no-clone workstation unexpectedly has native review markdown"
    return None


def _strategy_for_clone(strategy: Any) -> Any:
    """Hydrate a missing NAICS record only from affirmative authored evidence.

    This is provenance preservation, not a new boundary decision.  Code order
    remains ``inferred_naics`` order. An exact NAICS keyword rationale can
    explain why a code belongs. A near-miss reason cannot: its schema records
    why a candidate was close *and why it was cut*. The unchanged near-miss
    record remains available to the workshop as review context while the
    affirmative rationale stays visibly blank for operator judgment.
    """
    cloned = strategy.model_copy(deep=True)
    if not cloned.inferred_naics:
        return cloned
    from agents.decisions.schemas import NaicsEntry

    # Existing metadata is authored provenance. Preserve every entry and its
    # order exactly; append only boundary codes that have no record yet.
    entries = list(cloned.naics_meta)
    recorded = {entry.code for entry in entries}
    for code in cloned.inferred_naics:
        if code in recorded:
            continue
        rationale = ""
        keyword = next((row for row in cloned.keywords
                        if getattr(row.category, "value", row.category) == "naics"
                        and row.term == code), None)
        if keyword is not None:
            rationale = keyword.rationale
        entries.append(NaicsEntry(
            code=code,
            title="",
            role="boundary",
            origin="system",
            rationale=rationale,
            note="",
        ))
        recorded.add(code)
    cloned.naics_meta = entries
    return cloned


def _build_creation_files(
    client_name: str,
    ref: WorkstationRef,
    *,
    source_packet: Any,
    source_sha256: str,
    packet_target: Path,
    clone_baseline: bool,
) -> tuple[dict, dict[Path, str]]:
    """Build deterministic transaction bytes; receipt is promoted first."""
    from agents.review import ReviewPacket, ReviewStatus, render_markdown

    if ref.created_at is None:
        raise WorkstationError("created workstation requires a timestamp")
    packet_text: Optional[str] = None
    markdown: Optional[str] = None
    native_sha: Optional[str] = None
    if clone_baseline:
        cloned_strategy = _strategy_for_clone(source_packet.strategy)
        packet = ReviewPacket(
            client_name=client_name,
            status=ReviewStatus.PENDING,
            strategy=cloned_strategy,
            reviewer_note="",
            created_at=ref.created_at,
            decided_at=None,
            revised_at=None,
            revision_count=0,
            search_scope=ref.scope.as_dict(),
        )
        packet_text = packet.model_dump_json(indent=2)
        native_sha = _sha256(packet_text.encode("utf-8"))
        markdown = render_markdown(
            packet.strategy, workstation_id=ref.id)
    receipt = _creation_receipt_payload(
        client_name, ref,
        clone_baseline=clone_baseline,
        source_sha256=source_sha256,
        native_sha256=native_sha,
    )
    receipt_target = creation_receipt_path(
        client_name, ref.id, review_dir=packet_target.parent)
    files: dict[Path, str] = {
        receipt_target: json.dumps(
            receipt, indent=2, ensure_ascii=False) + "\n",
    }
    if packet_text is not None and markdown is not None:
        files[packet_target] = packet_text
        files[packet_target.with_suffix(".md")] = markdown
    files[_journal_path(packet_target)] = (
        json.dumps(_creation_event(receipt), default=str) + "\n")
    return receipt, files


@contextmanager
def _cross_process_creation_lock(client_name: str, review_dir: Path):
    """Serialize creators for one client without adding repository artifacts."""
    lock_key = _sha256(
        (str(review_dir.resolve()) + "\0" + client_name).encode("utf-8"))
    lock_path = Path(tempfile.gettempdir()) / (
        f"lila-workstation-create-{lock_key}.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _commit_creation(*, source_path: Path, source_sha256: str,
                     prepared_files: dict[Path, str], registry_target: Path,
                     registry_payload: dict) -> None:
    """Promote exact transaction files and registry-last with source CAS."""
    if _sha256(source_path.read_bytes()) != source_sha256:
        raise WorkstationError(
            "baseline strategy changed during workstation creation; retry")
    created_paths: list[Path] = []
    try:
        prior_registry = registry_target.read_bytes()
    except FileNotFoundError:
        prior_registry = None
    try:
        from tools.atomic_io import atomic_write_text
        for path, text in prepared_files.items():
            expected = text.encode("utf-8")
            try:
                actual = path.read_bytes()
            except FileNotFoundError:
                actual = None
            if actual is not None:
                if actual != expected:
                    raise WorkstationError(
                        f"incomplete workstation artifact conflicts: {path.name}")
                continue
            try:
                atomic_write_text(str(path), text)
            finally:
                if path.exists():
                    created_paths.append(path)
        if _sha256(source_path.read_bytes()) != source_sha256:
            raise WorkstationError(
                "baseline strategy changed during workstation creation; retry")
        from tools.artifacts import atomic_write_json
        atomic_write_json(registry_target, registry_payload)
    except Exception:
        # Ordinary failures roll back only files this call introduced. A hard
        # process death (BaseException or kill) intentionally leaves durable
        # prefixes that the next identical request can complete.
        try:
            current_registry = registry_target.read_bytes()
        except FileNotFoundError:
            current_registry = None
        if current_registry != prior_registry:
            try:
                if prior_registry is None:
                    registry_target.unlink(missing_ok=True)
                else:
                    from tools.atomic_io import atomic_write_text
                    atomic_write_text(
                        str(registry_target), prior_registry.decode("utf-8"))
            except OSError:
                pass
        for path in reversed(created_paths):
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _ref_from_orphan_receipt(
    client_name: str,
    scope: WorkstationScope,
    wid: str,
    payload: dict,
) -> WorkstationRef:
    try:
        ref = WorkstationRef(
            id=wid,
            scope=scope,
            label=workstation_label(scope),
            created_at=payload.get("created_at"),
            created_from=payload.get("created_from"),
        )
    except Exception as exc:
        raise WorkstationError(
            f"incomplete workstation receipt is invalid: {exc}") from exc
    _validate_creation_receipt(client_name, ref, payload)
    return ref


def _validate_initial_orphan_packet(
    exact_bytes: bytes,
    *,
    client_name: str,
    scope: WorkstationScope,
    source_packet: Any,
) -> Any:
    """Admit only an untouched revision-zero clone before registry commit."""
    try:
        raw = json.loads(exact_bytes)
        if not isinstance(raw, dict):
            raise ValueError("root is not an object")
        from agents.review import ReviewPacket, ReviewStatus
        packet = ReviewPacket.model_validate_json(exact_bytes)
    except Exception as exc:
        raise WorkstationError(
            f"incomplete workstation packet is invalid: {exc}") from exc
    if (raw.get("client_name") != client_name
            or packet.client_name != client_name
            or not isinstance(raw.get("strategy"), dict)
            or raw["strategy"].get("client_name") != client_name
            or packet.strategy.client_name != client_name
            or raw.get("search_scope") != scope.as_dict()
            or packet.search_scope != scope.as_dict()
            or packet.status != ReviewStatus.PENDING
            or packet.reviewer_note
            or packet.decided_at is not None
            or packet.revised_at is not None
            or packet.revision_count != 0
            or packet.strategy != _strategy_for_clone(source_packet.strategy)):
        raise WorkstationError(
            "incomplete workstation packet does not match baseline transaction")
    return packet


def create_workstation(
    client_name: str,
    scope: dict | WorkstationScope,
    *,
    review_dir: os.PathLike | str,
    clone_baseline: bool = True,
    created_from: str = "operator",
) -> WorkstationCreation:
    """Explicitly create one canonical workstation, optionally cloning strategy.

    Cloning copies the validated strategy only. The new packet starts Pending
    at revision zero and carries no human decision, sweep, approval, evidence,
    report, release, pointer, or Target state.
    """
    if not isinstance(clone_baseline, bool):
        raise WorkstationError("clone_baseline must be boolean")
    if (not isinstance(client_name, str) or not client_name.strip()
            or client_name != client_name.strip()):
        raise WorkstationError("client identity must be an exact nonblank string")
    if (not isinstance(created_from, str) or not created_from.strip()):
        raise WorkstationError("created_from must be a nonblank string")
    canonical = canonical_scope(scope)
    wid = workstation_id(canonical)
    root = Path(review_dir)
    source_path = root / f"{_slug(client_name)}.review.json"

    with _CREATION_LOCK, _cross_process_creation_lock(client_name, root):
        try:
            source_bytes = source_path.read_bytes()
        except FileNotFoundError as exc:
            raise WorkstationError(
                "client baseline review packet does not exist") from exc
        source_sha = _sha256(source_bytes)
        try:
            from agents.review import ReviewPacket
            source_packet = ReviewPacket.model_validate_json(source_bytes)
        except Exception as exc:
            raise WorkstationError(
                f"client baseline review packet is invalid: {exc}") from exc
        if (source_packet.client_name != client_name
                or source_packet.strategy.client_name != client_name):
            raise WorkstationError("client baseline identity mismatch")
        try:
            legacy_scope = canonical_scope(source_packet.search_scope)
        except WorkstationError as exc:
            raise WorkstationError(
                f"client baseline scope is invalid: {exc}") from exc
        legacy_id = workstation_id(legacy_scope)

        refs = list(load_registry(client_name, review_dir=root))
        existing = next((ref for ref in refs if ref.id == wid), None)
        if existing is not None and existing.archived_at is not None:
            raise WorkstationError("archived workstation cannot be recreated")
        packet_target = native_packet_path(
            client_name, wid, review_dir=root)
        receipt_target = creation_receipt_path(
            client_name, wid, review_dir=root)
        journal_target = _journal_path(packet_target)
        markdown_target = packet_target.with_suffix(".md")
        receipt_payload: Optional[dict] = None
        if receipt_target.exists():
            raw_receipt, _receipt_digest = _read_json_snapshot(
                receipt_target, surface="workstation creation receipt")
            assert raw_receipt is not None
            if existing is None:
                recovered_ref = _ref_from_orphan_receipt(
                    client_name, canonical, wid, raw_receipt)
            else:
                recovered_ref = existing
                _validate_creation_receipt(
                    client_name, recovered_ref, raw_receipt)
            receipt_payload = raw_receipt
        else:
            recovered_ref = existing

        # A registry row is the last promotion. Once present, creation is
        # complete and replay is observational; missing/corrupt owned files
        # fail closed instead of rebuilding over possible operator edits.
        if existing is not None and receipt_payload is not None:
            if receipt_payload["clone_baseline"]:
                payload, _digest, problem = _load_native_packet(
                    client_name, existing, root)
                if problem or payload is None:
                    raise WorkstationError(
                        problem or "existing native review packet is invalid")
            elif packet_target.exists():
                raise WorkstationError(
                    "no-clone workstation unexpectedly has a native packet")
            sidecar_problem = _creation_sidecar_problem(
                packet_target, receipt_payload)
            if sidecar_problem:
                raise WorkstationError(sidecar_problem)
            return WorkstationCreation(
                ref=existing,
                created=False,
                cloned_baseline=receipt_payload["clone_baseline"],
            )

        # The legacy packet already owns this exact client/scope identity.
        # Tranche 3 may create only an alternate workstation; replacing the
        # same-id legacy boundary with a fresh Pending native packet would be a
        # Tranche-5 cutover. A clean duplicate is therefore byte-inert. Orphan
        # native prefixes fail closed rather than being adopted as migration.
        if wid == legacy_id:
            if (receipt_payload is not None or packet_target.exists()
                    or journal_target.exists() or markdown_target.exists()):
                raise WorkstationError(
                    "legacy-current scope has incomplete native artifacts; "
                    "explicit migration is required")
            legacy_ref = existing or WorkstationRef(
                id=legacy_id,
                scope=legacy_scope,
                label=workstation_label(legacy_scope),
                created_from="legacy-current",
            )
            return WorkstationCreation(
                ref=legacy_ref, created=False, cloned_baseline=False)

        # Receipt-first crash recovery: reconstruct every missing deterministic
        # prefix and write the registry last. A changed baseline cannot be
        # guessed across, so this path fails closed on source drift.
        if existing is None and receipt_payload is not None:
            if receipt_payload["clone_baseline"] != clone_baseline:
                raise WorkstationError(
                    "creation request conflicts with interrupted transaction")
            if receipt_payload["source_packet_sha256"] != source_sha:
                raise WorkstationError(
                    "baseline changed since interrupted workstation creation")
            expected_receipt, prepared = _build_creation_files(
                client_name, recovered_ref,
                source_packet=source_packet,
                source_sha256=source_sha,
                packet_target=packet_target,
                clone_baseline=clone_baseline,
            )
            if receipt_payload != expected_receipt:
                raise WorkstationError(
                    "interrupted workstation receipt is not reproducible")
            _commit_creation(
                source_path=source_path,
                source_sha256=source_sha,
                prepared_files=prepared,
                registry_target=registry_path(client_name, review_dir=root),
                registry_payload=_registry_payload(
                    client_name, tuple([*refs, recovered_ref])),
            )
            return WorkstationCreation(
                ref=recovered_ref, created=True,
                cloned_baseline=clone_baseline)

        if existing is not None and (
                packet_target.exists() or journal_target.exists()
                or markdown_target.exists()):
            raise WorkstationError(
                "registered workstation has incomplete native creation state")

        # Compatibility recovery for the older packet-first implementation.
        # This admits only a pristine clone of the still-current baseline.
        if existing is None and packet_target.exists():
            exact_packet = packet_target.read_bytes()
            orphan_packet = _validate_initial_orphan_packet(
                exact_packet,
                client_name=client_name,
                scope=canonical,
                source_packet=source_packet,
            )
            recovered_ref = WorkstationRef(
                id=wid,
                scope=canonical,
                label=workstation_label(canonical),
                created_at=orphan_packet.created_at,
                created_from=created_from,
            )
            receipt = _creation_receipt_payload(
                client_name, recovered_ref,
                clone_baseline=True,
                source_sha256=source_sha,
                native_sha256=_sha256(exact_packet),
            )
            from agents.review import render_markdown
            prepared = {
                receipt_target: json.dumps(
                    receipt, indent=2, ensure_ascii=False) + "\n",
                packet_target: exact_packet.decode("utf-8"),
                markdown_target: render_markdown(
                    orphan_packet.strategy, workstation_id=recovered_ref.id),
                journal_target: json.dumps(
                    _creation_event(receipt), default=str) + "\n",
            }
            _commit_creation(
                source_path=source_path,
                source_sha256=source_sha,
                prepared_files=prepared,
                registry_target=registry_path(client_name, review_dir=root),
                registry_payload=_registry_payload(
                    client_name, tuple([*refs, recovered_ref])),
            )
            return WorkstationCreation(
                ref=recovered_ref, created=True, cloned_baseline=True)
        if existing is None and (
                journal_target.exists() or markdown_target.exists()):
            raise WorkstationError(
                "unreceipted workstation sidecar cannot be adopted")

        now = datetime.now(timezone.utc)
        if existing is None:
            ref = WorkstationRef(
                id=wid,
                scope=canonical,
                label=workstation_label(canonical),
                created_at=now,
                created_from=created_from,
            )
            next_refs = tuple([*refs, ref])
        elif existing.created_at is None:
            ref = existing.model_copy(update={"created_at": now})
            next_refs = tuple(
                ref if row.id == wid else row for row in refs)
        else:
            ref = existing
            next_refs = tuple(refs)
        _receipt, prepared = _build_creation_files(
            client_name, ref,
            source_packet=source_packet,
            source_sha256=source_sha,
            packet_target=packet_target,
            clone_baseline=clone_baseline,
        )
        _commit_creation(
            source_path=source_path,
            source_sha256=source_sha,
            prepared_files=prepared,
            registry_target=registry_path(client_name, review_dir=root),
            registry_payload=_registry_payload(client_name, next_refs),
        )
        return WorkstationCreation(
            ref=ref, created=True, cloned_baseline=clone_baseline)


def _load_legacy_packet(client_name: str, review_dir: Path) -> Optional[dict]:
    path = review_dir / f"{_slug(client_name)}.review.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkstationError(f"legacy review packet is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkstationError("legacy review packet root is not an object")
    exact = payload.get("client_name")
    if (not isinstance(exact, str) or not exact.strip()
            or exact != exact.strip()):
        raise WorkstationError("legacy review packet has no exact client identity")
    if exact != client_name or _slug(exact) != _slug(client_name):
        raise WorkstationError("legacy review packet client identity mismatch")
    return payload


def _sweep_path(slug: str, wid: str, cleaned_dir: Path) -> Path:
    name = f"searches_{slug}.json" if wid == "all" else f"searches_{slug}.{wid}.json"
    return cleaned_dir / name


def _artifact_count(slug: str, wid: str, report_dir: Path) -> int:
    if wid != "all":
        return sum(
            1 for raw in glob.glob(str(report_dir / f"{slug}.{wid}.*"))
            if Path(raw).is_file())
    count = 0
    for path in glob.glob(str(report_dir / f"{slug}.*")):
        rest = Path(path).name[len(slug) + 1:]
        if not rest.startswith("agency_") and Path(path).is_file():
            count += 1
    return count


def sweep_scope_binding(
    payload: dict, *, legacy_all_default: bool = False,
) -> WorkstationScope:
    """Resolve the exact scope carried by one sweep artifact.

    Pre-workstation unqualified sweeps did not persist ``search_scope``.  They
    are compatible only with the one legacy-current All Federal workstation;
    native and focused artifacts must always carry an explicit binding.
    Explicit scope metadata is authoritative and is never replaced by this
    compatibility default.
    """
    if "search_scope" not in payload:
        if legacy_all_default:
            return canonical_scope({"all": True})
        raise WorkstationError(
            "designated sweep has no exact search_scope binding")
    try:
        return canonical_scope(payload.get("search_scope"))
    except WorkstationError as exc:
        raise WorkstationError(
            f"designated sweep scope is invalid: {exc}") from exc


def _validated_sweep(
    path: Path, *, client_name: str, scope: WorkstationScope,
    legacy_all_default: bool = False,
) -> tuple[bool, Optional[dict], Optional[str]]:
    """Prove a regular JSON sweep binds the exact client and scope."""
    if not path.exists():
        return False, None, None
    if not path.is_file():
        return False, None, "designated sweep is not a regular file"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, None, f"designated sweep is unreadable: {exc}"
    if not isinstance(payload, dict):
        return False, None, "designated sweep root is not an object"
    exact = payload.get("client") or payload.get("client_name")
    if not isinstance(exact, str) or exact != client_name:
        return False, None, "designated sweep client identity mismatch"
    try:
        bound_scope = sweep_scope_binding(
            payload, legacy_all_default=legacy_all_default)
    except WorkstationError as exc:
        return False, None, str(exc)
    if bound_scope != scope:
        return False, None, "designated sweep scope does not match workstation"
    return True, payload, None


def sweep_freshness_problem(
    packet: dict, sweep: dict, *,
    strategy_packet_sha256: Optional[str] = None,
) -> Optional[str]:
    """Explain why a same-scope sweep predates the accepted boundary.

    New sweeps carry the exact strategy revision.  Legacy artifacts can still
    prove freshness by an aware generated_at timestamp, but only when the
    packet has actually been revised.  Missing/naive legacy timestamps fail
    closed after a keyword or NAICS amendment instead of lending old results
    to the new boundary.
    """
    revision = packet.get("revision_count", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return "legacy review packet revision_count is invalid"
    sweep_revision = sweep.get("strategy_revision")
    if strategy_packet_sha256 is not None:
        sweep_sha = sweep.get("strategy_packet_sha256")
        if (not isinstance(sweep_sha, str)
                or sweep_sha != strategy_packet_sha256):
            return (
                "designated sweep does not bind the current native strategy "
                "packet bytes")
        if (not isinstance(sweep_revision, int)
                or isinstance(sweep_revision, bool)
                or sweep_revision < 0):
            return "designated sweep has no valid native strategy revision"
        if sweep_revision != revision:
            return (
                "designated sweep predates the current keyword/NAICS boundary "
                f"(revision {sweep_revision} vs {revision})")
        return None
    if sweep_revision is not None:
        if (not isinstance(sweep_revision, int)
                or isinstance(sweep_revision, bool)
                or sweep_revision < 0):
            return "designated sweep strategy_revision is invalid"
        if sweep_revision != revision:
            return (
                "designated sweep predates the current keyword/NAICS boundary "
                f"(revision {sweep_revision} vs {revision})")
        return None

    revised_at = packet.get("revised_at")
    if revised_at in (None, ""):
        return None  # pre-revision legacy compatibility
    try:
        boundary_time = datetime.fromisoformat(
            str(revised_at).replace("Z", "+00:00"))
        sweep_time = datetime.fromisoformat(
            str(sweep.get("generated_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return "designated sweep cannot prove freshness after boundary revision"
    if boundary_time.tzinfo is None or sweep_time.tzinfo is None:
        return "designated sweep timestamp is timezone-naive after boundary revision"
    if sweep_time.astimezone(timezone.utc) < boundary_time.astimezone(timezone.utc):
        return "designated sweep predates the current keyword/NAICS boundary"
    return None


def _assess_status(client_name: str, review_dir: Path) -> tuple[str, list[str]]:
    from agents.assess.approval import assess_approval_for_release

    _, status, problems = assess_approval_for_release(
        client_name, review_dir=str(review_dir))
    return status, list(problems or [])


def _release_ready(client_name: str, review_dir: Path, report_dir: Path) -> bool:
    from agents.reports.release import release_state

    return bool(release_state(
        client_name, review_dir=str(review_dir),
        report_dir=str(report_dir)).get("releasable"))


def discover_workstations(
    client_name: str,
    *,
    review_dir: os.PathLike | str,
    cleaned_dir: os.PathLike | str,
    report_dir: os.PathLike | str,
) -> WorkstationCatalog:
    """Derive the non-mutating catalog used by Tranche-1 read surfaces.

    ``all`` is always present as the product's explicit possibility. The
    current legacy packet remains active at its exact id while alternate
    native workstations coexist. Any owned native-creation artifact latches
    only its own id; damage there becomes diagnostic/dormant. A bare registry
    identity is not ownership and cannot retire a legacy-current packet.
    Historical artifacts never imply progress or approval.
    """
    review_root, cleaned_root = Path(review_dir), Path(cleaned_dir)
    reports_root = Path(report_dir)
    slug = _slug(client_name)
    legacy_packet = _load_legacy_packet(client_name, review_root)
    refs: dict[str, WorkstationRef] = {}

    all_scope = canonical_scope({"all": True})
    refs["all"] = WorkstationRef(
        id="all", scope=all_scope, label="All Federal", created_from="product-default")
    registry_refs = load_registry(client_name, review_dir=review_root)
    for ref in registry_refs:
        if ref.archived_at is None:
            refs[ref.id] = ref

    legacy_id = None
    if legacy_packet is not None:
        legacy_scope = canonical_scope(legacy_packet.get("search_scope"))
        legacy_id = workstation_id(legacy_scope)
        refs.setdefault(legacy_id, WorkstationRef(
            id=legacy_id,
            scope=legacy_scope,
            label=workstation_label(legacy_scope),
            created_from="legacy-current",
        ))

    native_packets: dict[str, tuple[dict, str]] = {}
    native_problems: dict[str, str] = {}
    native_latched_ids: set[str] = set()
    for ref in registry_refs:
        if ref.archived_at is not None:
            continue
        packet_path = native_packet_path(
            client_name, ref.id, review_dir=review_root)
        receipt_path = creation_receipt_path(
            client_name, ref.id, review_dir=review_root)
        journal_path = _journal_path(packet_path)
        markdown_path = packet_path.with_suffix(".md")
        # The registry is identity-only: timestamp/source metadata cannot prove
        # native packet ownership. Receipt, packet, markdown, or creation-journal
        # bytes preserve the exact-id latch when one owned surface is damaged,
        # while a bare Tranche-1 registry row must not retire legacy-current.
        if (packet_path.exists() or receipt_path.exists()
                or markdown_path.exists() or journal_path.exists()):
            native_latched_ids.add(ref.id)
        payload, digest, problem = _load_native_packet(
            client_name, ref, review_root)
        if payload is not None and digest is not None:
            native_packets[ref.id] = (payload, digest)
        elif problem:
            native_problems[ref.id] = problem
        elif (ref.id in native_latched_ids and not receipt_path.exists()
              and not packet_path.exists()):
            native_problems[ref.id] = (
                "registered native ownership has no creation receipt or packet")

    rows: list[WorkstationSummary] = []
    for wid, ref in refs.items():
        native = native_packets.get(wid)
        is_native = native is not None
        is_legacy_current = bool(
            not is_native and legacy_packet is not None
            and wid == legacy_id and wid not in native_latched_ids)
        packet = native[0] if native is not None else (
            legacy_packet if is_legacy_current else None)
        packet_digest = native[1] if native is not None else None
        is_current = packet is not None
        diagnostics: list[str] = []
        if wid in native_problems:
            diagnostics.append(native_problems[wid])
        raw_status = str((packet or {}).get("status") or "pending") \
            if is_current else "absent"
        if is_current and raw_status not in {"pending", "approved", "rejected"}:
            raise WorkstationError(
                f"review packet has invalid status {raw_status!r}")
        boundary = (raw_status if raw_status in {
            "pending", "approved", "rejected"} else "absent")
        historical_sweep, sweep_payload, sweep_problem = _validated_sweep(
            _sweep_path(slug, wid, cleaned_root),
            client_name=client_name, scope=ref.scope,
            legacy_all_default=bool(
                is_legacy_current and ref.id == "all" and ref.scope.all))
        freshness_problem = (
            sweep_freshness_problem(
                packet, sweep_payload,
                strategy_packet_sha256=packet_digest)
            if is_current and historical_sweep
            and sweep_payload is not None else None
        )
        sweep_exists = bool(
            is_current and historical_sweep and not freshness_problem)
        sweep_status = (
            "invalid" if sweep_problem else
            "stale" if freshness_problem else
            ("current" if is_current else "historical")
            if historical_sweep else "absent"
        )
        if is_current and (sweep_problem or freshness_problem):
            diagnostics.append(sweep_problem or freshness_problem)
        if not is_current:
            phase = "dormant"
            boundary = "absent"
            sweep_exists = False
        elif boundary != "approved":
            phase = "configure"
        elif not sweep_exists:
            phase = "search"
        elif is_native:
            # Strategy and search are partitioned in Tranche 3. Legacy Assess
            # approvals and releases remain history until their own tranche,
            # so a native sweep can advance no farther than Assess.
            phase = "assess"
        else:
            try:
                assess_status, problems = _assess_status(client_name, review_root)
            except Exception as exc:  # fail closed in a navigation read model
                assess_status, problems = "invalid", [
                    f"Assess approval state unavailable: {type(exc).__name__}: {exc}"]
            diagnostics.extend(problems)
            if assess_status != "approved":
                phase = "assess"
            else:
                try:
                    phase = ("target" if _release_ready(
                        client_name, review_root, reports_root) else "produce")
                except Exception as exc:  # release ambiguity never advances
                    phase = "produce"
                    diagnostics.append(
                        f"release state unavailable: {type(exc).__name__}: {exc}")
        rows.append(WorkstationSummary(
            ref=ref,
            phase=phase,
            boundary_status=boundary,
            is_legacy_current=is_legacy_current,
            is_native=is_native,
            sweep_exists=sweep_exists,
            historical_sweep_exists=historical_sweep,
            sweep_status=sweep_status,
            historical_artifacts=_artifact_count(slug, wid, reports_root),
            diagnostics=tuple(diagnostics),
        ))

    rows.sort(key=lambda row: (
        0 if row.is_legacy_current else 1,
        1 if row.ref.id == "all" else 0,
        row.ref.label.lower(),
    ))
    return WorkstationCatalog(client_name=client_name, workstations=tuple(rows))


__all__ = [
    "WorkstationCatalog",
    "WorkstationCreation",
    "WorkstationError",
    "WorkstationOwnership",
    "WorkstationRef",
    "WorkstationScope",
    "WorkstationSummary",
    "canonical_scope",
    "create_workstation",
    "creation_receipt_path",
    "discover_workstations",
    "load_registry",
    "native_packet_path",
    "native_workstation_ownership",
    "registry_path",
    "sweep_scope_binding",
    "sweep_freshness_problem",
    "workstation_id",
    "workstation_label",
]
