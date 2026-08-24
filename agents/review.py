"""Human Review Gate for the intake strategy (Step 1 → searches).

Enforces "alert me to review and approve strategy and keywords before launching" as
code: a strategy is persisted with status="pending", an alert is raised, and the
search orchestrator refuses to run until status becomes "approved".

Alerting is pluggable: the default writes a review packet + prints a banner. Swap
`alert_fn` for email (Gmail), Slack, or a push notification without touching the gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from typing import Callable, Optional

import fcntl

from pydantic import BaseModel, Field

from agents.decisions.schemas import IntakeStrategy, NaicsEntry

REVIEW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "review")


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class WorkstationBindingError(RuntimeError):
    """A child job no longer owns the client/scope it was launched for.

    The dashboard passes the expected immutable workstation identity through
    the environment.  Every packet/scope read in that child fails closed if
    the mutable legacy gate has moved before the runner snapshots its inputs.
    Normal CLI use has no expectation variables and keeps legacy behavior.
    """


class ReviewPacket(BaseModel):
    client_name: str
    status: ReviewStatus = ReviewStatus.PENDING
    strategy: IntakeStrategy
    reviewer_note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None
    revised_at: Optional[datetime] = None
    revision_count: int = 0
    # search scope: None or {"all": True} -> every agency (the default, and the
    # report carries NO designator: "Federal Opportunity Assessment" IS the
    # all-agencies statement). {"agencies": ["CBP", ...]} -> the sweep filters
    # to those agencies pre-triage and reports carry the focus designator.
    # An operator search-time choice, settable before or after approval.
    search_scope: Optional[dict] = None

    @property
    def is_approved(self) -> bool:
        return self.status == ReviewStatus.APPROVED

    def scope_agencies(self) -> list:
        """Resolved agency records for a specific-agency scope, else []."""
        from tools.agencies import find
        names = (self.search_scope or {}).get("agencies") or []
        return [a for a in (find(n) for n in names) if a]

    def designator(self) -> Optional[str]:
        """The engagement designator this scope mints, else None (L19)."""
        agencies = self.scope_agencies()
        return scope_designator(
            {"agencies": [a["abbr"] for a in agencies]} if agencies else None)


@dataclass(frozen=True)
class PacketSnapshot:
    """One validated parse of one exact packet byte string."""

    packet: ReviewPacket
    path: str
    sha256: str
    revision: int
    workstation_id: Optional[str]
    receipt_sha256: Optional[str] = None
    registry_sha256: Optional[str] = None


# ── scope designator (L19) ──────────────────────────────────────────────────
# ONE grammar, adopted from the existing agency-report convention: the
# designator is dot-segment 2 of every artifact name ("agency_dhs"), which
# the dashboard shelf already parses ("Agency Focus · DHS"). Scope=all mints
# NO designator: the unqualified "Federal Opportunity Assessment" IS the
# all-agencies statement. Per-agency runs therefore coexist as separate
# artifact families and can never masquerade as the all-market assessment.

def scope_designator(scope: Optional[dict]) -> Optional[str]:
    """'agency_dhs' (multi: 'agency_dhs_cisa') for an agency scope; None for
    all/absent. Accepts the artifact form ({'agencies': [{'abbr': ...}]}) and
    the gate form ({'agencies': ['DHS']})."""
    agencies = (scope or {}).get("agencies") or []
    abbrs = []
    for a in agencies:
        abbr = (a.get("abbr") if isinstance(a, dict) else str(a) or "").strip()
        if abbr:
            abbrs.append("".join(c if c.isalnum() else "_" for c in abbr).lower())
    return ("agency_" + "_".join(abbrs)) if abbrs else None


def _artifact_slug(name: str) -> str:
    # Aligned with tools.capability._slug (one slug per identity; the
    # per-character variant minted jtg__inc while the sweep looked for
    # jtg_inc, measured 2026-08-19). Collapse runs to one underscore.
    from tools.slug import client_slug
    return client_slug(name)


def canonical_client_name(client_ref: str,
                          review_dir: Optional[str] = None) -> str:
    """Resolve a URL/artifact slug to the packet's exact client identity.

    Dashboard routes carry slugs, while approval artifacts bind the exact
    client name. The review packet is the one owner of that mapping. Missing
    legacy packets preserve the caller's value; malformed or cross-client
    packets fail loudly so release code can refuse rather than guess.
    """
    slug = _artifact_slug(client_ref)
    path = os.path.join(review_dir or REVIEW_DIR, f"{slug}.review.json")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle) or {}
    except FileNotFoundError:
        return client_ref
    if not isinstance(payload, dict):
        raise ValueError("review packet root is not an object")
    exact = payload.get("client_name") or payload.get("client")
    if not isinstance(exact, str) or not exact.strip():
        raise ValueError("review packet has no exact client identity")
    exact = exact.strip()
    if _artifact_slug(exact) != slug:
        raise ValueError("review packet client identity does not match its filename")
    return exact


def gate_designator(client_name: str,
                    review_dir: Optional[str] = None,
                    workstation_id: Optional[str] = None) -> Optional[str]:
    """The designator the CURRENT gate scope mints, read straight from the
    review packet JSON. A MISSING packet means all-agencies (None); any other
    read failure raises — a scoped gate must never silently resolve plain.
    review_dir overrides the module default so callers with their own dirs
    (the dashboard, tests) stop re-implementing this resolution."""
    requested = _workstation_id_value(
        workstation_id or os.environ.get("LILA_EXPECT_WORKSTATION_ID"))
    path = _resolved_packet_path(client_name, requested, review_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle) or {}
    except FileNotFoundError:
        _assert_expected_workstation(client_name, None, packet_client=None)
        return None
    if not isinstance(payload, dict):
        raise ValueError("review packet root is not an object")
    scope = payload.get("search_scope")
    if requested is not None:
        try:
            from agents.workstations import workstation_id as _workstation_id
            actual = _workstation_id(scope)
        except Exception as exc:
            raise WorkstationBindingError(
                f"review packet scope is invalid: {exc}") from exc
        if actual != requested:
            raise WorkstationBindingError(
                "review packet does not belong to the requested workstation")
    _assert_expected_workstation(
        client_name, scope, packet_client=payload.get("client_name"))
    scope = scope or {}
    names = scope.get("agencies") or []
    if not names:
        return None
    from tools.agencies import find
    abbrs = [a["abbr"] for a in (find(n) for n in names) if a]
    return scope_designator({"agencies": abbrs} if abbrs else None)


def sweep_artifact_path(client_name: str,
                        review_dir: Optional[str] = None,
                        workstation_id: Optional[str] = None) -> str:
    """The sweep artifact the CURRENT gate scope designates (L19).

    Scope=all (or no packet) -> data/cleaned/searches_<slug>.json, exactly
    the historical name. A focused gate -> searches_<slug>.<designator>.json;
    if that scoped artifact does not exist the resolution FAILS LOUDLY with
    the command that produces it — never a silent fallback to the all-market
    artifact, so a scoped run can never quietly build from the wrong universe
    (and vice versa)."""
    slug = _artifact_slug(client_name)
    # DIVERGENT SLUG FAMILIES (2026-08-19): artifacts written before the
    # slug alignment carry the per-character shape ("jtg__inc"). The
    # canonical name wins; a missing canonical falls back to an EXISTING
    # legacy-named artifact so history stays findable. New artifacts are
    # always minted canonical.
    from tools.slug import legacy_client_slug
    legacy = legacy_client_slug(client_name)
    root = os.path.join(os.path.dirname(review_dir or REVIEW_DIR), "cleaned")
    d = gate_designator(client_name, review_dir, workstation_id)
    if d is None:
        canonical = os.path.join(root, f"searches_{slug}.json")
        legacy_path = os.path.join(root, f"searches_{legacy}.json")
        if (legacy != slug and not os.path.exists(canonical)
                and os.path.exists(legacy_path)):
            return legacy_path
        return canonical
    scoped = os.path.join(root, f"searches_{slug}.{d}.json")
    legacy_scoped = os.path.join(root, f"searches_{legacy}.{d}.json")
    if (legacy != slug and not os.path.exists(scoped)
            and os.path.exists(legacy_scoped)):
        return legacy_scoped
    if not os.path.exists(scoped):
        raise FileNotFoundError(
            f"gate scope designates '{d}' but {scoped} does not exist; run the "
            f"searches step under this scope (or set the gate scope to all "
            f"for the unqualified assessment)")
    return scoped


def artifact_stem(client_name: str, kind: str,
                  review_dir: Optional[str] = None,
                  workstation_id: Optional[str] = None) -> str:
    """data/reports basename stem for the CURRENT gate scope: '<slug>.<kind>'
    unscoped, '<slug>.<designator>.<kind>' scoped — the designator occupies
    dot-segment 2, matching the shelf parser and the agency-report precedent.
    Sidecars (.draft.json/.qa.json/.arbiters.json/.internal.md/.pdf) derive
    from this stem so a scoped build can never cross-read another scope's
    state."""
    slug = _artifact_slug(client_name)
    d = gate_designator(client_name, review_dir, workstation_id)
    return f"{slug}.{d}.{kind}" if d else f"{slug}.{kind}"


def set_scope(client_name: str, scope: Optional[dict]) -> "ReviewPacket":
    """Persist the search scope. Valid: None, {'all': True}, or
    {'agencies': [known names/abbrs...]} — unknown agencies are rejected
    loudly, never silently dropped."""
    from tools.agencies import find
    if scope and scope.get("agencies") is not None:
        unknown = [n for n in scope["agencies"] if not find(n)]
        if unknown:
            raise RevisionError(f"unknown agencies in scope: {unknown}")
        if not scope["agencies"]:
            scope = {"all": True}
    packet = load_packet(client_name)
    journal(client_name, "scope_change",
            {"before": packet.search_scope, "after": scope})
    packet.search_scope = scope
    with open(_path(client_name), "w") as f:
        f.write(packet.model_dump_json(indent=2))
    return packet


# Fields a human may hand-edit at the gate. Everything else on IntakeStrategy
# (confidence, sources_reviewed, review_gate, requires_human_review) is the
# model's own read and is never operator-editable.
EDITABLE_FIELDS = ("pursuit_strategy", "keywords", "inferred_naics",
                   "target_agencies", "set_aside_angles", "kept_out",
                   "naics_meta", "kept_out_naics", "research_entities")

# Keyword Workshop (2026-07-12): the additive provenance fields a gate edit
# preserves round-trip, so who-inferred / consultant-added / prior-wording
# survive every save. Legacy edits that omit them keep the defaults.
_KEYWORD_PROVENANCE = ("origin", "edited_from", "source", "note")


_NAICS_RE = re.compile(r"^[0-9]{6}$")


def _validate_naics_codes(codes: list) -> None:
    """Reject a non-six-digit NAICS BEFORE persistence (NAICS Boundary
    Workshop, 2026-07-12). A NAICS code is exactly six digits; anything else is
    a loud RevisionError, never silently dropped or coerced."""
    bad = [repr(c) for c in codes
           if not isinstance(c, str) or not _NAICS_RE.match(c.strip())]
    if bad:
        raise RevisionError(
            "NAICS codes must be exactly six digits; rejected: "
            + ", ".join(sorted(set(bad))))


def _clean_naics_codes(vals, *, field_name: str = "inferred_naics") -> list[str]:
    """Validate the boundary container before normalizing/deduplicating it."""
    if not isinstance(vals, (list, tuple)):
        raise RevisionError(f"{field_name} must be a list of six-digit codes")
    _validate_naics_codes(vals)
    return _clean_list([code.strip() for code in vals], keep_case=False)


def _clean_naics_entries(vals, *, field_name: str) -> list[dict]:
    """Validate and normalize a complete NAICS-entry list.

    Invalid containers, item shapes, codes, roles, and origins are operator
    errors, never omissions to silently discard. Duplicate codes retain the
    first complete record, matching the workshop's stable card order.
    """
    if not isinstance(vals, (list, tuple)):
        raise RevisionError(f"{field_name} must be a list of NAICS entries")
    out, seen = [], set()
    for index, raw in enumerate(vals):
        if isinstance(raw, NaicsEntry):
            payload = raw.model_dump(mode="json")
        elif isinstance(raw, dict):
            payload = dict(raw)
        else:
            raise RevisionError(
                f"{field_name}[{index}] must be an object with a six-digit code")
        code = payload.get("code")
        if isinstance(code, str):
            payload["code"] = code.strip()
        for key in ("title", "rationale", "note"):
            val = payload.get(key)
            if val is None or (isinstance(val, str) and not val.strip()):
                payload[key] = ""
            elif isinstance(val, str):
                payload[key] = val.strip()
        for key in ("role", "origin"):
            if key in payload and isinstance(payload[key], str):
                payload[key] = payload[key].strip()
        try:
            entry = NaicsEntry.model_validate(payload)
        except Exception as exc:
            raise RevisionError(
                f"invalid {field_name}[{index}]: {exc}") from exc
        item = entry.model_dump(mode="json")
        if item["code"] not in seen:
            seen.add(item["code"])
            out.append(item)
    return out


def _entry_map(entries: list[dict]) -> dict[str, dict]:
    return {entry["code"]: dict(entry) for entry in entries}


def _reconcile_naics_state(merged: dict,
                           previous: Optional[IntakeStrategy] = None) -> None:
    """Canonical owner for the three-lane NAICS state.

    ``inferred_naics`` is authoritative. Active metadata is pruned to that
    boundary; active codes can never remain kept out. A code leaving the
    boundary is preserved with its prior metadata, and a code returning from
    kept-out restores that record. A submitted replacement overrides it only
    when provenance explicitly marks the replacement ``origin='edited'``;
    generic Add-control placeholders never erase history. Existing kept-out
    history cannot be erased by omission.
    """
    boundary = _clean_naics_codes(merged.get("inferred_naics", []))
    active_raw = _clean_naics_entries(
        merged.get("naics_meta", []), field_name="naics_meta")
    kept_raw = _clean_naics_entries(
        merged.get("kept_out_naics", []), field_name="kept_out_naics")

    prior_boundary = list(previous.inferred_naics) if previous else []
    prior_active = ([entry.model_dump(mode="json")
                     for entry in previous.naics_meta] if previous else [])
    prior_kept = ([entry.model_dump(mode="json")
                   for entry in previous.kept_out_naics] if previous else [])
    active_by_code = _entry_map(active_raw)
    kept_by_code = _entry_map(kept_raw)
    prior_active_by_code = _entry_map(prior_active)
    prior_kept_by_code = _entry_map(prior_kept)
    prior_boundary_set = set(prior_boundary)
    boundary_set = set(boundary)

    active_meta: list[dict] = []
    for code in boundary:
        # A kept -> active transition is a restore. Preserve the complete
        # historical record even if the Add control submitted a generic new
        # card; deliberate edits can follow in a later revision.
        if code not in prior_boundary_set and code in prior_kept_by_code:
            submitted = active_by_code.get(code)
            entry = (submitted if submitted is not None
                     and submitted.get("origin") == "edited"
                     else prior_kept_by_code[code])
        else:
            entry = active_by_code.get(code)
        if entry is None and code in prior_boundary_set:
            entry = prior_active_by_code.get(code)
        if entry is None:
            entry = kept_by_code.get(code) or prior_kept_by_code.get(code)
        if entry is not None:
            active_meta.append(dict(entry))

    # Preserve explicit kept-out order, historical kept-out order, then codes
    # that just left the boundary and any submitted active orphan metadata.
    kept_order: list[str] = []
    for code in ([entry["code"] for entry in kept_raw]
                 + [entry["code"] for entry in prior_kept]
                 + [code for code in prior_boundary if code not in boundary_set]
                 + [entry["code"] for entry in active_raw
                    if entry["code"] not in boundary_set]):
        if code not in boundary_set and code not in kept_order:
            kept_order.append(code)

    kept_out: list[dict] = []
    for code in kept_order:
        entry = (kept_by_code.get(code) or prior_kept_by_code.get(code)
                 or active_by_code.get(code) or prior_active_by_code.get(code))
        if entry is None:
            entry = {
                "code": code, "title": "", "role": "boundary",
                "origin": "system",
                "rationale": "",
                "note": "moved out of the boundary by the operator",
            }
        kept_out.append(dict(entry))

    merged["inferred_naics"] = boundary
    merged["naics_meta"] = active_meta
    merged["kept_out_naics"] = kept_out


def _clean_keyword(k: dict, *, default_rationale: str) -> Optional[dict]:
    """One owner for turning a raw keyword payload into a schema-valid dict,
    carrying the workshop provenance through untouched. Returns None for a
    blank term (dropped, never persisted)."""
    term = str((k or {}).get("term", "")).strip()
    if not term:
        return None
    out = {
        "term": term,
        "category": str((k or {}).get("category") or "search_term").strip(),
        "rationale": str((k or {}).get("rationale") or "").strip()
                     or default_rationale,
    }
    for field in _KEYWORD_PROVENANCE:
        val = (k or {}).get(field)
        if val is not None and str(val).strip():
            out[field] = str(val).strip() if isinstance(val, str) else val
    return out


class RevisionError(ValueError):
    """A proposed revision is invalid (bad keyword category, or the strategy
    is already approved). Raised loudly so the gate never persists a corrupt
    or contradictory strategy."""


def _slug(name: str) -> str:
    # Same alignment as _artifact_slug: runs collapse to one underscore.
    from tools.slug import client_slug
    return client_slug(name)


def _workstation_id_value(workstation_id: Optional[str]) -> Optional[str]:
    """Validate the filename-safe workstation designator used by review files."""
    if workstation_id is None:
        return None
    if (not isinstance(workstation_id, str) or not workstation_id
            or workstation_id != workstation_id.strip()
            or not (workstation_id == "all"
                    or workstation_id.startswith("agency_"))
            or any(not (char.isalnum() or char == "_")
                   for char in workstation_id)):
        raise WorkstationBindingError("invalid workstation review-packet id")
    return workstation_id


def _path(client_name: str, workstation_id: Optional[str] = None,
          review_dir: Optional[str] = None) -> str:
    """Canonical review-packet path for legacy or one exact workstation.

    DIVERGENT SLUG FAMILIES (2026-08-19): packets written before the slug
    alignment carry the per-character shape. Canonical wins; a missing
    canonical falls back to an EXISTING legacy-named packet so history
    stays readable. New packets always mint canonical."""
    root = review_dir or REVIEW_DIR
    wid = _workstation_id_value(workstation_id)
    suffix = f".{wid}" if wid is not None else ""
    canonical = os.path.join(root, f"{_slug(client_name)}{suffix}.review.json")
    from tools.slug import legacy_client_slug
    legacy_slug = legacy_client_slug(client_name)
    if legacy_slug != _slug(client_name) and not os.path.exists(canonical):
        legacy = os.path.join(root, f"{legacy_slug}{suffix}.review.json")
        if os.path.exists(legacy):
            return legacy
    return canonical


def _resolved_packet_path(client_name: str,
                          workstation_id: Optional[str] = None,
                          review_dir: Optional[str] = None,
                          native_owner: Optional[bool] = None) -> str:
    """Prefer a native packet, with exact-scope legacy compatibility only.

    The caller still validates the packet's embedded client and scope.  The
    fallback therefore supports the one pre-cutover legacy-current owner but
    can never make an All packet satisfy a requested DHS workstation.
    """
    wid = _workstation_id_value(
        workstation_id or os.environ.get("LILA_EXPECT_WORKSTATION_ID"))
    native_env = os.environ.get("LILA_EXPECT_NATIVE_WORKSTATION")
    if native_env is not None and native_env not in {"0", "1"}:
        raise WorkstationBindingError(
            "invalid native-workstation binding in child environment")
    if native_owner is None and native_env is not None:
        native_owner = native_env == "1"
    # Production callers that have already resolved ownership must say which
    # family they authorized.  A native-looking filename is not authority: a
    # receipt-first creation can legitimately leave that file behind before
    # the registry-last visibility switch.  Forced legacy resolution therefore
    # never probes the native filename, while forced native resolution never
    # falls back to the legacy packet.
    if native_owner is True:
        if wid is None:
            raise WorkstationBindingError(
                "native packet resolution requires a workstation id")
        return _path(client_name, wid, review_dir)
    if native_owner is False:
        if review_dir is None:
            return _path(client_name)
        return _path(client_name, review_dir=review_dir)
    if wid is not None:
        native = _path(client_name, wid, review_dir)
        if os.path.exists(native):
            return native
    # Preserve the long-standing single-argument seam used by callers and
    # tests that substitute a legacy path resolver.  Native callers still use
    # the explicit three-argument form above.
    if review_dir is None:
        return _path(client_name)
    return _path(client_name, review_dir=review_dir)


def packet_sha256(client_name: str, *, workstation_id: Optional[str] = None,
                  review_dir: Optional[str] = None,
                  native_owner: Optional[bool] = None) -> str:
    """SHA-256 of the exact packet bytes selected for this workstation."""
    return _load_packet_snapshot(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner)[2]


def render_markdown(
    strategy: IntakeStrategy, *, workstation_id: Optional[str] = None,
) -> str:
    """Human-readable review summary (what the approver actually reads)."""
    native_id = _workstation_id_value(workstation_id)
    kw = "\n".join(f"- [{k.category.value}] {k.term} — {k.rationale}" for k in strategy.keywords)
    searches = "\n".join(
        f"- **{s.source}**: terms={s.query_terms} naics={s.naics_codes} — {s.rationale}"
        for s in strategy.searches
    )
    approval_instruction = (
        f"_Approve in Command Center while viewing workstation "
        f"`{native_id}`. No unbound CLI approval command is valid for this "
        f"native packet._\n"
        if native_id is not None else
        f"_Approve with:_ `python3 approve.py \"{strategy.client_name}\" --approve`\n"
    )
    return (
        f"# Analyst Layer: pursuit strategy for {strategy.client_name}\n\n"
        f"**Confidence:** {strategy.confidence}\n\n"
        f"## Pursuit strategy\n{strategy.pursuit_strategy}\n\n"
        f"## Inferred NAICS\n{', '.join(strategy.inferred_naics) or '(none)'}\n\n"
        f"## Target agencies\n{', '.join(strategy.target_agencies) or '(none)'}\n\n"
        f"## Keywords\n{kw or '(none)'}\n\n"
        f"## Planned searches\n{searches or '(none)'}\n\n"
        f"## Review gate\n{strategy.review_gate}\n\n"
        f"{approval_instruction}"
    )


def _default_alert(packet: ReviewPacket, md_path: str) -> None:
    bar = "=" * 64
    print(f"\n{bar}\nANALYST LAYER REVIEW REQUIRED: {packet.client_name}\n{bar}")
    print("Analyst Layer pending your approval before any search launches.")
    print(f"Review summary : {md_path}")
    print(f"Approve        : python3 approve.py \"{packet.client_name}\" --approve")
    print(f"Reject         : python3 approve.py \"{packet.client_name}\" --reject\n{bar}\n")


def _journal_path(client_name: str, workstation_id: Optional[str] = None,
                  review_dir: Optional[str] = None) -> str:
    path = (_path(client_name)
            if workstation_id is None and review_dir is None
            else _path(client_name, workstation_id, review_dir))
    return path.replace(
        ".review.json", ".journal.jsonl")


def _append_journal(path: str, event: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event, **payload}, default=str) + "\n")


def _journal_packet_path(packet_path: str, event: str, payload: dict) -> None:
    try:
        _append_journal(
            packet_path.replace(".review.json", ".journal.jsonl"),
            event, payload)
    except OSError:
        pass


def journal(client_name: str, event: str, payload: dict, *,
            workstation_id: Optional[str] = None,
            review_dir: Optional[str] = None) -> None:
    """Append-only judgment trail (GTM Playbook stage 1, docs/plans/
    gtm-playbook.md): every inference snapshot, gate edit, term amendment,
    and scope change lands here with a timestamp, so the debrief pass can
    diff what the machine thought against what the human taught it. Best
    effort by design — a journal hiccup must never block the operator."""
    try:
        _append_journal(
            _journal_path(client_name, workstation_id, review_dir),
            event, payload)
    except OSError:
        pass


def _term_state(strategy: IntakeStrategy) -> dict:
    return {"keywords": [{"term": k.term, "category": k.category.value}
                         for k in strategy.keywords],
            "inferred_naics": list(strategy.inferred_naics),
            "naics_meta": [entry.model_dump(mode="json")
                           for entry in strategy.naics_meta],
            "kept_out_naics": [entry.model_dump(mode="json")
                               for entry in strategy.kept_out_naics],
            "target_agencies": list(strategy.target_agencies)}


def request_approval(
    strategy: IntakeStrategy,
    alert_fn: Callable[[ReviewPacket, str], None] = _default_alert,
) -> ReviewPacket:
    """Persist the strategy as pending and raise an alert for human review."""
    os.makedirs(REVIEW_DIR, exist_ok=True)
    merged = strategy.model_dump(mode="json")
    _reconcile_naics_state(merged)
    _reconcile_searches(merged.get("searches") or [],
                        merged.get("inferred_naics") or [],
                        merged.get("set_aside_angles") or [],
                        keywords=merged.get("keywords") or [])
    strategy = IntakeStrategy.model_validate(merged)
    packet = ReviewPacket(client_name=strategy.client_name, strategy=strategy)
    # strategy v0: what the machine inferred BEFORE any human edit — the
    # debrief diffs every later state against this
    journal(strategy.client_name, "inference_v0",
            {"state": _term_state(strategy),
             "near_misses": [c.model_dump(mode="json")
                             for c in strategy.near_misses]})
    with open(_path(strategy.client_name), "w") as f:
        f.write(packet.model_dump_json(indent=2))
    md_path = _path(strategy.client_name).replace(".json", ".md")
    with open(md_path, "w") as f:
        f.write(render_markdown(strategy))
    alert_fn(packet, md_path)
    return packet


def _load_packet_snapshot(
    client_name: str, *, workstation_id: Optional[str] = None,
    review_dir: Optional[str] = None,
    native_owner: Optional[bool] = None,
) -> tuple[ReviewPacket, str, str]:
    requested = _workstation_id_value(
        workstation_id or os.environ.get("LILA_EXPECT_WORKSTATION_ID"))
    path = _resolved_packet_path(
        client_name, requested, review_dir, native_owner=native_owner)
    with open(path, "rb") as f:
        exact_bytes = f.read()
    packet = ReviewPacket.model_validate_json(exact_bytes)
    if packet.client_name != client_name:
        raise WorkstationBindingError(
            "review packet client does not match the requested client")
    if packet.strategy.client_name != client_name:
        raise WorkstationBindingError(
            "review packet strategy belongs to a different client")
    if requested is not None:
        try:
            from agents.workstations import (
                canonical_scope as _canonical_scope,
                workstation_id as _workstation_id,
            )
            canonical = _canonical_scope(packet.search_scope)
            actual = _workstation_id(canonical)
        except Exception as exc:
            raise WorkstationBindingError(
                f"review packet scope is invalid: {exc}") from exc
        if actual != requested:
            raise WorkstationBindingError(
                "review packet does not belong to the requested workstation: "
                f"expected {requested}, found {actual}")
        native_path = _path(client_name, requested, review_dir)
        if os.path.abspath(path) == os.path.abspath(native_path):
            if (packet.search_scope is None
                    or packet.search_scope != canonical.as_dict()):
                raise WorkstationBindingError(
                    "native review packet scope is not stored canonically")
    _assert_expected_workstation(
        client_name, packet.search_scope, packet_client=packet.client_name)
    return packet, path, hashlib.sha256(exact_bytes).hexdigest()


def _load_packet_with_path(
    client_name: str, *, workstation_id: Optional[str] = None,
    review_dir: Optional[str] = None,
    native_owner: Optional[bool] = None,
) -> tuple[ReviewPacket, str]:
    packet, path, _digest = _load_packet_snapshot(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner)
    return packet, path


def _native_markdown_workstation_id(
    client_name: str, *, workstation_id: Optional[str],
    review_dir: Optional[str], packet_path: str,
) -> Optional[str]:
    """Return the id only when the exact packet path is workstation-native."""
    requested = _workstation_id_value(
        workstation_id or os.environ.get("LILA_EXPECT_WORKSTATION_ID"))
    if requested is None:
        return None
    native_path = _path(client_name, requested, review_dir)
    if os.path.abspath(packet_path) == os.path.abspath(native_path):
        return requested
    return None


def load_packet(client_name: str, *, workstation_id: Optional[str] = None,
                review_dir: Optional[str] = None,
                native_owner: Optional[bool] = None) -> ReviewPacket:
    return _load_packet_with_path(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner)[0]


def load_packet_snapshot(
    client_name: str, *, workstation_id: Optional[str] = None,
    review_dir: Optional[str] = None,
    require_native_owner: Optional[bool] = None,
) -> PacketSnapshot:
    """Return packet + fingerprint from the same exact byte read."""
    requested = _workstation_id_value(
        workstation_id or os.environ.get("LILA_EXPECT_WORKSTATION_ID"))
    native_env = os.environ.get("LILA_EXPECT_NATIVE_WORKSTATION")
    owner_mode = require_native_owner
    if owner_mode is None and native_env in {"0", "1"}:
        owner_mode = native_env == "1"
    packet, path, digest = _load_packet_snapshot(
        client_name, workstation_id=requested, review_dir=review_dir,
        native_owner=owner_mode)
    native_path = (_path(client_name, requested, review_dir)
                   if requested is not None else None)
    selected_native = bool(
        native_path is not None
        and os.path.abspath(path) == os.path.abspath(native_path))
    child_native = native_env == "1"
    require_native = owner_mode is True
    receipt_digest = None
    registry_digest = None
    if require_native and (not requested or not selected_native):
        raise WorkstationBindingError(
            "native workstation packet ownership is unavailable")
    if selected_native and requested:
        try:
            from agents.workstations import native_workstation_ownership
            ownership = native_workstation_ownership(
                client_name, requested,
                review_dir=review_dir or REVIEW_DIR)
        except Exception as exc:
            if require_native:
                raise WorkstationBindingError(
                    f"native workstation ownership is invalid: {exc}") from exc
            ownership = None
        if ownership is not None:
            if not ownership.clone_baseline:
                if require_native:
                    raise WorkstationBindingError(
                        "native workstation has no cloned strategy packet authority")
                ownership = None
        if ownership is not None:
            receipt_digest = ownership.receipt_sha256
            registry_digest = ownership.registry_sha256
    if require_native:
        if receipt_digest is None or registry_digest is None:
            raise WorkstationBindingError(
                "native workstation packet ownership is unavailable")
        if child_native:
            expected_receipt = os.environ.get(
                "LILA_EXPECT_WORKSTATION_RECEIPT_SHA256")
            expected_registry = os.environ.get(
                "LILA_EXPECT_WORKSTATION_REGISTRY_SHA256")
            if not expected_receipt or not expected_registry:
                raise WorkstationBindingError(
                    "incomplete native ownership binding in child environment")
            if (expected_receipt != receipt_digest
                    or expected_registry != registry_digest):
                raise WorkstationBindingError(
                    "native workstation ownership changed after authorization")
    return PacketSnapshot(
        packet=packet,
        path=path,
        sha256=digest,
        revision=packet.revision_count,
        workstation_id=requested,
        receipt_sha256=receipt_digest,
        registry_sha256=registry_digest,
    )


def _assert_expected_packet_sha(actual: str,
                                expected: Optional[str]) -> None:
    if expected is None:
        return
    if (not isinstance(expected, str) or len(expected) != 64
            or any(char not in "0123456789abcdef" for char in expected)):
        raise WorkstationBindingError("invalid expected packet fingerprint")
    if actual != expected:
        raise WorkstationBindingError(
            "review packet changed since this workstation was loaded")


@contextmanager
def _packet_write_lock(
    client_name: str, *, workstation_id: Optional[str],
    review_dir: Optional[str],
    native_owner: Optional[bool] = None,
):
    """Cross-process serialization for one review-packet mutation family."""
    root = os.path.abspath(review_dir or REVIEW_DIR)
    wid = _workstation_id_value(workstation_id)
    if native_owner is False:
        wid = None
    elif (native_owner is None and wid is not None
          and not os.path.exists(_path(client_name, wid, review_dir))):
        # Explicit legacy-current ids and omitted legacy callers must share a
        # lock during the compatibility window.
        wid = None
    lock_key = hashlib.sha256(
        f"{root}\0{client_name}\0{wid or 'legacy'}".encode("utf-8")
    ).hexdigest()
    lock_root = os.path.join(tempfile.gettempdir(), "lila-review-packet-locks")
    os.makedirs(lock_root, exist_ok=True)
    with open(os.path.join(lock_root, f"{lock_key}.lock"), "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _native_ownership_lock(
    client_name: str, *, review_dir: Optional[str],
):
    """Share the registry-last creation lock while native authority is used.

    ``agents.workstations`` intentionally keeps creation's lock file outside
    the repository.  Native packet mutations and the final pre-fan-out read use
    the identical key so registry/receipt ownership cannot change between its
    generation check and the first write or source submission.
    """
    root = os.path.realpath(review_dir or REVIEW_DIR)
    lock_key = hashlib.sha256(
        f"{root}\0{client_name}".encode("utf-8")).hexdigest()
    lock_path = os.path.join(
        tempfile.gettempdir(), f"lila-workstation-create-{lock_key}.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def packet_binding_lock(
    client_name: str, *, workstation_id: Optional[str] = None,
    review_dir: Optional[str] = None,
    native_owner: Optional[bool] = None,
):
    """Serialize one final packet/owner check with writers and fan-out start."""
    with _packet_write_lock(
            client_name, workstation_id=workstation_id,
            review_dir=review_dir, native_owner=native_owner):
        if native_owner is True:
            with _native_ownership_lock(
                    client_name, review_dir=review_dir):
                yield
        else:
            yield


def _assert_expected_native_ownership(
    client_name: str, *, workstation_id: Optional[str],
    review_dir: Optional[str], native_owner: Optional[bool],
    expected_registry_sha256: Optional[str],
    expected_receipt_sha256: Optional[str],
) -> None:
    expected = (expected_registry_sha256, expected_receipt_sha256)
    if native_owner is not True:
        if any(value is not None for value in expected):
            raise WorkstationBindingError(
                "native ownership tokens require native packet authority")
        return
    if workstation_id is None:
        raise WorkstationBindingError(
            "native mutation requires a workstation id")
    for value in expected:
        if (not isinstance(value, str) or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)):
            raise WorkstationBindingError(
                "native mutation has incomplete ownership generation")
    try:
        from agents.workstations import native_workstation_ownership
        ownership = native_workstation_ownership(
            client_name, workstation_id,
            review_dir=review_dir or REVIEW_DIR)
    except Exception as exc:
        raise WorkstationBindingError(
            f"native workstation ownership is invalid: {exc}") from exc
    if not ownership.clone_baseline:
        raise WorkstationBindingError(
            "native workstation has no cloned strategy packet authority")
    if (ownership.registry_sha256 != expected_registry_sha256
            or ownership.receipt_sha256 != expected_receipt_sha256):
        raise WorkstationBindingError(
            "native workstation ownership changed before mutation")


def _serialized_packet_writer(fn):
    @wraps(fn)
    def wrapped(client_name, *args, **kwargs):
        with packet_binding_lock(
                client_name,
                workstation_id=kwargs.get("workstation_id"),
                review_dir=kwargs.get("review_dir"),
                native_owner=kwargs.get("native_owner")):
            return fn(client_name, *args, **kwargs)
    return wrapped


def _assert_expected_workstation(
    client_name: str,
    search_scope: Optional[dict],
    *,
    packet_client: Optional[str],
) -> None:
    """Enforce the launch-time workstation identity inside a child process."""
    expected_client = os.environ.get("LILA_EXPECT_CLIENT_NAME")
    expected_workstation = os.environ.get("LILA_EXPECT_WORKSTATION_ID")
    if not expected_client and not expected_workstation:
        return
    if not expected_client or not expected_workstation:
        raise WorkstationBindingError(
            "incomplete workstation binding in child environment")
    if client_name != expected_client or packet_client != expected_client:
        raise WorkstationBindingError(
            "review packet client changed after this job was launched")
    try:
        # Lazy import avoids a module cycle: workstations uses this module's
        # established scope-designator grammar.
        from agents.workstations import workstation_id
        actual_workstation = workstation_id(search_scope)
    except Exception as exc:
        raise WorkstationBindingError(
            f"review packet scope became invalid after launch: {exc}") from exc
    if actual_workstation != expected_workstation:
        raise WorkstationBindingError(
            "review packet scope changed after this job was launched: "
            f"expected {expected_workstation}, found {actual_workstation}")


@_serialized_packet_writer
def decide(client_name: str, approve: bool, note: str = "", *,
           workstation_id: Optional[str] = None,
           review_dir: Optional[str] = None,
           expected_sha256: Optional[str] = None,
           native_owner: Optional[bool] = None,
           expected_registry_sha256: Optional[str] = None,
           expected_receipt_sha256: Optional[str] = None) -> ReviewPacket:
    _assert_expected_native_ownership(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner,
        expected_registry_sha256=expected_registry_sha256,
        expected_receipt_sha256=expected_receipt_sha256)
    packet, packet_path, digest = _load_packet_snapshot(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner)
    _assert_expected_packet_sha(digest, expected_sha256)
    packet.status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
    packet.reviewer_note = note
    packet.decided_at = datetime.now(timezone.utc)
    _journal_packet_path(packet_path, "decision", {
        "approved": approve, "note": note,
        "state": _term_state(packet.strategy)})
    from tools.atomic_io import atomic_write_text
    atomic_write_text(packet_path, packet.model_dump_json(indent=2))

    # GENERATE THE CLIENT FILES ON APPROVAL, never on intake. A cold NetApp
    # run reached a PENDING packet and stopped dead, because the sweep gate
    # needs three files under clients/<slug>/ and nothing created them.
    #
    # This runs HERE and not at intake because a pending strategy has not been
    # read by a human, and these files decide what the sweep looks for.
    # Neither gate is weakened: the 409 on an unapproved strategy is
    # untouched, and the generated scope carries an EMPTY preset, so the sweep
    # gate stays shut until an operator supplies what only they can.
    #
    # It cannot fail an approval. An approval that succeeded and then reported
    # failure would leave the operator unsure whether the decision landed.
    if approve:
        try:
            from agents.decisions.client_files import write_generated
            result = write_generated(json.loads(packet.model_dump_json()))
            print(f"[client-files] {result['slug']}: wrote "
                  f"{len(result['written'])}, kept {len(result['kept_existing'])}"
                  f" existing; {len(result['human_required'])} fields still "
                  f"need a human, sweep still blocked by "
                  f"{result['sweep_blocked_by']}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - approval is the decision
            # stderr, NOT the packet journal. That journal is the record of
            # intake JUDGMENT (inference, gate revision, scope change) and a
            # reader counts its entries; an infrastructure error in it is
            # both a category confusion and a broken sequence.
            print(f"[client-files] generation failed for "
                  f"{packet.client_name!r} ({type(exc).__name__}: {exc}); "
                  f"the approval itself stands",
                  file=sys.stderr)
    return packet


def _clean_research_entities(values) -> list[dict]:
    """Operator-supplied entity rows at the review gate (truth-purge
    completion, 2026-08-03): when web research returns no cited findings the
    gate is where the rival/channel side gets supplied, so the gate must
    accept it. Every row needs a nonblank name and a closed kind; rows
    dedupe on (kind, name); provenance defaults to the operator so a
    gate-supplied rival can never masquerade as researched evidence."""
    out: list[dict] = []
    seen: set = set()
    for value in values or []:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if not isinstance(value, dict):
            raise RevisionError("research_entities rows must be objects")
        name = " ".join(str(value.get("name") or "").split())
        kind = str(value.get("kind") or "").strip()
        if not name:
            raise RevisionError("a research entity needs a nonblank name")
        if kind not in ("product", "competitor", "reseller"):
            raise RevisionError(
                f"research entity {name!r} has unknown kind {kind!r}")
        key = (kind, name.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name, "kind": kind,
            "rationale": " ".join(str(value.get("rationale") or "").split())
            or "supplied at the review gate by the operator",
            "source": " ".join(str(value.get("source") or "").split()),
        })
    return out


def _clean_list(values, *, keep_case: bool = True) -> list[str]:
    out, seen = [], set()
    for v in values or []:
        s = str(v).strip()
        key = s if keep_case else s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out


# ── Target gate (2026-07-12): Assess -> Target is an explicit operator door ──
# The outreach lane (contact plan, Apollo, target report) stays physically
# locked until the operator, with a CURRENT Assess approval in hand, clicks
# "proceed to Target". Fresh evidence stales the Assess approval, which
# re-locks Target automatically; revocation is journaled, never deleted.

def _target_approval_path(client_name: str,
                          review_dir: Optional[str] = None) -> str:
    return os.path.join(review_dir or REVIEW_DIR,
                        f"{_artifact_slug(client_name)}.target_approval.json")


def load_target_approval(client_name: str,
                         review_dir: Optional[str] = None) -> Optional[dict]:
    try:
        with open(_target_approval_path(client_name, review_dir),
                  encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def approve_target(client_name: str, *, note: str = "",
                   review_dir: Optional[str] = None) -> dict:
    from tools.atomic_io import atomic_write_text
    payload = {
        "client": client_name,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved_by": "operator",
        "note": note,
    }
    atomic_write_text(_target_approval_path(client_name, review_dir),
                      json.dumps(payload, indent=2, ensure_ascii=False))
    journal(client_name, "target_approval", {"note": note})
    return payload


def revoke_target(client_name: str, *,
                  review_dir: Optional[str] = None) -> bool:
    from tools.atomic_io import atomic_write_text
    payload = load_target_approval(client_name, review_dir)
    if payload is None:
        return False
    payload["revoked_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    atomic_write_text(_target_approval_path(client_name, review_dir),
                      json.dumps(payload, indent=2, ensure_ascii=False))
    journal(client_name, "target_approval_revoked", {})
    return True


def target_gate_status(client_name: str, *,
                       review_dir: Optional[str] = None
                       ) -> tuple[bool, list[str]]:
    """(unlocked, problems). Unlocked means BOTH: an unrevoked explicit
    proceed-to-Target decision on file AND a currently valid Assess approval
    (the same evidence-bound gate Produce uses, so evidence drift re-locks
    the outreach lane without any new click)."""
    problems: list[str] = []
    payload = load_target_approval(client_name, review_dir)
    if payload is None or payload.get("revoked_at"):
        problems.append(
            "Target is locked: approve the assessment and proceed to Target "
            "from the Review step first")
    try:
        from agents.assess.approval import assess_approval_for_release
        _, status, assess_problems = assess_approval_for_release(
            client_name, review_dir=review_dir)
        if status != "approved":
            problems.append(
                "current Assess results are not approved: "
                + "; ".join(assess_problems[:2] or [str(status)]))
    except Exception as exc:  # noqa: BLE001 - gate machinery down: fail closed
        problems.append(f"Assess approval gate unavailable: {exc}")
    return (not problems, problems)


# Target unlock and Targeting Review completion are intentionally separate.
# The former permits work; the latter is an evidence-bound release receipt.
TARGETING_LANES = (
    "buyer", "acquisition", "partner", "incumbent", "positioning", "event",
)
TARGETING_DISPOSITIONS = {
    "ready_to_contact", "needs_enrichment", "needs_relationship_validation",
    "role_identified_person_unknown", "partner_route", "buyer_route",
    "influencer_route", "watch", "defer", "reject",
}
TARGETING_ACTION_FIELDS = (
    "lane", "disposition", "route", "proposed_role", "first_ask", "message",
    "call_to_action", "action_window", "owner", "learn", "desired_outcome",
    "qualification_question", "stop_condition", "promotion_criteria",
)
TARGETING_GAP_FIELDS = (
    "evidence_checked", "missing", "next_action", "owner", "deadline",
)


def targeting_play_key(reason: str) -> str:
    """Stable, non-semantic key for a promoted play label."""
    return hashlib.sha256(str(reason).strip().encode("utf-8")).hexdigest()[:16]


def _targeting_plan_path(client_name: str,
                         review_dir: Optional[str] = None) -> str:
    return os.path.join(review_dir or REVIEW_DIR,
                        f"{_artifact_slug(client_name)}.targeting_plan.json")


def _targeting_review_path(client_name: str,
                           review_dir: Optional[str] = None) -> str:
    return os.path.join(review_dir or REVIEW_DIR,
                        f"{_artifact_slug(client_name)}.targeting_review.json")


def _load_json_object(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def load_targeting_plan(client_name: str,
                        review_dir: Optional[str] = None) -> dict:
    return _load_json_object(_targeting_plan_path(client_name, review_dir)) or {
        "version": 2, "client": client_name, "actions": {},
        "play_lane_dispositions": {},
    }


def load_targeting_review(client_name: str,
                          review_dir: Optional[str] = None) -> Optional[dict]:
    return _load_json_object(_targeting_review_path(client_name, review_dir))


def save_targeting_plan(client_name: str, *, actions: Optional[dict] = None,
                        lane_dispositions: Optional[dict] = None,
                        play_lane_dispositions: Optional[dict] = None,
                        review_dir: Optional[str] = None) -> dict:
    from tools.atomic_io import atomic_write_text
    plan = load_targeting_plan(client_name, review_dir)
    if actions is not None:
        plan["actions"] = actions
    if lane_dispositions is not None:
        plan["lane_dispositions"] = lane_dispositions
    if play_lane_dispositions is not None:
        plan["play_lane_dispositions"] = play_lane_dispositions
    plan.update({
        "version": 2,
        "client": client_name,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_by": "operator",
    })
    atomic_write_text(_targeting_plan_path(client_name, review_dir),
                      json.dumps(plan, indent=2, ensure_ascii=False))
    return plan


def _target_source(target: dict) -> str:
    for channel in (target.get("email"), target.get("phone")):
        if isinstance(channel, dict) and str(channel.get("source_url") or "").strip():
            return str(channel["source_url"]).strip()
    return str(target.get("source_url") or "").strip()


def targeting_target_set_sha256(targets: list[dict]) -> str:
    rows = [{
        "id": str(row.get("id") or ""),
        "person_name": str(row.get("person_name") or ""),
        "company": str(row.get("company") or ""),
        "agency": str(row.get("agency") or ""),
        "reason": str(row.get("reason") or ""),
        "reason_kind": str(row.get("reason_kind") or ""),
        "reason_rank": row.get("reason_rank"),
        "title": str(row.get("title") or ""),
        "last_observed": str(row.get("last_observed") or ""),
        "bucket": str(row.get("bucket") or ""),
        "needs": row.get("needs") or [],
        "email": row.get("email") or {},
        "phone": row.get("phone") or {},
        "source_url": _target_source(row),
    } for row in targets]
    body = json.dumps(sorted(rows, key=lambda row: row["id"]),
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _assess_approval_sha256(client_name: str,
                            review_dir: Optional[str] = None) -> Optional[str]:
    try:
        from agents.assess.approval import assess_approval_for_release
        approval, status, _problems = assess_approval_for_release(
            client_name, review_dir=review_dir)
    except Exception:  # noqa: BLE001 - absent gate machinery fails closed below
        return None
    if status != "approved" or approval is None:
        return None
    body = json.dumps(approval, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _target_unlock_sha256(client_name: str,
                          review_dir: Optional[str] = None) -> Optional[str]:
    payload = load_target_approval(client_name, review_dir)
    if payload is None or payload.get("revoked_at"):
        return None
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def validate_targeting_plan(plan: dict, targets: list[dict]) -> list[str]:
    problems: list[str] = []
    by_id = {str(row.get("id") or ""): row for row in targets
             if str(row.get("id") or "")}
    actions = plan.get("actions") if isinstance(plan.get("actions"), dict) else {}
    play_lane_dispositions = (
        plan.get("play_lane_dispositions")
        if isinstance(plan.get("play_lane_dispositions"), dict) else {})
    complete_ids: set[str] = set()
    covered_by_play: set[tuple[str, str]] = set()
    for target_id, action in actions.items():
        target = by_id.get(str(target_id))
        if target is None:
            problems.append(f"target action no longer matches current inventory: {target_id}")
            continue
        if not isinstance(action, dict):
            problems.append(f"target action is not an object: {target_id}")
            continue
        lane = str(action.get("lane") or "")
        disposition = str(action.get("disposition") or "")
        if lane not in TARGETING_LANES:
            problems.append(f"target {target_id} has no valid route lane")
        if disposition not in TARGETING_DISPOSITIONS:
            problems.append(f"target {target_id} has no valid disposition")
        if not _target_source(target):
            problems.append(f"target {target_id} has no authoritative source")
        if disposition in {"reject", "defer"}:
            if len(str(action.get("reject_reason") or "").strip()) < 8:
                problems.append(f"target {target_id} needs a reject or defer reason")
            continue
        missing = [field for field in TARGETING_ACTION_FIELDS
                   if not str(action.get(field) or "").strip()]
        if missing:
            problems.append(f"target {target_id} is missing: {', '.join(missing)}")
            continue
        complete_ids.add(str(target_id))
        covered_by_play.add((targeting_play_key(target.get("reason") or ""), lane))

    top_plays = {targeting_play_key(play): play for play in {
        str(row.get("reason") or "").strip()
        for row in targets if int(row.get("reason_rank") or 999) < 90
    } if play}
    for play_id, play in sorted(top_plays.items()):
        ids = {str(row.get("id") or "") for row in targets
               if str(row.get("reason") or "").strip() == play}
        if not (ids & complete_ids):
            problems.append(f"top play has no complete target action: {play}")
        dispositions = play_lane_dispositions.get(play_id)
        if not isinstance(dispositions, dict):
            problems.append(f"top play has no lane dispositions: {play}")
            continue
        for lane in TARGETING_LANES:
            disposition = dispositions.get(lane)
            if not isinstance(disposition, dict):
                problems.append(f"top play {play} has no {lane} lane disposition")
                continue
            status = str(disposition.get("status") or "")
            if status == "covered":
                if (play_id, lane) not in covered_by_play:
                    problems.append(
                        f"top play {play} marks {lane} covered without a complete action")
            elif status in {"no_qualified_target", "not_applicable"}:
                missing_gap = [field for field in TARGETING_GAP_FIELDS
                               if not str(disposition.get(field) or "").strip()]
                if missing_gap:
                    problems.append(
                        f"top play {play} {lane} gap is missing: "
                        + ", ".join(missing_gap))
            else:
                problems.append(f"top play {play} has no valid {lane} lane disposition")
    if not top_plays:
        problems.append("no promoted play has a source-bound target")
    return problems


def approve_targeting_review(client_name: str, targets: list[dict], *,
                              review_dir: Optional[str] = None) -> dict:
    from tools.atomic_io import atomic_write_text
    unlocked, gate_problems = target_gate_status(client_name, review_dir=review_dir)
    if not unlocked:
        raise ValueError("Target lane is locked: " + "; ".join(gate_problems))
    plan = load_targeting_plan(client_name, review_dir)
    problems = validate_targeting_plan(plan, targets)
    if problems:
        raise ValueError("Targeting Review is incomplete: " + "; ".join(problems))
    plan_body = json.dumps(plan, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    payload = {
        "version": 2,
        "client": client_name,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved_by": "operator",
        "target_set_sha256": targeting_target_set_sha256(targets),
        "plan_sha256": hashlib.sha256(plan_body.encode("utf-8")).hexdigest(),
        "assess_approval_sha256": _assess_approval_sha256(
            client_name, review_dir),
        "target_unlock_sha256": _target_unlock_sha256(
            client_name, review_dir),
    }
    atomic_write_text(_targeting_review_path(client_name, review_dir),
                      json.dumps(payload, indent=2, ensure_ascii=False))
    journal(client_name, "targeting_review_approved", {
        "target_set_sha256": payload["target_set_sha256"],
        "plan_sha256": payload["plan_sha256"],
    })
    return payload


def targeting_review_status(client_name: str, targets: list[dict], *,
                            review_dir: Optional[str] = None
                            ) -> tuple[bool, list[str]]:
    problems: list[str] = []
    unlocked, gate_problems = target_gate_status(client_name, review_dir=review_dir)
    if not unlocked:
        problems.extend(gate_problems)
    plan = load_targeting_plan(client_name, review_dir)
    problems.extend(validate_targeting_plan(plan, targets))
    approval = load_targeting_review(client_name, review_dir)
    if approval is None:
        problems.append("Targeting Review has not been approved")
    else:
        if approval.get("target_set_sha256") != targeting_target_set_sha256(targets):
            problems.append("target inventory changed after Targeting Review approval")
        plan_body = json.dumps(plan, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        if approval.get("plan_sha256") != hashlib.sha256(plan_body.encode("utf-8")).hexdigest():
            problems.append("target action plan changed after Targeting Review approval")
        if approval.get("assess_approval_sha256") != _assess_approval_sha256(
                client_name, review_dir):
            problems.append("Assess approval binding changed after Targeting Review approval")
        if approval.get("target_unlock_sha256") != _target_unlock_sha256(
                client_name, review_dir):
            problems.append("Target unlock binding changed after Targeting Review approval")
    return (not problems, problems)


def targeting_review_binding_receipt(
    client_name: str, *, targets: Optional[list[dict]] = None,
    expected_target_set_sha256: Optional[str] = None,
    review_dir: Optional[str] = None,
) -> tuple[Optional[dict], bool, list[str]]:
    """Return the exact, currently bound Targeting Review release receipt.

    The release boundary must provide either the current target inventory or
    the exact inventory hash derived by the Control Room at job launch. The
    former also validates the action plan against the live rows; the latter
    preserves that exact binding across the child-process boundary.
    """
    problems: list[str] = []
    unlocked, gate_problems = target_gate_status(
        client_name, review_dir=review_dir)
    if not unlocked:
        problems.extend(gate_problems)
    plan = load_targeting_plan(client_name, review_dir)
    approval = load_targeting_review(client_name, review_dir)
    if approval is None:
        return None, False, problems + [
            "Targeting Review has not been approved"]

    keys = (
        "target_set_sha256", "plan_sha256",
        "assess_approval_sha256", "target_unlock_sha256",
    )
    receipt = {
        "version": approval.get("version"),
        "approved_at": approval.get("approved_at"),
        "approved_by": approval.get("approved_by"),
        **{key: approval.get(key) for key in keys},
    }
    for key in keys:
        value = receipt.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            problems.append(f"Targeting Review has no exact {key} binding")

    plan_body = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if receipt.get("plan_sha256") != hashlib.sha256(
            plan_body.encode("utf-8")).hexdigest():
        problems.append("target action plan changed after Targeting Review approval")
    if receipt.get("assess_approval_sha256") != _assess_approval_sha256(
            client_name, review_dir):
        problems.append("Assess approval binding changed after Targeting Review approval")
    if receipt.get("target_unlock_sha256") != _target_unlock_sha256(
            client_name, review_dir):
        problems.append("Target unlock binding changed after Targeting Review approval")
    if expected_target_set_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", expected_target_set_sha256):
        problems.append("current target inventory binding is not a SHA-256")
    if targets is not None:
        current_target_set_sha256 = targeting_target_set_sha256(targets)
        problems.extend(validate_targeting_plan(plan, targets))
        if (expected_target_set_sha256 is not None
                and expected_target_set_sha256 != current_target_set_sha256):
            problems.append(
                "supplied target inventory disagrees with its launch binding")
        if receipt.get("target_set_sha256") != current_target_set_sha256:
            problems.append(
                "target inventory changed after Targeting Review approval")
    elif expected_target_set_sha256 is not None:
        if receipt.get("target_set_sha256") != expected_target_set_sha256:
            problems.append(
                "target inventory changed after Targeting Review approval")
    else:
        problems.append(
            "current target inventory binding is required for release")
    return receipt, not problems, problems


def effective_query_terms(strategy, spec, *, source: Optional[str] = None
                          ) -> list[str]:
    """THE terms a source searches with — one owner (2026-07-12).

    The operator's approved capability/technology/search terms ARE the
    SAM.gov opportunity screen: an edit at the gate must reach the sweep even
    when a stale packet still carries curated sam.gov terms. Agency,
    set-aside, and NAICS keywords are procurement lenses, not capability text;
    putting them in the full-text screen floods the candidate pool with
    unrelated notices. Other sources keep their curated vocabulary on
    purpose."""
    from tools.query_terms import capability_query_terms

    approved_keywords = list(getattr(strategy, "keywords", None) or [])
    keyword_terms = [str(getattr(k, "term", "")).strip()
                     for k in approved_keywords]
    keyword_terms = [t for t in keyword_terms if t]
    if spec is None:
        if source == "sam.gov" and keyword_terms:
            return capability_query_terms(approved_keywords)
        return keyword_terms
    if isinstance(spec, dict):
        source = source or spec.get("source")
        spec_terms = list(spec.get("query_terms") or [])
    else:
        source = source or getattr(spec, "source", None)
        spec_terms = list(getattr(spec, "query_terms", None) or [])
    if source == "sam.gov" and keyword_terms:
        return capability_query_terms(approved_keywords)
    return spec_terms or keyword_terms


def _reconcile_searches(searches: list[dict], naics: list[str],
                        set_asides: list[str],
                        keywords: Optional[list[dict]] = None) -> None:
    """The search plan is DERIVED from the strategy; keep it from contradicting
    an edit. A source's naics_codes mirror inferred_naics (verified: sam.gov
    and usaspending.gov specs pin the same NAICS), so a NAICS edit that did not
    propagate here would silently not take effect. Reconcile by stable source
    identity, not by whether a list happened to be nonempty: SAM.gov and
    USAspending always mirror the boundary, while web is always cleared.
    Non-web specs mirror the edited set-aside angles (run_searches reads
    spec.set_asides directly, not the strategy fallback, when a spec exists).

    Keyword doctrine: the sam.gov spec's query_terms are DERIVED from the
    edited capability/technology/search terms, so the editor always shows the
    terms the opportunity screen will actually run without agency,
    set-aside, or NAICS pollution.
    usaspending (vendor/reseller vocabulary) and web (credential probes) keep
    their curated terms; see effective_query_terms."""
    from tools.query_terms import capability_query_terms
    keyword_terms = capability_query_terms(keywords or [])
    for spec in searches:
        source = str(spec.get("source") or "").strip().lower()
        if source in ("sam.gov", "usaspending.gov"):
            spec["naics_codes"] = list(naics)
        elif source == "web":
            spec["naics_codes"] = []
        if source != "web":
            spec["set_asides"] = list(set_asides)
        if source == "sam.gov" and keywords:
            spec["query_terms"] = list(keyword_terms)


@_serialized_packet_writer
def revise(client_name: str, updates: dict, *,
           workstation_id: Optional[str] = None,
           review_dir: Optional[str] = None,
           expected_sha256: Optional[str] = None,
           native_owner: Optional[bool] = None,
           expected_registry_sha256: Optional[str] = None,
           expected_receipt_sha256: Optional[str] = None) -> ReviewPacket:
    """Apply operator edits to the strategy AT THE GATE, re-validate through the
    schema, and persist. Only EDITABLE_FIELDS are touched; the merged strategy
    is validated so a bad edit (e.g. an unknown keyword category) is rejected
    loudly, never written. Revising a REJECTED strategy re-opens it as PENDING
    (this is the 'revise intake' path made real); an APPROVED strategy cannot be
    revised (searches are already gated on it — reject or re-run instead)."""
    _assert_expected_native_ownership(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner,
        expected_registry_sha256=expected_registry_sha256,
        expected_receipt_sha256=expected_receipt_sha256)
    packet, packet_path, digest = _load_packet_snapshot(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner)
    _assert_expected_packet_sha(digest, expected_sha256)
    if packet.status == ReviewStatus.APPROVED:
        raise RevisionError(
            f"strategy for {client_name!r} is already approved — revise before "
            "approval, or reject and rebuild")

    merged = packet.strategy.model_dump(mode="json")
    touched: list[str] = []
    for field in EDITABLE_FIELDS:
        if field not in updates:
            continue
        val = updates[field]
        if field == "pursuit_strategy":
            merged[field] = str(val or "").strip()
        elif field == "keywords":
            merged[field] = [
                k for k in (_clean_keyword(
                    kw, default_rationale="added at review by the operator")
                    for kw in (val or [])) if k]
        elif field == "kept_out":
            # preserved, restorable — never a search term. Same schema shape as
            # keywords so a restore round-trips with full provenance intact.
            merged[field] = [
                k for k in (_clean_keyword(
                    kw, default_rationale="moved out of the search by the operator")
                    for kw in (val or [])) if k]
        elif field == "inferred_naics":
            merged[field] = _clean_naics_codes(val)
        elif field == "naics_meta":
            merged[field] = val
        elif field == "kept_out_naics":
            merged[field] = val
        elif field == "research_entities":
            merged[field] = _clean_research_entities(val)
        else:  # the plain string lists
            merged[field] = _clean_list(val, keep_case=True)
        touched.append(field)

    if not touched:
        return packet  # nothing to do — never stamp a no-op revision

    _reconcile_naics_state(merged, packet.strategy)
    _reconcile_searches(merged.get("searches") or [],
                        merged.get("inferred_naics") or [],
                        merged.get("set_aside_angles") or [],
                        keywords=merged.get("keywords") or [])

    try:
        revised = IntakeStrategy.model_validate(merged)
    except Exception as e:  # pydantic ValidationError et al. — never persist a bad edit
        raise RevisionError(f"revision rejected by the schema: {e}") from e

    _journal_packet_path(packet_path, "gate_revision", {
        "revision": packet.revision_count + 1, "touched": touched,
        "before": _term_state(packet.strategy),
        "after": _term_state(revised)})
    packet.strategy = revised
    packet.revised_at = datetime.now(timezone.utc)
    packet.revision_count += 1
    # a hand-revised rejected strategy is a fresh proposal awaiting a decision
    if packet.status == ReviewStatus.REJECTED:
        packet.status = ReviewStatus.PENDING
        packet.decided_at = None
        packet.reviewer_note = ""

    from tools.atomic_io import atomic_write_text
    atomic_write_text(packet_path, packet.model_dump_json(indent=2))
    atomic_write_text(
        packet_path.replace(".review.json", ".review.md"),
        render_markdown(
            revised,
            workstation_id=_native_markdown_workstation_id(
                client_name, workstation_id=workstation_id,
                review_dir=review_dir, packet_path=packet_path)))
    return packet


@_serialized_packet_writer
def amend_terms(client_name: str, keywords: Optional[list] = None,
                inferred_naics: Optional[list] = None,
                kept_out: Optional[list] = None,
                naics_meta: Optional[list] = None,
                kept_out_naics: Optional[list] = None, *,
                workstation_id: Optional[str] = None,
                review_dir: Optional[str] = None,
                expected_sha256: Optional[str] = None,
                native_owner: Optional[bool] = None,
                expected_registry_sha256: Optional[str] = None,
                expected_receipt_sha256: Optional[str] = None) -> ReviewPacket:
    """The ITERATE-LOOP carve-out: keyword/NAICS tuning on an APPROVED
    strategy, approval intact. The gate approval covers the strategy;
    search-term tuning during the search<->review loop is operational and
    must not force an unlock. Same validation as revise(); records a
    revision; touches NOTHING else.

    kept_out rides this path too (Cycle 5 review P1): removing a keyword in
    the post-approval loop must preserve its restorable provenance, exactly
    like the pre-approval workshop, or 'kept out, not deleted' silently breaks
    once a strategy is locked."""
    _assert_expected_native_ownership(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner,
        expected_registry_sha256=expected_registry_sha256,
        expected_receipt_sha256=expected_receipt_sha256)
    packet, packet_path, digest = _load_packet_snapshot(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir, native_owner=native_owner)
    _assert_expected_packet_sha(digest, expected_sha256)
    merged = packet.strategy.model_dump(mode="json")
    touched = []
    if keywords is not None:
        merged["keywords"] = [
            k for k in (_clean_keyword(
                kw, default_rationale="amended in the search-review loop")
                for kw in keywords) if k]
        touched.append("keywords")
    if kept_out is not None:
        merged["kept_out"] = [
            k for k in (_clean_keyword(
                kw, default_rationale="moved out of the search by the operator")
                for kw in kept_out) if k]
        touched.append("kept_out")
    if inferred_naics is not None:
        merged["inferred_naics"] = _clean_naics_codes(inferred_naics)
        touched.append("inferred_naics")
    if naics_meta is not None:
        merged["naics_meta"] = naics_meta
        touched.append("naics_meta")
    if kept_out_naics is not None:
        merged["kept_out_naics"] = kept_out_naics
        touched.append("kept_out_naics")
    if not touched:
        return packet
    _reconcile_naics_state(merged, packet.strategy)
    _reconcile_searches(merged.get("searches") or [],
                        merged.get("inferred_naics") or [],
                        merged.get("set_aside_angles") or [],
                        keywords=merged.get("keywords") or [])
    try:
        revised = IntakeStrategy.model_validate(merged)
    except Exception as e:  # never persist a bad edit
        raise RevisionError(f"term amendment rejected by the schema: {e}") from e
    _journal_packet_path(packet_path, "term_amendment", {
        "revision": packet.revision_count + 1, "touched": touched,
        "before": _term_state(packet.strategy),
        "after": _term_state(revised)})
    packet.strategy = revised
    packet.revised_at = datetime.now(timezone.utc)
    packet.revision_count += 1
    from tools.atomic_io import atomic_write_text
    atomic_write_text(packet_path, packet.model_dump_json(indent=2))
    atomic_write_text(
        packet_path.replace(".review.json", ".review.md"),
        render_markdown(
            revised,
            workstation_id=_native_markdown_workstation_id(
                client_name, workstation_id=workstation_id,
                review_dir=review_dir, packet_path=packet_path)))
    return packet


def load_approved(client_name: str, *, workstation_id: Optional[str] = None,
                  review_dir: Optional[str] = None) -> IntakeStrategy:
    """Return the approved strategy, or raise if not approved — this is the hard gate."""
    return load_approved_packet(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir).strategy


def load_approved_packet(client_name: str, *,
                         workstation_id: Optional[str] = None,
                         review_dir: Optional[str] = None) -> ReviewPacket:
    """Return one approved packet snapshot for a bound runner."""
    packet = load_packet(
        client_name, workstation_id=workstation_id,
        review_dir=review_dir)
    if not packet.is_approved:
        raise PermissionError(
            f"strategy for {client_name!r} is '{packet.status.value}', not approved — "
            "searches are blocked until a human approves."
        )
    return packet
