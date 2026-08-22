"""Operational human approval for releasing an Assess deliverable.

The approval is bound to the exact operator-selected evidence universe: current
gate scope, canonical sweep contents, and the complete capability-profile JSON.
Changing any of those inputs makes the stored approval ineligible without
deleting its audit record. Legacy timestamp-only approvals are readable but can
never authorize release.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from agents.assess.binding import (
    binding_drift, build_binding, digest as _digest, jsonable as _jsonable,
)
from agents.review import _artifact_slug as _slug
from tools.atomic_io import atomic_write_json as _atomic_write_json

_UNSET = object()


def approval_path(client_name: str, *, review_dir: Optional[str] = None) -> str:
    if review_dir is None:
        from agents.review import REVIEW_DIR
        review_dir = REVIEW_DIR
    return os.path.join(review_dir, f"{_slug(client_name)}.assess_approval.json")


def _scope_and_sweep(client_name: str, review_dir: Optional[str],
                     *, need_path: bool = True) -> tuple[str, Optional[str]]:
    """Resolve the same scope designator and sweep family as the report gate.

    need_path=False resolves the designator ONLY (lazy, like the horizon
    binding): a caller supplying explicit in-memory evidence via sweep_path/
    sweep must never be failed by an on-disk sweep lookup it does not need
    (the spurious DO-NOT-SEND on scoped agency reports, review finding #2)."""
    from agents import review

    if review_dir is None or os.path.abspath(review_dir) == os.path.abspath(review.REVIEW_DIR):
        designator = review.gate_designator(client_name)
        if not need_path:
            return designator or "all", None
        return designator or "all", review.sweep_artifact_path(client_name)

    packet_path = os.path.join(review_dir, f"{_slug(client_name)}.review.json")
    try:
        with open(packet_path, encoding="utf-8") as f:
            scope = (json.load(f) or {}).get("search_scope") or {}
    except FileNotFoundError:
        scope = {}
    names = scope.get("agencies") or []
    if names:
        from tools.agencies import find
        agencies = [a for a in (find(n) for n in names) if a]
        if len(agencies) != len(names):
            raise ValueError("current Assess scope contains an unresolved agency")
        designator = review.scope_designator(
            {"agencies": [a["abbr"] for a in agencies]})
    else:
        designator = None
    if not need_path:
        return designator or "all", None
    cleaned = os.path.join(os.path.dirname(review_dir), "cleaned")
    basename = (f"searches_{_slug(client_name)}.{designator}.json"
                if designator else f"searches_{_slug(client_name)}.json")
    path = os.path.join(cleaned, basename)
    if designator and not os.path.exists(path):
        raise FileNotFoundError(
            f"gate scope designates '{designator}' but {path} does not exist")
    return designator or "all", path


_BINDING_MEMO: dict = {}


def current_assess_binding(client_name: str, *, review_dir: Optional[str] = None,
                           sweep_path: Optional[str] = None,
                           sweep: Any = _UNSET,
                           profile: Any = _UNSET) -> dict:
    """Fingerprint the exact current scope, selected sweep, and full profile.

    Disk-loaded bindings memoize on (sweep stat, profile stat): the dashboard
    polls this for every approved client, and re-hashing an unchanged
    multi-MB sweep per poll was pure waste (review finding #10). Explicit
    in-memory evidence (sweep=/profile=) is never memoized."""
    scope_designator, selected_path = _scope_and_sweep(
        client_name, review_dir, need_path=sweep_path is None)
    path = sweep_path or selected_path

    memo_key = None
    if sweep is _UNSET and profile is _UNSET:
        try:
            st = os.stat(path)
            from tools.capability import profile_path
            pp = profile_path(client_name)
            pst = os.stat(pp)
            memo_key = (client_name, scope_designator, path,
                        st.st_mtime_ns, st.st_size,
                        pp, pst.st_mtime_ns, pst.st_size)
        except OSError:
            memo_key = None
        if memo_key is not None:
            hit = _BINDING_MEMO.get(memo_key)
            if hit is not None:
                return dict(hit)

    active_sweep = sweep
    if active_sweep is _UNSET:
        with open(path, encoding="utf-8") as f:
            active_sweep = json.load(f)
    if not isinstance(active_sweep, dict):
        raise ValueError("current Assess sweep root is not an object")

    active_profile = profile
    if active_profile is _UNSET:
        from tools.capability import load_profile
        checked = load_profile(client_name)
        if checked is None or not checked.is_populated():
            raise ValueError("current capability profile is not populated")
        active_profile = checked
    active_profile = _jsonable(active_profile)
    if not isinstance(active_profile, dict):
        raise ValueError("current capability profile is missing or invalid")

    binding = build_binding(scope_designator=scope_designator,
                            sweep_artifact=os.path.basename(path),
                            sweep=active_sweep, profile=active_profile)
    if memo_key is not None:
        _BINDING_MEMO[memo_key] = dict(binding)
        if len(_BINDING_MEMO) > 64:  # a handful of clients; never grows unbounded
            _BINDING_MEMO.pop(next(iter(_BINDING_MEMO)))
    return binding


def load_assess_approval(client_name: str, *, review_dir: Optional[str] = None
                         ) -> Optional[dict]:
    path = approval_path(client_name, review_dir=review_dir)
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def assess_approval_problems(payload: dict, client_name: str, *,
                             current_binding: Optional[dict] = None,
                             review_dir: Optional[str] = None) -> list[str]:
    if not isinstance(payload, dict):
        return ["Assess approval artifact is unreadable"]
    if payload.get("client") != client_name:
        return ["Assess approval belongs to a different client"]
    if payload.get("revoked_at"):
        return ["Assess approval was revoked; review and approve again"]
    if not payload.get("approved_at"):
        return ["Assess approval has no approval timestamp"]
    stored = payload.get("binding")
    if current_binding is None and isinstance(stored, dict):
        try:
            current_binding = current_assess_binding(
                client_name, review_dir=review_dir)
        except Exception as exc:  # noqa: BLE001 - a release gate fails closed
            return [f"current Assess approval binding unavailable: {exc}"]
    return binding_drift(stored, current_binding or {},
                         subject="Assess approval",
                         remedy="review and approve the current results again")


def _utc_today():
    """One patchable UTC cutoff for strict date-only response deadlines."""
    from agents.assess.live_report import utc_today
    return utc_today()


def required_blocker_manifest(coverage) -> dict:
    """Canonical required-source gaps an operator may explicitly accept."""
    rows = sorted((
        row.model_dump(mode="json") for row in coverage
        if row.required and row.status.value != "complete"
    ), key=lambda row: (row["lane"], row["source"]))
    return {"rows": rows, "sha256": _digest(rows)}


def strict_assess_run_for_release(
    client_name: str,
    *,
    current_binding: Optional[dict] = None,
    review_dir: Optional[str] = None,
) -> tuple[Optional[bool], list[str]]:
    """Return the additive strict-run release leg for backfilled clients.

    ``None`` means the operator-selected client/scope has no current pointer,
    preserving the established approval path exactly. Once a pointer exists,
    only the immutable run bound to the same sweep/profile and current mutable
    projection inputs may authorize release. Pointer removal is the explicit
    reversible rollback; an invalid or stale pointer fails closed.
    """
    from agents.assess.contracts import AssessRun
    from agents.assess.ledger import (
        assess_projection_input_manifest,
        current_assess_pointer_path,
        load_current_assess_run,
    )

    if current_binding is None:
        try:
            current_binding = current_assess_binding(
                client_name, review_dir=review_dir)
        except Exception as exc:  # noqa: BLE001 - present pointers fail closed below
            scope_designator, _ = _scope_and_sweep(
                client_name, review_dir, need_path=False)
            pointer = current_assess_pointer_path(
                client_name, scope_designator)
            if not pointer.exists():
                return None, []
            return False, [f"strict Assess binding is unavailable: {exc}"]
    scope_designator = str(current_binding.get("scope_designator") or "")
    try:
        pointer = current_assess_pointer_path(
            client_name, scope_designator)
    except Exception as exc:  # noqa: BLE001 - release checks fail closed
        return False, [f"strict Assess pointer is invalid: {exc}"]
    if not pointer.exists():
        return None, []
    payload = load_current_assess_run(client_name, scope_designator)
    if payload is None:
        return False, ["current strict Assess pointer or immutable run is invalid"]
    stored_binding = payload.get("binding")
    drift = binding_drift(
        stored_binding,
        current_binding,
        subject="strict Assess run",
        remedy="refresh the strict run before release",
    )
    if drift:
        return False, drift
    expected_inputs = assess_projection_input_manifest(
        client_name, review_dir=review_dir)
    if payload.get("projection_inputs") != expected_inputs:
        return False, [
            "strict Assess review or reference inputs changed; refresh the "
            "strict run before release"]
    try:
        run = AssessRun.model_validate(payload.get("run") or {})
    except Exception as exc:  # noqa: BLE001 - malformed gate fails closed
        return False, [f"current strict Assess run is invalid: {exc}"]
    expired = [
        record.notice_id for record in run.live.records
        if (record.classification.value == "bid_now"
            and record.recommendation.value == "pursue"
            and (record.response_deadline is None
                 or record.response_deadline <= _utc_today()))
    ]
    if expired:
        return False, [
            "current strict Assess run has BID_NOW deadline(s) no longer "
            "future: " + ", ".join(expired[:8])]
    if run.can_release():
        return True, []
    blockers = [f"{row.lane.value}:{row.source}"
                for row in run.blocking_sources()]
    if blockers and not run.partial_release_approved:
        return False, [
            "current strict Assess run has required-source coverage gaps: "
            + ", ".join(blockers)]
    return False, [
        "current strict Assess run has not cleared its human approval gate"]


def _assess_approval_for_materialization(
    client_name: str,
    *,
    current_binding: Optional[dict] = None,
    review_dir: Optional[str] = None,
) -> tuple[Optional[dict], str, list[str]]:
    """Validate the human approval while rebuilding its strict run.

    This is deliberately private and is not a release decision. Requiring the
    prior current run to pass before materializing its replacement would make
    stale or pending pointers unrecoverable.
    """
    path = approval_path(client_name, review_dir=review_dir)
    payload = load_assess_approval(client_name, review_dir=review_dir)
    if payload is None:
        if os.path.exists(path):
            return None, "invalid", ["Assess approval artifact is unreadable"]
        return None, "missing", ["Assess results have not been approved for release"]
    problems = assess_approval_problems(
        payload, client_name, current_binding=current_binding,
        review_dir=review_dir)
    if problems:
        return None, "invalid", problems
    return payload, "approved", []


def assess_approval_for_release(client_name: str, *,
                                current_binding: Optional[dict] = None,
                                review_dir: Optional[str] = None,
                                ) -> tuple[Optional[dict], str, list[str]]:
    """Return ``(approval, effective_status, problems)`` for release."""
    payload, status, problems = _assess_approval_for_materialization(
        client_name,
        current_binding=current_binding,
        review_dir=review_dir,
    )
    if status != "approved":
        return payload, status, problems
    strict_state, strict_problems = strict_assess_run_for_release(
        client_name,
        current_binding=current_binding,
        review_dir=review_dir,
    )
    if strict_state is False:
        return None, "invalid", strict_problems
    return payload, "approved", []


class AssessApprovalError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


_MANIFEST_VERSION = 1
_NOTICE_FIELDS = ("title", "agency", "response_deadline", "naics_code")
_TRIAGE_REASON_FIELDS = ("reason", "rationale", "reasoning")


def _flatten(value: Any, prefix: str = "") -> dict:
    """Flatten a small JSON object into dotted paths for compact comparison."""
    out = {}
    if isinstance(value, dict):
        for key, item in sorted(value.items()):
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(item, child))
    elif isinstance(value, list):
        out[prefix] = _digest(value)[:12]
    else:
        out[prefix] = value
    return out


def evidence_manifest(sweep: dict, profile: Any,
                      scope_designator: str) -> dict:
    """Snapshot the approved evidence in a compact, operator-readable form.

    The release binding remains the staleness authority. This additive
    manifest only explains *what* changed so reapproval is a focused review.
    Notice hashes include the complete notice and its triage record; therefore
    a verdict-only or rationale-only edit cannot be miscounted as unchanged.
    """
    results = sweep.get("results")
    if results is None:
        results = {}
    if not isinstance(results, dict):
        raise ValueError("Assess sweep results are not an object")
    triage = results.get("triage")
    if triage is not None and not isinstance(triage, dict):
        raise ValueError("Assess sweep triage is not an object")
    triage = triage or {}
    rows = results.get("sam.gov")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("Assess sweep sam.gov results are not a list")
    notices = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Assess sweep contains a malformed SAM notice")
        source_id = row.get("source_id")
        if not isinstance(source_id, (str, int)) or not str(source_id).strip():
            raise ValueError("Assess sweep contains a SAM notice without an ID")
        notice_id = str(source_id)
        if notice_id in notices:
            raise ValueError(
                f"Assess sweep contains duplicate SAM notice ID {notice_id}")
        triage_row = triage.get(source_id)
        if triage_row is None:
            triage_row = triage.get(notice_id)
        if triage_row is None:
            triage_row = {}
        if not isinstance(triage_row, dict):
            raise ValueError(
                f"Assess sweep triage for {notice_id} is not an object")
        entry = {
            field: row.get(field)
            for field in _NOTICE_FIELDS
            if row.get(field) is not None
        }
        verdict = triage_row.get("verdict")
        if verdict:
            entry["verdict"] = verdict
        triage_reason = next((
            triage_row.get(field)
            for field in _TRIAGE_REASON_FIELDS
            if triage_row.get(field) not in (None, "")
        ), None)
        if triage_reason is not None:
            entry["triage_reason"] = triage_reason
        entry["h"] = _digest({"notice": row, "triage": triage_row})[:12]
        notices[notice_id] = entry
    return {
        "version": _MANIFEST_VERSION,
        "scope_designator": scope_designator,
        "notices": notices,
        "profile": _flatten(_jsonable(profile) or {}),
    }


def approval_evidence_diff(client_name: str, *,
                           review_dir: Optional[str] = None) -> dict:
    """Explain drift between approved and current Assess evidence.

    This is a best-effort UI surface, never a gate. Any failure returns an
    unavailable explanation while the exact binding comparison continues to
    determine whether the prior approval is eligible for release.
    """
    payload = load_assess_approval(client_name, review_dir=review_dir)
    if payload is None:
        return {"available": False, "why": "no Assess approval on file"}
    if payload.get("client") != client_name:
        return {
            "available": False,
            "why": "Assess approval belongs to a different client",
        }
    stored = payload.get("evidence_manifest")
    if not isinstance(stored, dict) or not isinstance(
            stored.get("notices"), dict):
        return {
            "available": False,
            "why": (
                "approval predates evidence manifests; approving the current "
                "results enables change diffs"
            ),
        }
    if stored.get("version") != _MANIFEST_VERSION:
        return {
            "available": False,
            "why": "approval uses an unsupported evidence manifest version",
        }
    if any(not isinstance(entry, dict)
           for entry in stored["notices"].values()):
        return {
            "available": False,
            "why": "approval evidence manifest contains malformed notices",
        }
    if not isinstance(stored.get("profile"), dict):
        return {
            "available": False,
            "why": "approval evidence manifest contains a malformed profile",
        }
    try:
        scope_designator, path = _scope_and_sweep(client_name, review_dir)
        with open(path, encoding="utf-8") as handle:
            sweep = json.load(handle)
        from tools.capability import load_profile
        profile = load_profile(client_name)
        if profile is None or not profile.is_populated():
            raise ValueError("current capability profile is not populated")
        current = evidence_manifest(sweep, profile, scope_designator)
    except Exception as exc:  # noqa: BLE001 - diff is best-effort UX
        return {
            "available": False,
            "why": f"current evidence unavailable: {exc}",
        }

    old_notices = stored["notices"]
    new_notices = current["notices"]

    def _brief(source: dict, notice_id: str) -> dict:
        entry = source.get(notice_id) or {}
        return {
            "id": notice_id,
            "title": entry.get("title"),
            "agency": entry.get("agency"),
            "deadline": entry.get("response_deadline"),
            "naics_code": entry.get("naics_code"),
            "verdict": entry.get("verdict"),
            "triage_reason": entry.get("triage_reason"),
        }

    entered = [
        _brief(new_notices, notice_id)
        for notice_id in sorted(new_notices.keys() - old_notices.keys())
    ]
    left = [
        _brief(old_notices, notice_id)
        for notice_id in sorted(old_notices.keys() - new_notices.keys())
    ]
    changed = []
    for notice_id in sorted(old_notices.keys() & new_notices.keys()):
        old_entry = old_notices[notice_id]
        new_entry = new_notices[notice_id]
        if old_entry.get("h") == new_entry.get("h"):
            continue
        fields = sorted(
            field for field in (*_NOTICE_FIELDS, "verdict", "triage_reason")
            if old_entry.get(field) != new_entry.get(field)
        ) or ["content"]
        changed.append({
            **_brief(new_notices, notice_id),
            "fields": fields,
            "was": {
                field: old_entry.get(field)
                for field in fields
                if field != "content"
            },
        })

    old_profile = stored.get("profile") or {}
    new_profile = current.get("profile") or {}
    profile_changed = sorted(
        path for path in old_profile.keys() | new_profile.keys()
        if old_profile.get(path) != new_profile.get(path)
    )
    scope = {
        "from": stored.get("scope_designator"),
        "to": current.get("scope_designator"),
    }
    summary = []
    if entered:
        summary.append(f"{len(entered)} notice(s) entered")
    if left:
        summary.append(f"{len(left)} notice(s) left")
    if changed:
        summary.append(f"{len(changed)} notice(s) changed")
    if profile_changed:
        summary.append(f"{len(profile_changed)} profile field(s) changed")
    if scope["from"] != scope["to"]:
        summary.append(f"scope moved {scope['from']} -> {scope['to']}")
    return {
        "available": True,
        "summary": "; ".join(summary) or "no evidence changes detected",
        "entered": entered,
        "left": left,
        "changed": changed,
        "profile_changed": profile_changed,
        "scope": scope,
        "unchanged_count": sum(
            1
            for notice_id in old_notices.keys() & new_notices.keys()
            if old_notices[notice_id].get("h")
            == new_notices[notice_id].get("h")
        ),
    }


def _substantively_same_approval(prior: Any, candidate: dict) -> bool:
    """Ignore only a regenerated timestamp when every approval fact matches."""
    if not isinstance(prior, dict) or prior.get("revoked_at"):
        return False
    raw_timestamp = prior.get("approved_at")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        return False
    try:
        parsed = datetime.fromisoformat(
            raw_timestamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    comparable = dict(candidate)
    comparable["approved_at"] = raw_timestamp
    return comparable == prior


def approve_assess_results(
    client_name: str,
    *,
    note: str = "",
    current_binding: Optional[dict] = None,
    review_dir: Optional[str] = None,
    partial_release_approved: Optional[bool] = False,
    assess_state_dir: Optional[str] = None,
    partial_release_run_id: Optional[str] = None,
    partial_release_blockers_sha256: Optional[str] = None,
) -> dict:
    """Record the human sign-off against the exact current Assess inputs."""
    if partial_release_approved is not None \
            and not isinstance(partial_release_approved, bool):
        raise AssessApprovalError([
            "partial_release_approved must be a boolean operator decision"])
    prior = load_assess_approval(client_name, review_dir=review_dir)
    carried_partial = None
    if partial_release_approved is None:
        # Cycle 3 review finding (2026-07-12): a plain re-approval must not
        # silently revoke a standing partial-release grant. None means the
        # caller did not touch that axis: an unrevoked prior grant carries
        # forward verbatim. Downstream run assembly still compares the exact
        # blocker rows, digest, and run id against the CURRENT strict run, so
        # a drifted grant stays inert, and an explicitly revoked approval
        # stays dead. Explicit False keeps its revoke-the-grant meaning.
        if (isinstance(prior, dict) and not prior.get("revoked_at")
                and prior.get("partial_release_approved") is True):
            carried_partial = {
                "partial_release_approved": True,
                "partial_release_run_id": prior.get("partial_release_run_id"),
                "partial_release_blockers": prior.get(
                    "partial_release_blockers"),
                "partial_release_blockers_sha256": prior.get(
                    "partial_release_blockers_sha256"),
            }
        partial_release_approved = False
    if partial_release_approved:
        if not isinstance(partial_release_run_id, str) \
                or not partial_release_run_id.strip():
            raise AssessApprovalError([
                "partial release requires the displayed strict run id; refresh "
                "the review before approving"])
        if not isinstance(partial_release_blockers_sha256, str) \
                or not partial_release_blockers_sha256.strip():
            raise AssessApprovalError([
                "partial release requires the displayed blocker manifest; "
                "refresh the review before approving"])
    manifest = None
    if current_binding is None:
        evidence = None
        try:
            scope_designator, path = _scope_and_sweep(client_name, review_dir)
            with open(path, encoding="utf-8") as handle:
                sweep = json.load(handle)
            if not isinstance(sweep, dict):
                raise ValueError("current Assess sweep root is not an object")
            from tools.capability import load_profile
            profile = load_profile(client_name)
            if profile is None or not profile.is_populated():
                raise ValueError("current capability profile is not populated")
            evidence = (scope_designator, path, sweep, profile)
        except Exception:  # noqa: BLE001 - manifest degrades; binding decides
            evidence = None
        try:
            if evidence is None:
                current_binding = current_assess_binding(
                    client_name, review_dir=review_dir)
            else:
                scope_designator, path, sweep, profile = evidence
                current_binding = build_binding(
                    scope_designator=scope_designator,
                    sweep_artifact=os.path.basename(path),
                    sweep=sweep,
                    profile=profile,
                )
                try:
                    manifest = evidence_manifest(
                        sweep, profile, scope_designator)
                except Exception:  # noqa: BLE001 - optional explanation only
                    manifest = None
        except Exception as exc:  # noqa: BLE001 - present a stable gate error
            raise AssessApprovalError(
                [f"cannot approve current Assess results: {exc}"]) from exc
    payload = {
        "client": client_name,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
        "binding": current_binding,
        "partial_release_approved": False,
    }
    if partial_release_approved:
        from agents.assess.contracts import AssessRun
        from agents.assess.ledger import load_current_assess_run
        designator = str(current_binding.get("scope_designator") or "")
        current = load_current_assess_run(
            client_name, designator, state_dir=assess_state_dir)
        if current is None or current.get("binding") != current_binding:
            raise AssessApprovalError([
                "cannot approve partial release without a current strict run "
                "bound to this scope, sweep, and capability profile"])
        expected_inputs = None
        try:
            from agents.assess.ledger import assess_projection_input_manifest
            expected_inputs = assess_projection_input_manifest(
                client_name, review_dir=review_dir)
        except Exception:  # noqa: BLE001 - mismatch below remains fail-closed
            pass
        if current.get("projection_inputs") != expected_inputs:
            raise AssessApprovalError([
                "cannot approve partial release because the strict run's "
                "review or reference inputs changed; refresh it first"])
        try:
            run = AssessRun.model_validate(current.get("run") or {})
        except Exception as exc:  # noqa: BLE001 - operator gate fails closed
            raise AssessApprovalError([
                f"cannot approve partial release for an invalid strict run: {exc}"]
            ) from exc
        if run.run_id != partial_release_run_id:
            raise AssessApprovalError([
                "the strict Assess run changed after it was displayed; refresh "
                "the review before approving partial release"])
        blocker_manifest = required_blocker_manifest(run.coverage)
        if blocker_manifest["sha256"] != partial_release_blockers_sha256:
            raise AssessApprovalError([
                "the required source-coverage gaps changed after they were "
                "displayed; refresh the review before approving partial release"])
        if not blocker_manifest["rows"]:
            raise AssessApprovalError([
                "partial release authorization requires at least one current "
                "required source-coverage gap"])
        payload.update({
            "partial_release_approved": True,
            "partial_release_run_id": run.run_id,
            "partial_release_blockers": blocker_manifest["rows"],
            "partial_release_blockers_sha256": blocker_manifest["sha256"],
        })
    if carried_partial is not None:
        payload.update(carried_partial)
    if manifest is not None:
        payload["evidence_manifest"] = manifest
    elif (isinstance(prior, dict) and not prior.get("revoked_at")
          and prior.get("binding") == current_binding
          and "evidence_manifest" in prior
          and {key: value for key, value in prior.items()
               if key not in {"approved_at", "evidence_manifest"}}
          == {key: value for key, value in payload.items()
              if key not in {"approved_at", "evidence_manifest"}}):
        # The manifest explains the binding but does not authorize it. A
        # transient failure to regenerate that optional explanation must not
        # turn an otherwise identical click into new approval/run identity.
        payload["evidence_manifest"] = prior["evidence_manifest"]
    if _substantively_same_approval(prior, payload):
        return prior
    _atomic_write_json(
        approval_path(client_name, review_dir=review_dir), payload)
    return payload


def revoke_assess_results(client_name: str, *, review_dir: Optional[str] = None
                          ) -> bool:
    path = approval_path(client_name, review_dir=review_dir)
    payload = load_assess_approval(client_name, review_dir=review_dir)
    if payload is None:
        return False
    payload["revoked_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    _atomic_write_json(path, payload)
    return True
