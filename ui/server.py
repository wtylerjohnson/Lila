"""LILA control room — local web dashboard.

Serves the guided pipeline at http://127.0.0.1:8321. Localhost-only by design:
this app can approve strategies and launch API-spending runs.

The UI's mental model is one PIPELINE per client with six steps. The server's job
is to translate raw artifacts into that model: each step reports its state
(done / ready / blocked / waiting), a human summary ("23 notices, 9 verified"),
and the one action that moves it forward. The dashboard renders; it never decides.

Everything shown derives from the same artifacts the CLI writes — the dashboard
is a window onto the pipeline, not a second source of truth.
"""

from __future__ import annotations

import base64
import glob
import re
import hashlib
import hmac
import html as html_lib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Flask, jsonify, redirect, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _scrub_legacy_openai_environment() -> None:
    """Prevent ambient legacy arbiter settings from entering this server.

    Candidate Review credentials are explicit, session-only vault state.  A
    shell or old ``.env`` value must neither activate the retired release
    arbiter nor survive to a child process.  This changes only this Control
    Room process; generic CLI execution outside the server is untouched.
    """

    for name in (
        "OPENAI_API_KEY",
        "LILA_ENABLE_ARBITER_OPENAI",
        "OPENAI_ARBITER_MODEL",
    ):
        os.environ.pop(name, None)


_scrub_legacy_openai_environment()

from agents.review import (  # noqa: E402
    ReviewPacket, ReviewStatus, WorkstationBindingError, canonical_client_name,
    decide, load_packet, load_packet_snapshot,
)
from agents.candidate_review_v1.collaboration_session import (  # noqa: E402
    CollaborationSessionInputError,
    clear_openai_collaboration,
    configure_openai_collaboration,
    openai_collaboration_status,
)
from agents.workstations import (  # noqa: E402
    WorkstationError, canonical_scope, create_workstation,
    creation_receipt_path, discover_workstations, native_packet_path,
    native_workstation_ownership, registry_path, sweep_freshness_problem,
    sweep_scope_binding,
)
from tools.toggles import is_enabled  # noqa: E402

REVIEW_DIR = os.path.join(ROOT, "data", "review")
CLEANED_DIR = os.path.join(ROOT, "data", "cleaned")
REPORT_DIR = os.path.join(ROOT, "data", "reports")
HANDOFF_DIR = os.path.join(ROOT, "data", "handoff")
INTAKE_DIR = os.path.join(ROOT, "data", "intake")
CLIENTS_DIR = os.path.join(ROOT, "clients")
# One store, one resolution rule: the gate legs (agents/assess/ledger.py
# _state_dir) honor LILA_ASSESS_RUN_DIR, so the dashboard must too or an
# operator env override desyncs the two (Cycle 3 review finding, 2026-07-12).
ASSESS_RUN_DIR = os.environ.get(
    "LILA_ASSESS_RUN_DIR", os.path.join(ROOT, "data", "state", "assess_runs"))
CHANGE_DIGEST_DIR = os.path.join(ROOT, "data", "state", "change_digests")

app = Flask(__name__, static_folder=None)
_SIGNAL_BOARD_EDITOR_TOKEN = uuid.uuid4().hex
_MAX_LOGO_UPLOAD_BODY = 2 * 1024 * 1024 + 256 * 1024
_MAX_LOGO_SIZE_BODY = 2 * 1024
_MAX_HEADER_COMPANION_BODY = 4 * 1024
_MAX_COLLABORATION_SESSION_BODY = 2 * 1024

import time as _time  # noqa: E402 — post-app import, matching the file's late-import style
BOOT_TIME = _time.time()


@app.get("/api/build")
def api_build():
    """Staleness detector: if any core file is newer than the boot, the UI shows
    RESTART NEEDED instead of silently serving yesterday's behavior."""
    newest = 0.0
    for rel in ("ui/server.py", "ui/index.html",
                "ui/signal_board_logo_editor.js", "run_searches.py",
                "run_capture_brief.py", "run_target.py"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            newest = max(newest, os.path.getmtime(p))
    return jsonify({"boot": BOOT_TIME, "code_mtime": newest,
                    "stale": newest > BOOT_TIME + 1})


# ----------------------------------------------------------------------------- #
# Small helpers
# ----------------------------------------------------------------------------- #
def _load_json(path: str) -> Optional[Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT)


def _slugify(name: str) -> str:
    """One slug per client identity, aligned with tools.capability._slug.

    Collapses every run of non-alphanumerics to ONE underscore. The
    per-character version diverged on names with consecutive separators
    ("JTG, inc." minted clients/jtg__inc while the sweep looked for
    clients/jtg_inc, measured 2026-08-19); no pre-existing client name
    carries a consecutive-separator run, so alignment changes no existing
    slug.
    """
    from tools.slug import client_slug
    return client_slug(name)


def _workstation_catalog_for_slug(slug: str):
    """Resolve a URL slug to one exact-client, read-only catalog.

    The route value is never treated as the client identity.  A real review
    packet must own that mapping, and every storage root is passed explicitly
    so tests and alternate local workspaces cannot cross-read one another.
    """
    packet_path = os.path.join(REVIEW_DIR, f"{slug}.review.json")
    if not os.path.isfile(packet_path):
        raise FileNotFoundError(packet_path)
    try:
        exact = canonical_client_name(slug, REVIEW_DIR)
    except Exception as exc:
        raise WorkstationError(
            f"review packet client identity is invalid: {exc}") from exc
    if _slugify(exact) != slug:
        raise WorkstationError("review packet client identity does not match URL slug")
    catalog = discover_workstations(
        exact, review_dir=REVIEW_DIR, cleaned_dir=CLEANED_DIR,
        report_dir=REPORT_DIR)
    return exact, catalog


def _workstation_route_error(slug: str):
    """Shared fail-closed route resolution with stable HTTP semantics."""
    if not slug or _slugify(slug) != slug:
        return None, (_no_store({"error": "invalid client slug"}, 400))
    try:
        return _workstation_catalog_for_slug(slug), None
    except FileNotFoundError:
        return None, _no_store({"error": "no such client"}, 404)
    except WorkstationError as exc:
        return None, _no_store({"error": str(exc)}, 409)


def _mutation_workstation_binding(client_name: str,
                                  posted_workstation_id: Any) -> tuple[
                                      str, bool, Optional[str], Optional[str]]:
    """Resolve one exact strategy owner for a mutating request."""
    if not isinstance(client_name, str) or not client_name.strip():
        raise ValueError("client_name is required")
    slug = _slugify(client_name)
    exact, catalog = _workstation_catalog_for_slug(slug)
    if exact != client_name:
        raise WorkstationError("client identity does not match review packet")
    native_rows = [row for row in catalog.workstations
                   if getattr(row, "is_native", False)]
    if posted_workstation_id is None:
        if native_rows:
            raise WorkstationError(
                "workstation_id is required once a native workstation exists")
        legacy_rows = [row for row in catalog.workstations
                       if row.is_legacy_current]
        if len(legacy_rows) != 1:
            raise WorkstationError(
                "mutation cannot resolve one legacy-current workstation")
        return legacy_rows[0].ref.id, False, None, None
    if (not isinstance(posted_workstation_id, str)
            or _slugify(posted_workstation_id) != posted_workstation_id
            or not (posted_workstation_id == "all"
                    or posted_workstation_id.startswith("agency_"))):
        raise ValueError("invalid workstation_id")
    selected = next((row for row in catalog.workstations
                     if row.ref.id == posted_workstation_id), None)
    if selected is None:
        raise WorkstationError("no such workstation")
    if not (selected.is_native or selected.is_legacy_current):
        raise WorkstationError("workstation has no mutable strategy packet")
    if selected.is_native:
        ownership = native_workstation_ownership(
            client_name, selected.ref.id, review_dir=REVIEW_DIR)
        if not ownership.clone_baseline or ownership.ref != selected.ref:
            raise WorkstationError("workstation creation ownership changed")
        return (
            selected.ref.id, True,
            ownership.registry_sha256, ownership.receipt_sha256,
        )
    return selected.ref.id, False, None, None


def _mutation_workstation_id(client_name: str,
                             posted_workstation_id: Any) -> str:
    """Compatibility wrapper for callers that need only the exact id."""
    return _mutation_workstation_binding(
        client_name, posted_workstation_id)[0]


def _expected_mutation_sha(body: dict, *, native: bool) -> Optional[str]:
    expected = body.get("expected_packet_sha256")
    if not native and expected is None:
        return None
    if (not isinstance(expected, str) or len(expected) != 64
            or any(char not in "0123456789abcdef" for char in expected)):
        if native:
            raise WorkstationError(
                "expected_packet_sha256 is required for native mutation")
        raise ValueError("expected_packet_sha256 is invalid")
    return expected


def _packet_model_sha256(packet) -> str:
    exact = packet.model_dump_json(indent=2).encode("utf-8")
    return hashlib.sha256(exact).hexdigest()


def _no_store(payload: Any, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _workstation_sweep_name(slug: str, workstation_id: str) -> str:
    return (f"searches_{slug}.json" if workstation_id == "all"
            else f"searches_{slug}.{workstation_id}.json")


def _bound_legacy_snapshot(slug: str, client_name: str, selected,
                           *, include_sweep: bool) -> tuple[dict, Optional[dict],
                                                            tuple[tuple[str, str], ...]]:
    """Read one exact legacy-current packet/sweep snapshot for a response.

    Catalog discovery proves what was true at its own read instant.  This
    second exact binding prevents a packet or sweep replacement between that
    discovery and payload assembly from borrowing another scope's data.  The
    returned hashes are checked again before the response is released.
    """
    tokens: list[tuple[str, str]] = []

    def read_json(path: str, noun: str) -> dict:
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
            payload = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkstationError(f"{noun} is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise WorkstationError(f"{noun} root is not an object")
        tokens.append((path, hashlib.sha256(raw).hexdigest()))
        return payload

    packet = read_json(
        os.path.join(REVIEW_DIR, f"{slug}.review.json"),
        "legacy review packet")
    if packet.get("client_name") != client_name:
        raise WorkstationError("legacy review packet client identity changed")
    try:
        packet_scope = canonical_scope(packet.get("search_scope"))
    except WorkstationError as exc:
        raise WorkstationError(f"legacy review packet scope is invalid: {exc}") from exc
    if packet_scope != selected.ref.scope:
        raise WorkstationError("legacy review packet scope changed during navigation")
    packet_status = packet.get("status", "pending")
    if packet_status not in {"pending", "approved", "rejected"}:
        raise WorkstationError("legacy review packet status is invalid")
    if packet_status != selected.boundary_status:
        raise WorkstationError("legacy review packet status changed during navigation")

    searches = None
    if include_sweep:
        searches = read_json(
            os.path.join(CLEANED_DIR, _workstation_sweep_name(
                slug, selected.ref.id)),
            "designated sweep")
        exact = searches.get("client") or searches.get("client_name")
        if exact != client_name:
            raise WorkstationError("designated sweep client identity changed")
        try:
            sweep_scope = sweep_scope_binding(
                searches,
                legacy_all_default=bool(
                    selected.ref.id == "all" and selected.ref.scope.all),
            )
        except WorkstationError as exc:
            raise WorkstationError(str(exc)) from exc
        if sweep_scope != selected.ref.scope:
            raise WorkstationError("designated sweep scope changed during navigation")
        freshness_problem = sweep_freshness_problem(packet, searches)
        if freshness_problem:
            raise WorkstationError(freshness_problem)
    return packet, searches, tuple(tokens)


def _bound_native_snapshot(slug: str, client_name: str, selected,
                           *, include_sweep: bool) -> tuple[
                               dict, Optional[dict],
                               tuple[tuple[str, str], ...], str]:
    """Read one exact native packet/sweep snapshot for a response."""
    tokens: list[tuple[str, str]] = []

    def read_json(path: str, noun: str) -> tuple[dict, str, bytes]:
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
            payload = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkstationError(f"{noun} is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise WorkstationError(f"{noun} root is not an object")
        digest = hashlib.sha256(raw).hexdigest()
        tokens.append((path, digest))
        return payload, digest, raw

    ownership = native_workstation_ownership(
        client_name, selected.ref.id, review_dir=REVIEW_DIR)
    if not ownership.clone_baseline or ownership.ref != selected.ref:
        raise WorkstationError("workstation creation ownership changed")
    tokens.extend([
        (str(registry_path(client_name, review_dir=REVIEW_DIR)),
         ownership.registry_sha256),
        (str(creation_receipt_path(
            client_name, selected.ref.id, review_dir=REVIEW_DIR)),
         ownership.receipt_sha256),
    ])
    packet_path = str(native_packet_path(
        client_name, selected.ref.id, review_dir=REVIEW_DIR))
    packet, packet_digest, packet_bytes = read_json(
        packet_path, "native review packet")
    try:
        validated_packet = ReviewPacket.model_validate_json(packet_bytes)
    except Exception as exc:
        raise WorkstationError(
            f"native review packet is invalid: {exc}") from exc
    raw_strategy = packet.get("strategy")
    revision = packet.get("revision_count")
    if (packet.get("client_name") != client_name
            or validated_packet.client_name != client_name):
        raise WorkstationError("native review packet client identity changed")
    if (not isinstance(raw_strategy, dict)
            or raw_strategy.get("client_name") != client_name
            or validated_packet.strategy.client_name != client_name):
        raise WorkstationError("native review packet strategy identity changed")
    if "search_scope" not in packet:
        raise WorkstationError("native review packet lost its scope binding")
    try:
        packet_scope = canonical_scope(packet.get("search_scope"))
    except WorkstationError as exc:
        raise WorkstationError(
            f"native review packet scope is invalid: {exc}") from exc
    if (packet_scope != selected.ref.scope
            or packet["search_scope"] != selected.ref.scope.as_dict()):
        raise WorkstationError("native review packet scope changed during navigation")
    if (packet.get("status") not in {"pending", "approved", "rejected"}
            or validated_packet.status.value != selected.boundary_status
            or packet.get("status") != selected.boundary_status):
        raise WorkstationError("native review packet status changed during navigation")
    if (not isinstance(revision, int) or isinstance(revision, bool)
            or revision < 0
            or validated_packet.revision_count != revision):
        raise WorkstationError("native review packet revision is invalid")

    searches = None
    if include_sweep:
        searches, _, _ = read_json(
            os.path.join(CLEANED_DIR, _workstation_sweep_name(
                slug, selected.ref.id)),
            "designated sweep")
        exact = searches.get("client") or searches.get("client_name")
        if exact != client_name:
            raise WorkstationError("designated sweep client identity changed")
        try:
            sweep_scope = sweep_scope_binding(searches)
        except WorkstationError as exc:
            raise WorkstationError(str(exc)) from exc
        if sweep_scope != selected.ref.scope:
            raise WorkstationError("designated sweep scope changed during navigation")
        freshness_problem = sweep_freshness_problem(
            packet, searches, strategy_packet_sha256=packet_digest)
        if freshness_problem:
            raise WorkstationError(freshness_problem)
    return packet, searches, tuple(tokens), packet_digest


def _snapshot_is_unchanged(tokens: tuple[tuple[str, str], ...]) -> bool:
    for path, expected in tokens:
        try:
            with open(path, "rb") as handle:
                actual = hashlib.sha256(handle.read()).hexdigest()
        except OSError:
            return False
        if actual != expected:
            return False
    return True


def _bound_aux_workstation(slug: str):
    """Bind a scope-owned auxiliary GET to the exact current workstation.

    No query parameter preserves the legacy endpoint for callers outside the
    workstation SPA.  Once a workstation_id is supplied, dormant identities,
    phase drift, and packet/sweep replacement all fail closed.
    """
    workstation_id = request.args.get("workstation_id")
    if workstation_id is None:
        return None, None
    if (not workstation_id or _slugify(workstation_id) != workstation_id
            or not (workstation_id == "all"
                    or workstation_id.startswith("agency_"))):
        return None, _no_store({"error": "invalid workstation id"}, 400)
    resolved, error = _workstation_route_error(slug)
    if error:
        return None, error
    _, catalog = resolved
    selected = next((row for row in catalog.workstations
                     if row.ref.id == workstation_id), None)
    if selected is None:
        return None, _no_store({"error": "no such workstation"}, 404)
    if (not selected.is_legacy_current
            or selected.phase not in {"assess", "produce", "target"}
            or selected.sweep_status != "current"):
        return None, _no_store({
            "error": "auxiliary data is unavailable outside the exact current "
                     "post-search workstation",
            "workstation_id": workstation_id,
        }, 409)
    try:
        packet, searches, tokens = _bound_legacy_snapshot(
            slug, catalog.client_name, selected, include_sweep=True)
    except WorkstationError as exc:
        return None, _no_store({
            "error": str(exc), "workstation_id": workstation_id,
        }, 409)
    return {
        "client_name": catalog.client_name,
        "workstation_id": workstation_id,
        "selected": selected,
        "packet": packet,
        "searches": searches,
        "tokens": tokens,
    }, None


def _aux_snapshot_changed(bound: Optional[dict]):
    if bound is not None and not _snapshot_is_unchanged(bound["tokens"]):
        return _no_store({
            "error": "workstation binding changed during auxiliary read; reload",
            "workstation_id": bound["workstation_id"],
        }, 409)
    return None


def _utc_today():
    """One patchable cutoff for date-only federal response deadlines."""
    from agents.assess.live_report import utc_today
    return utc_today()


def _assess_release_gate(client_name: str) -> tuple[Optional[dict], str, list[str]]:
    """Current operational Assess approval, resolved against this UI's roots."""
    from agents.assess.approval import assess_approval_for_release
    return assess_approval_for_release(client_name, review_dir=REVIEW_DIR)


def _assess_release_gate_for_slug(slug: str
                                  ) -> tuple[Optional[dict], str, list[str]]:
    packet = _load_json(os.path.join(REVIEW_DIR, f"{slug}.review.json")) or {}
    client_name = packet.get("client_name")
    if not client_name:
        return None, "invalid", ["Assess client review packet is unavailable"]
    return _assess_release_gate(client_name)


def _assess_ledger_snapshot(client_name: str, *, include_run: bool = False) -> dict:
    """Validated current ledger and a compact operator-facing summary."""
    try:
        from agents.assess.approval import (
            current_assess_binding, required_blocker_manifest,
        )
        from agents.assess.contracts import AssessRun, CoverageStatus
        from agents.assess.ledger import (
            assess_projection_input_manifest, load_current_assess_run,
        )

        slug = _slugify(client_name)
        designator = _gate_designator(slug, REVIEW_DIR) or "all"
        payload = load_current_assess_run(
            client_name, designator, state_dir=ASSESS_RUN_DIR)
        if not payload:
            return {
                "exists": False,
                "state": "missing",
                "diagnostics": [
                    "The strict evidence ledger has not been built for the "
                    "current search. Assessment generation remains available."],
            }
        current_binding = current_assess_binding(
            client_name, review_dir=REVIEW_DIR)
        if payload.get("binding") != current_binding:
            return {
                "exists": False, "state": "stale", "stale": True,
                "diagnostics": [
                    "The stored ledger does not match the current scope, sweep, "
                    "or capability profile. It is not shown as current; refresh "
                    "is required. Assessment generation remains available."],
            }
        current_projection_inputs = assess_projection_input_manifest(
            client_name, review_dir=REVIEW_DIR)
        if payload.get("projection_inputs") != current_projection_inputs:
            return {
                "exists": False, "state": "stale", "stale": True,
                "diagnostics": [
                    "The stored ledger predates the current Horizon, qualification, "
                    "approval, or partnering inputs. It is not shown as current; "
                    "refresh is required. Assessment generation remains available."],
            }
        run = AssessRun.model_validate(payload.get("run") or {})
        expired_actionable = [
            record for record in run.live.records
            if record.classification.value == "bid_now"
            and (record.response_deadline is None
                 or record.response_deadline <= _utc_today())
        ]
        if expired_actionable:
            notices = ", ".join(
                record.notice_id for record in expired_actionable[:5])
            return {
                "exists": False, "state": "stale", "stale": True,
                "diagnostics": [
                    "The stored ledger still marks an opportunity actionable "
                    f"at or after its response deadline ({notices}). Refresh "
                    "the search before review. Assessment generation remains "
                    "available."],
            }
        classifications: dict[str, int] = {}
        for record in run.live.records:
            key = record.classification.value
            classifications[key] = classifications.get(key, 0) + 1
        requirement_candidate_records = [
            record for record in run.live.records
            if record.requirement_excerpt
            and record.classification.value in {"unscreened", "bid_now"}
        ]
        requirement_review_candidates = len(requirement_candidate_records)
        requirement_reviews_approved = sum(
            1 for record in requirement_candidate_records
            if record.requirement_reviewed_at is not None)
        coverage_issues = [
            row for row in run.coverage
            if row.status != CoverageStatus.COMPLETE
        ]
        blocker_manifest = required_blocker_manifest(run.coverage)
        summary = {
            "exists": True,
            "state": "current",
            "run_id": run.run_id,
            "as_of": run.as_of.isoformat(),
            "scope": run.scope.model_dump(mode="json"),
            "approval_status": run.approval_status.value,
            "partial_release_approved": run.partial_release_approved,
            "live_records": len(run.live.records),
            "live_actionable": classifications.get("bid_now", 0),
            "live_classifications": classifications,
            "requirement_review_candidates": requirement_review_candidates,
            "requirement_reviews_approved": requirement_reviews_approved,
            "horizon_theses": len(run.horizon.items),
            "horizon_approved": sum(
                1 for item in run.horizon.items if item.status.value == "approved"),
            "horizon_proposed": sum(
                1 for item in run.horizon.items if item.status.value != "approved"),
            "partner_paths": len(run.partners.items),
            "coverage_gaps": len(run.blocking_sources()),
            "coverage_warnings": len(coverage_issues),
            "blocking_sources": blocker_manifest["rows"],
            "blocking_sources_sha256": blocker_manifest["sha256"],
            "coverage_issues": [row.model_dump(mode="json")
                                for row in coverage_issues],
            "diagnostics": payload.get("diagnostics") or [],
            "can_release": run.can_release(),
            "binding": payload.get("binding") or {},
        }
        if include_run:
            summary["run"] = run.model_dump(mode="json")
        return summary
    except Exception as exc:  # noqa: BLE001 - additive ledger never 500s dashboard
        return {
            "exists": False, "state": "unavailable", "unavailable": True,
            "diagnostics": [
                f"Strict ledger is temporarily unavailable: {type(exc).__name__}. "
                "Assessment generation remains available."],
        }


def _refresh_assess_ledger(client_name: str) -> str:
    """Best effort after a human gate action; return its operator-facing note."""
    try:
        # Creation-free (2026-07-12): a gate click refreshes an EXISTING
        # strict run so the approval binds into the current envelope; it can
        # never mint the cutover pointer itself. Outcomes log to the server
        # console so a failed refresh (stale pointer, lanes held closed) is
        # visible instead of silently swallowed.
        from tools.assess_refresh import refresh_current_assess_run_if_active
        slug = _slugify(client_name)
        selected = os.path.join(CLEANED_DIR, _sweep_name(slug, REVIEW_DIR))
        outcome = refresh_current_assess_run_if_active(
            client_name, sweep_path=selected, state_dir=ASSESS_RUN_DIR,
            review_dir=REVIEW_DIR)
        if not outcome.startswith(("refreshed", "absent")):
            print(f"[assess-ledger] {client_name}: {outcome}",
                  file=sys.stderr)
        return outcome
    except Exception as exc:  # noqa: BLE001 - durable gate action still wins
        outcome = (
            f"FAILED ({type(exc).__name__}: {str(exc)[:160]}); the human gate "
            "was saved, but the live lane holds closed until a successful "
            "strict-run refresh")
        print(f"[assess-ledger] {client_name}: {outcome}", file=sys.stderr)
        return outcome


# ----------------------------------------------------------------------------- #
# Legacy stage view (kept: tests + attention queue build on it)
# ----------------------------------------------------------------------------- #
STAGES = ["intake", "approved", "searched", "qualified", "contacts", "reports"]


def _exists(*parts: str) -> bool:
    return os.path.exists(os.path.join(*parts))


def _gate_designator(slug: str, review_dir: Optional[str] = None) -> Optional[str]:
    """L19 designator via agents.review (the ONE owner of gate naming); the
    dashboard passes its own REVIEW_DIR so tests keep monkeypatching it."""
    from agents.review import gate_designator
    try:
        return gate_designator(slug, review_dir or REVIEW_DIR)
    except Exception:  # noqa: BLE001 — unreadable packet: treat as all-agencies
        return None


def _sweep_name(slug: str, review_dir: Optional[str] = None,
                *, workstation_id: Optional[str] = None) -> str:
    if workstation_id is not None:
        return _workstation_sweep_name(slug, workstation_id)
    d = _gate_designator(slug, review_dir)
    return f"searches_{slug}.{d}.json" if d else f"searches_{slug}.json"


def _stem(slug: str, kind: str, review_dir: Optional[str] = None) -> str:
    d = _gate_designator(slug, review_dir)
    return f"{slug}.{d}.{kind}" if d else f"{slug}.{kind}"


def client_state(slug: str, review_dir: Optional[str] = None, *,
                 packet: Optional[dict] = None,
                 workstation_id: Optional[str] = None) -> dict:
    review_dir = review_dir or REVIEW_DIR  # resolve at call time (tests monkeypatch)
    if packet is None:
        packet_path = os.path.join(review_dir, f"{slug}.review.json")
        with open(packet_path) as f:
            packet = json.load(f)
    status = packet.get("status", "pending")
    root = os.path.dirname(os.path.dirname(review_dir))
    cleaned = os.path.join(root, "data", "cleaned")
    reports = os.path.join(root, "data", "reports")
    handoff = os.path.join(root, "data", "handoff")

    done = {
        "intake": True,
        "approved": status == "approved",
        "searched": _exists(cleaned, _sweep_name(
            slug, review_dir, workstation_id=workstation_id)),
        "qualified": _exists(review_dir, f"{slug}.qualify.json"),
        "contacts": _exists(review_dir, f"{slug}.contacts.json"),
        "reports": bool(glob.glob(os.path.join(reports, f"{slug}.*.md"))),
    }
    if status == "rejected":
        next_step = "revise Analyst Layer (strategy rejected)"
    else:
        next_step = next((s for s in STAGES[1:] if not done[s]), "complete")
    website = packet.get("website") or (packet.get("submission") or {}).get("website")
    if website:
        logo_domain = website.split("//")[-1].split("/")[0].removeprefix("www.")
    else:
        # No website on file: best-effort guess from the name; the UI hides
        # the logo on a 404 so a wrong guess costs nothing.
        name = packet.get("client_name", slug)
        logo_domain = "".join(c for c in name.lower() if c.isalnum()) + ".com"
    # Fresh-client cliff (2026-07-24): the page says when the populated
    # capability profile is missing and opens the guided forms, instead of
    # letting the sweep gate name it later inside a job log.
    try:
        from tools.capability import load_profile
        _profile = load_profile(packet.get("client_name", slug))
        profile_populated = bool(
            _profile is not None and _profile.is_populated())
    except Exception:  # noqa: BLE001 - a broken profile reads as absent
        profile_populated = False
    return {
        "slug": slug,
        "client_name": packet.get("client_name", slug),
        "status": status,
        "profile_populated": profile_populated,
        "reviewer_note": packet.get("reviewer_note", ""),
        "stages": done,
        "next_step": next_step,
        "has_handoff": _exists(handoff, f"{slug}.apollo.json"),
        "website": website,
        "logo_domain": logo_domain,
    }


_ORDER_PATH = os.path.join(os.path.dirname(REVIEW_DIR), "state", "client_order.json")


def _client_order() -> list[str]:
    try:
        with open(_ORDER_PATH) as f:
            order = json.load(f)
        return order if isinstance(order, list) else []
    except (OSError, ValueError):
        return []


def _save_client_order(order: list[str]) -> None:
    os.makedirs(os.path.dirname(_ORDER_PATH), exist_ok=True)
    with open(_ORDER_PATH, "w") as f:
        json.dump([s for s in order if isinstance(s, str)], f)


def all_clients(review_dir: Optional[str] = None) -> list[dict]:
    review_dir = review_dir or REVIEW_DIR  # resolve at call time (tests monkeypatch)
    out = []
    for path in sorted(glob.glob(os.path.join(review_dir, "*.review.json"))):
        slug = os.path.basename(path)[: -len(".review.json")]
        # Workstation-native packets are children of the one client
        # foundation, not additional clients on the home board. Canonical
        # client slugs never contain a dot; native packet stems always do
        # (`<slug>.<workstation_id>.review.json`).
        if "." in slug:
            continue
        try:
            state = client_state(slug, review_dir)
            state["attention"] = _attention(state)
            out.append(state)
        except (json.JSONDecodeError, OSError) as exc:
            out.append({"slug": slug, "client_name": slug, "error": str(exc)})
    # Operator-chosen order first; anything unknown keeps alphabetical at the end.
    order = _client_order()
    rank = {s: i for i, s in enumerate(order)}
    out.sort(key=lambda c: (rank.get(c["slug"], len(order)), c["slug"]))
    return out


def _attention(state: dict) -> Optional[dict]:
    """The one thing (if any) this client needs from a human right now."""
    if state["status"] == "pending":
        return {
            "kind": "approve",
            "label": "Analyst Layer waiting for your approval",
        }
    if state["status"] == "rejected":
        return {
            "kind": "revise",
            "label": "Analyst Layer rejected: rebuild and re-review",
        }
    slug = state["slug"]
    for qa_path in glob.glob(os.path.join(REPORT_DIR, f"{slug}.*.qa.json")):
        qa = _load_json(qa_path) or {}
        audit_ok = (qa.get("audit") or {}).get("passed", True)
        if not (qa.get("lint_ok", True) and audit_ok):
            kind = os.path.basename(qa_path).split(".")[1]
            names = {"teaser": "Federal Snapshot", "pre_assessment": "Full Capture Pre-Assessment",
                     "capture_brief": "Opportunity Assessment",
                     "federal_opportunity_assessment": "Opportunity Assessment",
                     "target_report": "Target Report"}
            label = names.get(kind, kind.replace("_", " ").title())
            return {"kind": "qa_fail", "label": f"{label} failed its QA gates"}
    if state["next_step"] != "complete":
        return {"kind": "run", "label": f"Ready to run: {state['next_step']}"}
    return None


# ----------------------------------------------------------------------------- #
# Step model — what the guided pipeline renders
# ----------------------------------------------------------------------------- #
def _step(key: str, title: str, state: str, summary: str,
          action: Optional[dict] = None, artifacts: Optional[list] = None,
          detail: Optional[dict] = None, stage: str = "ASSESS") -> dict:
    return {"key": key, "title": title, "state": state, "summary": summary,
            "action": action, "artifacts": artifacts or [], "detail": detail or {},
            "stage": stage}


def _search_summary(slug: str, *, searches: Optional[dict] = None,
                    workstation_id: Optional[str] = None) -> tuple[str, list]:
    data = searches if searches is not None else (
        _load_json(os.path.join(CLEANED_DIR, _sweep_name(
            slug, workstation_id=workstation_id))) or {})
    results = data.get("results", {})
    parts, artifacts = [], []
    sam = results.get("sam.gov")
    if isinstance(sam, list):
        parts.append(f"{len(sam)} SAM.gov notices")
    elif isinstance(sam, dict):
        parts.append("SAM.gov errored")
    usa = results.get("usaspending.gov")
    if isinstance(usa, list):
        with_data = [e for e in usa if (e.get("summary") or {}).get("award_count")]
        parts.append(f"market evidence for {len(with_data)}/{len(usa)} NAICS")
    web = results.get("web")
    if isinstance(web, list):
        parts.append(f"{len(web)} web leads")
    news = results.get("news")
    if isinstance(news, dict) and news.get("items"):
        parts.append(f"{len(news['items'])} news signals")
    fr = results.get("federal_register")
    if isinstance(fr, dict) and not fr.get("error"):
        parts.append(f"{sum(len(v) for v in fr.values() if isinstance(v, list))} Fed Register docs")
    subs = results.get("subawards")
    if isinstance(subs, dict) and subs.get("primes"):
        parts.append(f"{len(subs['primes'])} teaming primes")
    aw = results.get("contract_awards")
    if isinstance(aw, dict) and aw.get("recompetes"):
        parts.append(f"{len(aw['recompetes'])} expiring contracts (recompetes)")
    kev = results.get("cisa_kev")
    if isinstance(kev, dict) and kev.get("recent_count"):
        parts.append(f"KEV: {kev['recent_count']} new exploited vulns")
    regs = results.get("regulations_gov")
    if isinstance(regs, dict) and not regs.get("error") and regs:
        n = sum(len(v) for v in regs.values() if isinstance(v, list))
        if n:
            parts.append(f"{n} regulatory docs")
    gi = results.get("govinfo")
    if isinstance(gi, dict) and not gi.get("error") and gi:
        n = sum(len(v) for v in gi.values() if isinstance(v, list))
        if n:
            parts.append(f"{n} legislative docs (funding proof)")
    for key, noun in (("grants_gov", "grant programs"),
                      ("sbir_gov", "SBIR topics"),
                      ("gdelt", "global news hits")):
        d = results.get(key)
        if isinstance(d, dict) and not d.get("error") and d:
            n = sum(len(v) for v in d.values() if isinstance(v, list))
            if n:
                parts.append(f"{n} {noun}")
    # These legacy markdown names carry no scope binding.  They stay in the
    # client-global depository and never appear inside an exact workstation.
    if workstation_id is None:
        pic_md = os.path.join(REVIEW_DIR, f"{slug}.research_picture.md")
        if os.path.exists(pic_md):
            artifacts.append({"name": "Research picture (full-sweep synthesis)",
                              "path": _rel(pic_md), "fmt": "md"})
        dos_md = os.path.join(REVIEW_DIR, f"{slug}.dossiers.md")
        if os.path.exists(dos_md):
            artifacts.append({"name": "Pursuit dossiers (deadline-aware depth)",
                              "path": _rel(dos_md), "fmt": "md"})
    path = os.path.join(CLEANED_DIR, _sweep_name(
        slug, workstation_id=workstation_id))
    if os.path.exists(path):
        artifacts.append({"name": "Raw search results", "path": _rel(path), "fmt": "json"})
    return (", ".join(parts) if parts else "Not run yet."), artifacts


def _research_picture(slug: str, *, searches: Optional[dict] = None,
                      workstation_id: Optional[str] = None) -> Optional[dict]:
    """The synthesis block for the search step detail: Claude's read of the sweep."""
    data = searches if searches is not None else (
        _load_json(os.path.join(CLEANED_DIR, _sweep_name(
            slug, workstation_id=workstation_id))) or {})
    pic = (data.get("results") or {}).get("research_picture")
    if not isinstance(pic, dict) or pic.get("error") or not pic.get("headline"):
        return None
    return {
        "headline": pic.get("headline"),
        "next_action": pic.get("next_action"),
        "top": [{"id": t.get("id"), "title": t.get("title"),
                 "why_now": t.get("why_now"), "deadline": t.get("deadline")}
                for t in (pic.get("top_opportunities") or [])],
        "signals": pic.get("demand_signals") or [],
        "watchlist": pic.get("watchlist") or [],
        "market": pic.get("market_structure"),
        "gaps": pic.get("gaps") or [],
    }


def _search_opportunities(slug: str, *, searches: Optional[dict] = None,
                          workstation_id: Optional[str] = None) -> list[dict]:
    """The reviewable list: every notice the search found, ready for human eyes."""
    data = searches if searches is not None else (
        _load_json(os.path.join(CLEANED_DIR, _sweep_name(
            slug, workstation_id=workstation_id))) or {})
    sam = (data.get("results") or {}).get("sam.gov")
    if not isinstance(sam, list):
        return []
    triage = (data.get("results") or {}).get("triage") or {}
    if not isinstance(triage, dict) or triage.get("error"):
        triage = {}
    opps = []
    for o in sam:
        oid = o.get("source_id") or o.get("notice_id")
        t = triage.get(oid) or {}
        opps.append({
            "title": o.get("title"),
            "agency": o.get("agency"),
            "deadline": o.get("response_deadline") or o.get("deadline"),
            "type": o.get("notice_type") or o.get("type"),
            "naics": o.get("naics_code"),
            "url": o.get("url") or o.get("api_url") or o.get("link"),
            "id": oid,
            "verdict": t.get("verdict"),
            "reason": t.get("reason"),
        })
    rank = {"pursue": 0, "monitor": 1, "unscreened": 2, None: 2, "discard": 3}
    opps.sort(key=lambda x: (rank.get(x.get("verdict"), 2), x.get("deadline") or "9999"))
    return opps


def _qualify_summary(slug: str) -> tuple[str, list]:
    path = os.path.join(REVIEW_DIR, f"{slug}.qualify.json")
    q = _load_json(path)
    if not q:
        return "Not run yet.", []
    n, v = q.get("candidate_count", 0), q.get("verified_count", 0)
    fits = sum(1 for c in q.get("candidates", [])
               if (c.get("fit_rationale") or {}).get("verdict") in ("strong_fit", "partial_fit"))
    return (f"{n} candidates, {v} verified, {fits} rated a fit.",
            [{"name": "Qualified candidates", "path": _rel(path), "fmt": "json"}])


def _contacts_summary(slug: str) -> tuple[str, list]:
    path = os.path.join(REVIEW_DIR, f"{slug}.contacts.json")
    plan = _load_json(path)
    if not plan:
        return "Not run yet.", []
    arts = [{"name": "Contact plan", "path": _rel(path.replace(".json", ".md")), "fmt": "md"}]
    titles = os.path.join(REVIEW_DIR, f"{slug}.titles.json")
    if os.path.exists(titles):
        arts.insert(0, {"name": "Buyer titles (Claude deliberation)", "path": _rel(titles), "fmt": "json"})
    handoff = os.path.join(HANDOFF_DIR, f"{slug}.apollo.json")
    if os.path.exists(handoff):
        arts.append({"name": "Apollo handoff (run in Cowork)", "path": _rel(handoff), "fmt": "json"})
    return (f"{len(plan.get('known_pocs', []))} verified POCs from notices, "
            f"{len(plan.get('searches', []))} recommended people-searches."), arts


from agents.reports import release as _release  # noqa: E402
from agents.reports.release import (  # noqa: E402, F401 — one owner
    _mtime_or_none, html_sha256 as _html_sha256, release_state,
    stable_status as _stable_foa_status,
)
_HTML_SHA_MEMO = _release._HTML_SHA_MEMO  # same dict; tests clear it via srv


def _foa_artifact_state(slug: str) -> dict:
    """Compat shape over release_state (2026-07-11): the resolver in
    agents/reports/release.py is the ONE owner of the releasable verdict;
    this keeps the older dict shape for _report_rows and tests."""
    rs = release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    path = rs["preview_path"]
    qa_path = (path[:-len(".html")] + ".qa.json"
               if path and rs["family"] in ("stable", "signal_board") else None)
    return {"kind": rs["family"] if path else "missing", "path": path,
            "qa_path": qa_path, "qa": _load_json(qa_path) if qa_path else None,
            "qa_pass": rs["releasable"], "reason": rs["reason"],
            "updated": rs["updated"]}


def _report_rows(slug: str) -> list[dict]:
    rows = []
    for kind, label in [("pre_assessment", "Full Capture Pre-Assessment")]:
        md = os.path.join(REPORT_DIR, f"{slug}.{kind}.md")
        qa = _load_json(os.path.join(REPORT_DIR, f"{slug}.{kind}.qa.json")) or {}
        exists = os.path.exists(md)
        audit_ok = (qa.get("audit") or {}).get("passed", False)
        rows.append({
            "kind": kind, "label": label, "exists": exists,
            "qa_pass": bool(exists and qa.get("lint_ok") and audit_ok),
            "path": _rel(md) if exists else None,
            "qa_path": _rel(os.path.join(REPORT_DIR, f"{slug}.{kind}.qa.json")) if exists else None,
        })
    # Stable-name assessments derive state from their QA sidecar; legacy
    # capture_brief artifacts retain their explicit filename contract.
    foa = _foa_artifact_state(slug)
    cb_path = foa["path"]
    rows.insert(0, {
        "kind": "capture_brief", "label": "Opportunity Assessment",
        "exists": cb_path is not None,
        "qa_pass": foa["qa_pass"],
        "path": _rel(cb_path) if cb_path else None,
        "qa_path": _rel(foa["qa_path"]) if foa["qa_path"] else None,
    })
    return rows


def _desktop_root() -> str:
    # Overridable for tests; defaults to the operator's Desktop.
    return os.environ.get("LILA_DESKTOP_ROOT", os.path.expanduser("~/Desktop"))


def _doc_prefs_path(slug: str, workstation_id: Optional[str] = None) -> str:
    suffix = f".{workstation_id}" if workstation_id is not None else ""
    return os.path.join(
        os.path.dirname(REVIEW_DIR), "state",
        f"doc_prefs_{slug}{suffix}.json")


def _doc_prefs(slug: str, workstation_id: Optional[str] = None) -> dict:
    try:
        with open(_doc_prefs_path(slug, workstation_id)) as f:
            p = json.load(f)
        return {"hidden": list(p.get("hidden") or []), "order": list(p.get("order") or [])}
    except (OSError, ValueError):
        return {"hidden": [], "order": []}


def _save_doc_prefs(slug: str, prefs: dict,
                    workstation_id: Optional[str] = None) -> None:
    path = _doc_prefs_path(slug, workstation_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(prefs, f)


def client_foundation_documents(slug: str) -> list[dict]:
    """Strict client-global documents shared by every exact workstation.

    This is an allowlist, not a family glob. Only intake, capability/product
    identity, the adopted exact client mark, and the baseline strategy
    Markdown may appear here. Reports, sweeps, approvals, outreach state, and
    Desktop files therefore cannot be relabeled as shared foundation.
    """
    if not slug or _slugify(slug) != slug:
        return []

    candidates: list[tuple[str, str, str]] = [
        (os.path.join(INTAKE_DIR, f"{slug}.submission.json"),
         "Intake submission", "intake"),
        (os.path.join(CLIENTS_DIR, slug, "profile.json"),
         "Capability profile", "capability_profile"),
        (os.path.join(REVIEW_DIR, f"{slug}.review.md"),
         "Baseline strategy template", "baseline_strategy"),
    ]
    from tools.brand_marks import client_marks_dir, find_mark
    mark = find_mark(client_marks_dir(), slug)
    if mark is not None and mark.is_file():
        candidates.append((str(mark), "Client brand mark", "client_mark"))

    documents = []
    for path, label, kind in candidates:
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(path)[1].lower().lstrip(".") or "file"
        documents.append({
            "label": label,
            "stage": "Shared foundation",
            "kind": kind,
            "fmt": ext,
            "qa_pass": None,
            "path": _rel(path),
            "updated": os.path.getmtime(path),
            "key": os.path.basename(path),
            "source": "client-foundation",
        })
    return documents


def client_documents(slug: str, client_name: str = "",
                     foa: Optional[dict] = None,
                     workstation_id: Optional[str] = None) -> list[dict]:
    """Every client-facing deliverable on file, newest first — the docs shelf."""
    if foa is None and glob.glob(os.path.join(
            REPORT_DIR, f"{slug}.federal_opportunity_signals*.html")):
        foa = release_state(
            slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    labels = {
        "capture_brief": ("Opportunity Assessment", "Assess"),  # legacy filename
        "federal_opportunity_assessment": ("Opportunity Assessment", "Assess"),
        "federal_opportunity_signals": (
            "Federal Opportunity Pre-Assessment", "Assess"),
        "target_report": ("Target Report", "Target"),
        "teaser": ("Federal Snapshot", "Assess"),
        "pre_assessment": ("Full Capture Pre-Assessment", "Assess"),
    }
    docs = []
    # once a federal_opportunity_assessment artifact exists, the stale legacy
    # capture_brief card is superseded (both carried the same label otherwise)
    # same-family only: a scoped agency build (slug.<designator>.federal_...)
    # must never suppress the all-scope legacy deliverable (review finding #9)
    def in_family(path: str) -> bool:
        if workstation_id is None:
            return True
        base = os.path.basename(path)
        if workstation_id == "all":
            rest = base[len(slug) + 1:] if base.startswith(f"{slug}.") else ""
            return bool(rest and not rest.startswith("agency_"))
        return base.startswith(f"{slug}.{workstation_id}.")

    family_prefix = (slug if workstation_id in {None, "all"}
                     else f"{slug}.{workstation_id}")
    has_new = bool(glob.glob(os.path.join(
        REPORT_DIR, f"{family_prefix}.federal_opportunity_assessment*.html")))
    for path in glob.glob(os.path.join(REPORT_DIR, f"{slug}.*")):
        if not in_family(path):
            continue
        base = os.path.basename(path)
        if base.endswith(".qa.json") or base.endswith(".arbiters.json"):
            continue
        # The Signal Board's diagnostic/verdict sidecar is strictly internal;
        # only the canonical HTML belongs on the client documents shelf.
        if base.endswith(".federal_opportunity_signals.internal.md"):
            continue
        if has_new and ".capture_brief" in base:
            continue
        parts = base.split(".")
        kind = parts[1] if len(parts) > 1 else "doc"
        label, stage = labels.get(kind, (kind.replace("_", " ").title(), ""))
        if kind == "assessment" and len(parts) > 2:
            view = parts[2].replace("DO-NOT-SEND", "").strip(".") or "client"
            label, stage = f"Assessment · {view.title()} View", "Assess"
        if kind.startswith("agency_"):
            label = f"Agency Focus · {kind.split('_', 1)[1].upper()}"
            stage = "Assess"
        bad = ".DO-NOT-SEND." in base
        fmt = "html" if base.endswith(".html") else "md"
        if fmt == "md":
            qa = _load_json(os.path.join(REPORT_DIR, f"{slug}.{kind}.qa.json")) or {}
            qa_pass = bool(qa.get("lint_ok") and (qa.get("audit") or {}).get("passed"))
        elif ".federal_opportunity_signals" in base:
            # A clean-looking filename is not evidence of release.  The shelf
            # consumes the same hash-certified resolver verdict as download.
            qa_pass = bool(
                foa
                and foa.get("family") == "signal_board"
                and foa.get("releasable")
                and foa.get("path")
                and os.path.realpath(foa["path"]) == os.path.realpath(path)
            )
        elif base.endswith(".federal_opportunity_assessment.html"):
            st = _stable_foa_status(path)
            qa_pass = st["qa_pass"]
            if qa_pass and foa is not None and not foa.get("releasable"):
                qa_pass = False  # sidecar clean but approval lapsed/superseded
        else:
            qa_pass = not bad
        docs.append({
            "label": label, "stage": stage, "kind": kind, "fmt": fmt,
            "qa_pass": qa_pass, "path": _rel(path),
            "updated": os.path.getmtime(path),
        })
    # Legacy working-intelligence filenames carry no scope identity.  They
    # remain visible in the client-global depository, but an exact workstation
    # shelf may never visually adopt them as DHS/DoD/All merely by proximity.
    if workstation_id is None:
        for fname, label in (
                (f"{slug}.research_picture.md", "Research Picture"),
                (f"{slug}.dossiers.md", "Pursuit Dossiers"),
                (f"{slug}.review.md", "Strategy (approved)"),
                (f"{slug}.contacts.md", "Contact Plan")):
            p = os.path.join(REVIEW_DIR, fname)
            if os.path.exists(p):
                docs.append({
                    "label": label, "stage": "Intelligence", "kind": "working",
                    "fmt": "md", "qa_pass": True, "path": _rel(p),
                    "updated": os.path.getmtime(p),
                })
    # Mirror the client's Desktop folder: everything in ~/Desktop/<Client>/ shows
    # on the shelf — including files the operator drops there by hand. The Desktop
    # folder is the human-curated set, so it wins on duplicate filenames.
    seen = {os.path.basename(d["path"]) for d in docs}
    # Desktop files carry no machine-verifiable workstation id.  They remain
    # visible on the client-global depository, but never enter an exact scope
    # shelf merely because their filename happens to mention an agency.
    folder = (os.path.join(_desktop_root(), client_name)
              if client_name and workstation_id is None else None)
    if folder and os.path.isdir(folder):
        for path in glob.glob(os.path.join(folder, "*")):
            base = os.path.basename(path)
            if base.startswith(".") or not os.path.isfile(path) or base in seen:
                continue
            ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
            if ext not in ("html", "md", "pdf", "docx", "pptx", "xlsx"):
                continue
            docs.append({
                "label": base.rsplit(".", 1)[0].replace("_", " "),
                "stage": "Client folder", "kind": "desktop", "fmt": ext,
                # mirrors are operator-curated files; the dashboard tracks no
                # release state for them and must not badge them QA PASSED
                "qa_pass": None, "path": path, "source": "desktop",
                "updated": os.path.getmtime(path),
            })
    # Operator prefs: hidden docs drop off the shelf (files untouched); the
    # chosen order wins, anything unranked follows newest-first.
    prefs = (_doc_prefs(slug) if workstation_id is None
             else _doc_prefs(slug, workstation_id))
    for d in docs:
        d["key"] = os.path.basename(d["path"])
    docs = [d for d in docs if d["key"] not in prefs["hidden"]]
    rank = {k: i for i, k in enumerate(prefs["order"])}
    docs.sort(key=lambda d: (rank.get(d["key"], len(rank)), -d["updated"]))
    return docs


@app.post("/api/docs/prefs")
def api_docs_prefs():
    body = request.get_json(force=True) or {}
    slug = body.get("slug", "")
    if not slug or "/" in slug or ".." in slug:
        return jsonify({"error": "bad slug"}), 400
    workstation_id = body.get("workstation_id")
    if (workstation_id is not None and (
            not isinstance(workstation_id, str)
            or not (workstation_id == "all"
                    or workstation_id.startswith("agency_"))
            or _slugify(workstation_id) != workstation_id)):
        return jsonify({"error": "bad workstation id"}), 400
    if workstation_id is not None:
        resolved, error = _workstation_route_error(slug)
        if error:
            return error
        _, catalog = resolved
        if not any(row.ref.id == workstation_id
                   for row in catalog.workstations):
            return _no_store({"error": "no such workstation"}, 404)
    prefs = _doc_prefs(slug, workstation_id)
    if body.get("hide"):
        if body["hide"] not in prefs["hidden"]:
            prefs["hidden"].append(body["hide"])
    if body.get("unhide_all"):
        prefs["hidden"] = []
    if isinstance(body.get("order"), list):
        prefs["order"] = [k for k in body["order"] if isinstance(k, str)]
    _save_doc_prefs(slug, prefs, workstation_id)
    return jsonify({"ok": True, "hidden": len(prefs["hidden"])})


def build_steps(slug: str, *, packet: Optional[dict] = None,
                searches: Optional[dict] = None,
                workstation_id: Optional[str] = None) -> list[dict]:
    """The six-step guided pipeline for one client."""
    state = client_state(
        slug, packet=packet, workstation_id=workstation_id)
    approved = state["status"] == "approved"
    packet = packet if packet is not None else (
        _load_json(os.path.join(REVIEW_DIR, f"{slug}.review.json")) or {})
    strategy = packet.get("strategy") or {}
    steps: list[dict] = []

    # 1 — intake & research
    kw, naics = len(strategy.get("keywords", [])), strategy.get("inferred_naics", [])
    steps.append(_step(
        "intake", "Intake & research", "done",
        f"Strategy built: {kw} keywords, NAICS {', '.join(naics) if naics else 'n/a'}, "
        f"confidence {strategy.get('confidence', 0):.0%}." if strategy else "Intake artifacts on file.",
    ))

    # 2 — approval gate
    md_path = os.path.join(REVIEW_DIR, f"{slug}.review.md")
    strategy_md = open(md_path).read() if os.path.exists(md_path) else ""
    if approved:
        gate_state, gate_summary = "done", (
            "Analyst Layer locked. Reopen it any time; refinements preserve "
            "approval. Set the scope, then run the search.")
    elif state["status"] == "rejected":
        gate_state = "blocked"
        gate_summary = f"Rejected{': ' + state['reviewer_note'] if state['reviewer_note'] else ''}. Revise and re-review."
    else:
        gate_state, gate_summary = "ready", (
            "The Analyst Layer needs your sign-off before anything spends "
            "API calls.")
    steps.append(_step("approve", "Analyst Layer · strategy & search boundary", gate_state, gate_summary,
                       action=None if approved else {"kind": "gate"},
                       detail={"strategy_md": strategy_md, "status": state["status"],
                               "note": state["reviewer_note"],
                               # structured strategy for the inline editor;
                               # editable only while not approved
                               "strategy": strategy,
                               "editable": not approved,
                               "search_scope": packet.get("search_scope") or {"all": True},
                               "revision_count": packet.get("revision_count", 0),
                               "revised_at": packet.get("revised_at")}))

    # 3 — opportunity search
    done = state["stages"]["searched"]
    summary, arts = _search_summary(
        slug, searches=searches, workstation_id=workstation_id)
    opps = (_search_opportunities(
        slug, searches=searches, workstation_id=workstation_id)
            if done else [])
    steps.append(_step(
        "search", "Opportunity search", "done" if done else ("ready" if approved else "waiting"),
        summary if done else ("Runs all data sources in parallel using the approved keywords."
                              if approved else "Unlocks after approval."),
        action={"kind": "run", "step": "searches", "label": "Run search" if not done else "Re-run search"}
        if approved else None,
        artifacts=arts,
        detail={},
    ))

    # 4 — REVIEW opportunities: the human checkpoint between search and the
    # assessment. Review the triaged notices, suggest a correction, re-triage
    # (run_picture --guidance, zero quota), review again, then produce.
    #
    # Qualify & Verify and the Assess-report deliverable are now INTERNAL —
    # they are NOT dashboard steps. Their code paths stay fully runnable via
    # run_qualify.py / run_report.py and POST /api/run {step: "qualify"|"report"}.
    from collections import Counter as _Counter
    vc = _Counter(o.get("verdict") or "unscreened" for o in opps) if done else _Counter()
    legacy_summary = (
        f"{vc.get('pursue', 0)} pursue · {vc.get('monitor', 0)} monitor · "
        f"{vc.get('discard', 0)} discard"
        + (f" · {vc.get('unscreened', 0)} unscreened" if vc.get("unscreened") else ""))
    # ONE opportunity truth (2026-07-12, UX plan A3): when the strict pointer
    # is CURRENT the ledger records ARE the Review board; raw-sweep triage
    # demotes to a labeled diagnostic view. INVALID/stale states say the live
    # lane is held closed instead of showing legacy rows as truth.
    strict_detail = None
    if done:
        snap = _assess_ledger_snapshot(state["client_name"], include_run=True)
        if snap.get("exists") and snap.get("state") == "current":
            rows = []
            for rec in ((snap.get("run") or {}).get("live") or {}).get(
                    "records", []):
                evidence = rec.get("authoritative_evidence") or []
                rows.append({
                    "notice_id": rec.get("notice_id"),
                    "title": rec.get("title"),
                    "agency": rec.get("agency"),
                    "classification": rec.get("classification"),
                    "recommendation": rec.get("recommendation"),
                    "deadline": rec.get("response_deadline"),
                    "requirement_reviewed": bool(
                        rec.get("requirement_reviewed_at")),
                    "url": (evidence[-1] or {}).get("source_url")
                           if evidence else None,
                })
            strict_detail = {
                "state": "current",
                "run_id": snap.get("run_id"),
                "classifications": snap.get("live_classifications") or {},
                "actionable": snap.get("live_actionable") or 0,
                "rows": rows,
            }
        elif snap.get("state") in ("stale", "unavailable"):
            strict_detail = {"state": snap.get("state"),
                             "diagnostics": snap.get("diagnostics") or []}
    if not done:
        rv_summary = "Unlocks after the opportunity search."
    elif strict_detail and strict_detail.get("state") == "current":
        cls = strict_detail["classifications"]
        rv_summary = (
            f"STRICT: {cls.get('bid_now', 0)} bid-now · "
            f"{cls.get('unscreened', 0)} research · "
            f"{cls.get('excluded', 0)} excluded of "
            f"{len(strict_detail['rows'])} live census. Legacy screen "
            f"({legacy_summary}) is diagnostic only. Review, then produce.")
    else:
        rv_summary = (legacy_summary
                      + ". Review, suggest a correction, re-triage, then "
                        "produce the assessment.")
    steps.append(_step(
        "review", "Review opportunities",
        "ready" if done else "waiting",
        rv_summary,
        action=None,
        detail={"opportunities": opps, "verdicts": dict(vc),
                "strict": strict_detail,
                "research_picture": _research_picture(
                    slug, searches=searches, workstation_id=workstation_id)
                if done else None},
    ))

    # 5 — TARGET: contact sourcing (Apollo). Physically locked behind the
    # operator's explicit proceed-to-Target decision, which itself requires a
    # CURRENT Assess approval (2026-07-12; evidence drift re-locks the lane).
    done_c = state["stages"]["contacts"]
    summary, arts = _contacts_summary(slug)
    from agents.review import target_gate_status
    target_ok, _target_problems = target_gate_status(
        state["client_name"], review_dir=REVIEW_DIR)
    ready = approved and state["stages"]["searched"] and target_ok
    steps.append(_step(
        "contacts", "Contact sourcing → Apollo", "done" if done_c else ("ready" if ready else "waiting"),
        summary if done_c else ("Claude maps who to reach: verified POCs plus recommended titles at "
                                "agencies, incumbents, and primes." if ready
                                else "Locked. Approve the assessment and proceed to Target "
                                     "(Review step) to unlock outreach."),
        action={"kind": "run", "step": "contacts", "label": "Build contact plan" if not done_c else "Rebuild plan"}
        if ready else None,
        artifacts=arts,
        stage="TARGET",
    ))

    # 7 — TARGET deliverable: the target report, rendered from the contact plan.
    tr_ok = os.path.join(REPORT_DIR, f"{slug}.target_report.html")
    tr_bad = os.path.join(REPORT_DIR, f"{slug}.target_report.DO-NOT-SEND.html")
    tr_path = tr_ok if os.path.exists(tr_ok) else (tr_bad if os.path.exists(tr_bad) else None)
    tr_arts = [{"name": "Target report (client HTML)", "path": _rel(tr_path), "fmt": "html"}] if tr_path else []
    steps.append(_step(
        "target_report", "Target report",
        ("done" if tr_path else "ready") if done_c else "waiting",
        ("QA passed — Desktop copy in the client folder." if os.path.exists(tr_ok)
         else "FAILED QA — stamped DO-NOT-SEND; open it to see why." if tr_path
         else "Renders the contact plan into the client-facing HTML deliverable."
         if done_c else "Needs the contact plan first."),
        action={"kind": "run", "step": "target_report",
                "label": "Build target report" if not tr_path else "Rebuild target report"}
        if done_c else None,
        artifacts=tr_arts,
        stage="TARGET",
    ))

    # 8 — EXECUTE placeholder (campaign results reporting — next build)
    steps.append(_step(
        "execute", "Campaign results", "waiting",
        "EXECUTE-stage reporting (campaign outcomes) — parked by request; Assess and Target first.",
        stage="EXECUTE",
    ))
    return steps


def _early_workstation_steps(
    slug: str, packet: dict, *, workstation_id: Optional[str] = None,
    searches: Optional[dict] = None, native: bool = False,
) -> list[dict]:
    """Configure/Search-only legacy adapter with no downstream reads.

    A pending boundary or an approved boundary without a current sweep must
    not inherit an older assessment, approval, report, or Target artifact.
    These three cards are the complete allowed operating surface until a
    scope-bound sweep exists.
    """
    strategy = packet.get("strategy") or {}
    status = packet.get("status", "pending")
    approved = status == "approved"
    kw = len(strategy.get("keywords", []))
    naics = strategy.get("inferred_naics", [])
    steps = [_step(
        "intake", "Intake & research", "done",
        (f"Strategy built: {kw} keywords, NAICS "
         f"{', '.join(naics) if naics else 'n/a'}, confidence "
         f"{strategy.get('confidence', 0):.0%}.")
        if strategy else "Intake artifacts on file.",
    )]
    packet_suffix = f".{workstation_id}" if workstation_id is not None else ""
    md_path = os.path.join(
        REVIEW_DIR, f"{slug}{packet_suffix}.review.md")
    strategy_md = open(md_path).read() if os.path.exists(md_path) else ""
    if approved:
        gate_state = "done"
        gate_summary = (
            "Analyst Layer locked to this workstation. Reopen it any time; "
            "refinements preserve approval. Then run the exact search."
            if native else
            "Analyst Layer locked. Reopen it any time; refinements preserve "
            "approval. Set the scope, then run the search.")
    elif status == "rejected":
        gate_state = "blocked"
        note = packet.get("reviewer_note", "")
        gate_summary = f"Rejected{': ' + note if note else ''}. Revise and re-review."
    else:
        gate_state = "ready"
        gate_summary = (
            "The Analyst Layer needs your sign-off before anything spends "
            "API calls.")
    steps.append(_step(
        "approve", "Analyst Layer · strategy & search boundary", gate_state,
        gate_summary, action=None if approved else {"kind": "gate"},
        detail={
            "strategy_md": strategy_md,
            "status": status,
            "note": packet.get("reviewer_note", ""),
            "strategy": strategy,
            "editable": not approved,
            "search_scope": packet.get("search_scope") or {"all": True},
            "revision_count": packet.get("revision_count", 0),
            "revised_at": packet.get("revised_at"),
        }))
    if searches is not None:
        summary, artifacts = _search_summary(
            slug, searches=searches, workstation_id=workstation_id)
        steps.append(_step(
            "search",
            "Current search results" if native else "Opportunity search",
            "done", summary,
            # Tranche-3 native workstations stop at Review.  Repeating a
            # metered sweep is an explicit later operator act, never the
            # primary action immediately after a successful exact search.
            action=None if native else {
                "kind": "run", "step": "searches",
                "label": "Re-run search",
            },
            artifacts=artifacts,
            detail={"opportunities": _search_opportunities(
                slug, searches=searches,
                workstation_id=workstation_id)},
        ))
    else:
        steps.append(_step(
            "search", "Opportunity search", "ready" if approved else "waiting",
            ("Runs all data sources in parallel using the approved keywords."
             if approved else "Unlocks after approval."),
            action={"kind": "run", "step": "searches", "label": "Run search"}
            if approved else None,
        ))
    return steps


# ----------------------------------------------------------------------------- #
# Job runner
# ----------------------------------------------------------------------------- #
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_JOB_HEARTBEAT_STALE_SECONDS = 10.0


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_snapshot(job: dict, *, now: Optional[float] = None) -> dict:
    """Public, server-authoritative timing and runner health for one job.

    A fresh heartbeat proves only that the Control Room recently observed the
    pipeline subprocess alive. It does not claim that a nested model/network
    call is making progress. A stale heartbeat is diagnostic and never changes
    job status, releases the one-client lock, retries, kills, or authorizes an
    artifact.
    """
    now = _time.monotonic() if now is None else now
    started = job.get("_started_monotonic", now)
    finished = job.get("_finished_monotonic")
    end = finished if finished is not None else now
    last_output = job.get("_last_output_monotonic", started)
    heartbeat = job.get("_heartbeat_monotonic")

    public = {key: value for key, value in job.items()
              if not key.startswith("_")}
    lines = list(job.get("lines") or [])
    public["lines"] = lines
    public["artifacts_written"] = sum(
        1 for line in lines
        if line.startswith("[out:") or line.startswith("[out] /")
    )
    public["elapsed_seconds"] = round(max(0.0, end - started), 1)
    public["quiet_seconds"] = round(max(0.0, end - last_output), 1)
    public["heartbeat_age_seconds"] = (
        None if heartbeat is None
        else round(max(0.0, end - heartbeat), 1)
    )
    if job.get("status") != "running":
        public["runner_state"] = "finished"
    elif heartbeat is None:
        public["runner_state"] = "starting"
    elif now - heartbeat > _JOB_HEARTBEAT_STALE_SECONDS:
        public["runner_state"] = "heartbeat_stale"
    else:
        public["runner_state"] = "process_alive"
    return public


def _step_cmd(step: str, client: str, args: dict) -> list[str]:
    py = sys.executable
    if step == "searches":
        # Always forward-looking: run_searches defaults to responses due today+.
        cmd = [py, "run_searches.py", "--client", client]
    elif step == "picture":
        # Claude-layer retry / review-loop re-triage: re-screen + re-synthesize
        # the EXISTING artifact without re-querying any source (zero quota).
        # Optional operator guidance refines the triage (never invents a fit).
        cmd = [py, "run_picture.py", "--client", client]
        if args.get("guidance"):
            cmd += ["--guidance", args["guidance"]]
    elif step == "dossiers":
        # Deadline-aware depth on the pursue-grade shortlist (budgeted).
        cmd = [py, "run_dossiers.py", "--client", client]
    elif step == "qualify":
        cmd = [py, "run_qualify.py", "--client", client]
        if args.get("no_llm"):
            cmd.append("--no-llm")
    elif step == "contacts":
        cmd = [py, "run_target.py", "--client", client]
    elif step == "target_report":
        cmd = [py, "run_target_report.py", "--client", client]
    elif step == "report":
        kind = args.get("kind", "teaser")
        if kind == "capture_brief":
            # the Command Center press IS operator intent: PDF always, and
            # the release switch on — RELEASE only happens when zero flags
            # and consensus hold; anything else ships as watermarked DRAFT
            cmd = [py, "run_capture_brief.py", "--client", client,
                   "--pdf", "--release"]
            if args.get("compose_split"):
                cmd.append("--compose-split")
        else:
            cmd = [py, "run_report.py", "--client", client, "--kind", kind]
    elif step == "views":
        # Three-view assessment build (client/sales/internal). The watchlist
        # section inside it diffs this sweep against the previous snapshot; a
        # fresh sweep first (the searches step) gives "new since last week".
        cmd = [py, "run_views.py", "--client", client]
    elif step == "agency_report":
        # agency-focused pass: deterministic scope of the canonical sweep +
        # fresh agency-scoped market slice + gated client-view report
        cmd = [py, "run_agency_report.py", "--client", client,
               "--agency", args.get("agency") or ""]
    elif step == "recompete":
        # standing recompete calendar (L16): USASpending-only, zero SAM quota,
        # zero LLM; incumbents crosswalk-resolved, attack value decomposable
        cmd = [py, "run_recompete.py", "--client", client]
        if args.get("months"):
            cmd += ["--months", str(args["months"])]
    elif step == "change_digest":
        # Internal, deterministic comparison against the prior per-client
        # digest baseline. No model, network, or release gate is involved.
        cmd = [py, "-m", "agents.change_digest", "--client", client]
    elif step == "refresh_press":
        # Retention product: the stateful assessment adapter owns the C3
        # delegation so the exact seven-stage run also persists named
        # failure/resume history and the terminal DRAFT_READY transition.
        cmd = [py, "run_assessment.py", "--client", client, "--refresh"]
    elif step == "horizon_compose":
        # Developing Horizon draft (Max-plan compose over the fact bank).
        # Overwriting an existing draft is an explicit operator act.
        cmd = [py, "run_horizon.py", "--client", client]
        if args.get("recompose"):
            cmd.append("--recompose")
    elif step == "horizon_refine":
        # one round of the dialogue gate; the validator re-checks sources
        cmd = [py, "run_horizon.py", "--client", client,
               "--refine", args.get("instruction") or ""]
    elif step == "ranking_workbook":
        # Discernment session tool (2026-07-25): ranks the pressed
        # Pre-Assessment; deterministic, zero LLM, zero network.
        cmd = [py, "run_ranking_workbook.py", "--client", client]
    elif step == "candidate_review":
        # The ONE deliverable: the Federal Opportunity Pre-Assessment.
        # Watch generation + document press in a single zero-LLM process
        # (same-run as_of keeps the 24h current-notice window open). The
        # step id is deliberately NOT pre_assessment: that id already names
        # an existing artifact family in _report_rows/_status_banner.
        cmd = [py, "run_candidate_review.py", "--client", client,
               "--with-watch", "--live"]
        # ADDITIVE (2026-08-03): golden_only presses the golden deliverable
        # directly from the approved packet and unmetered lanes, skipping
        # the watch-generation requirement. The zero-SAM re-press path for
        # clients without a watch generation; existing calls are unchanged.
        if args.get("golden_only"):
            cmd = [py, "run_candidate_review.py", "--client", client,
                   "--golden-only"]
        # Run-scoped ONLY, and never a gate edit: the stored engagement scope
        # is read and left as found. The press labels the artifact family and
        # the Desktop filename so a wider-scope run cannot be mistaken for
        # the engagement's own deliverable.
        if args.get("scope_override"):
            cmd += ["--scope-override", str(args["scope_override"])]
    elif step == "intake":
        cmd = [py, "run_intake.py", "--submission", args["submission_path"]]
    else:
        raise ValueError(f"unknown step: {step}")
    return cmd


def _run_job(
    job_id: str,
    cmd: list[str],
    expected_client: Optional[str] = None,
    expected_workstation_id: Optional[str] = None,
    expected_packet_sha256: Optional[str] = None,
    expected_strategy_revision: Optional[int] = None,
    native_workstation: bool = False,
    expected_registry_sha256: Optional[str] = None,
    expected_receipt_sha256: Optional[str] = None,
) -> None:
    """Jobs write to a DISK-BACKED log, never a live pipe (2026-07-10):
    when the server restarts mid-job, a piped child dies of BrokenPipeError
    at its next print — the NETSCOUT report was killed twice this way. A
    file survives any number of server restarts; the console tails it."""
    log_dir = os.path.join(ROOT, "data", "state", "job_logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{job_id}.log")
    with open(log_path, "w", encoding="utf-8") as logf:
        try:
            # Every Control Room child receives an explicit environment, even
            # for an unbound legacy job.  The optional OpenAI collaborator is
            # process-local to this web server and must never leak into an
            # unrelated pipeline subprocess (including through an ambient
            # shell variable that predated server startup).
            child_env = os.environ.copy()
            child_env.pop("OPENAI_API_KEY", None)
            if expected_client is not None or expected_workstation_id is not None:
                if not expected_client or not expected_workstation_id:
                    raise ValueError("incomplete workstation job binding")
                child_env["LILA_EXPECT_CLIENT_NAME"] = expected_client
                child_env["LILA_EXPECT_WORKSTATION_ID"] = expected_workstation_id
                child_env["LILA_EXPECT_NATIVE_WORKSTATION"] = (
                    "1" if native_workstation else "0")
                if (expected_packet_sha256 is None) != (
                        expected_strategy_revision is None):
                    raise ValueError("incomplete strategy packet job binding")
                if native_workstation and expected_packet_sha256 is None:
                    raise ValueError(
                        "native job has no authoritative packet binding")
                if expected_packet_sha256 is not None:
                    child_env["LILA_EXPECT_PACKET_SHA256"] = (
                        expected_packet_sha256)
                    child_env["LILA_EXPECT_STRATEGY_REVISION"] = str(
                        expected_strategy_revision)
                if (expected_registry_sha256 is None) != (
                        expected_receipt_sha256 is None):
                    raise ValueError("incomplete native ownership job binding")
                if native_workstation:
                    if (expected_registry_sha256 is None
                            or expected_receipt_sha256 is None):
                        raise ValueError(
                            "native job has no authoritative ownership binding")
                    child_env["LILA_EXPECT_WORKSTATION_REGISTRY_SHA256"] = (
                        expected_registry_sha256)
                    child_env["LILA_EXPECT_WORKSTATION_RECEIPT_SHA256"] = (
                        expected_receipt_sha256)
                elif expected_registry_sha256 is not None:
                    raise ValueError(
                        "legacy job cannot carry native ownership binding")
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=logf,
                                    stderr=subprocess.STDOUT, text=True,
                                    env=child_env)
        except Exception as exc:  # noqa: BLE001 - no child exists; fail visibly
            now = _time.monotonic()
            line = (f"[control] process failed to start: {type(exc).__name__}: "
                    f"{str(exc)[:240]}")
            logf.write(line + "\n")
            logf.flush()
            with _JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["log_path"] = log_path
                    job["lines"].append(line)
                    job["status"] = "failed"
                    job["returncode"] = None
                    job["finished_at"] = _utc_timestamp()
                    job["_heartbeat_monotonic"] = now
                    job["_last_output_monotonic"] = now
                    job["_finished_monotonic"] = now
            return
        now = _time.monotonic()
        with _JOBS_LOCK:
            JOBS[job_id]["log_path"] = log_path
            JOBS[job_id]["_heartbeat_monotonic"] = now
        pos = 0
        while proc.poll() is None:
            now = _time.monotonic()
            with _JOBS_LOCK:
                JOBS[job_id]["_heartbeat_monotonic"] = now
            _time.sleep(0.5)
            with open(log_path, encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if chunk:
                with _JOBS_LOCK:
                    JOBS[job_id]["lines"].extend(chunk.rstrip("\n").split("\n"))
                    JOBS[job_id]["_last_output_monotonic"] = _time.monotonic()
        with open(log_path, encoding="utf-8") as f:
            f.seek(pos)
            chunk = f.read()
        if chunk:
            with _JOBS_LOCK:
                JOBS[job_id]["lines"].extend(chunk.rstrip("\n").split("\n"))
                JOBS[job_id]["_last_output_monotonic"] = _time.monotonic()
    now = _time.monotonic()
    with _JOBS_LOCK:
        JOBS[job_id]["status"] = "done" if proc.returncode == 0 else "failed"
        JOBS[job_id]["returncode"] = proc.returncode
        JOBS[job_id]["finished_at"] = _utc_timestamp()
        JOBS[job_id]["_heartbeat_monotonic"] = now
        JOBS[job_id]["_finished_monotonic"] = now


class JobRunning(RuntimeError):
    """A job for this client is already active; carries the running job id."""

    def __init__(self, job_id: str, step: str):
        super().__init__(f"job {job_id} ({step}) already running")
        self.job_id = job_id
        self.step = step


def start_job(step: str, client: str, args: dict) -> str:
    expected_workstation_id = args.get("workstation_id")
    expected_packet_sha256 = args.get("_lila_expected_packet_sha256")
    expected_strategy_revision = args.get("_lila_expected_strategy_revision")
    native_workstation = args.get("_lila_native_workstation", False)
    expected_registry_sha256 = args.get(
        "_lila_expected_workstation_registry_sha256")
    expected_receipt_sha256 = args.get(
        "_lila_expected_workstation_receipt_sha256")
    if not isinstance(native_workstation, bool):
        raise ValueError("invalid native workstation job binding")
    if expected_workstation_id is not None and (
            not isinstance(expected_workstation_id, str)
            or _slugify(expected_workstation_id) != expected_workstation_id
            or not (expected_workstation_id == "all"
                    or expected_workstation_id.startswith("agency_"))):
        raise ValueError("invalid workstation job binding")
    if (expected_packet_sha256 is None) != (
            expected_strategy_revision is None):
        raise ValueError("incomplete strategy packet job binding")
    if expected_packet_sha256 is not None:
        if (not isinstance(expected_packet_sha256, str)
                or len(expected_packet_sha256) != 64
                or any(char not in "0123456789abcdef"
                       for char in expected_packet_sha256)
                or not isinstance(expected_strategy_revision, int)
                or isinstance(expected_strategy_revision, bool)
                or expected_strategy_revision < 0):
            raise ValueError("invalid strategy packet job binding")
    if native_workstation and expected_packet_sha256 is None:
        raise ValueError("native job has no authoritative packet binding")
    if (expected_registry_sha256 is None) != (
            expected_receipt_sha256 is None):
        raise ValueError("incomplete native ownership job binding")
    for value in (expected_registry_sha256, expected_receipt_sha256):
        if value is not None and (
                not isinstance(value, str) or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)):
            raise ValueError("invalid native ownership job binding")
    if native_workstation and expected_registry_sha256 is None:
        raise ValueError("native job has no authoritative ownership binding")
    if not native_workstation and expected_registry_sha256 is not None:
        raise ValueError("legacy job cannot carry native ownership binding")
    if (native_workstation or expected_packet_sha256 is not None
            or expected_registry_sha256 is not None) \
            and expected_workstation_id is None:
        raise ValueError("bound job has no workstation id")
    if native_workstation and step != "searches":
        raise ValueError("native workstation jobs may run only searches")
    cmd = _step_cmd(step, client, args)
    job_id = uuid.uuid4().hex[:12]
    started = _time.monotonic()
    with _JOBS_LOCK:
        # ONE pipeline per client (2026-07-11): two concurrent jobs for the
        # same client race on the same artifact files (the DRAFT-html/
        # RELEASE-sidecar mispair). Refuse loudly with the running job id;
        # the operator decides whether to wait or investigate.
        for j in JOBS.values():
            if j.get("client") == client and j.get("status") == "running":
                raise JobRunning(j["id"], j.get("step") or "?")
        JOBS[job_id] = {"id": job_id, "step": step, "client": client,
                        "workstation_id": args.get("workstation_id"),
                        "cmd": " ".join(cmd), "status": "running",
                        "lines": [], "returncode": None,
                        "started_at": _utc_timestamp(), "finished_at": None,
                        "_started_monotonic": started,
                        "_heartbeat_monotonic": None,
                        "_last_output_monotonic": started,
                        "_finished_monotonic": None}
    thread_args = (
        job_id, cmd, client if expected_workstation_id else None,
        expected_workstation_id, expected_packet_sha256,
        expected_strategy_revision, native_workstation,
        expected_registry_sha256, expected_receipt_sha256,
    )
    threading.Thread(target=_run_job, args=thread_args, daemon=True).start()
    return job_id


# ----------------------------------------------------------------------------- #
# Routes
# ----------------------------------------------------------------------------- #
@app.get("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


# ui/client-view build (2026-07-24): serve the dark Command Center assets and
# the read-only client-view model. static_folder is None on this app, so the
# /static route is explicit. The model builder never writes and never spends.
@app.get("/static/<path:filename>")
def cc_static(filename):
    if ".." in filename:
        return "not found", 404
    return send_from_directory(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), filename)


@app.get("/api/client/<slug>/cc-model")
def api_cc_model(slug):
    """Read-only model for the dark client view (ui/static/client-view.js)."""
    if "/" in slug or ".." in slug:
        return "not found", 404
    from ui.static.cc_model import build_model
    try:
        model = build_model(slug)
    except Exception as exc:  # surface build errors without 500-ing the SPA
        return jsonify({"error": str(exc)}), 500
    if not model:
        return jsonify({"error": "no artifacts for this client yet"}), 404
    return jsonify(model)


@app.get("/api/source-network")
def api_source_network():
    """Read-only inventory for the operator-facing LILA research surface."""
    from ui.static.cc_model import build_sources
    return jsonify(build_sources(ROOT, {}))


@app.get("/api/client/<slug>/press-status")
def api_press_status(slug):
    """Truthful press rail source: live in-process job first, else the newest
    on-disk candidate-review log for the slug, READ-ONLY (never a fake stage).
    Poll cadence 2s while a press is live."""
    if "/" in slug or ".." in slug:
        return "not found", 404
    from ui.static.cc_model import press_status
    with _JOBS_LOCK:
        jobs = {jid: {"id": jid, "step": j.get("step"), "client": j.get("client"),
                      "status": j.get("status"), "log_path": j.get("log_path"),
                      "started_at": j.get("started_at")}
                for jid, j in JOBS.items()}
    try:
        status = press_status(slug, jobs=jobs)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    if not status:
        return jsonify({"error": "no press has run for this client"}), 404
    return jsonify(status)


@app.get("/client/<slug>")
def client_page(slug):
    """Deep link into one client. Serves the SPA — the INTERNAL view (the
    existing dashboard) is the default; the SPA reads the path and opens
    the client."""
    if "/" in slug or ".." in slug:
        return "not found", 404
    return index()


@app.get("/client/<slug>/workstation/<workstation_id>")
def client_workstation_page(slug, workstation_id):
    """Canonical deep link for one exact scope workstation."""
    if (not workstation_id or _slugify(workstation_id) != workstation_id
            or not (workstation_id == "all"
                    or workstation_id.startswith("agency_"))):
        return "not found", 404
    resolved, error = _workstation_route_error(slug)
    if error:
        return error
    _, catalog = resolved
    if not any(row.ref.id == workstation_id for row in catalog.workstations):
        return "not found", 404
    return index()


# client view law (2026-07-18): the sales view is retired from the UI;
# route preserved intact behind this flag for later resurrection.
SALES_VIEW_ENABLED = os.environ.get("LILA_SALES_VIEW", "") == "1"

# one deliverable law (2026-07-18): the Signal Board is the product; the
# capture-brief press is retired from the Command Center. The runner, its
# arbiters, and its tests stay intact; this flag resurrects the press.
CAPTURE_BRIEF_ENABLED = os.environ.get("LILA_CAPTURE_BRIEF", "") == "1"

# one-gate law (2026-07-18): the legacy per-stage workstation cockpit is
# curtained in the UI (SHOW_LEGACY_WORKSTATION in index.html). This flag is
# the declared pairing; it gates no route today because every /api/run step
# a curtained button posts (searches, picture) also serves a KEPT control
# (the review iterate loop) or the orchestrator's own path, and the
# approval routes are contract surfaces left untouched. Recorded here so
# the resurrection switch has one canonical name.
LEGACY_WORKSTATION_ENABLED = os.environ.get(
    "LILA_LEGACY_WORKSTATION", "") == "1"


@app.get("/client/<slug>/sales")
def client_sales_page(slug):
    """SALES view of the same client dashboard: the teaser rendering, built
    server-side from the GATED model (gate_for_sales) exactly like the
    standalone sales document — a template bug here cannot leak paid
    content because the model it renders physically lacks it."""
    if not SALES_VIEW_ENABLED:
        return ("sales view is retired from the UI (client view law, "
                "2026-07-18); set LILA_SALES_VIEW=1 to resurrect"), 404
    if "/" in slug or ".." in slug:
        return "not found", 404
    import html as _html
    try:
        state = client_state(slug)
    except OSError:
        return "no such client", 404
    name = _html.escape(state.get("client_name", slug))
    from agents.reports.views import house_css, render_sales_teaser_body

    doc = _assessment_doc(slug)
    if doc is None:
        body = ('<div class="v-note" style="margin:30px 0;font-size:14px">No assessment has '
                'run for this client yet — run the pipeline from the internal view first.</div>')
    else:
        from agents.reports.views import render_scoreboard
        body = render_scoreboard(doc, "sales") + render_sales_teaser_body(doc)
    page = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} · Sales View</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{house_css()}
.v-mode{{position:sticky;top:0;z-index:30;display:flex;gap:14px;align-items:center;
  background:var(--navy,#16283f);color:#f6f4ef;padding:9px 16px;margin:0 0 18px;
  font-family:var(--mono);font-size:11px;letter-spacing:.1em}}
.v-mode a{{color:#9db2c9;text-decoration:none}}
.v-mode a:hover{{color:#fff}}
.v-mode b{{color:#fff}}
.v-mode .vm-note{{margin-left:auto;color:#9db2c9;letter-spacing:.04em;text-transform:none}}
@media print{{.v-mode{{display:none !important}}}}
</style></head><body><div class="page view-sales">
<div class="v-mode">VIEW · <a href="/client/{slug}">INTERNAL</a> · <b>SALES</b>
<span class="vm-note">the teaser: everything verifiable, nothing actionable</span>
<a class="vm-dl" href="/client/{slug}/download/teaser.html"
   style="background:#f6f4ef;color:#16283f;padding:5px 12px;font-weight:700;text-decoration:none">
   Download HTML</a>
<button id="vmPdf" style="background:transparent;color:#f6f4ef;border:1px solid #9db2c9;
   padding:4px 12px;font-family:inherit;font-size:inherit;letter-spacing:inherit;cursor:pointer">PDF</button>
<span id="vmMsg"></span></div>
<script>
document.getElementById('vmPdf').onclick = async () => {{
  const m = document.getElementById('vmMsg');
  m.textContent = 'exporting (gates first) …';
  try {{
    const r = await fetch('/api/export/pdf', {{ method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ slug: '{slug}', kind: 'teaser' }}) }});
    const j = await r.json();
    if (r.ok && j.pdf) {{ m.textContent = ''; window.open('/report?path=' + encodeURIComponent(j.pdf), '_blank'); }}
    else m.textContent = (j.do_not_send ? 'DO NOT SEND · no PDF: ' : '') + (j.error || 'failed');
  }} catch (e) {{ m.textContent = 'failed: ' + e; }}
}};
</script>
<div class="v-headline" style="margin-top:4px"><strong>{name}</strong> · Federal Opportunity Assessment · Preview</div>
{body}
</div></body></html>"""
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/api/clients")
def api_clients():
    return jsonify(all_clients())


@app.get("/api/depository")
def api_depository():
    """Front page payload: every client with pipeline status AND deliverables.
    The home page is the main depository, not just an attention queue."""
    from ui.static.cc_model import list_meta
    out = []
    for c in all_clients():
        if not c.get("error"):
            docs = client_documents(c["slug"], c.get("client_name", ""))
            c["documents"] = docs[:8]
            c["doc_total"] = len(docs)
            # dark client-list restyle (ui/client-view): capability line +
            # records/pressed line, read-only from the same artifacts the
            # client view binds. Never fails the depository.
            try:
                c["cc_meta"] = list_meta(c["slug"])
            except Exception:  # noqa: BLE001
                c["cc_meta"] = None
        out.append(c)
    return jsonify(out)


@app.post("/api/clients/reorder")
def api_clients_reorder():
    order = (request.get_json(force=True) or {}).get("order") or []
    _save_client_order(order)
    return jsonify({"ok": True, "order": order})


@app.post("/api/clients/archive")
def api_clients_archive():
    """Remove a client from the board WITHOUT destroying anything: every
    artifact moves to data/archive/<slug>/ (timestamped on collision)."""
    import shutil as _shutil
    import time as _t
    slug = (request.get_json(force=True) or {}).get("slug", "")
    if not slug or "/" in slug or ".." in slug:
        return jsonify({"error": "bad slug"}), 400
    packet = _load_json(os.path.join(REVIEW_DIR, f"{slug}.review.json")) or {}
    client_name = packet.get("client_name") or slug
    root = os.path.dirname(REVIEW_DIR)
    dest = os.path.join(root, "archive", slug)
    if os.path.exists(dest):
        dest += "-" + _t.strftime("%Y%m%d%H%M%S")
    os.makedirs(dest, exist_ok=True)
    moved = 0
    for sub in ("review", "cleaned", "reports", "handoff"):
        d = os.path.join(root, sub)
        for p in glob.glob(os.path.join(d, f"*{slug}*")):
            base = os.path.basename(p)
            # exact-slug guard: 'acme' must not sweep up 'acme_corp' artifacts
            stem = base.split(".")[0]
            if stem not in (slug, f"searches_{slug}"):
                continue
            _shutil.move(p, os.path.join(dest, f"{sub}__{base}"))
            moved += 1
    from agents.assess.ledger import assess_client_storage_key
    ledger_dir = os.path.join(
        ASSESS_RUN_DIR, assess_client_storage_key(client_name))
    if os.path.isdir(ledger_dir):
        _shutil.move(
            ledger_dir,
            os.path.join(dest, f"state__assess_runs__{os.path.basename(ledger_dir)}"))
        moved += 1
    _save_client_order([s for s in _client_order() if s != slug])
    return jsonify({"ok": True, "moved": moved, "archived_to": dest})


def _assessment_doc(slug: str, *, searches: Optional[dict] = None,
                    workstation_id: Optional[str] = None):
    """AssessmentDocument for the dashboard — deterministic joins only, no LLM
    compose. None when no sweep artifact exists (nothing to assess yet)."""
    searches = searches if searches is not None else _load_json(
        os.path.join(CLEANED_DIR, _sweep_name(
            slug, workstation_id=workstation_id)))
    if not isinstance(searches, dict) or not searches.get("results"):
        return None
    from agents.reports.document import build_document
    qualify = _load_json(os.path.join(REVIEW_DIR, f"{slug}.qualify.json"))
    name = searches.get("client") or slug
    # A GET is an observation, not entity-telemetry ingestion.  Rendering the
    # dashboard must never update unresolved.log merely because the operator
    # switched workstations.
    return build_document(
        name, searches=searches, qualify=qualify,
        _log_unresolved_entities=False)


def _final_brief(slug: str, foa: Optional[dict] = None) -> Optional[dict]:
    """FINAL PRODUCT state, straight from the release resolver: the badge and
    the download boundary now consume the SAME verdict (approval folded in),
    so CLIENT READY can never contradict a 409."""
    rs = foa or release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    if rs["preview_path"] is None:
        return None
    return {"path": _rel(rs["preview_path"]), "qa_pass": rs["releasable"],
            "reason": rs["reason"], "approval_status": rs["approval_status"],
            "updated": rs["updated"]}


def _signal_board(slug: str, foa: Optional[dict] = None) -> Optional[dict]:
    """Canonical Signal Board state for Command Center surfaces.

    Legacy assessment families remain readable in the documents shelf, but
    they can never drive the CLIENT READY badge after the one-deliverable
    cutover.  Only the release resolver's Signal Board family is admitted.
    """
    rs = foa or release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    if rs.get("family") not in {"signal_board", "signal_board_bad"}:
        return None
    path = rs.get("preview_path")
    if not path:
        return None
    return {
        "path": _rel(path),
        "qa_pass": bool(rs.get("releasable")
                        and rs.get("family") == "signal_board"),
        "reason": rs.get("reason") or "release state unavailable",
        "approval_status": rs.get("approval_status"),
        "updated": rs.get("updated") or 0.0,
        "family": rs.get("family"),
    }


def _safe_client_display_name(client_name: str) -> str:
    """Presentation metadata must never replace or break exact identity."""
    try:
        from tools.capability import client_display_name
        return client_display_name(client_name)
    except Exception:  # noqa: BLE001 - a broken optional label falls back safely
        return client_name


def _client_payload(slug: str, *, workstation_id: Optional[str] = None,
                    packet: Optional[dict] = None,
                    searches: Optional[dict] = None) -> dict:
    """Legacy-current operating payload, assembled without HTTP concerns."""
    if packet is None and searches is None and workstation_id is None:
        state = client_state(slug)
        state["steps"] = build_steps(slug)
    else:
        state = client_state(
            slug, packet=packet, workstation_id=workstation_id)
        state["steps"] = build_steps(
            slug, packet=packet, searches=searches,
            workstation_id=workstation_id)
    state["display_name"] = _safe_client_display_name(state["client_name"])
    # ONE resolver verdict per request: badge, shelf, and downloads all read
    # the same release_state (agents/reports/release.py), never re-derive
    foa = release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    # The legacy endpoint keeps its client-global shelf.  Only the additive
    # workstation route passes an exact id and receives an exact family.
    current_workstation_id = workstation_id
    state["documents"] = client_documents(
        slug, state.get("client_name", ""), foa=foa,
        workstation_id=current_workstation_id)
    prefs = (_doc_prefs(slug) if workstation_id is None
             else _doc_prefs(slug, workstation_id))
    state["docs_hidden"] = len(prefs["hidden"])
    state["final_brief"] = _final_brief(slug, foa=foa)
    # The canonical deliverable has its own field so no legacy assessment can
    # accidentally light CLIENT READY in the Command Center.
    state["signal_board"] = _signal_board(slug, foa=foa)
    # the assessment unlocks after the opportunity search + review (qualify is
    # internal and the assessment degrades gracefully without it)
    state["assess_ready"] = (state["status"] == "approved"
                             and state["stages"]["searched"])
    # The human gate is effective only for the exact current scope, sweep, and
    # capability profile. A legacy/stale approval remains auditable on disk but
    # cannot activate Produce.
    _, approval_status, approval_problems = _assess_release_gate(
        state["client_name"])
    state["review_approved"] = approval_status == "approved"
    state["review_approval_status"] = approval_status
    state["review_approval_problems"] = approval_problems
    state["assess_ledger"] = _assess_ledger_snapshot(state["client_name"])
    from agents.review import target_gate_status as _tgs
    state["target_approved"], state["target_problems"] = _tgs(
        state["client_name"], review_dir=REVIEW_DIR)
    # client-scoped sidebar: ONLY this client's targets and artifacts
    doc = (_assessment_doc(slug)
           if searches is None and workstation_id is None
           else _assessment_doc(
               slug, searches=searches, workstation_id=workstation_id))
    state["sidebar"] = {
        "targets": [{"rank": p.rank, "title": p.title, "agency": p.agency,
                     "grade": p.grade.letter, "deadline": p.response_deadline,
                     "dossier": bool(p.dossier)}
                    for p in doc.board.pursuits] if doc else [],
        "artifacts": [{"label": d["label"], "path": d["path"], "fmt": d["fmt"],
                       "qa_pass": d["qa_pass"]} for d in state["documents"][:12]],
    }
    return state


@app.get("/api/client/<slug>/workstations")
def api_client_workstations(slug):
    """Additive, read-only catalog for the dominant scope switcher."""
    resolved, error = _workstation_route_error(slug)
    if error:
        return error
    _, catalog = resolved
    return _no_store(catalog.model_dump(mode="json"))


@app.post("/api/client/<slug>/workstations")
def api_create_client_workstation(slug):
    """Explicit creation door; it never decides, searches, or adopts history."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _no_store({"error": "request body must be a JSON object"}, 400)
    if set(body) != {"scope", "clone_baseline"}:
        return _no_store({
            "error": "scope and boolean clone_baseline are the only fields",
        }, 400)
    if not isinstance(body.get("clone_baseline"), bool):
        return _no_store({"error": "clone_baseline must be boolean"}, 400)
    if body["clone_baseline"] is False:
        return _no_store({
            "error": ("Tranche 3 scope creation requires the client baseline "
                      "strategy; no-clone creation is not available here"),
        }, 409)
    try:
        scope = canonical_scope(body.get("scope"))
    except WorkstationError as exc:
        return _no_store({"error": str(exc)}, 400)
    resolved, error = _workstation_route_error(slug)
    if error:
        return error
    exact, _catalog = resolved
    try:
        result = create_workstation(
            exact, scope, review_dir=REVIEW_DIR,
            clone_baseline=body["clone_baseline"],
            created_from="operator")
        catalog = discover_workstations(
            exact, review_dir=REVIEW_DIR, cleaned_dir=CLEANED_DIR,
            report_dir=REPORT_DIR)
    except WorkstationError as exc:
        return _no_store({"error": str(exc)}, 409)
    selected = next(
        (row for row in catalog.workstations if row.ref.id == result.ref.id),
        None)
    if selected is None:  # a committed row must immediately be addressable
        return _no_store({
            "error": "created workstation is not readable; reload",
        }, 409)
    location = f"/client/{slug}/workstation/{result.ref.id}"
    return _no_store({
        "created": result.created,
        "workstation": selected.model_dump(mode="json"),
        "location": location,
    }, 201 if result.created else 200)


@app.get("/api/client/<slug>/workstation/<workstation_id>")
def api_client_workstation(slug, workstation_id):
    """One exact workstation context; dormant rows are a strict whitelist."""
    if (not workstation_id or _slugify(workstation_id) != workstation_id
            or not (workstation_id == "all"
                    or workstation_id.startswith("agency_"))):
        return _no_store({"error": "invalid workstation id"}, 400)
    resolved, error = _workstation_route_error(slug)
    if error:
        return error
    _, catalog = resolved
    selected = next(
        (row for row in catalog.workstations if row.ref.id == workstation_id),
        None,
    )
    if selected is None:
        return _no_store({"error": "no such workstation"}, 404)

    row_payload = selected.model_dump(mode="json")
    switcher = [row.model_dump(mode="json") for row in catalog.workstations]
    foundation_documents = client_foundation_documents(slug)
    if selected.is_native:
        include_sweep = selected.phase == "assess"
        try:
            packet, searches, tokens, strategy_packet_sha256 = _bound_native_snapshot(
                slug, catalog.client_name, selected,
                include_sweep=include_sweep)
        except WorkstationError as exc:
            return _no_store({
                "error": str(exc),
                "workstation_id": selected.ref.id,
            }, 409)
        foundation = client_state(
            slug, packet=packet, workstation_id=selected.ref.id)
    elif selected.is_legacy_current:
        strategy_packet_sha256 = None
        include_sweep = selected.phase in {"assess", "produce", "target"}
        try:
            packet, searches, tokens = _bound_legacy_snapshot(
                slug, catalog.client_name, selected,
                include_sweep=include_sweep)
        except WorkstationError as exc:
            return _no_store({
                "error": str(exc),
                "workstation_id": selected.ref.id,
            }, 409)
        foundation = client_state(
            slug, packet=packet, workstation_id=selected.ref.id)
    else:
        strategy_packet_sha256 = None
        packet, searches, tokens = None, None, ()
        foundation = client_state(slug)

    if ((selected.is_legacy_current or selected.is_native)
            and selected.phase == "configure"):
        state = {
            "slug": slug,
            "client_name": catalog.client_name,
            "website": foundation.get("website"),
            "logo_domain": foundation.get("logo_domain"),
            "status": packet.get("status", "pending"),
            "workstation_mode": (
                "native-early" if selected.is_native
                else "legacy-current-early"),
            "workstation": row_payload,
            "workstations": switcher,
            "strategy_packet_sha256": strategy_packet_sha256,
            "steps": _early_workstation_steps(
                slug, packet, workstation_id=selected.ref.id,
                native=selected.is_native),
            "foundation_documents": foundation_documents,
            "documents": [],
            "sidebar": {"targets": [], "artifacts": []},
            "target_approved": False,
        }
        if not _snapshot_is_unchanged(tokens):
            return _no_store({
                "error": "workstation binding changed during navigation; reload",
                "workstation_id": selected.ref.id,
            }, 409)
        return _no_store(state)

    if ((selected.is_legacy_current or selected.is_native)
            and selected.phase == "search"
            and selected.sweep_status != "invalid"):
        state = {
            "slug": slug,
            "client_name": catalog.client_name,
            "website": foundation.get("website"),
            "logo_domain": foundation.get("logo_domain"),
            "status": packet.get("status", "approved"),
            "workstation_mode": (
                "native-early" if selected.is_native
                else "legacy-current-early"),
            "workstation": row_payload,
            "workstations": switcher,
            "strategy_packet_sha256": strategy_packet_sha256,
            "steps": _early_workstation_steps(
                slug, packet, workstation_id=selected.ref.id,
                native=selected.is_native),
            "foundation_documents": foundation_documents,
            "documents": [],
            "sidebar": {"targets": [], "artifacts": []},
            "target_approved": False,
        }
        if not _snapshot_is_unchanged(tokens):
            return _no_store({
                "error": "workstation binding changed during navigation; reload",
                "workstation_id": selected.ref.id,
            }, 409)
        return _no_store(state)

    if selected.is_native and selected.phase == "assess":
        state = {
            "slug": slug,
            "client_name": catalog.client_name,
            "website": foundation.get("website"),
            "logo_domain": foundation.get("logo_domain"),
            "status": packet.get("status", "approved"),
            "workstation_mode": "native-early",
            "workstation": row_payload,
            "workstations": switcher,
            "strategy_packet_sha256": strategy_packet_sha256,
            "steps": _early_workstation_steps(
                slug, packet, workstation_id=selected.ref.id,
                searches=searches, native=True),
            "foundation_documents": foundation_documents,
            "documents": [],
            "sidebar": {"targets": [], "artifacts": []},
            "target_approved": False,
        }
        if not _snapshot_is_unchanged(tokens):
            return _no_store({
                "error": "workstation binding changed during navigation; reload",
                "workstation_id": selected.ref.id,
            }, 409)
        return _no_store(state)

    if selected.is_legacy_current and selected.sweep_status == "invalid":
        # A filename exists but its embedded client/scope binding failed.
        # Never put the legacy downstream pipeline behind a new trustworthy
        # workstation label: that would surface another scope's approvals,
        # reports, and Target state under this identity.
        state = {
            "slug": slug,
            "client_name": catalog.client_name,
            "website": foundation.get("website"),
            "logo_domain": foundation.get("logo_domain"),
            "workstation_mode": "legacy-current-held",
            "workstation": row_payload,
            "workstations": switcher,
            "foundation_documents": foundation_documents,
            "documents": [],
            "sidebar": {"targets": [], "artifacts": []},
            "target_approved": False,
        }
        if not _snapshot_is_unchanged(tokens):
            return _no_store({
                "error": "workstation binding changed during navigation; reload",
                "workstation_id": selected.ref.id,
            }, 409)
        return _no_store(state)
    if selected.is_legacy_current:
        state = _client_payload(
            slug, workstation_id=selected.ref.id,
            packet=packet, searches=searches)
        foundation_paths = {
            row["path"] for row in foundation_documents if row.get("path")}
        if isinstance(state.get("documents"), list):
            state["documents"] = [
                row for row in state["documents"]
                if not (isinstance(row, dict)
                        and row.get("path") in foundation_paths)
            ]
        state.update({
            "workstation_mode": "legacy-current",
            "workstation": row_payload,
            "workstations": switcher,
            "foundation_documents": foundation_documents,
        })
        if not _snapshot_is_unchanged(tokens):
            return _no_store({
                "error": "workstation binding changed during navigation; reload",
                "workstation_id": selected.ref.id,
            }, 409)
        return _no_store(state)

    # Compatibility tranches may display a registered or product-default
    # dormant identity, but they may not borrow the legacy-current strategy,
    # approvals, evidence, reports, targets, or sidebar.  Whitelist only the
    # client-global identity needed to render the shell.
    state = {
        "slug": slug,
        "client_name": catalog.client_name,
        "website": foundation.get("website"),
        "logo_domain": foundation.get("logo_domain"),
        "workstation_mode": "dormant",
        "workstation": row_payload,
        "workstations": switcher,
        "foundation_documents": foundation_documents,
        "documents": [],
        "sidebar": {"targets": [], "artifacts": []},
        "target_approved": False,
    }
    return _no_store(state)


@app.get("/api/client/<slug>")
def api_client(slug):
    return jsonify(_client_payload(slug))


# ── downloads: first-class controls ─────────────────────────────────────────
# RECONCILIATION (2026-07-06): the internal dashboard is the WORKING SURFACE,
# but its "Download HTML — Federal Opportunity Assessment" control exports
# the full-detail, gates-passed DELIVERABLE — the client-view artifact — not
# the DO-NOT-SEND-stamped internal working document. The CLIENT view enum
# member exists precisely for this: it is the deliverable rendering, even
# though no client dashboard mode exists. A stamped-only state is refused
# and surfaces as DO-NOT-SEND in the dashboard.

def _attachment(html: str, filename: str):
    return html, 200, {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }


def _deliverable_artifact(slug: str, kind: str) -> tuple[Optional[str], Optional[str]]:
    """(clean_path, stamped_path) for a deliverable kind: foa | teaser."""
    # L19: downloads follow the gate — scoped engagements resolve the
    # scoped artifact family; scope=all resolves the unqualified names
    if kind == "foa":
        rs = release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
        if rs["preview_path"] is None:
            return None, None
        return rs["path"], rs["blocked_path"]
    else:
        names = [_stem(slug, "assessment") + ".sales.html"]
        stamped_names = [
            _stem(slug, "assessment") + ".sales.DO-NOT-SEND.html"]
    clean = [os.path.join(REPORT_DIR, n) for n in names
             if os.path.exists(os.path.join(REPORT_DIR, n))]
    stamped = [os.path.join(REPORT_DIR, n) for n in stamped_names
               if os.path.exists(os.path.join(REPORT_DIR, n))]
    newest_clean = max(clean, key=os.path.getmtime) if clean else None
    newest_stamped = max(stamped, key=os.path.getmtime) if stamped else None
    # A failed rebuild supersedes an older clean artifact. Returning that old
    # file would let the download boundary hide the latest DO-NOT-SEND result.
    if newest_stamped and (newest_clean is None
                           or os.path.getmtime(newest_stamped)
                           >= os.path.getmtime(newest_clean)):
        return None, newest_stamped
    return newest_clean, newest_stamped


def _dns(msg: str):
    return jsonify({"error": msg, "do_not_send": True}), 409


def _targeting_download_gate(slug: str):
    """Fail closed before any client-facing attachment leaves the server.

    Target unlock is only permission to work the lane. A download additionally
    requires a current Targeting Review receipt bound to the live target set and
    operator-authored plan.
    """
    payload, status = _client_targets_payload(slug)
    readiness = ((payload or {}).get("targeting_readiness") or {})
    if status == 200 and readiness.get("ready") is True:
        return None
    return _dns(
        "a current, complete Targeting Review is required before any "
        "client-ready attachment can be downloaded: "
        + "; ".join(readiness.get("problems") or [
            "current target inventory is unavailable"
        ])
    )


CLIENT_LINK_CHECKS_ENABLED = True


def _audit_client_download_links(
    html: str,
    *,
    enabled: bool,
    expected_federal_links,
):
    """Injectable wrapper around the shared external-link release gate."""
    from agents.reports.link_integrity import run_client_link_gate
    return run_client_link_gate(
        html, enabled=enabled,
        expected_federal_links=expected_federal_links)


def _gate_client_download_links(
    html: str,
    *,
    link_checks_enabled: Optional[bool] = None,
    sidecar_stem: Optional[str] = None,
    expected_federal_links=None,
    normalize_workspace_links: bool = True,
):
    """Normalize and gate the exact HTML bytes returned to a client.

    Deterministic workspace/construction failures short-circuit before any
    network work.  UNVERIFIABLE external checks are warning-only by contract;
    only the outcome's hard violations are returned here.
    """
    from agents.reports.lint import (
        LintViolation, lint_federal_link_construction,
        lint_sam_workspace_links,
    )
    from agents.reports.links import (
        format_workspace_link_issue, normalize_sam_workspace_links,
    )
    if normalize_workspace_links:
        normalized = normalize_sam_workspace_links(html)
        rendered = normalized.html
        violations = [LintViolation(
            rule="sam_workspace_link",
            detail=format_workspace_link_issue(issue),
            excerpt=issue.url,
        ) for issue in normalized.remaining]
    else:
        # A certified Signal Board is immutable at download: normalization
        # would make the returned bytes differ from the certified SHA.
        rendered = html
        violations = list(lint_sam_workspace_links(rendered).violations)
    violations.extend(
        lint_federal_link_construction(
            rendered, expected_links=expected_federal_links).violations)
    if violations:
        return rendered, violations
    enabled = (CLIENT_LINK_CHECKS_ENABLED
               if link_checks_enabled is None else link_checks_enabled)
    outcome = _audit_client_download_links(
        rendered, enabled=enabled,
        # This boundary has no structured pre-render model.  Rendered data
        # attributes are not allowed to attest themselves, so federal SPA
        # links fail closed until a builder-aware runner hands off a manifest.
        expected_federal_links=(expected_federal_links
                                if expected_federal_links is not None else ()))
    if sidecar_stem:
        sidecar = os.path.join(
            REPORT_DIR, f"{sidecar_stem}.link-integrity.internal.md")
        try:
            if outcome.manual_checks or outcome.claim_warnings:
                from agents.reports.link_integrity import (
                    render_link_integrity_internal_md,
                )
                from tools.atomic_io import atomic_write_text
                atomic_write_text(
                    sidecar,
                    render_link_integrity_internal_md(sidecar_stem, outcome),
                )
            elif os.path.exists(sidecar):
                os.remove(sidecar)
        except OSError as exc:
            app.logger.warning("link sidecar unavailable: %s", exc)
    return rendered, list(outcome.violations)


def _client_link_failure(violations) -> str:
    return "; ".join(
        f"{violation.rule}: {violation.detail}"
        for violation in violations[:5]
    )


def _signal_board_certificate(slug: str, *, path: str, html: str,
                              html_digest: str):
    """Validate and materialize the trusted federal-link hand-off.

    Rendered data attributes never attest themselves.  Only occurrence rows
    in the independent, exact-hash QA certificate become expected links at
    the download boundary, and their order/multiplicity must match the HTML.
    """
    from agents.reports.link_integrity import extract_link_occurrences
    from agents.reports.links import (
        CanonicalFederalLink, SAM_NOTICE_BUILDER,
        USASPENDING_AWARD_BUILDER, federal_link_domain,
    )

    qa_path = path[:-len(".html")] + ".qa.json"
    qa = _load_json(qa_path)
    if not isinstance(qa, dict):
        raise ValueError("Signal Board QA certificate is missing or unreadable")
    try:
        from agents.reports.signal_board_release import (
            validate_certificate_schema,
        )
        validate_certificate_schema(qa)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Signal Board QA certificate schema is invalid: {exc}"
        ) from exc
    exact_client = canonical_client_name(slug, REVIEW_DIR)
    if _slugify(exact_client) != slug:
        raise ValueError("Signal Board URL slug does not match the review client")
    try:
        from tools.capability import client_display_name
        presentation_name = client_display_name(exact_client)
    except Exception as exc:  # noqa: BLE001 - release identity fails closed
        raise ValueError(
            "Signal Board presentation identity is invalid: "
            f"{type(exc).__name__}: {str(exc)[:120]}"
        ) from exc
    expected_title = (
        f"<title>{html_lib.escape(presentation_name)} · "
        "Federal Opportunity Pre-Assessment</title>"
    )
    required_lints = {
        "signal_board", "sam_workspace_links",
        "federal_link_construction", "whitelabel",
        "client_bleed", "emdash",
    }
    lints = qa.get("static_lints")
    if not (
        qa.get("schema_version") == 2
        and qa.get("client_name") == exact_client
        and qa.get("presentation_name") == presentation_name
        and qa.get("slug") == slug
        and qa.get("state") == "release"
        and qa.get("release_eligible") is True
        and qa.get("gate_verdict") == "- CLEAN (client-final)"
        and qa.get("html_sha256") == html_digest
        and isinstance(qa.get("presentation_sha256"), str)
        and html.count(expected_title) == 1
        and isinstance(lints, dict)
        and set(lints) == required_lints
        and all(isinstance(row, dict) and row.get("ok") is True
                for row in lints.values())
    ):
        raise ValueError("Signal Board QA certificate does not authorize these bytes")
    try:
        if os.stat(qa_path).st_mtime_ns < os.stat(path).st_mtime_ns:
            raise ValueError("Signal Board QA certificate is stale")
        from agents.reports.signal_board_presentation import presentation_digest
        if qa["presentation_sha256"] != presentation_digest(
                exact_client, root=ROOT):
            raise ValueError(
                "presentation marks changed after the certified build; "
                "refresh the Federal Opportunity Pre-Assessment")
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Signal Board certificate freshness is unavailable: {exc}") from exc

    manifest = qa.get("federal_link_manifest")
    if not isinstance(manifest, list):
        raise ValueError("Signal Board federal-link manifest is missing")
    allowed_builders = {SAM_NOTICE_BUILDER, USASPENDING_AWARD_BUILDER}
    expected = []
    normalized_manifest = []
    for row in manifest:
        if not (
            isinstance(row, dict)
            and set(row) == {"url", "builder", "record_id", "reconciled"}
            and isinstance(row.get("url"), str)
            and federal_link_domain(row["url"]) is not None
            and row.get("builder") in allowed_builders
            and isinstance(row.get("record_id"), str)
            and bool(row.get("record_id"))
            and row.get("reconciled") is True
        ):
            raise ValueError("Signal Board federal-link manifest is malformed")
        normalized_manifest.append(row)
        expected.append(CanonicalFederalLink(
            url=row["url"], builder=row["builder"],
            record_id=row["record_id"], reconciled=True))

    actual = [
        {"url": occurrence.url, "builder": occurrence.builder,
         "record_id": occurrence.record_id,
         "reconciled": occurrence.reconciled}
        for occurrence in extract_link_occurrences(html)
        if federal_link_domain(occurrence.url) is not None
    ]
    if actual != normalized_manifest:
        raise ValueError(
            "Signal Board federal-link manifest does not match rendered occurrences")
    return tuple(expected)


def _download_signal_board(slug: str, *, rs: Optional[dict] = None):
    if "/" in slug or ".." in slug:
        return "not found", 404
    state = rs or release_state(
        slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    if state.get("family") not in {"signal_board", "signal_board_bad"}:
        return jsonify({"error": "no Signal Board built yet — refresh it first"}), 404
    if not state.get("releasable") or state.get("family") != "signal_board":
        return _dns(state.get("reason") or "Signal Board is not releasable")
    targeting_block = _targeting_download_gate(slug)
    if targeting_block is not None:
        return targeting_block
    clean = state.get("path")
    if not isinstance(clean, str) or not clean:
        return _dns("Signal Board release state carries no artifact path")
    try:
        html_bytes = Path(clean).read_bytes()
        html = html_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _dns(f"Signal Board artifact is unreadable: {exc}")
    digest = hashlib.sha256(html_bytes).hexdigest()
    try:
        expected_links = _signal_board_certificate(
            slug, path=clean, html=html, html_digest=digest)
    except ValueError as exc:
        return _dns(str(exc))
    html, link_failures = _gate_client_download_links(
        html,
        sidecar_stem=f"{slug}.download.signal-board",
        expected_federal_links=expected_links,
        normalize_workspace_links=False,
    )
    if link_failures:
        return _dns("client link gate failed: "
                    + _client_link_failure(link_failures))
    from datetime import date as _d
    return _attachment(
        html,
        f"{slug}_Federal_Opportunity_Pre-Assessment_"
        f"{_d.today().isoformat()}.html",
    )


@app.get("/client/<slug>/download/signal-board.html")
def download_signal_board(slug):
    """The canonical, hash-certified Signal Board HTML."""
    return _download_signal_board(slug)


@app.get("/client/<slug>/download/foa.html")
def download_foa(slug):
    """Compatibility URL for the current gates-passed deliverable."""
    if "/" in slug or ".." in slug:
        return "not found", 404
    rs = release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    if rs.get("family") in {"signal_board", "signal_board_bad"}:
        return _download_signal_board(slug, rs=rs)
    if rs["preview_path"] is None:
        return jsonify({"error": "no assessment built yet — produce it first"}), 404
    if not rs["releasable"]:
        return _dns(rs["reason"])
    targeting_block = _targeting_download_gate(slug)
    if targeting_block is not None:
        return targeting_block
    clean = rs["path"]
    with open(clean, encoding="utf-8") as f:
        html = f.read()
    html, link_failures = _gate_client_download_links(
        html, sidecar_stem=f"{slug}.download.foa")
    if link_failures:
        return _dns("client link gate failed: "
                    + _client_link_failure(link_failures))
    from datetime import date as _d
    return _attachment(html, f"{slug}{('_' + _gate_designator(slug).replace('agency_', '').upper()) if _gate_designator(slug) else ''}_Federal_Opportunity_Assessment_{_d.today().isoformat()}.html")


@app.get("/client/<slug>/download/teaser.html")
def download_teaser(slug):
    """The sales teaser: the artifact when the pipeline built one (composed
    prose, gated at build), else a live render from the gated model that
    must pass the full gate stack before it leaves."""
    if "/" in slug or ".." in slug:
        return "not found", 404
    from datetime import date as _d
    clean, stamped = _deliverable_artifact(slug, "teaser")
    if clean:
        targeting_block = _targeting_download_gate(slug)
        if targeting_block is not None:
            return targeting_block
        with open(clean, encoding="utf-8") as f:
            html, link_failures = _gate_client_download_links(
                f.read(), sidecar_stem=f"{slug}.download.teaser")
        if link_failures:
            return _dns("client link gate failed: "
                        + _client_link_failure(link_failures))
        return _attachment(html, f"{slug}_Federal_Opportunity_Preview_{_d.today().isoformat()}.html")
    if stamped:
        return _dns("the sales view on file is stamped DO-NOT-SEND; rebuild the views first")
    doc = _assessment_doc(slug)
    if doc is None:
        return jsonify({"error": "no assessment has run for this client yet"}), 404
    from agents.reports.views import render_assessment
    from tools.export_pdf import run_gates
    html = render_assessment(doc, "sales")
    html, link_failures = _gate_client_download_links(
        html, sidecar_stem=f"{slug}.download.teaser")
    if link_failures:
        return _dns("client link gate failed: "
                    + _client_link_failure(link_failures))
    violations = run_gates(html)
    if violations:
        return _dns("live teaser render failed the gate stack: "
                    + "; ".join(v.rule for v in violations[:5]))
    targeting_block = _targeting_download_gate(slug)
    if targeting_block is not None:
        return targeting_block
    return _attachment(html, f"{slug}_Federal_Opportunity_Preview_{_d.today().isoformat()}.html")


@app.get("/client/<slug>/download/dossier/<int:rank>.html")
def download_dossier(slug, rank):
    if "/" in slug or ".." in slug:
        return "not found", 404
    doc = _assessment_doc(slug)
    if doc is None:
        return jsonify({"error": "no assessment has run for this client yet"}), 404
    from agents.reports.views import render_dossier_html
    html = render_dossier_html(doc, rank)
    if html is None:
        return jsonify({"error": f"no dossier built for pursuit #{rank}"}), 404
    targeting_block = _targeting_download_gate(slug)
    if targeting_block is not None:
        return targeting_block
    html, link_failures = _gate_client_download_links(
        html, sidecar_stem=f"{slug}.download.dossier-{rank}")
    if link_failures:
        return _dns("client link gate failed: "
                    + _client_link_failure(link_failures))
    from agents.reports.lint import (
        lint_gtm_logo, lint_whitelabel,
    )
    violations = (lint_whitelabel(html).violations
                  + lint_gtm_logo(html).violations)
    if violations:
        return _dns("dossier render failed gates: " + "; ".join(v.rule for v in violations[:5]))
    return _attachment(html, f"{slug}_pursuit_dossier_{rank}.html")


@app.get("/client/<slug>/download/dossiers.html")
def download_all_dossiers(slug):
    if "/" in slug or ".." in slug:
        return "not found", 404
    doc = _assessment_doc(slug)
    if doc is None:
        return jsonify({"error": "no assessment has run for this client yet"}), 404
    from agents.reports.views import render_all_dossiers_html
    html = render_all_dossiers_html(doc)
    if html is None:
        return jsonify({"error": "no dossiers built yet"}), 404
    targeting_block = _targeting_download_gate(slug)
    if targeting_block is not None:
        return targeting_block
    html, link_failures = _gate_client_download_links(
        html, sidecar_stem=f"{slug}.download.dossiers")
    if link_failures:
        return _dns("client link gate failed: "
                    + _client_link_failure(link_failures))
    from agents.reports.lint import (
        lint_gtm_logo, lint_whitelabel,
    )
    violations = (lint_whitelabel(html).violations
                  + lint_gtm_logo(html).violations)
    if violations:
        return _dns("dossier render failed gates: " + "; ".join(v.rule for v in violations[:5]))
    return _attachment(html, f"{slug}_pursuit_dossiers.html")


@app.post("/api/export/pdf")
def api_export_pdf():
    """PDF via the existing gated exporter — its refusal rules stand
    unchanged: gates run on the HTML BEFORE Chrome, a stamped or failing
    document produces no PDF, ever."""
    body = request.get_json(force=True) or {}
    slug, kind = body.get("slug", ""), body.get("kind", "")
    if not slug or "/" in slug or ".." in slug or kind not in ("foa", "teaser"):
        return jsonify({"error": "need slug and kind: foa | teaser"}), 400
    if kind == "foa":
        rs = release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
        if rs["preview_path"] is not None and not rs["releasable"]:
            return _dns(rs["reason"])
    clean, stamped = _deliverable_artifact(slug, kind)
    if clean is None and stamped is not None:
        return _dns("the built artifact is DRAFT or stamped DO-NOT-SEND; "
                    "fix the QA failures and rebuild before exporting")
    target = clean or stamped
    if target is None:
        return jsonify({"error": "no built artifact for that kind — produce the "
                                 "assessment (or Refresh Watchlist + 3 Views) first"}), 404
    targeting_block = _targeting_download_gate(slug)
    if targeting_block is not None:
        return targeting_block
    from tools.export_pdf import ChromeNotFound, export_deliverable
    try:
        pdf, violations = export_deliverable(target)
    except ChromeNotFound as e:
        return jsonify({"error": str(e)}), 503
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    if pdf is None:
        return _dns("no PDF: " + "; ".join(f"{v.rule}: {v.detail}" for v in violations[:3]))
    return jsonify({"ok": True, "pdf": _rel(pdf)})


def _openai_collaboration_status() -> dict:
    """Return only non-secret process-session metadata."""

    return openai_collaboration_status().as_dict()


def _openai_arbiter_status() -> dict:
    """Compatibility name for callers of the retired arbiter surface."""

    return _openai_collaboration_status()


@app.get("/api/collaboration/openai")
@app.get("/api/arbiter/openai")
def api_openai_collaboration_status():
    return _no_store(_openai_collaboration_status())


@app.post("/api/collaboration/openai")
@app.post("/api/arbiter/openai")
def api_openai_collaboration():
    """Configure the optional intake/inference collaborator for this process.

    The legacy ``/api/arbiter/openai`` path remains an alias, but this setting
    is not a report-release arbiter.  The credential lives only inside the
    in-process vault and is never placed in ``os.environ`` or inherited by a
    Control Room child.  ``off``, ``advisory``, and ``required`` describe the
    later collaboration run policy; they do not modify the Analyst Layer.
    """

    if (request.content_length is not None
            and request.content_length > _MAX_COLLABORATION_SESSION_BODY):
        return _no_store({"error": "session update is too large"}, 413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _no_store({"error": "session update must be a JSON object"}, 400)
    allowed = {"api_key", "mode", "model", "clear", "deactivate"}
    if set(body) - allowed:
        return _no_store({"error": "session update has unsupported fields"}, 400)
    for flag in ("clear", "deactivate"):
        if flag in body and type(body[flag]) is not bool:
            return _no_store({"error": f"{flag} must be true or false"}, 400)
    if body.get("clear") or body.get("deactivate"):
        status = clear_openai_collaboration().as_dict()
        return _no_store({**status, "cleared": True})
    updates = {
        field: body[field]
        for field in ("api_key", "mode", "model")
        if field in body
    }
    if not updates:
        return _no_store({"error": "session update has no changes"}, 400)
    try:
        status = configure_openai_collaboration(**updates).as_dict()
    except CollaborationSessionInputError as exc:
        # Validation errors never contain the submitted value.
        return _no_store({"error": str(exc), "stored": False}, 400)
    return _no_store({**status, "configured": True, "activated": status["active"]})


@app.post("/api/export/review-pdf")
def api_export_review_pdf():
    """INTERNAL review PDF for any report html, releasable or not
    (operator-directed, 2026-07-12). This is NOT a release door and does not
    weaken one: the release-gated /api/export/pdf is untouched, the output is
    force-stamped with an INTERNAL REVIEW banner, written only under
    data/state/review_exports (never data/reports, never the Desktop delivery
    folder), and named .INTERNAL-REVIEW.pdf so it can never be mistaken for a
    client deliverable.
    """
    body = request.get_json(force=True) or {}
    rel = body.get("path", "")
    data_root = os.path.realpath(os.path.join(ROOT, "data"))
    desktop_root = os.path.realpath(_desktop_root())
    full = os.path.realpath(rel if os.path.isabs(rel)
                            else os.path.join(ROOT, rel))
    allowed = (full.startswith(data_root + os.sep)
               or full.startswith(desktop_root + os.sep))
    if not allowed or not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404
    if not full.endswith(".html"):
        return jsonify({"error": "review PDF exports html reports only"}), 400
    with open(full, encoding="utf-8") as f:
        page = f.read()
    banner = (
        '<div style="background:#7b241c;color:#fff;padding:14px 22px;'
        "font:700 14px/1.4 Arial,sans-serif;letter-spacing:.04em\">"
        'INTERNAL REVIEW COPY · NOT FOR CLIENT DELIVERY'
        '<span style="display:block;font-weight:400;letter-spacing:0">'
        'Exported for operator review before the release gate has cleared '
        'this build. The client deliverable ships only through the gated '
        'export.</span></div>')
    stamped = (page.replace("<body>", "<body>" + banner, 1)
               if "<body>" in page else banner + page)
    out_dir = os.path.realpath(os.environ.get(
        "LILA_REVIEW_EXPORT_DIR",
        os.path.join(ROOT, "data", "state", "review_exports")))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(full)[:-len(".html")]
    tmp_html = os.path.join(out_dir, f".{base}.review-src.html")
    pdf_path = os.path.join(out_dir, f"{base}.INTERNAL-REVIEW.pdf")
    # Defense in depth (Cycle 5 review P2): os.path.basename already strips any
    # traversal, but assert BOTH destinations resolve strictly under the
    # review-export dir so no future change (or an odd basename) can escape the
    # physically separate directory.
    if not (os.path.realpath(pdf_path).startswith(out_dir + os.sep)
            and os.path.realpath(tmp_html).startswith(out_dir + os.sep)):
        return jsonify({"error": "invalid review export destination"}), 400
    from tools.export_pdf import ChromeNotFound, export_pdf
    try:
        with open(tmp_html, "w", encoding="utf-8") as f:
            f.write(stamped)
        pdf = export_pdf(tmp_html, pdf_path=pdf_path)
    except ChromeNotFound as e:
        return jsonify({"error": str(e)}), 503
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": f"review export failed: {e}"}), 500
    finally:
        try:
            os.remove(tmp_html)
        except OSError:
            pass
    return jsonify({"ok": True, "pdf": _rel(pdf), "internal_only": True})


@app.get("/api/client/<slug>/calendar")
def api_client_calendar(slug):
    """The Federal opportunity calendar, straight from the Pre-Assessment.

    Read-only projection of the persisted candidate-review document: every
    dated commitment across candidates, vehicle milestones, and verified
    events, in the exact chronological order the report renders. Absent
    document means an empty list, never filler.
    """
    if not slug or _slugify(slug) != slug:
        return jsonify({"items": []})
    from agents.candidate_review_v1.calendar_engine import (
        project_document_calendar,
    )
    from agents.candidate_review_v1.contracts import CandidateReviewDocument

    state_root = os.path.join(ROOT, "data", "state", "candidate_review_v1")
    candidates = [slug, slug.replace("_", "-")]
    document_path = next(
        (path for candidate_id in candidates
         for path in [os.path.join(
             state_root, candidate_id,
             f"{candidate_id}.candidate_review.document.json")]
         if os.path.isfile(path)),
        None)
    # EVENTS_LANE (2026-07-27): the events lane is INDEPENDENT of the
    # candidate-review document. A client with verified events but no pressed
    # document still has a calendar, so neither an absent nor a corrupt
    # document may short-circuit the event rows.
    items = []
    if document_path is not None:
        try:
            document = CandidateReviewDocument.model_validate_json(
                open(document_path, encoding="utf-8").read())
            visible = tuple(
                item.candidate_id for item in document.candidates)
            items = project_document_calendar(document, candidate_ids=visible)
        except Exception:  # noqa: BLE001 - a corrupt document reads as empty
            items = []
    payload = [{
        "date": item.timing.sort_date.isoformat(),
        "label": item.timing.label,
        "kind": item.timing.kind.value,
        "status": item.timing.status.value,
        "title": item.title,
        "agency": item.agency,
        "origin": item.origin.value,
        "source_urls": [str(url) for url in item.source_urls],
    } for item in items]
    payload.extend(_golden_event_calendar_items(slug))
    payload.sort(key=lambda row: (row.get("date") or "", row.get("title") or ""))
    response = jsonify({"items": payload})
    response.headers["Cache-Control"] = "no-store"
    return response


def _golden_event_calendar_items(slug: str) -> list[dict]:
    """EVENTS_LANE (2026-07-27): verified events joined onto the operating
    calendar.

    Each event yields up to TWO rows so a registration deadline reads on its
    own date, never folded into the event date. Events carry no release
    state and never enter evidence surfaces: this is a read-only projection
    of the pressed pack, and an absent pack is an absent row, never filler.
    """
    pack_path = os.path.join(
        ROOT, "data", "state", "candidate_review_v1", slug,
        f"{slug}.golden_report.evidence_pack.json")
    if not os.path.isfile(pack_path):
        return []
    try:
        with open(pack_path, encoding="utf-8") as handle:
            events = (json.load(handle) or {}).get("events") or []
    except (OSError, ValueError):
        return []
    rows: list[dict] = []
    for event in events:
        if not isinstance(event, dict) or not event.get("url_verified"):
            continue                      # unverified never reaches a surface
        url = str(event.get("url") or "")
        common = {
            "title": event.get("name") or "",
            "agency": event.get("host") or "",
            "origin": "events_lane",
            "source_urls": [url] if url else [],
            "relevance": event.get("relevance") or "",
        }
        if event.get("event_start"):
            rows.append({**common, "date": event["event_start"],
                         "label": "Industry event", "kind": "event",
                         "status": "scheduled"})
        if event.get("registration_deadline"):
            rows.append({**common, "date": event["registration_deadline"],
                         "label": "Registration deadline",
                         "kind": "registration_deadline",
                         "status": ("closed" if event.get("registration_closed")
                                    else "open")})
    return rows


@app.get("/api/client/<slug>/banner")
def api_client_banner(slug):
    """The scoreboard banner, internal view — an HTML fragment computed from
    the AssessmentDocument, never assembled ad hoc. Empty when no assessment
    has run (the dashboard renders no empty scoreboard).

    This GET is deliberately observational.  Brand-mark adoption moves files
    and may rebuild report views, so it belongs to the existing explicit CLI
    workflow rather than client navigation or workstation switching.
    """
    bound, error = _bound_aux_workstation(slug)
    if error:
        return error
    doc = (_assessment_doc(
        slug, searches=bound["searches"],
        workstation_id=bound["workstation_id"])
        if bound is not None else _assessment_doc(slug))
    if doc is None:
        changed = _aux_snapshot_changed(bound)
        if changed:
            return changed
        return jsonify({"html": "", "marks_adopted": [], "refresh_jobs": []})
    from agents.reports.views import render_scoreboard
    html = render_scoreboard(doc, "internal")
    # L13 render-time check: population figures must reconcile internally.
    # Warn-only; the banner never blocks.
    from agents.reports.lint import lint_scoreboard_populations
    for viol in lint_scoreboard_populations(html).violations:
        print(f"[lint] scoreboard: {viol.detail}", file=sys.stderr)
    changed = _aux_snapshot_changed(bound)
    if changed:
        return changed
    return jsonify({"html": html, "marks_adopted": [], "refresh_jobs": []})


@app.get("/api/marks/client/<slug>")
def api_client_mark(slug):
    """The client's cached logo for dashboard chrome; 404 becomes the UI's
    explicit asset-required state, never a runtime network fetch."""
    if "/" in slug or ".." in slug:
        return jsonify({"error": "bad slug"}), 400
    from flask import Response, send_file
    from agents.reports.report_assets import client_assets_dir
    from agents.reports.signal_board_presentation import resolve_logo_override
    from tools.brand_marks import (
        MARK_MIMES, client_marks_dir, find_mark,
    )
    override = resolve_logo_override(
        slug, kind="client", label=slug, root=ROOT)
    if override:
        header, encoded = override.split(",", 1)
        return Response(
            base64.b64decode(encoded),
            mimetype=header[5:].split(";", 1)[0],
            headers={"Cache-Control": "no-store"},
        )
    p = find_mark(client_assets_dir(slug), "logo")
    if p is None:
        p = find_mark(client_marks_dir(), slug)
    if p is None:
        return jsonify({"error": "no mark"}), 404
    return send_file(p, mimetype=MARK_MIMES.get(p.suffix.lower(), "image/png"))


@app.get("/api/marks/agency/<path:agency>")
def api_agency_mark(agency):
    """Serve the cached official seal for an agency label used by Command Center."""
    if not agency or ".." in agency or "\x00" in agency:
        return jsonify({"error": "bad agency"}), 400
    from flask import send_file
    from agents.reports.report_assets import agency_seal_path
    from tools.brand_marks import MARK_MIMES
    p = agency_seal_path(agency)
    if p is None:
        return jsonify({"error": "no seal"}), 404
    return send_file(p, mimetype=MARK_MIMES.get(p.suffix.lower(), "image/png"))


@app.get("/api/marks/company/<path:company>")
def api_company_mark(company):
    """Serve a locally imported company logo without fetching in the render path."""
    if not company or ".." in company or "\x00" in company:
        return jsonify({"error": "bad company"}), 400
    from flask import send_file
    from tools.brand_marks import MARK_MIMES, company_marks_dir, company_slug, find_mark
    p = find_mark(company_marks_dir(), company_slug(company))
    if p is None:
        return jsonify({"error": "no mark"}), 404
    return send_file(p, mimetype=MARK_MIMES.get(p.suffix.lower(), "image/png"))


def _signal_board_brand_targets(slug: str) -> tuple[
        Optional[str], set[tuple[str, str, str]]]:
    """Latest pressed board plus the exact editable identities it renders."""
    candidates = [
        os.path.join(REPORT_DIR, f"{slug}.federal_opportunity_signals.html"),
        os.path.join(
            REPORT_DIR,
            f"{slug}.federal_opportunity_signals.DO-NOT-SEND.html",
        ),
    ]
    current = [path for path in candidates if os.path.isfile(path)]
    if not current:
        return None, set()
    path = max(current, key=os.path.getmtime)
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return path, set()
    from html.parser import HTMLParser

    class _TargetParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.targets: set[tuple[str, str, str]] = set()

        def handle_starttag(self, _tag, attrs):
            row = dict(attrs)
            kind = row.get("data-brand-kind")
            key = row.get("data-brand-key")
            label = row.get("data-brand-label")
            if all(isinstance(value, str) and value
                   for value in (kind, key, label)):
                self.targets.add((kind, key, label))

    parser = _TargetParser()
    parser.feed(source)
    return path, parser.targets


def _signal_board_editor_host_is_loopback() -> bool:
    """Keep the editor token and mutation route on a fixed local origin."""
    from urllib.parse import urlsplit

    try:
        hostname = urlsplit(f"//{request.host}").hostname
    except ValueError:
        return False
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


@app.post("/api/client/<slug>/signal-board/logo")
def api_signal_board_logo(slug):
    """Persist one operator-dropped mark without mutating certified HTML."""
    if not _signal_board_editor_host_is_loopback():
        return _no_store({"error": "mark editor is local-only"}, 403)
    supplied = request.headers.get("X-LILA-Editor-Token", "")
    if not hmac.compare_digest(supplied, _SIGNAL_BOARD_EDITOR_TOKEN):
        return _no_store({"error": "invalid mark-editor session"}, 403)
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return _no_store({"error": "foreign mark-editor origin"}, 403)
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        return _no_store({"error": "foreign mark-editor request"}, 403)
    if (request.content_length is None
            or request.content_length > _MAX_LOGO_UPLOAD_BODY):
        return _no_store({"error": "logo upload body is too large"}, 413)
    resolved, error = _workstation_route_error(slug)
    if error:
        return error
    exact_client, _catalog = resolved
    if len(request.files) != 1 or "file" not in request.files:
        return _no_store({"error": "exactly one logo file is required"}, 400)
    kind = request.form.get("kind", "")
    key = request.form.get("key", "")
    label = request.form.get("label", "")
    try:
        from agents.reports.signal_board_presentation import (
            MAX_LOGO_BYTES, logo_identity, store_logo_override,
        )
        derived = logo_identity(exact_client, kind, label)
    except ValueError as exc:
        return _no_store({"error": str(exc)}, 400)
    if key != derived:
        return _no_store({"error": "logo identity does not match its label"}, 409)
    report_path, targets = _signal_board_brand_targets(slug)
    if report_path is None:
        return _no_store({
            "error": "no Federal Opportunity Pre-Assessment draft exists",
        }, 404)
    if (kind, key, label) not in targets:
        return _no_store({
            "error": "that logo target is not present in the current draft",
        }, 409)
    uploaded = request.files["file"]
    try:
        raw = uploaded.read(MAX_LOGO_BYTES + 1)
    except OSError as exc:
        return _no_store({"error": f"logo upload is unreadable: {exc}"}, 400)
    if len(raw) > MAX_LOGO_BYTES:
        return _no_store({"error": "logo file exceeds the 2 MiB limit"}, 413)
    try:
        row = store_logo_override(
            exact_client, kind=kind, label=label, raw=raw, root=ROOT)
    except ValueError as exc:
        return _no_store({"error": str(exc)}, 400)
    from agents.reports import report_assets
    report_assets.clear_caches()
    image_label = "Portrait" if kind == "person" else "Logo"
    return _no_store({
        "saved": True,
        "identity": row["identity"],
        "sha256": row["sha256"],
        "message": (
            f"{image_label} saved for future builds. Refresh the Federal Opportunity "
            "Pre-Assessment to bake and certify it."
        ),
    }, 201)


@app.post("/api/client/<slug>/signal-board/logo-size")
def api_signal_board_logo_size(slug):
    """Persist one rendered mark's identity-wide presentation size."""
    if not _signal_board_editor_host_is_loopback():
        return _no_store({"error": "mark editor is local-only"}, 403)
    supplied = request.headers.get("X-LILA-Editor-Token", "")
    if not hmac.compare_digest(supplied, _SIGNAL_BOARD_EDITOR_TOKEN):
        return _no_store({"error": "invalid mark-editor session"}, 403)
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return _no_store({"error": "foreign mark-editor origin"}, 403)
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        return _no_store({"error": "foreign mark-editor request"}, 403)
    if (request.content_length is None
            or request.content_length > _MAX_LOGO_SIZE_BODY):
        return _no_store({"error": "logo-size body is too large"}, 413)
    if not request.is_json:
        return _no_store({"error": "request body must be JSON"}, 400)
    payload = request.get_json(silent=True)
    expected_fields = {"kind", "key", "label", "percent"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        return _no_store({
            "error": "request body must contain kind, key, label, and percent",
        }, 400)
    resolved, error = _workstation_route_error(slug)
    if error:
        return error
    exact_client, _catalog = resolved
    kind = payload["kind"]
    key = payload["key"]
    label = payload["label"]
    if any(not isinstance(value, str) or not value.strip()
           for value in (kind, key, label)):
        return _no_store({
            "error": "kind, key, and label must be non-empty strings",
        }, 400)
    try:
        from agents.reports.signal_board_presentation import (
            logo_identity,
            store_logo_size,
        )
        derived = logo_identity(exact_client, kind, label)
    except (TypeError, ValueError) as exc:
        return _no_store({"error": str(exc)}, 400)
    if key != derived:
        return _no_store({"error": "logo identity does not match its label"}, 409)
    report_path, targets = _signal_board_brand_targets(slug)
    if report_path is None:
        return _no_store({
            "error": "no Federal Opportunity Pre-Assessment draft exists",
        }, 404)
    if (kind, key, label) not in targets:
        return _no_store({
            "error": "that logo target is not present in the current draft",
        }, 409)
    try:
        row = store_logo_size(
            exact_client,
            kind=kind,
            label=label,
            percent=payload["percent"],
            root=ROOT,
        )
    except ValueError as exc:
        return _no_store({"error": str(exc)}, 400)
    image_label = "Portrait" if kind == "person" else "Logo"
    return _no_store({
        "saved": True,
        "identity": row["identity"],
        "percent": row["percent"],
        "message": (
            f"{image_label} size saved for future builds. Refresh the Federal "
            "Opportunity Pre-Assessment to bake and certify it."
        ),
    }, 201)


@app.post("/api/client/<slug>/signal-board/header-companion")
def api_signal_board_header_companion(slug):
    """Persist operator-edited client-logo companion copy for future presses."""
    if not _signal_board_editor_host_is_loopback():
        return _no_store({"error": "mark editor is local-only"}, 403)
    supplied = request.headers.get("X-LILA-Editor-Token", "")
    if not hmac.compare_digest(supplied, _SIGNAL_BOARD_EDITOR_TOKEN):
        return _no_store({"error": "invalid mark-editor session"}, 403)
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return _no_store({"error": "foreign mark-editor origin"}, 403)
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        return _no_store({"error": "foreign mark-editor request"}, 403)
    if (request.content_length is None
            or request.content_length > _MAX_HEADER_COMPANION_BODY):
        return _no_store({"error": "header companion body is too large"}, 413)
    if not request.is_json:
        return _no_store({"error": "request body must be JSON"}, 400)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"text"}:
        return _no_store({
            "error": "request body must contain exactly text",
        }, 400)
    resolved, error = _workstation_route_error(slug)
    if error:
        return error
    exact_client, _catalog = resolved
    report_path, _targets = _signal_board_brand_targets(slug)
    if report_path is None:
        return _no_store({
            "error": "no Federal Opportunity Pre-Assessment draft exists",
        }, 404)
    try:
        from agents.reports.signal_board_presentation import (
            store_header_companion_text,
        )
        text = store_header_companion_text(
            exact_client, payload["text"], root=ROOT)
    except ValueError as exc:
        return _no_store({"error": str(exc)}, 400)
    return _no_store({
        "saved": True,
        "text": text,
        "message": (
            "Header label saved for future builds. Refresh the Federal "
            "Opportunity Pre-Assessment to bake and certify it."
        ),
    }, 201)


@app.get("/api/marks/gtm")
def api_gtm_mark():
    """The GTM Group logo for dashboard chrome — the same committed asset the
    deliverables embed on their cover, so the app and the documents match."""
    import base64

    from flask import Response
    from agents.reports.capture_brief import _gtm_logo_b64, _gtm_logo_mime
    try:
        data = base64.b64decode(_gtm_logo_b64())
    except Exception as e:  # noqa: BLE001 — chrome logo, never fatal
        return jsonify({"error": f"no gtm logo: {e}"}), 404
    return Response(data, mimetype=_gtm_logo_mime(),
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/client/<slug>/competitors")
def api_client_competitors(slug):
    """Competitor drill-down for the INTERNAL dashboard: every figure traces
    to award rows (ids, amounts, agencies, USAspending links). Renders from
    the same AssessmentDocument as the banner — never assembled ad hoc."""
    bound, error = _bound_aux_workstation(slug)
    if error:
        return error
    doc = (_assessment_doc(
        slug, searches=bound["searches"],
        workstation_id=bound["workstation_id"])
        if bound is not None else _assessment_doc(slug))
    if doc is None:
        changed = _aux_snapshot_changed(bound)
        if changed:
            return changed
        return jsonify({"competitors": [], "coverage_note": "no assessment yet"})
    from agents.reports.document import _fmt_money, normalize_company
    comp = doc.competitive
    inc_keys = {normalize_company(x.company) for x in doc.buyer_incumbents}

    def _population(c) -> str:
        """Same one-label-per-company assignment as the scoreboard (L13):
        incumbent-in-buyer-accounts > product competitor > lane awardee."""
        if not comp.product_tagged:
            return "competitor"
        if normalize_company(c.name) in inc_keys:
            return "product incumbent in buyer accounts"
        return "product competitor" if c.product else "lane awardee"

    payload = {
        "coverage_note": comp.coverage_note,
        "competitors": [{
            "name": c.name,
            "population": _population(c),
            "lineage_note": c.lineage_note,
            "merged_names": c.merged_names,
            "dollars_label": c.dollars_label,
            "annual_label": _fmt_money((c.dollars or 0) / 3) if c.dollars else None,
            "basis": c.basis,
            "agencies": c.agencies,
            "naics": c.naics,
            "pursuit_ids": c.pursuit_ids,
            "awards": c.awards,
        } for c in comp.competitors],
    }
    changed = _aux_snapshot_changed(bound)
    if changed:
        return changed
    return jsonify(payload)


@app.get("/api/client/<slug>/recompetes")
def api_client_recompetes(slug):
    """Recompete calendar for the INTERNAL workbench (L16): standing artifact
    from run_recompete, sorted by PoP end, every score decomposable. Empty
    payload (never an error) when the calendar hasn't been built."""
    bound, error = _bound_aux_workstation(slug)
    if error:
        return error
    if bound is not None:
        # The current calendar schema is client-only.  Until Tranche 3 gives
        # it an embedded workstation binding, no scoped page may visually
        # adopt a prior DHS/DoD/All calendar by filename proximity.
        changed = _aux_snapshot_changed(bound)
        if changed:
            return changed
        return jsonify({
            "built": False, "attack": [], "defend": [],
            "state": "withheld_unbound",
            "note": "recompete calendar is withheld until it carries an exact scope binding",
        })
    from tools.api.recompete import load_calendar
    try:
        with open(os.path.join(REVIEW_DIR, f"{slug}.review.json")) as f:
            name = json.load(f).get("client_name") or slug
    except (OSError, ValueError):
        name = slug
    cal = load_calendar(name)
    if cal is None:
        return jsonify({"built": False, "attack": [], "defend": [],
                        "note": "no calendar yet; run the recompete step"})
    attack = sorted(cal.get("attack") or [],
                    key=lambda r: r.get("pop_end") or "9999")
    return jsonify({"built": True, "generated": cal.get("generated"),
                    "window_months": cal.get("window_months"),
                    "population": cal.get("population"),
                    "dollar_note": cal.get("dollar_note"),
                    "attack": attack, "defend": cal.get("defend") or []})


@app.get("/api/client/<slug>/change-digest")
def api_client_change_digest(slug):
    """Internal Control Room read surface for the recurring PROGRAM digest."""
    bound, error = _bound_aux_workstation(slug)
    if error:
        return error
    from agents.change_digest import (
        ChangeDigestError, digest_for_display, load_change_digest,
    )
    from agents.review import canonical_client_name

    try:
        client_name = canonical_client_name(slug, REVIEW_DIR)
        payload = load_change_digest(
            client_name, state_dir=CHANGE_DIGEST_DIR)
    except (OSError, ValueError, ChangeDigestError) as exc:
        return jsonify({
            "built": False,
            "state": "unavailable",
            "note": f"change digest unavailable: {exc}",
            "visibility": "internal_only",
        })
    if payload is None:
        changed = _aux_snapshot_changed(bound)
        if changed:
            return changed
        return jsonify({
            "built": False,
            "state": "missing",
            "note": "capture the first baseline; no change will be asserted",
            "visibility": "internal_only",
        })
    if bound is not None:
        actual_scope = ((payload.get("source_cursors") or {})
                        .get("scope_designator"))
        if actual_scope != bound["workstation_id"]:
            changed = _aux_snapshot_changed(bound)
            if changed:
                return changed
            return jsonify({
                "built": False,
                "state": "withheld_scope_mismatch",
                "note": "stored change digest belongs to another scope",
                "visibility": "internal_only",
            })
    changed = _aux_snapshot_changed(bound)
    if changed:
        return changed
    return jsonify({"built": True, **digest_for_display(payload)})


def _target_bucket(entry: dict, prime_names: set) -> str:
    """Rail buckets (2026-07-10): SOLICITATION targets are POCs on live
    graded solicitations; CANDIDATE targets come from watchlist sightings
    and hand-added names; PRIME targets are contacts at teaming primes
    (matched by organization name)."""
    blob = " ".join(str(entry.get(k) or "") for k in
                    ("title", "agency", "org", "company", "source_note")).lower()
    if any(p for p in prime_names if p and p in blob):
        return "prime"
    return {"pursuit": "solicitation",
            "watchlist": "candidate",
            "manual": "candidate"}.get(entry.get("reason_kind"), "candidate")


def _client_prime_names(slug: str, *, searches: Optional[dict] = None,
                        workstation_id: Optional[str] = None) -> set:
    """Teaming-prime organization names from the client's sweep artifact:
    subaward primes plus buyer-map product vendors."""
    names: set = set()
    try:
        if searches is None:
            p = os.path.join(CLEANED_DIR, _sweep_name(
                slug, workstation_id=workstation_id))
            with open(p, encoding="utf-8") as f:
                searches = json.load(f)
        r = (searches.get("results") or {})
        for pr in (r.get("subawards") or {}).get("primes") or []:
            nm = str(pr.get("name") or "").strip().lower()
            if nm:
                names.add(nm.split(",")[0][:40])
    except Exception:
        pass
    return names


@app.get("/api/client/<slug>/targets")
def api_client_targets(slug):
    """The client rail: PROSPECTING TARGETS. Every person here carries a
    stated reason tied to this client's live work — POC on a graded pursuit
    (amendment postings included), sighted on a watchlist item, or added for
    this client by hand. A shared agency is NOT a reason; that crowd stays
    in the main database.

    targets      — outreach entries with a reason (channels editable).
    pursuit_pocs — graph people with a reason who are not on the outreach
                   list yet; one click promotes them to targets.
    """
    from datetime import date as _date
    from tools.contact_graph.names import normalize_name
    from tools.contact_graph.outreach import OutreachReadError, _entry_id
    if "/" in slug or ".." in slug:
        return jsonify({"error": "bad slug"}), 400
    bound, error = _bound_aux_workstation(slug)
    if error:
        return error
    today = _date.today()

    # why-map: notice id -> the reason a person seen there is a target
    doc = (_assessment_doc(
        slug, searches=bound["searches"],
        workstation_id=bound["workstation_id"])
        if bound is not None else _assessment_doc(slug))
    notice_reason: dict[str, dict] = {}
    if doc is not None:
        for p in doc.board.pursuits:
            ref = {"kind": "pursuit", "rank": p.rank,
                   "label": f"POC on pursuit #{p.rank} · {p.title}"}
            notice_reason[p.source_id] = ref
            for a in p.amendments:
                if a.get("source_id"):
                    notice_reason[a["source_id"]] = ref
        for e in doc.watchlist.entries:
            if e.id and e.id not in notice_reason:
                notice_reason[e.id] = {
                    "kind": "watchlist", "rank": 90,
                    "label": f"sighted on watchlist item · {e.title or e.id}"}

    profiles, _review, obs = _contacts_snapshot()
    person_reason: dict[tuple, dict] = {}
    for o in obs:
        r = notice_reason.get(o.notice_id)
        if not r:
            continue
        key = (normalize_name(o.person_name), (o.agency or "").lower())
        cur = person_reason.get(key)
        if cur is None or r["rank"] < cur["rank"]:
            person_reason[key] = r

    try:
        outreach_entries = _outreach().render(recover_corrupt=False)
    except OutreachReadError as exc:
        return jsonify({
            "error": f"outreach list is unavailable: {exc}",
            "state": "unavailable",
        }), 503
    targets, out_ids = [], set()
    for e in outreach_entries:
        reason = ({"kind": "manual", "rank": 50, "label": "added for this client"}
                  if e.get("client") == slug else None)
        pr = person_reason.get((e.get("normalized_name"),
                                (e.get("agency") or "").lower()))
        if pr and (reason is None or pr["rank"] < reason["rank"]):
            reason = pr
        if reason is None:
            continue  # no reason, not a target — find them in the database
        out_ids.add(e["id"])
        targets.append({**e, "reason": reason["label"],
                        "reason_kind": reason["kind"], "reason_rank": reason["rank"]})
    prime_names = _client_prime_names(
        slug,
        searches=bound["searches"] if bound is not None else None,
        workstation_id=bound["workstation_id"] if bound is not None else None,
    )
    for t in targets:
        t["bucket"] = _target_bucket(t, prime_names)

    pocs = []
    for p in profiles:
        r = person_reason.get((p.normalized_name, (p.agency or "").lower()))
        if not r or _entry_id(p.normalized_name, p.agency) in out_ids:
            continue
        row = _profile_row(p, today)
        row.update(reason=r["label"], reason_kind=r["kind"], reason_rank=r["rank"])
        row["bucket"] = _target_bucket(row, prime_names)
        pocs.append(row)

    targets.sort(key=lambda t: (t["reason_rank"], -(t.get("sighting_count") or 0)))
    pocs.sort(key=lambda t: (t["reason_rank"], -(t.get("sighting_count") or 0)))
    changed = _aux_snapshot_changed(bound)
    if changed:
        return changed
    from agents.review import (
        load_targeting_plan,
        targeting_play_key,
        targeting_review_status,
    )
    exact_client = canonical_client_name(slug, REVIEW_DIR)
    for target in targets:
        target["play_id"] = targeting_play_key(target.get("reason") or "")
    targeting_ready, targeting_problems = targeting_review_status(
        exact_client, targets, review_dir=REVIEW_DIR)
    return jsonify({
        "targets": targets,
        "pursuit_pocs": pocs,
        "targeting_plan": load_targeting_plan(exact_client, review_dir=REVIEW_DIR),
        "targeting_readiness": {
            "ready": targeting_ready,
            "problems": targeting_problems,
        },
    })


def _client_targets_payload(slug: str) -> tuple[Optional[dict], int]:
    """Reuse the exact client-target derivation at mutation and release gates."""
    response = api_client_targets(slug)
    status = 200
    if isinstance(response, tuple):
        response, status = response[0], int(response[1])
    payload = response.get_json(silent=True) if hasattr(response, "get_json") else None
    return (payload if isinstance(payload, dict) else None, status)


@app.get("/api/client/<slug>/targeting-plan")
def api_targeting_plan(slug):
    if not slug or _slugify(slug) != slug:
        return jsonify({"error": "bad slug"}), 400
    from agents.review import load_targeting_plan
    exact_client = canonical_client_name(slug, REVIEW_DIR)
    return jsonify(load_targeting_plan(exact_client, review_dir=REVIEW_DIR))


@app.post("/api/client/<slug>/targeting-plan")
def api_targeting_plan_update(slug):
    if not slug or _slugify(slug) != slug:
        return jsonify({"error": "bad slug"}), 400
    body = _body_dict()
    from agents.review import (
        TARGETING_ACTION_FIELDS,
        TARGETING_DISPOSITIONS,
        TARGETING_GAP_FIELDS,
        TARGETING_LANES,
        load_targeting_plan,
        save_targeting_plan,
        targeting_play_key,
        validate_targeting_plan,
    )
    current, status = _client_targets_payload(slug)
    if status != 200 or current is None:
        return jsonify({"error": "current target inventory is unavailable"}), 409
    exact_client = canonical_client_name(slug, REVIEW_DIR)
    plan = load_targeting_plan(exact_client, review_dir=REVIEW_DIR)
    actions = dict(plan.get("actions") or {})
    play_lane_dispositions = dict(plan.get("play_lane_dispositions") or {})
    target_id = body.get("target_id")
    if target_id is not None:
        if not isinstance(target_id, str) or not target_id:
            return jsonify({"error": "target_id must be a non-empty string"}), 400
        current_ids = {str(row.get("id") or "") for row in current.get("targets") or []}
        if target_id not in current_ids:
            return jsonify({"error": "target is not in the current client inventory"}), 409
        action = body.get("action")
        if action is None:
            actions.pop(target_id, None)
        elif not isinstance(action, dict):
            return jsonify({"error": "action must be an object or null"}), 400
        else:
            allowed = set(TARGETING_ACTION_FIELDS) | {"reject_reason"}
            unknown = sorted(set(action) - allowed)
            if unknown:
                return jsonify({"error": "unsupported action fields", "fields": unknown}), 400
            normalized = {}
            for key, value in action.items():
                if not isinstance(value, str):
                    return jsonify({"error": f"{key} must be a string"}), 400
                normalized[key] = value.strip()[:2000]
            if normalized.get("lane") not in TARGETING_LANES:
                return jsonify({"error": "invalid target lane"}), 400
            if normalized.get("disposition") not in TARGETING_DISPOSITIONS:
                return jsonify({"error": "invalid target disposition"}), 400
            actions[target_id] = normalized
    play_lanes = body.get("play_lane_dispositions")
    if play_lanes is not None:
        if not isinstance(play_lanes, dict):
            return jsonify({"error": "play_lane_dispositions must be an object"}), 400
        current_targets = current.get("targets") or []
        valid_plays = {targeting_play_key(row.get("reason") or "")
                       for row in current_targets
                       if int(row.get("reason_rank") or 999) < 90}
        if any(play_id not in valid_plays for play_id in play_lanes):
            return jsonify({"error": "lane disposition references a non-current top play"}), 409
        for play_id, lanes in play_lanes.items():
            if not isinstance(lanes, dict) or any(key not in TARGETING_LANES for key in lanes):
                return jsonify({"error": f"{play_id} contains an invalid lane"}), 400
            normalized_lanes = dict(play_lane_dispositions.get(play_id) or {})
            for lane, disposition in lanes.items():
                if not isinstance(disposition, dict):
                    return jsonify({"error": f"{play_id} {lane} disposition must be an object"}), 400
                allowed = {"status"} | set(TARGETING_GAP_FIELDS)
                unknown = sorted(set(disposition) - allowed)
                if unknown:
                    return jsonify({"error": "unsupported lane-gap fields",
                                    "fields": unknown}), 400
                lane_status = str(disposition.get("status") or "")
                if lane_status not in {"covered", "no_qualified_target", "not_applicable"}:
                    return jsonify({"error": f"{play_id} {lane} has an invalid status"}), 400
                normalized = {"status": lane_status}
                for field in TARGETING_GAP_FIELDS:
                    value = disposition.get(field, "")
                    if not isinstance(value, str):
                        return jsonify({"error": f"{field} must be a string"}), 400
                    normalized[field] = value.strip()[:2000]
                normalized_lanes[lane] = normalized
            play_lane_dispositions[play_id] = normalized_lanes
    plan = save_targeting_plan(
        exact_client, actions=actions,
        play_lane_dispositions=play_lane_dispositions,
        review_dir=REVIEW_DIR)
    problems = validate_targeting_plan(plan, current.get("targets") or [])
    return jsonify({"ok": True, "plan": plan, "complete": not problems,
                    "problems": problems})


@app.post("/api/client/<slug>/targeting-review-approve")
def api_targeting_review_approve(slug):
    if not slug or _slugify(slug) != slug:
        return jsonify({"error": "bad slug"}), 400
    current, status = _client_targets_payload(slug)
    if status != 200 or current is None:
        return jsonify({"error": "current target inventory is unavailable"}), 409
    from agents.review import approve_targeting_review, targeting_review_status
    exact_client = canonical_client_name(slug, REVIEW_DIR)
    try:
        receipt = approve_targeting_review(
            exact_client, current.get("targets") or [], review_dir=REVIEW_DIR)
    except ValueError as exc:
        return jsonify({"error": str(exc), "targeting_review_approved": False}), 409
    ready, problems = targeting_review_status(
        exact_client, current.get("targets") or [], review_dir=REVIEW_DIR)
    return jsonify({"targeting_review_approved": ready,
                    "problems": problems, "receipt": receipt})


@app.post("/api/decide")
def api_decide():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be an object"}), 400
    client = body.get("client_name")
    if not isinstance(body.get("approve"), bool):
        return jsonify({"error": "approve must be boolean"}), 400
    try:
        (workstation_id, is_native,
         expected_registry_sha256,
         expected_receipt_sha256) = _mutation_workstation_binding(
             client, body.get("workstation_id"))
        expected_sha256 = _expected_mutation_sha(body, native=is_native)
        packet = decide(
            client, approve=body["approve"], note=body.get("note", ""),
            workstation_id=workstation_id, review_dir=REVIEW_DIR,
            expected_sha256=expected_sha256,
            native_owner=is_native,
            expected_registry_sha256=expected_registry_sha256,
            expected_receipt_sha256=expected_receipt_sha256)
    except (OSError, WorkstationError, WorkstationBindingError) as exc:
        return jsonify({"error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    response = {
        "client_name": client,
        "status": packet.status.value,
    }
    if is_native:
        response["packet_sha256"] = _packet_model_sha256(packet)
    if body["approve"] and packet.status.value == "approved":
        # One-gate law (operator ruling 2026-07-23): input -> inference ->
        # Analyst Layer approve -> search -> report. The approval click IS
        # the go, so the one-deliverable press starts as a normal job. A
        # start refusal (for example a job already running for this client)
        # never rolls back the durable approval; it is reported additively.
        try:
            response["job_id"] = start_job("candidate_review", client, {})
        except Exception as exc:  # noqa: BLE001 - report, never roll back
            response["auto_run_note"] = (
                f"approved; press not auto-started: {str(exc)[:200]}")
    return jsonify(response)


@app.get("/api/client/<slug>/approval-diff")
def api_approval_diff(slug):
    """Explain what changed since the prior Assess-results approval.

    The endpoint is UX only; the binding remains the release gate. Client
    identity is resolved by the review packet's canonical owner and never
    guessed from a malformed packet.
    """
    from agents.assess.approval import approval_evidence_diff
    from agents.review import canonical_client_name
    try:
        client_name = canonical_client_name(slug, REVIEW_DIR)
    except (OSError, ValueError) as exc:
        return jsonify({
            "available": False,
            "why": f"client identity unavailable: {exc}",
        })
    return jsonify(approval_evidence_diff(
        client_name, review_dir=REVIEW_DIR))


@app.post("/api/review/assess-approve")
def api_review_assess_approve():
    """The review-step sign-off: full picture + pursue-grade opportunities
    reviewed -> Produce activates. Fresh search/screen runs invalidate it."""
    body = request.get_json(force=True) or {}
    client = body.get("client_name")
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    from agents.assess.approval import (
        AssessApprovalError, approve_assess_results, revoke_assess_results,
    )
    if body.get("revoke"):
        revoke_assess_results(client, review_dir=REVIEW_DIR)
        refresh_note = _refresh_assess_ledger(client)
        try:
            from agents.review import journal
            journal(client, "assess_approval_revoked", {})
        except Exception:
            pass
        return jsonify({"review_approved": False,
                        "refresh_note": refresh_note})
    # Absent means "leave the partial-release axis alone" (a standing grant
    # carries forward; Cycle 3 review finding, 2026-07-12). Explicit false
    # keeps its revoke-the-grant meaning for the partial panel.
    partial_release_approved = body.get("partial_release_approved")
    if partial_release_approved is not None \
            and not isinstance(partial_release_approved, bool):
        return jsonify({
            "error": "partial_release_approved must be a boolean",
            "review_approved": False,
        }), 400
    try:
        approval = approve_assess_results(
            client, note=body.get("note", ""), review_dir=REVIEW_DIR,
            partial_release_approved=partial_release_approved,
            assess_state_dir=ASSESS_RUN_DIR,
            partial_release_run_id=body.get("partial_release_run_id"),
            partial_release_blockers_sha256=body.get(
                "partial_release_blockers_sha256"))
    except AssessApprovalError as exc:
        return jsonify({
            "error": "Assess approval failed: current results are unavailable",
            "review_approved": False,
            "problems": exc.problems,
        }), 409
    effective_partial = bool(approval.get("partial_release_approved"))
    try:
        from agents.review import journal
        journal(client, "assess_approval", {
            "note": body.get("note", ""),
            "partial_release_approved": effective_partial,
            "partial_release_carried": partial_release_approved is None
            and effective_partial,
        })
    except Exception:
        pass
    refresh_note = _refresh_assess_ledger(client)
    return jsonify({"review_approved": True,
                    "partial_release_approved": effective_partial,
                    "binding": approval["binding"],
                    "refresh_note": refresh_note})


@app.post("/api/review/target-approve")
def api_review_target_approve():
    """The explicit Assess -> Target door. Approving requires a CURRENT
    Assess approval; revoking re-locks outreach and is journaled."""
    body = request.get_json(force=True) or {}
    client = body.get("client_name")
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    from agents.review import approve_target, revoke_target
    if body.get("revoke"):
        revoke_target(client, review_dir=REVIEW_DIR)
        return jsonify({"target_approved": False})
    from agents.assess.approval import assess_approval_for_release
    _, status, problems = assess_approval_for_release(
        client, review_dir=REVIEW_DIR)
    if status != "approved":
        return jsonify({
            "error": "approve the current Assess results before proceeding "
                     "to Target",
            "target_approved": False,
            "problems": problems,
        }), 409
    approve_target(client, note=body.get("note", ""), review_dir=REVIEW_DIR)
    return jsonify({"target_approved": True})


@app.get("/api/assess/run")
def api_assess_run():
    """Read the exact immutable intelligence run presented at review."""
    client = request.args.get("client_name") or ""
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    snapshot = _assess_ledger_snapshot(client, include_run=True)
    return jsonify(snapshot), (200 if snapshot.get("exists") else 404)


def _review_attachment_inventory(record: dict) -> tuple[list[dict], Any, Any, bool]:
    """Normalized rows plus the exact declared inventory binding.

    ``valid`` is recomputed at the API boundary instead of trusting a flag from
    a serialized run. Legacy and malformed runs remain reviewable for rejection
    but can never be approved as actionable evidence.
    """
    raw_attachments = record.get("attachments")
    attachments = raw_attachments if isinstance(raw_attachments, list) else []
    count = record.get("attachment_inventory_count")
    inventory_hash = record.get("attachment_inventory_hash")
    rows_valid = all(
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and bool(item["name"].strip())
        for item in attachments
    )
    valid = bool(
        record.get("attachment_inventory_valid") is True
        and isinstance(raw_attachments, list)
        and type(count) is int and count >= 0
        and count == len(attachments)
        and isinstance(inventory_hash, str) and inventory_hash.strip()
        and rows_valid
    )
    return attachments, count, inventory_hash, valid


def _live_requirement_rows(snapshot: dict) -> list[dict]:
    run = snapshot.get("run") or {}
    rows = []
    for record in ((run.get("live") or {}).get("records") or []):
        excerpt = record.get("requirement_excerpt")
        if not excerpt:
            continue
        evidence = next((
            item for item in reversed(record.get("authoritative_evidence") or [])
            if "requirement" in (item.get("supports") or [])
            and excerpt in str(item.get("excerpt") or "")
        ), None)
        if not evidence:
            continue
        attachments, inventory_count, inventory_hash, inventory_valid = \
            _review_attachment_inventory(record)
        rows.append({
            "notice_id": record.get("notice_id"),
            "title": record.get("title"),
            "agency": record.get("agency"),
            "response_deadline": record.get("response_deadline"),
            "classification": record.get("classification"),
            "recommendation": record.get("recommendation"),
            "excerpt": excerpt,
            "evidence_id": evidence.get("evidence_id"),
            "source_url": evidence.get("source_url"),
            "reviewed_by": record.get("requirement_reviewed_by"),
            "reviewed_at": record.get("requirement_reviewed_at"),
            "attachments": attachments,
            "attachment_inventory_count": inventory_count,
            "attachment_inventory_hash": inventory_hash,
            "attachment_inventory_valid": inventory_valid,
            "attachments_checked": record.get("attachments_checked"),
            "gap": record.get("attachment_gap"),
        })
    return rows


@app.get("/api/assess/requirements")
def api_assess_requirements():
    """Exact SAM requirement spans awaiting or carrying human review."""
    client = request.args.get("client_name") or ""
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    snapshot = _assess_ledger_snapshot(client, include_run=True)
    if not snapshot.get("exists"):
        return jsonify(snapshot), 409
    return jsonify({
        "client_name": client,
        "run_id": snapshot.get("run_id"),
        "items": _live_requirement_rows(snapshot),
    })


@app.post("/api/assess/requirements")
def api_assess_requirement_decision():
    """Approve/reject one exact, current source span, then refresh the ledger."""
    body = request.get_json(force=True) or {}
    client = body.get("client_name") or ""
    notice_id = str(body.get("notice_id") or "").strip()
    if (not client or not notice_id or not isinstance(body.get("approve"), bool)
            or not all(str(body.get(key) or "").strip()
                       for key in ("run_id", "evidence_id", "excerpt"))):
        return jsonify({
            "error": ("client_name, notice_id, run_id, evidence_id, excerpt, "
                      "and boolean approve are required")
        }), 400
    snapshot = _assess_ledger_snapshot(client, include_run=True)
    if not snapshot.get("exists"):
        return jsonify({
            "error": "The current strict ledger is unavailable or stale",
            "state": snapshot.get("state"),
            "diagnostics": snapshot.get("diagnostics") or [],
        }), 409
    candidate = next((row for row in _live_requirement_rows(snapshot)
                      if row.get("notice_id") == notice_id), None)
    if not candidate:
        return jsonify({
            "error": "No exact current requirement span is available for review"
        }), 409
    count_matches = (
        "attachment_inventory_count" in body
        and type(body.get("attachment_inventory_count"))
        is type(candidate.get("attachment_inventory_count"))
        and body.get("attachment_inventory_count")
        == candidate.get("attachment_inventory_count"))
    hash_matches = (
        "attachment_inventory_hash" in body
        and body.get("attachment_inventory_hash")
        == candidate.get("attachment_inventory_hash"))
    if (body.get("run_id") != snapshot.get("run_id")
            or body.get("evidence_id") != candidate.get("evidence_id")
            or body.get("excerpt") != candidate.get("excerpt")
            or not count_matches or not hash_matches):
        return jsonify({
            "error": ("The requirement evidence changed after it was shown; "
                      "reload and review the current source span")
        }), 409
    if body["approve"] and candidate.get("attachment_inventory_valid") is not True:
        return jsonify({
            "error": ("The SAM attachment inventory is incomplete or malformed; "
                      "refresh the notice before approving this requirement")
        }), 409
    if (body["approve"] and candidate.get("attachment_inventory_count", 0) > 0
            and body.get("attachments_reviewed") is not True):
        return jsonify({
            "error": ("Review every listed attachment and confirm the exact "
                      "inventory before approving this requirement")
        }), 400
    from tools.capability import match_capability, require_profile
    match = match_capability(require_profile(client), candidate["excerpt"])
    if match.tier != "core" or not match.matched:
        return jsonify({
            "error": "The current span no longer contains an approved core capability"
        }), 409
    from agents.assess.live_review import record_requirement_review
    try:
        decision = record_requirement_review(
            client, binding=snapshot.get("binding") or {},
            notice_id=notice_id, evidence_id=candidate["evidence_id"],
            excerpt=candidate["excerpt"], capability_terms=match.matched,
            approved=body["approve"],
            attachment_inventory_hash=candidate.get("attachment_inventory_hash"),
            attachment_inventory_count=candidate.get("attachment_inventory_count"),
            attachments_reviewed=(
                bool(body["approve"])
                and (candidate.get("attachment_inventory_count") == 0
                     or body.get("attachments_reviewed") is True)),
            reviewed_by=str(body.get("reviewed_by") or "operator"),
            note=str(body.get("note") or ""), review_dir=REVIEW_DIR,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _refresh_assess_ledger(client)
    return jsonify({"saved": True, "decision": decision})


@app.post("/api/strategy/terms")
def api_strategy_terms():
    """Iterate-loop term amendment: keywords/NAICS tuning that keeps an
    approved strategy approved (add terms, subtract terms, re-run)."""
    from agents.review import RevisionError, amend_terms
    body = request.get_json(force=True) or {}
    client = body.get("client_name")
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    for field in ("inferred_naics", "naics_meta", "kept_out_naics"):
        if field in body and body[field] is None:
            return jsonify({"error": f"{field} must not be null"}), 400
    try:
        (workstation_id, is_native,
         expected_registry_sha256,
         expected_receipt_sha256) = _mutation_workstation_binding(
             client, body.get("workstation_id"))
        expected_sha256 = _expected_mutation_sha(body, native=is_native)
        packet = amend_terms(client, keywords=body.get("keywords"),
                             inferred_naics=body.get("inferred_naics"),
                             kept_out=body.get("kept_out"),
                             naics_meta=body.get("naics_meta"),
                             kept_out_naics=body.get("kept_out_naics"),
                             workstation_id=workstation_id,
                             review_dir=REVIEW_DIR,
                             expected_sha256=expected_sha256,
                             native_owner=is_native,
                             expected_registry_sha256=expected_registry_sha256,
                             expected_receipt_sha256=expected_receipt_sha256)
    except RevisionError as e:
        return jsonify({"error": str(e)}), 400
    except (WorkstationError, WorkstationBindingError) as e:
        return jsonify({"error": str(e)}), 409
    except (OSError, KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    response = {
        "client_name": client,
        "revision_count": packet.revision_count,
        "strategy": packet.strategy.model_dump(mode="json"),
    }
    if is_native:
        response["packet_sha256"] = _packet_model_sha256(packet)
    return jsonify(response)


@app.post("/api/strategy/scope")
def api_strategy_scope():
    """Persist the search scope: {'all': true} or {'agencies': [...]}. An
    operator search-time choice, legal before or after approval; unknown
    agencies are a 400, never silently dropped."""
    from agents.review import RevisionError, set_scope
    body = request.get_json(force=True) or {}
    client = body.get("client_name")
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    try:
        _exact, catalog = _workstation_catalog_for_slug(_slugify(client))
    except (FileNotFoundError, WorkstationError) as exc:
        return jsonify({"error": str(exc)}), 409
    if body.get("workstation_id") is not None or any(
            row.is_native for row in catalog.workstations):
        return jsonify({
            "error": "workstation scope is immutable; use + New scope",
        }), 409
    try:
        packet = set_scope(client, body.get("scope"))
    except RevisionError as e:
        return jsonify({"error": str(e)}), 400
    except (OSError, KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"client_name": client,
                    "search_scope": packet.search_scope or {"all": True}})


def _pressed_documents():
    """Every client's pressed Pre-Assessment document, validated, by id."""
    from agents.candidate_review_v1.contracts import CandidateReviewDocument

    state_root = os.path.join(ROOT, "data", "state", "candidate_review_v1")
    if not os.path.isdir(state_root):
        return []
    documents = []
    for client_id in sorted(os.listdir(state_root)):
        path = os.path.join(
            state_root, client_id,
            f"{client_id}.candidate_review.document.json")
        if not os.path.isfile(path):
            continue
        try:
            documents.append(CandidateReviewDocument.model_validate_json(
                open(path, encoding="utf-8").read()))
        except Exception:  # noqa: BLE001 - one corrupt press never hides the rest
            continue
    return documents


_SAM_KEY_ENV_NAMES = ("SAM_GOV_API_KEY", "SAM_GOV_API_KEY_2")


@app.get("/keys")
def keys_page():
    """A dedicated, server-rendered key page: works from any browser state,
    no app shell, no cache dependence. Values never render back."""
    primary = "set" if os.environ.get("SAM_GOV_API_KEY") else "not set"
    secondary = "set" if os.environ.get("SAM_GOV_API_KEY_2") else "not set"
    return (
        "<!doctype html><meta charset='utf-8'><title>API keys</title>"
        "<body style='font-family:-apple-system,Helvetica,sans-serif;"
        "max-width:520px;margin:70px auto;color:#0b1728'>"
        "<h2 style='font-weight:700'>SAM.gov API keys</h2>"
        "<p style='color:#667789;font-size:14px'>Paste one or both keys and "
        "save. They store to the repo .env and activate immediately; the "
        "next report run uses them. Two keys pool their daily budgets and "
        "rotate automatically when one throttles.</p>"
        f"<p style='font-size:13px'>Primary: <b>{primary}</b> &middot; "
        f"Second: <b>{secondary}</b></p>"
        "<form onsubmit='save(event)'>"
        "<input id=k1 type=password autocomplete=off placeholder='primary key'"
        " style='width:100%;padding:10px;margin:6px 0;font-size:14px;"
        "border:1px solid #d9e1e8'>"
        "<input id=k2 type=password autocomplete=off placeholder='second key "
        "(optional)' style='width:100%;padding:10px;margin:6px 0;"
        "font-size:14px;border:1px solid #d9e1e8'>"
        "<button style='padding:10px 22px;font-size:14px;background:#12233a;"
        "color:#fff;border:none;cursor:pointer;margin-top:8px'>Save keys"
        "</button> <span id=msg style='font-size:13px;color:#13735b'></span>"
        "</form>"
        "<p style='margin-top:26px'><a href='/' style='color:#157eaf'>"
        "Back to the Control Room</a></p>"
        "<script>async function save(e){e.preventDefault();"
        "const b={};const k1=document.getElementById('k1').value.trim();"
        "const k2=document.getElementById('k2').value.trim();"
        "if(k1)b.primary=k1;if(k2)b.secondary=k2;"
        "const m=document.getElementById('msg');"
        "if(!Object.keys(b).length){m.textContent='paste at least one key';return}"
        "m.textContent='saving\u2026';"
        "const r=await fetch('/api/keys/sam',{method:'POST',"
        "headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});"
        "const j=await r.json();"
        "m.textContent=r.ok?('saved \u00b7 '+((j.primary_set?1:0)+(j.secondary_set?1:0))+' key(s) active'):(j.error||'not saved');"
        "if(r.ok){document.getElementById('k1').value='';"
        "document.getElementById('k2').value='';}}</script></body>")


@app.get("/api/keys/sam")
def api_sam_keys_state():
    """Presence booleans and the pooled budget; never the key values."""
    from tools.api import sam_quota
    return jsonify({
        "primary_set": bool(os.environ.get("SAM_GOV_API_KEY")),
        "secondary_set": bool(os.environ.get("SAM_GOV_API_KEY_2")),
        "summary": sam_quota.summary(),
    })


@app.post("/api/keys/sam")
def api_sam_keys_save():
    """Operator-entered SAM keys, straight from the dashboard form.

    The operator types the values; the server stores them in the repo .env
    (atomic rewrite, other lines preserved) and activates them in-process so
    the next job inherits them without a restart. Values are never echoed,
    logged, or returned.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be an object"}), 400
    updates = {}
    for field, env_name in (("primary", "SAM_GOV_API_KEY"),
                            ("secondary", "SAM_GOV_API_KEY_2")):
        value = body.get(field)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value.strip()) < 20 \
                or any(ch.isspace() for ch in value.strip()) is False \
                and len(value.strip()) > 128:
            return jsonify({"error": f"{field} key looks malformed"}), 400
        cleaned = value.strip()
        if any(ch.isspace() for ch in cleaned):
            return jsonify({"error": f"{field} key looks malformed"}), 400
        updates[env_name] = cleaned
    if not updates:
        return jsonify({"error": "no key supplied"}), 400
    env_path = os.path.join(ROOT, ".env")
    lines = []
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    for env_name, value in updates.items():
        replaced = False
        for index, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == env_name:
                lines[index] = f"{env_name}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{env_name}={value}")
    tmp_path = env_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(tmp_path, env_path)
    for env_name, value in updates.items():
        os.environ[env_name] = value
    return jsonify({
        "primary_set": bool(os.environ.get("SAM_GOV_API_KEY")),
        "secondary_set": bool(os.environ.get("SAM_GOV_API_KEY_2")),
    })


@app.get("/api/calendar")
def api_calendar_all():
    """The book-of-business calendar: every dated commitment, every client."""
    from agents.candidate_review_v1.calendar_engine import (
        project_document_calendar,
    )

    items = []
    for document in _pressed_documents():
        visible = tuple(item.candidate_id for item in document.candidates)
        try:
            projected = project_document_calendar(
                document, candidate_ids=visible)
        except Exception:  # noqa: BLE001
            continue
        for item in projected:
            items.append({
                "date": item.timing.sort_date.isoformat(),
                "label": item.timing.label,
                "kind": item.timing.kind.value,
                "status": item.timing.status.value,
                "title": item.title,
                "agency": item.agency,
                "origin": item.origin.value,
                "client_id": document.binding.client_id,
                "client_name": document.client_name,
                "source_urls": [str(url) for url in item.source_urls],
            })
    items.sort(key=lambda row: (row["date"], row["client_name"]))
    response = jsonify({"items": items})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/targets")
def api_targets_all():
    """Key targets, every client: approved target agencies + the reviewed
    candidate set from the pressed report, soonest commitment first."""
    clients = []
    by_id = {}
    for document in _pressed_documents():
        rows = []
        evidence_by_id = {
            item.evidence_id: item for item in document.evidence}
        for candidate in document.candidates:
            next_date = min(
                (item.sort_date for item in candidate.dates), default=None)
            url = next(
                (str(evidence_by_id[eid].source_url)
                 for member in candidate.members
                 for eid in member.member_evidence_ids
                 if eid in evidence_by_id), "")
            rows.append({
                "title": candidate.title,
                "agency": candidate.agency,
                "kind": candidate.kind.value,
                "next_date": next_date.isoformat() if next_date else None,
                "source_url": url,
            })
        rows.sort(key=lambda row: row["next_date"] or "9999")
        entry = {
            "client_id": document.binding.client_id,
            "client_name": document.client_name,
            "target_agencies": [],
            "candidates": rows,
        }
        clients.append(entry)
        by_id[document.binding.client_id] = entry
    review_dir = os.path.join(ROOT, "data", "review")
    if os.path.isdir(review_dir):
        for name in sorted(os.listdir(review_dir)):
            if not name.endswith(".review.json"):
                continue
            try:
                packet = json.load(open(
                    os.path.join(review_dir, name), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            slug = name[:-len(".review.json")]
            agencies = ((packet.get("strategy") or {})
                        .get("target_agencies") or [])
            entry = by_id.get(slug) or by_id.get(slug.replace("_", "-"))
            if entry is not None:
                entry["target_agencies"] = agencies
            elif agencies and packet.get("status") == "approved":
                clients.append({
                    "client_id": slug,
                    "client_name": packet.get("client_name") or slug,
                    "target_agencies": agencies,
                    "candidates": [],
                })
    response = jsonify({"clients": clients})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/ticker")
def api_ticker():
    """The cross-client tape: what the pipeline already knows, aggregated.

    Existing data only, every item client-attributed, soonest first; the
    lifecycle module owns derivation and the cap (reported, never silent).
    """
    from ui.lifecycle import radar, ticker
    try:
        payload = ticker(radar()["cards"])
    except Exception:  # noqa: BLE001 - an empty tape, never a broken home
        payload = {"items": [], "capped": False}
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/radar")
def api_radar():
    """The practice radar: lifecycle cards for every client under clients/,
    the cross-client scoreboard, and the aggregate ticker. Read-only
    derivation over existing artifacts; nothing pulled or pressed."""
    from ui.lifecycle import radar, ticker
    payload = radar()
    payload["ticker"] = ticker(payload["cards"])
    return jsonify(payload)


@app.get("/onboarding")
def onboarding_stub():
    """The guided flow lives in the Control Room; this door just goes home."""
    return redirect("/")


@app.get("/api/onboarding/<slug>")
def api_onboarding_state(slug):
    """Onboarding edit-view payload: per-file disk truth plus the runner
    loaders' own verdicts. The client_name resolves from the stored profile
    when present, else the slug (a not-yet-created client)."""
    from ui.onboarding import load_state
    client_name = slug
    try:
        from tools.capability import load_profile
        profile = load_profile(slug)
        if profile is not None:
            client_name = profile.client_name
    except Exception:  # noqa: BLE001 - a corrupt profile still loads the view
        pass
    return jsonify(load_state(client_name))


@app.post("/api/onboarding")
def api_onboarding_save():
    """Validated save of any subset of the three onboarding files. The
    schemas and loaders the runners use are the only validators; their
    error text returns verbatim. CAS per file via expected_shas."""
    from ui.onboarding import (
        FILE_KINDS, OnboardingConflict, OnboardingValidationError, save,
    )
    body = request.get_json(force=True) or {}
    client = (body.get("client_name") or "").strip()
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    payloads = {k: body[k] for k in FILE_KINDS if k in body}
    try:
        state = save(client, payloads,
                     expected_shas=body.get("expected_shas"))
    except OnboardingValidationError as e:
        return jsonify({"error": "validation failed", "errors": e.errors}), 400
    except OnboardingConflict as e:
        return jsonify({"error": str(e)}), 409
    except OSError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(state)


@app.post("/api/onboarding/draft")
def api_onboarding_draft():
    """Optional assist, clearly separated from the deterministic flow: LLM-
    suggest profile and taxonomy content from pasted public materials. The
    suggestion is prefill for human review, validated by the same runner
    schemas as a save, and NEVER written to disk by this endpoint."""
    from agents.decisions import maxplan_cli
    from ui.onboarding import validate_payloads
    body = request.get_json(force=True) or {}
    client = (body.get("client_name") or "").strip()
    materials = (body.get("materials") or "").strip()
    if not client or not materials:
        return jsonify({"error": "client_name and materials are required"}), 400
    if not maxplan_cli.cli_available():
        return jsonify({"error": "drafting assist unavailable (no CLI); "
                                 "the deterministic form flow is unaffected"}), 503
    prompt = (
        "Draft federal-sales onboarding content for a company from its "
        "public materials. Respond with ONLY one JSON object, keys "
        "'profile' and 'taxonomy'.\n"
        "profile: {client_name, capability_summary, capability_terms: "
        "{core: [..], adjacent: [..], excluded: [..]}, "
        "named_competitors_and_incumbents: [..], mission_components: [..], "
        "naics_boundary: [six-digit strings]}.\n"
        "taxonomy: {client_name, version: 1, updated: ISO date, core: "
        "[{term, mode: stemmed|exact_phrase|acronym, expansion_context: "
        "[..] for acronyms}], adjacent: [same], exclude: [{term, scope: "
        "span|record, reason}]}.\n"
        "CORE terms are the company's own capability vocabulary; ADJACENT "
        "contribute but never suffice; EXCLUDE kills known false-positive "
        "shapes (acronym collisions, name-as-common-noun). Be specific and "
        "conservative; never invent NAICS codes.\n\n"
        f"Company name: {client}\nMaterials:\n{materials[:6000]}"
    )
    try:
        reply = maxplan_cli.run_claude(prompt=prompt)
        suggestion = maxplan_cli.extract_json(reply)
    except maxplan_cli.MaxPlanError as e:
        return jsonify({"error": f"assist failed: {e}"}), 502
    payloads = {k: v for k, v in suggestion.items()
                if k in ("profile", "taxonomy") and isinstance(v, dict)}
    errors = validate_payloads(client, payloads)
    return jsonify({
        "suggestion": suggestion,
        "validation_errors": errors or None,
        "note": "prefill only; nothing was written. Review every field; "
                "save runs the full validators again.",
    })


@app.get("/api/agencies")
def api_agencies():
    """Agency autocomplete: ranked matches for the query, or the default
    quick-pick chips when empty. Powers the agency-pass picker."""
    from tools.agencies import DEFAULT_CHIPS, suggest
    q = request.args.get("q") or ""
    return jsonify({
        "chips": DEFAULT_CHIPS,
        "matches": [{"name": a["name"], "abbr": a["abbr"],
                     "parent": a["parent"]} for a in suggest(q)],
    })


@app.get("/api/horizon")
def api_horizon():
    """The dialogue gate's read side: current payload + exact section preview.

    ``effective_status`` and ``eligibility_problems`` state whether that
    preview may ship under the current scope, sweep, and profile.
    """
    client = request.args.get("client_name") or ""
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    from agents.reports.capture_brief import _TPL, _horizon_section
    from agents.reports.horizon import (
        horizon_binding_problems, horizon_for_report, load_horizon,
    )
    payload = load_horizon(client)
    if not payload:
        return jsonify({"exists": False})
    # A full standalone document (locked stylesheet + the exact fragment) lets
    # the operator refine the real rendering. Eligibility is reported beside
    # it; a stale preview remains inspectable but can never enter a report.
    css = (_TPL / "capture_brief.css").read_text()
    frag = _horizon_section(payload.get("set"))
    preview_doc = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                   f"<style>{css}</style></head><body>"
                   f"<div class='page' style='padding:28px'>{frag}</div>"
                   f"</body></html>")
    from agents.reports.horizon import current_horizon_binding
    try:
        cb = current_horizon_binding(client)
    except Exception:  # noqa: BLE001 — problems helpers report it themselves
        cb = None
    binding_problems = horizon_binding_problems(payload, current_binding=cb)
    _, effective_status, render_problems = horizon_for_report(
        client, current_binding=cb)
    if payload.get("status") != "approved" and binding_problems:
        effective_status = "invalid"
        render_problems = binding_problems
    return jsonify({
        "exists": True,
        "status": payload.get("status"),
        "generated_at": payload.get("generated_at"),
        "approved_at": payload.get("approved_at"),
        "rounds": payload.get("rounds") or [],
        "set": payload.get("set") or {},
        "fact_count": len(payload.get("fact_bank") or []),
        "effective_status": effective_status,
        "binding": payload.get("binding"),
        "eligibility_problems": render_problems,
        "preview_doc": preview_doc,
    })


@app.post("/api/horizon/approve")
def api_horizon_approve():
    """The human act at the gate — instant, no LLM. Only an approved set ever
    renders into a deliverable; refining after approval reopens the gate."""
    body = request.get_json(force=True) or {}
    client = body.get("client_name") or ""
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    from agents.reports.horizon import (
        HorizonValidationError, approve_horizon, load_horizon, save_horizon,
    )
    payload = load_horizon(client)
    if not payload:
        return jsonify({"error": "no horizon draft exists — compose first"}), 409
    try:
        approved = approve_horizon(payload)
    except HorizonValidationError as exc:
        return jsonify({
            "error": "horizon approval failed deterministic validation",
            "problems": exc.problems,
            "status": payload.get("status"),
        }), 409
    save_horizon(approved)
    # Approval is the operator's primary action. Ledger projection follows the
    # durable approval and can never turn a successful approval into an error.
    try:
        _refresh_assess_ledger(client)
    except Exception:
        pass
    return jsonify({"status": "approved",
                    "items": len((payload.get("set") or {}).get("items") or [])})


@app.post("/api/strategy/revise")
def api_strategy_revise():
    """Hand-edit the strategy AT THE GATE. Only the whitelisted fields are
    applied; the merged strategy is re-validated by the schema, so a bad edit
    is a 400 and never persisted. An already-approved strategy is a 409."""
    from agents.review import RevisionError, revise
    body = request.get_json(force=True) or {}
    client = body.get("client_name")
    if not client:
        return jsonify({"error": "client_name is required"}), 400
    raw = body.get("updates") or {}
    if not isinstance(raw, dict):
        return jsonify({"error": "updates must be an object"}), 400
    # whitelist: the client can only touch operator-editable fields
    from agents.review import EDITABLE_FIELDS
    updates = {k: raw[k] for k in EDITABLE_FIELDS if k in raw}
    try:
        (workstation_id, is_native,
         expected_registry_sha256,
         expected_receipt_sha256) = _mutation_workstation_binding(
             client, body.get("workstation_id"))
        expected_sha256 = _expected_mutation_sha(body, native=is_native)
        packet = revise(
            client, updates, workstation_id=workstation_id,
            review_dir=REVIEW_DIR,
            expected_sha256=expected_sha256,
            native_owner=is_native,
            expected_registry_sha256=expected_registry_sha256,
            expected_receipt_sha256=expected_receipt_sha256)
    except RevisionError as e:
        # 409 for the approved-strategy block, 400 for a schema rejection
        code = 409 if "already approved" in str(e) else 400
        return jsonify({"error": str(e)}), code
    except (WorkstationError, WorkstationBindingError) as e:
        return jsonify({"error": str(e)}), 409
    except (OSError, KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    response = {
        "client_name": client,
        "status": packet.status.value,
        "revision_count": packet.revision_count,
        "revised_at": packet.revised_at.isoformat()
        if packet.revised_at else None,
        "strategy": packet.strategy.model_dump(mode="json"),
    }
    if is_native:
        response["packet_sha256"] = _packet_model_sha256(packet)
    return jsonify(response)


@app.post("/api/intake")
def api_intake():
    """Start a new client from the browser: save submission, run Step 1 as a job."""
    body = request.get_json(force=True)
    name = (body.get("client_name") or "").strip()
    if not name:
        return jsonify({"error": "client_name is required"}), 400
    # Duplicate identity guard (A4, 2026-07-12): intake for a name whose slug
    # already carries a review packet would OVERWRITE that client's strategy
    # gate and approvals ("Acme, Inc." and "Acme Inc" collide on one slug).
    # Existing clients are revised at the gate or archived first, never
    # silently re-intaken.
    def _identity(n: str) -> str:
        return "".join(c for c in (n or "").lower() if c.isalnum())

    collision = None
    if os.path.exists(os.path.join(
            REVIEW_DIR, f"{_slugify(name)}.review.json")):
        collision = _slugify(name)
    else:
        # near-duplicates mint DIFFERENT slugs ("Acme, Federal" vs
        # "Acme Federal"), so slug existence alone cannot protect the gate;
        # compare alphanumeric identity against every existing packet
        for packet_path in glob.glob(os.path.join(REVIEW_DIR, "*.review.json")):
            existing = _load_json(packet_path) or {}
            if _identity(existing.get("client_name")) == _identity(name):
                collision = os.path.basename(packet_path)[:-len(".review.json")]
                break
    if collision:
        return jsonify({
            "error": f"a client already exists at this identity "
                     f"({collision}). Open it and revise at the gate, "
                     "or archive it first; running intake again would "
                     "overwrite its strategy gate and approvals.",
            "slug": collision,
        }), 409
    submission = {
        "client_name": name,
        "website": body.get("website") or None,
        "contact_email": body.get("contact_email") or None,
        "primary_services": body.get("primary_services", ""),
        "differentiators": body.get("differentiators", ""),
        "past_performance": body.get("past_performance", ""),
        "certifications": [c.strip() for c in (body.get("certifications") or "").split(",") if c.strip()],
    }
    os.makedirs(INTAKE_DIR, exist_ok=True)
    path = os.path.join(INTAKE_DIR, f"{_slugify(name)}.submission.json")
    with open(path, "w") as f:
        json.dump(submission, f, indent=2)
    # Logo presence is part of the Command Center identity contract. Import
    # from the attested website (or the deterministic name-domain fallback)
    # before the client can appear on the board. A failed import is visible
    # in the UI as an asset-required state; it never becomes a broken image.
    try:
        from tools.brand_marks import prefetch_client_logo
        logo_imported = prefetch_client_logo(name)
    except Exception as exc:  # noqa: BLE001 - intake research still starts
        print(f"[marks] client logo import failed for {name}: {exc}", file=sys.stderr)
        logo_imported = False
    try:
        job_id = start_job("intake", name, {"submission_path": path})
    except JobRunning as exc:
        return jsonify({"error": "a job for this client is already running",
                        "running_job_id": exc.job_id}), 409
    return jsonify({"job_id": job_id, "slug": _slugify(name),
                    "logo_imported": logo_imported})


@app.get("/api/company_suggest")
def api_company_suggest():
    """Company typeahead for the intake form (Clearbit Autocomplete, free, no key).

    Prefix-matched upstream, so an overshoot typo ('Recorded Futures') returns
    nothing — we back off by trimming the query until something matches, and the
    UI offers it as 'did you mean'. Toggle: LILA_ENABLE_COMPANY_SUGGEST.
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2 or not is_enabled("company-suggest", True):
        return jsonify([])
    import httpx

    def hit(query: str) -> list:
        try:
            r = httpx.get(
                "https://autocomplete.clearbit.com/v1/companies/suggest",
                params={"query": query}, timeout=6.0,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001 — suggestions are best-effort, never block typing
            return []

    results, trimmed = hit(q), q
    while not results and len(trimmed) > 3:
        trimmed = trimmed[:-2]
        results = hit(trimmed)
    return jsonify(results[:6])


@app.post("/api/run")
def api_run():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    client, step = body.get("client_name"), body.get("step")
    if not isinstance(client, str) or not client.strip() or client != client.strip():
        return jsonify({"error": "client_name must be an exact nonblank string"}), 400
    if not isinstance(step, str) or not step:
        return jsonify({"error": "step must be a nonblank string"}), 400
    raw_args = body.get("args", {})
    if not isinstance(raw_args, dict):
        return jsonify({"error": "args must be a JSON object"}), 400
    args = dict(raw_args)
    if any(isinstance(key, str) and key.startswith("_lila_") for key in args):
        return jsonify({"error": "args contains a reserved server field"}), 400

    # one deliverable law (2026-07-18): the capture-brief press refuses
    # before any packet or gate work; the Signal Board is the product.
    if (step == "report" and args.get("kind") == "capture_brief"
            and not CAPTURE_BRIEF_ENABLED):
        return jsonify({"error": "the capture-brief press is retired "
                        "(one deliverable law, 2026-07-18); the Signal "
                        "Board is the product. Set LILA_CAPTURE_BRIEF=1 "
                        "to resurrect."}), 403

    # Compatibility migration: omission is accepted only by resolving the
    # one unambiguous legacy-current row. A posted dormant/stale id never
    # means "run whatever scope happens to be current now."
    try:
        exact, catalog = _workstation_catalog_for_slug(_slugify(client))
    except FileNotFoundError:
        return jsonify({"error": "no review packet for client"}), 404
    except WorkstationError as exc:
        return jsonify({"error": str(exc)}), 409
    if exact != client:
        return jsonify({"error": "client identity does not match review packet"}), 409
    current_rows = [row for row in catalog.workstations
                    if row.is_legacy_current]
    native_rows = [row for row in catalog.workstations
                   if getattr(row, "is_native", False)]
    posted_workstation_id = args.get("workstation_id")
    if posted_workstation_id is None:
        if native_rows:
            return jsonify({
                "error": "workstation_id is required once a native workstation exists",
            }), 409
        if len(current_rows) != 1:
            return jsonify({
                "error": "run cannot resolve one unambiguous current workstation",
            }), 409
        selected = current_rows[0]
        args["workstation_id"] = selected.ref.id
    else:
        if (not isinstance(posted_workstation_id, str)
                or _slugify(posted_workstation_id) != posted_workstation_id
                or not (posted_workstation_id == "all"
                        or posted_workstation_id.startswith("agency_"))):
            return jsonify({"error": "invalid workstation_id"}), 400
        selected = next((row for row in catalog.workstations
                         if row.ref.id == posted_workstation_id), None)
        if selected is None:
            return jsonify({"error": "no such workstation"}), 409
        if not (selected.is_legacy_current
                or getattr(selected, "is_native", False)):
            return jsonify({
                "error": "workstation has no executable strategy packet",
            }), 409

    try:
        if getattr(selected, "is_native", False):
            snapshot = load_packet_snapshot(
                client, workstation_id=selected.ref.id,
                review_dir=REVIEW_DIR, require_native_owner=True)
            packet = snapshot.packet
            args["_lila_expected_packet_sha256"] = snapshot.sha256
            args["_lila_expected_strategy_revision"] = packet.revision_count
            args["_lila_native_workstation"] = True
            args["_lila_expected_workstation_registry_sha256"] = (
                snapshot.registry_sha256)
            args["_lila_expected_workstation_receipt_sha256"] = (
                snapshot.receipt_sha256)
        else:
            # Catalog authorization selected the compatibility owner.  Force
            # that legacy family so a registry-less creation prefix with the
            # same id can never become the packet merely by existing.
            # The server process has no child workstation environment and the
            # established single-argument seam resolves the unqualified packet.
            packet = load_packet(
                client, workstation_id=selected.ref.id,
                review_dir=REVIEW_DIR, native_owner=False)
        # Gate: no spend without the selected packet's exact approval.
    except (OSError, ValueError, WorkstationBindingError) as exc:
        return jsonify({"error": str(exc)}), 409
    try:
        current_packet_scope = canonical_scope(packet.search_scope)
    except WorkstationError as exc:
        return jsonify({"error": f"current packet scope is invalid: {exc}"}), 409
    if current_packet_scope != selected.ref.scope:
        return jsonify({
            "error": "review packet scope changed before job launch; reload",
        }), 409
    if packet.status != ReviewStatus.APPROVED:
        return jsonify({"error": f"strategy for {client} is {packet.status.value}, not approved"}), 409
    if getattr(selected, "is_native", False) and step != "searches":
        return jsonify({
            "error": ("native workstation downstream approvals are not yet "
                      "partitioned; only the exact search may run"),
        }), 409
    release_capable_assess = (
        (step == "report" and args.get("kind", "teaser") == "capture_brief")
        or step in ("views", "agency_report")
    )
    if release_capable_assess:
        _, approval_status, approval_problems = _assess_release_gate(client)
        if approval_status != "approved":
            return jsonify({
                "error": "current Assess results require human approval before release",
                "review_approved": False,
                "approval_status": approval_status,
                "problems": approval_problems,
            }), 409
    release_capable_targeting = release_capable_assess or (
        step in ("candidate_review", "views", "agency_report", "target_report")
    )
    if release_capable_targeting:
        target_payload, target_status = _client_targets_payload(_slugify(client))
        readiness = ((target_payload or {}).get("targeting_readiness") or {})
        if target_status != 200 or not readiness.get("ready"):
            return jsonify({
                "error": ("a current, complete Targeting Review is required "
                          "before any client-ready output can run"),
                "targeting_review_approved": False,
                "problems": readiness.get("problems") or [
                    "current target inventory is unavailable"],
            }), 409
    if step in ("contacts", "target_report"):
        from agents.review import target_gate_status
        target_ok, target_problems = target_gate_status(
            client, review_dir=REVIEW_DIR)
        if not target_ok:
            return jsonify({
                "error": "Target is locked until you approve the assessment "
                         "and proceed to Target",
                "problems": target_problems,
            }), 409
    try:
        job_id = start_job(step, client, args)
    except JobRunning as exc:
        return jsonify({"error": f"a job for {client} is already running "
                                 f"({exc.step}); wait for it or check its log",
                        "running_job_id": exc.job_id}), 409
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"job_id": job_id})


@app.get("/api/job/<job_id>")
def api_job(job_id):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        job = _job_snapshot(job) if job else None
    return (jsonify(job), 200) if job else (jsonify({"error": "no such job"}), 404)


def _preview_banner(full: str, page: str) -> str:
    """Stamp a NOT-RELEASABLE banner on deliverable-family html served through
    the /report preview when the resolver refuses release (review finding #4):
    the internal preview stays available, but it can never silently read as a
    client-ready file while the download boundary is refusing it."""
    base = os.path.basename(full)
    if not (".federal_opportunity_assessment" in base
            or ".federal_opportunity_signals" in base
            or ".assessment.client" in base or ".capture_brief" in base):
        return page
    slug = base.split(".")[0]
    try:
        rs = release_state(slug, report_dir=REPORT_DIR, review_dir=REVIEW_DIR)
    except Exception:  # noqa: BLE001 — preview must never 500 on gate errors
        return page
    if rs["releasable"] and rs["path"] and os.path.realpath(rs["path"]) == full:
        return page
    reason = rs["reason"] if not rs["releasable"] else "superseded preview copy"
    banner = ("<div style=\"position:sticky;top:0;z-index:9999;"
              "background:#7a1f1f;color:#fff;font:600 13px/1.4 sans-serif;"
              "padding:8px 14px\">INTERNAL PREVIEW · NOT RELEASABLE · "
              + html_lib.escape(reason) +
              " · the Command Center download is the release boundary</div>")
    import re as _re
    stamped, n = _re.subn(r"(<body[^>]*>)", lambda m: m.group(1) + banner,
                          page, count=1)
    return stamped if n else banner + page


@app.get("/report")
def _report_route():
    return report_page()


@app.get("/ui/signal-board-logo-editor.js")
def signal_board_logo_editor_script():
    """Operator-only behavior; certified/exported HTML never contains it."""
    return send_from_directory(
        os.path.dirname(os.path.abspath(__file__)),
        "signal_board_logo_editor.js",
        mimetype="application/javascript",
        max_age=0,
    )


def _inject_signal_board_logo_editor(full: str, page: str) -> str:
    if request.args.get("logo_editor") != "1":
        return page
    if not _signal_board_editor_host_is_loopback():
        return page
    report_root = os.path.realpath(REPORT_DIR)
    if os.path.dirname(full) != report_root:
        return page
    name = os.path.basename(full)
    marker = ".federal_opportunity_signals"
    if marker not in name or "data-brand-kind=" not in page:
        return page
    slug = name.split(marker, 1)[0]
    if not slug or _slugify(slug) != slug:
        return page
    # Editing marks never changes the client-facing coverage projection.  The
    # integration map stays collapsed and client-safe here; detailed refresh
    # status belongs to the Command Center evidence ledger and job log.
    script = (
        '<script defer src="/ui/signal-board-logo-editor.js" '
        f'data-client="{html_lib.escape(slug, quote=True)}" '
        f'data-editor-token="{_SIGNAL_BOARD_EDITOR_TOKEN}" '
        f'data-download-url="/client/{html_lib.escape(slug, quote=True)}'
        '/download/signal-board.html"></script>'
    )
    if "</body>" in page:
        return page.replace("</body>", script + "</body>", 1)
    return page + script


def _report_client_slug(full: str) -> Optional[str]:
    """Bind a preview path to the same canonical client used by its shelf.

    A caller-supplied slug is accepted only when the exact path is registered
    for that client.  The fallback scans the authoritative depository so legacy
    report links also gain the Targeting Review gate without trusting filenames.
    """
    exact = os.path.realpath(full)
    requested = str(request.args.get("client") or "").strip()
    rows = [row for row in all_clients() if not row.get("error")]
    if requested and _slugify(requested) == requested:
        rows.sort(key=lambda row: row.get("slug") != requested)
    for row in rows:
        slug = str(row.get("slug") or "")
        if requested and slug != requested:
            continue
        for doc in client_documents(slug, row.get("client_name", "")):
            path = str(doc.get("path") or "")
            candidate = os.path.realpath(
                path if os.path.isabs(path) else os.path.join(ROOT, path))
            if candidate == exact:
                return slug
    if requested:
        return None
    # Compatibility for a freshly built report that has not yet entered the
    # shelf snapshot: require an exact canonical slug prefix, never a guess
    # from arbitrary prose filenames.
    base = os.path.basename(exact)
    for row in rows:
        slug = str(row.get("slug") or "")
        if base.startswith(slug + "."):
            return slug
    return None


def report_page():
    """Serve a deliverable as a real page. Reachable roots: <repo>/data and the
    operator's Desktop client folders (the mirrored docs shelf)."""
    rel = request.args.get("path", "")
    data_root = os.path.realpath(os.path.join(ROOT, "data"))
    desktop_root = os.path.realpath(_desktop_root())
    full = os.path.realpath(rel if os.path.isabs(rel) else os.path.join(ROOT, rel))
    allowed = full.startswith(data_root + os.sep) or full.startswith(desktop_root + os.sep)
    if not allowed or not os.path.isfile(full):
        return "not found", 404
    if full.endswith((".html", ".pdf")):
        slug = _report_client_slug(full)
        if slug is None:
            return _dns(
                "report preview is not bound to a canonical client; open it "
                "from that client's registered Report Library"
            )
        targeting_block = _targeting_download_gate(slug)
        if targeting_block is not None:
            return targeting_block
    if full.endswith(".html"):
        with open(full, encoding="utf-8") as f:
            page = f.read()
        page = _preview_banner(full, page)
        page = _inject_signal_board_logo_editor(full, page)
        return page, 200, {"Content-Type": "text/html; charset=utf-8"}
    if full.endswith(".md") or full.endswith(".json"):
        with open(full, encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/plain; charset=utf-8"}
    from flask import send_file
    return send_file(full)


@app.get("/api/file")
def api_file():
    """Artifact JSON for the inline viewer.

    Operational artifacts remain confined to ``data``.  The only client-tree
    exception is the exact foundation capability profile selected by the
    server allowlist: ``clients/<canonical-slug>/profile.json``.
    """
    rel = request.args.get("path", "")
    full = os.path.realpath(os.path.join(ROOT, rel))
    data_root = os.path.realpath(os.path.join(ROOT, "data"))
    clients_root = os.path.realpath(os.path.join(ROOT, "clients"))
    try:
        profile_rel = os.path.relpath(full, clients_root).split(os.sep)
    except ValueError:
        profile_rel = []
    profile_allowed = (
        len(profile_rel) == 2
        and profile_rel[0] == _slugify(profile_rel[0])
        and profile_rel[1] == "profile.json"
        and full.startswith(clients_root + os.sep)
    )
    data_allowed = full.startswith(data_root + os.sep)
    if not (data_allowed or profile_allowed) or not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404
    image_mimes = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    ext = os.path.splitext(full)[1].lower()
    if ext in image_mimes:
        with open(full, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return jsonify({
            "path": rel,
            "content": f"data:{image_mimes[ext]};base64,{encoded}",
            "encoding": "data-url",
            "media_type": image_mimes[ext],
        })
    try:
        with open(full, encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return jsonify({"error": "unsupported binary artifact"}), 415
    return jsonify({"path": rel, "content": content})


_HEALTH_CACHE: dict = {"at": 0.0, "data": None}
_HEALTH_TTL = 60.0


def _source_statuses(registry, fresh: bool = False) -> list[dict]:
    """Config switch + LIVE health per source. The lights must tell the truth:
    a source is only 'on' if it is enabled AND its probe passes. Probes run in
    parallel with hard timeouts and are cached 60s so opening the panel is cheap.

    A slow probe never hangs the panel: anything still running at the deadline
    reports honestly as unresolved instead of blocking 20 fast answers."""
    import time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

    now = time.time()
    if (not fresh and _HEALTH_CACHE["data"] is not None
            and now - _HEALTH_CACHE["at"] < _HEALTH_TTL):
        return _HEALTH_CACHE["data"]

    sources = registry.all()

    def probe(s):
        enabled = is_enabled(s.name, True) and s.enabled
        if not enabled:
            return {"name": s.name, "on": False, "status": "off",
                    "detail": "switched off (LILA_ENABLE_* in .env)"}
        try:
            ok, detail = s.healthcheck()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"probe crashed: {e}"
        return {"name": s.name, "on": bool(ok), "status": "up" if ok else "down",
                "detail": detail}

    DEADLINE_S = 15.0
    with ThreadPoolExecutor(max_workers=len(sources) or 1) as ex:
        futures = {ex.submit(probe, s): s for s in sources}
        data, deadline = [], time.time() + DEADLINE_S
        for fut, s in futures.items():
            try:
                data.append(fut.result(timeout=max(0.1, deadline - time.time())))
            except FutTimeout:
                data.append({"name": s.name, "on": False, "status": "slow",
                             "detail": f"probe exceeded {DEADLINE_S:.0f}s — endpoint "
                                       f"slow, not necessarily down; refresh to retry"})
            except Exception as e:  # noqa: BLE001
                data.append({"name": s.name, "on": False, "status": "down",
                             "detail": f"probe failed: {e}"})
    data.sort(key=lambda d: d["name"])
    _HEALTH_CACHE.update(at=now, data=data)
    return data


@app.post("/api/open_folder")
def api_open_folder():
    """Pop the client's Desktop folder in Finder. Restricted to Desktop subfolders."""
    name = (request.get_json(force=True) or {}).get("client", "")
    folder = os.path.realpath(os.path.join(_desktop_root(), name))
    if not folder.startswith(os.path.realpath(_desktop_root()) + os.sep) or not os.path.isdir(folder):
        return jsonify({"error": "no such client folder"}), 404
    subprocess.Popen(["open", folder])
    return jsonify({"ok": True})


@app.get("/api/toggles")
def api_toggles():
    import tools.api  # noqa: F401 — ensure every adapter is registered
    from tools.api.base import REGISTRY
    from tools.crm.base import FINDERS
    from agents.decisions.arbiters import ARBITERS

    fresh = request.args.get("fresh") == "1"
    return jsonify({
        "sources": _source_statuses(REGISTRY, fresh=fresh),
        "finders": [{"name": f.name, "on": is_enabled(f.name, True) and f.enabled}
                    for f in FINDERS.all()],
        "arbiters": [{"name": "arbiter-anthropic", "on": True}] + [
            {"name": a.name, "on": a.active(), "available": a.available()}
            for a in ARBITERS.all()
        ],
    })


# ────────────────────────── contacts view (contact graph) ──────────────────
# Operator-only window onto data/state/contact_graph/. The dashboard renders;
# the only writes are HUMAN merge decisions recorded via the graph's own
# decision path. Client-facing artifacts never come from here (lint enforces
# grade+source on anything that does render a contact).

_AGENCY_DOMAINS_PATH = os.path.join(ROOT, "data", "reference", "agency_domains.json")
_contacts_cache: dict = {}  # keyed by observations.jsonl mtime — cheap per-load reuse


def _cg_store():
    from tools.contact_graph import ContactGraphStore
    return ContactGraphStore()  # honors LILA_CONTACT_GRAPH_DIR (tests)


def _agency_domains() -> dict:
    try:
        with open(_AGENCY_DOMAINS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {"agencies": d.get("agencies") or {}, "offices": d.get("offices") or {}}
    except (OSError, json.JSONDecodeError):
        return {"agencies": {}, "offices": {}}


def agency_domain(agency: str | None, office_path: str | None = None) -> str | None:
    """Domain for the s2-favicon mark. Office match (more specific) wins over
    agency; no mapping -> None and the UI renders an initial-letter tile."""
    maps = _agency_domains()
    hay = (office_path or "").upper()
    for name, dom in maps["offices"].items():
        if name.upper() in hay:
            return dom
    return maps["agencies"].get((agency or "").strip().upper()) or None


def _freshness_weight(last_observed, today) -> int:
    """Same thresholds as the UI's freshness dot: <30d green, <180d amber."""
    if not last_observed:
        return 1
    days = (today - last_observed).days
    return 3 if days < 30 else (2 if days < 180 else 1)


def _grade_rank(p) -> int:
    return max(({"A": 3, "B": 2, "C": 1}.get(c.grade, 0) for c in p.channels), default=0)


def _contacts_snapshot():
    """(profiles, review, observations) for the current store state, cached on
    the observation file's mtime so one page load computes once."""
    from datetime import date as _date
    from tools.contact_graph import derive_profiles
    store = _cg_store()
    try:
        mtime = os.path.getmtime(store.observations_path)
    except OSError:
        mtime = 0.0
    dec_key = len(store.read_decisions())
    key = (str(store.root), mtime, dec_key, _date.today().isoformat())
    if _contacts_cache.get("key") != key:
        obs = store.read_observations()
        profiles, review = derive_profiles(obs, now=_date.today(),
                                           decisions=store.read_decisions())
        _contacts_cache.update({"key": key, "profiles": profiles,
                                "review": review, "observations": obs})
    return (_contacts_cache["profiles"], _contacts_cache["review"],
            _contacts_cache["observations"])


def _profile_row(p, today) -> dict:
    return {
        "person_name": p.person_name,
        "normalized_name": p.normalized_name,
        "agency": p.agency,
        "offices": p.offices,
        "naics": p.naics,
        "title": p.titles[0] if p.titles else None,
        "seat": p.role_types[0] if p.role_types else None,
        "sighting_count": p.sighting_count,
        "first_observed": p.first_observed.isoformat() if p.first_observed else None,
        "last_observed": p.last_observed.isoformat() if p.last_observed else None,
        "days_since": (today - p.last_observed).days if p.last_observed else None,
        "channels": [{"kind": c.kind, "value": c.value, "grade": c.grade,
                      "last_observed": c.last_observed.isoformat() if c.last_observed else None,
                      "source_url": c.source_url, "notice_id": c.notice_id}
                     for c in p.channels],
        "rotation": p.rotation.model_dump(mode="json") if p.rotation else None,
        "logo_domain": agency_domain(p.agency, p.offices[0] if p.offices else None),
    }


# ── contacts VIEW preferences ────────────────────────────────────────────────
# Reorder + hide in the Contacts view are a per-operator OVERLAY, never a graph
# edit: the master database is derived from append-only observations and stays
# untouched. Same shape as the docs shelf (_doc_prefs): {hidden, order} keyed by
# a stable (name, agency) view-key. Hiding tombstones the row FROM THE VIEW;
# ordering pins keys to the top; both are reversible from the UI.
def _ct_prefs_path() -> str:
    return os.path.join(os.path.dirname(REVIEW_DIR), "state", "contacts_view.json")


def _ct_prefs() -> dict:
    try:
        with open(_ct_prefs_path()) as f:
            p = json.load(f)
        return {"hidden": list(p.get("hidden") or []), "order": list(p.get("order") or [])}
    except (OSError, ValueError):
        return {"hidden": [], "order": []}


def _save_ct_prefs(prefs: dict) -> None:
    path = _ct_prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(prefs, f)


def _ct_key(normalized_name: str, agency: Optional[str]) -> str:
    """View-key: stable across re-derivations, independent of any graph id."""
    return f"{normalized_name}||{(agency or '').lower()}"


@app.post("/api/contacts/prefs")
def api_contacts_prefs():
    """Record a VIEW-ONLY change (hide / unhide / reorder). Never writes to the
    contact graph — the master database is not touched."""
    body = request.get_json(force=True) or {}
    prefs = _ct_prefs()
    if body.get("hide") and body["hide"] not in prefs["hidden"]:
        prefs["hidden"].append(body["hide"])
    if body.get("unhide"):
        prefs["hidden"] = [k for k in prefs["hidden"] if k != body["unhide"]]
    if body.get("unhide_all"):
        prefs["hidden"] = []
    if isinstance(body.get("order"), list):
        prefs["order"] = [k for k in body["order"] if isinstance(k, str)]
    _save_ct_prefs(prefs)
    return jsonify({"ok": True, "hidden": len(prefs["hidden"])})


@app.get("/api/contacts")
def api_contacts():
    from datetime import date as _date
    today = _date.today()
    profiles, _review, _obs = _contacts_snapshot()

    agency = (request.args.get("agency") or "").strip().lower()
    office = (request.args.get("office") or "").strip().lower()
    seat = (request.args.get("seat") or "").strip().lower()
    naics = (request.args.get("naics") or "").strip()
    grade = (request.args.get("grade") or "").strip().upper()
    q = (request.args.get("q") or "").strip().lower()

    def keep(p):
        if agency and agency not in (p.agency or "").lower():
            return False
        if office and not any(office in (o or "").lower() for o in p.offices):
            return False
        if seat and not any(seat in (t or "").lower() for t in p.titles + p.role_types):
            return False
        if naics and naics not in p.naics:
            return False
        if grade and not any(c.grade == grade for c in p.channels):
            return False
        if q and q not in p.person_name.lower():
            return False
        return True

    hits = [p for p in profiles if keep(p)]
    sort = request.args.get("sort") or "relevance"
    if sort == "name":
        hits.sort(key=lambda p: p.person_name.lower())
    elif sort == "last_observed":
        from datetime import date as _d
        hits.sort(key=lambda p: p.last_observed or _d.min, reverse=True)
    else:  # relevance: sightings x freshness, grade as tiebreak
        hits.sort(key=lambda p: (p.sighting_count * _freshness_weight(p.last_observed, today),
                                 _grade_rank(p), p.sighting_count), reverse=True)

    # VIEW overlay (never touches the graph): drop hidden rows; pin the
    # operator's ordered keys to the top in their sequence, rest keep the sort
    prefs = _ct_prefs()
    hidden = set(prefs["hidden"])
    hits = [p for p in hits if _ct_key(p.normalized_name, p.agency) not in hidden]
    rank = {k: i for i, k in enumerate(prefs["order"])}
    hits.sort(key=lambda p: rank.get(_ct_key(p.normalized_name, p.agency), len(rank)))

    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
    except ValueError:
        return jsonify({"error": "page and per_page must be integers"}), 400
    start = (page - 1) * per_page
    rows = []
    for p in hits[start:start + per_page]:
        r = _profile_row(p, today)
        r["key"] = _ct_key(p.normalized_name, p.agency)
        rows.append(r)
    return jsonify({
        "total": len(hits),
        "page": page,
        "per_page": per_page,
        "hidden": len(hidden),
        "order": prefs["order"],
        "profiles": rows,
    })


@app.get("/api/contacts/profile")
def api_contacts_profile():
    from datetime import date as _date
    from tools.contact_graph.names import normalize_name
    today = _date.today()
    name = normalize_name(request.args.get("name") or "")
    agency = (request.args.get("agency") or "").strip().lower()
    profiles, _review, obs = _contacts_snapshot()
    prof = next((p for p in profiles if p.normalized_name == name
                 and (p.agency or "").lower() == agency), None)
    if prof is None:
        return jsonify({"error": "no such profile"}), 404
    # an approved merge folds aliases into this profile; their observations belong here
    names_in = {prof.normalized_name}
    for d in _cg_store().read_decisions():
        if d.get("action") == "approve" and (d.get("agency") or "").lower() == agency:
            dn = {normalize_name(n) for n in d.get("names") or []}
            if dn & names_in:
                names_in |= dn
    rows = [o for o in obs
            if normalize_name(o.person_name) in names_in
            and (o.agency or "").lower() == agency]
    rows.sort(key=lambda o: (o.observed_at or _date.min), reverse=True)
    detail = _profile_row(prof, today)
    detail["observations"] = [{
        "notice_id": o.notice_id, "notice_type": o.notice_type,
        "observed_at": o.observed_at.isoformat() if o.observed_at else None,
        "channel_kind": o.channel_kind, "channel_value": o.channel_value,
        "channel_source": o.channel_source, "role_type": o.role_type,
        "title": o.title, "office_path": o.office_path, "naics": o.naics,
        "source_url": o.source_url, "api_ref": o.api_ref,
    } for o in rows]
    return jsonify(detail)


@app.get("/api/contacts/summary")
def api_contacts_summary():
    from datetime import date as _date
    today = _date.today()
    profiles, review, obs = _contacts_snapshot()
    offices = {o for p in profiles for o in p.offices}
    return jsonify({
        "profiles": len(profiles),
        "observations": len(obs),
        "offices": len(offices),
        "observed_30d": sum(1 for p in profiles
                            if p.last_observed and (today - p.last_observed).days < 30),
        "review_pending": len(review),
    })


@app.get("/api/contacts/review")
def api_contacts_review():
    _profiles, review, _obs = _contacts_snapshot()
    return jsonify(review)


@app.post("/api/contacts/review")
def api_contacts_review_decide():
    """Record a HUMAN merge decision (the dashboard renders; the human decides).
    Body: {"action": "approve"|"reject", "agency": str, "names": [str, ...]}"""
    from datetime import date as _date, datetime as _dt
    from tools.contact_graph import rebuild
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    names = body.get("names") or []
    if action not in ("approve", "reject") or len(names) < 2 or not body.get("agency"):
        return jsonify({"error": "need action=approve|reject, agency, and >=2 names"}), 400
    store = _cg_store()
    store.append_decision({"action": action, "agency": body["agency"],
                           "names": names, "decided_at": _dt.now().isoformat()})
    profiles, review = rebuild(store, now=_date.today())
    _contacts_cache.clear()  # decision changes derivation
    return jsonify({"ok": True, "profiles": len(profiles), "review_pending": len(review)})


# ────────────────────────── outreach rail (operator shortlist) ─────────────
# The rail on the right of every view. Auto-grown from research passes,
# human-curated here: reorder, delete (tombstoned), hand-corrected channels,
# enrichment round-trip via the Apollo handoff pattern. Enriched data lives in
# the outreach list only — never written into the contact graph.


def _outreach():
    from tools.contact_graph.outreach import OutreachList
    return OutreachList(store=_cg_store())


def _body_dict() -> dict:
    """The JSON body as a dict — a valid-JSON array/string/number body must be
    a 400, not an AttributeError 500."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@app.get("/api/outreach")
def api_outreach():
    from tools.contact_graph.outreach import OutreachReadError
    try:
        entries = _outreach().render(recover_corrupt=False)
    except OutreachReadError as exc:
        return jsonify({
            "error": f"outreach list is unavailable: {exc}",
            "state": "unavailable",
        }), 503
    return jsonify({"entries": entries})


@app.post("/api/outreach/refresh")
def api_outreach_refresh():
    report = _outreach().grow_from_research()
    return jsonify(report)


@app.post("/api/outreach/reorder")
def api_outreach_reorder():
    ids = _body_dict().get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return jsonify({"error": "ids must be a list of strings"}), 400
    if not _outreach().reorder(ids):
        return jsonify({"error": "ids must be a permutation of the current list"}), 400
    return jsonify({"ok": True})


@app.post("/api/outreach/remove")
def api_outreach_remove():
    eid = _body_dict().get("id")
    if not isinstance(eid, str) or not eid:
        return jsonify({"error": "id is required"}), 400
    if not _outreach().remove(eid):
        return jsonify({"error": "no such entry"}), 404
    return jsonify({"ok": True})


@app.post("/api/outreach/add")
def api_outreach_add():
    body = _body_dict()
    name = body.get("person_name")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "person_name is required"}), 400
    extras = {k: body.get(k) for k in ("agency", "title", "client", "email", "phone")}
    if any(v is not None and not isinstance(v, str) for v in extras.values()):
        return jsonify({"error": "agency, title, client, email and phone must be strings"}), 400
    agency = extras.pop("agency")
    return jsonify(_outreach().add_manual(name.strip(), agency, **extras))


@app.post("/api/outreach/override")
def api_outreach_override():
    body = _body_dict()
    eid = body.get("id")
    if not isinstance(eid, str) or not eid:
        return jsonify({"error": "id is required"}), 400
    fields = {k: body[k] for k in ("email", "phone", "person_name") if k in body}
    if any(v is not None and not isinstance(v, str) for v in fields.values()):
        return jsonify({"error": "email, phone and person_name must be strings"}), 400
    entry = _outreach().set_override(eid, **fields)
    if entry is None:
        return jsonify({"error": "no such entry"}), 404
    return jsonify(entry)


@app.post("/api/outreach/enrich")
def api_outreach_enrich():
    path, count = _outreach().export_enrichment_spec()
    if not count:
        return jsonify({"ok": True, "count": 0, "path": None,
                        "note": "nothing needs enrichment"})
    return jsonify({"ok": True, "count": count, "path": path})


@app.post("/api/outreach/merge")
def api_outreach_merge():
    rows = _body_dict().get("rows") or []
    if not isinstance(rows, list):
        return jsonify({"error": "rows must be a list"}), 400
    return jsonify(_outreach().merge_enriched(rows))


def main() -> int:
    from tools.env import load_env

    load_env()
    # ``load_env`` may read a legacy gitignored OpenAI arbiter credential
    # after this module's import-time scrub.  Remove it again before source
    # registration or Flask startup so the actual long-lived server process
    # can activate OpenAI only through the explicit session vault.
    _scrub_legacy_openai_environment()
    import tools.api.sam_gov  # noqa: F401 — register sources for the toggle panel
    import tools.api.usaspending  # noqa: F401
    import tools.api.web_search  # noqa: F401
    import tools.crm.apollo_handoff  # noqa: F401

    port = int(os.environ.get("LILA_UI_PORT", "8321"))
    print(f"LILA control room -> http://127.0.0.1:{port}", file=sys.stderr)
    app.run(host="127.0.0.1", port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
