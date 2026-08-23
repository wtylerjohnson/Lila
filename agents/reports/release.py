"""ONE verdict for "is this client's assessment releasable" (2026-07-11).

Review of the release-gate build found four defects with one root cause:
release state was derived independently by the badge, the shelf, the download
boundary, and the PDF export, from different sources (QA sidecar only,
filename only, mismatched time bases, approval sometimes folded in and
sometimes not). This module is now the single derivation point; the dashboard
is a window onto it, never a second source of truth.

Rules encoded here:
- Candidate families for a gate scope: the stable sidecar-certified
  federal_opportunity_assessment, its explicit .DO-NOT-SEND sibling, the
  .assessment.client view pair (filename contract), and — for all-scope
  gates only — the hash-certified Signal Board and legacy capture_brief
  pairs. Once either Signal Board sibling exists, that family is the sole
  all-scope internal compatibility artifact; retired families remain history
  only. This resolver does not define the external product contract.
- ONE time basis: every candidate is ordered by its html mtime. Sidecar
  freshness/pairing is a validity check on the stable candidate, never the
  ordering key (a sidecar written after the html must not outrank a newer
  blocked build written between the two).
- A newer DO-NOT-SEND always supersedes an older clean file; at equal mtimes
  the blocked candidate wins (fail closed).
- The Assess approval axis is folded IN: releasable means the newest
  candidate is clean AND the operator's approval is current. The badge can
  never again say CLIENT READY while the download boundary refuses.
"""

from __future__ import annotations

import hashlib
from html import escape
import json
import os
from pathlib import Path
from typing import Optional

from agents.assess.approval import assess_approval_for_release

_HTML_SHA_MEMO: dict = {}
_RUN_IDENTITY_MEMO: dict = {}


def _current_run_identity_memo(client_name: str, designator: str,
                               pointer, review_dir):
    """T5 seam consumption (Cycle 4, 2026-07-12), memoized on pointer stat.

    agents.assess.current_run_identity validates the full immutable run
    behind the pointer, so a malformed, drifted, or cross-scope pointer can
    never supply a run id here even when its raw bytes look plausible. The
    (mtime_ns, size, inode) memo keeps dashboard polls from re-validating an
    unchanged run on every badge refresh; any pointer replacement changes
    the stat key and misses.
    """
    try:
        st = pointer.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    memo_key = (str(pointer), str(review_dir or ""))
    hit = _RUN_IDENTITY_MEMO.get(memo_key)
    if hit and hit[0] == key:
        return hit[1]
    from agents.assess import current_run_identity
    identity = current_run_identity(
        client_name, designator, review_dir=review_dir)
    _RUN_IDENTITY_MEMO[memo_key] = (key, identity)
    return identity


def _mtime_or_none(path: str):
    """getmtime that treats a mid-request removal as absence, never a crash."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _mtime_ns_or_none(path: str):
    """Nanosecond mtime for strict pointer-to-artifact build ordering."""
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def html_sha256(path: str):
    """sha256 of report HTML, memoized on replacement-sensitive file state.

    ``mtime`` and size alone are not an identity: an atomic replacement can
    preserve both.  Inode catches replacement and ctime catches an in-place
    rewrite whose mtime is restored, so a cached digest can never authorize
    bytes the download boundary would reject.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)
    hit = _HTML_SHA_MEMO.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None
    _HTML_SHA_MEMO[path] = (key, digest)
    return digest


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def stable_status(path: str) -> dict:
    """Release state for one stable-name Federal Opportunity Assessment.

    The HTML filename carries no state. Its same-family QA JSON is therefore
    mandatory and must certify THIS html: sidecars carrying html_sha256
    (build pairing) must match the html on disk, which makes a cross-build
    DRAFT-html/RELEASE-sidecar mispair unreadable as a release. Legacy
    sidecars without the hash fall back to the mtime freshness rule.
    Missing, unreadable, stale, mismatched, DRAFT, and explicitly ineligible
    sidecars all fail closed while the HTML remains a review preview.
    """
    qa_path = path[:-len(".html")] + ".qa.json"
    payload = _load_json(qa_path)
    html_mtime = _mtime_or_none(path)
    qa_mtime = _mtime_or_none(qa_path)
    fresh = bool(qa_mtime and html_mtime and qa_mtime >= html_mtime)
    updated = max(html_mtime or 0.0, qa_mtime or 0.0)
    paired = None
    if isinstance(payload, dict) and payload.get("html_sha256"):
        paired = payload["html_sha256"] == html_sha256(path)
    releasable = bool(
        isinstance(payload, dict)
        and payload.get("state") == "release"
        and payload.get("release_eligible") is not False
        and (paired if paired is not None else fresh)
    )
    if releasable:
        reason = "release"
    elif qa_mtime is None:
        reason = "missing QA sidecar"
    elif not isinstance(payload, dict):
        reason = "unreadable QA sidecar"
    elif paired is False:
        reason = "QA sidecar certifies a different build (concurrent-build mispair)"
    elif paired is None and not fresh:
        reason = "stale QA sidecar"
    elif payload.get("release_eligible") is False:
        reason = "release ineligible"
    else:
        reason = f"state is {payload.get('state') or 'unknown'}"
    return {"path": path, "qa_path": qa_path, "qa": payload,
            "qa_pass": releasable, "reason": reason, "updated": updated,
            "html_mtime": html_mtime}


def signal_board_status(path: str, *, exact_client_name: str,
                        presentation_name: str, slug: str,
                        qa_path: str | None = None) -> dict:
    """Validate a hash-bound Signal Board release certificate.

    Signal Board QA is intentionally stricter than the legacy stable family:
    there is no mtime-only compatibility mode.  The exact client, artifact
    slug, HTML bytes, independent lint floor, and occurrence-level federal
    link manifest must all be present.  QA must also be at least as new as
    the HTML even when the bytes happen to be identical, so an old
    certificate cannot re-authorize a fresh press whose certifier crashed.
    """
    qa_path = qa_path or path[:-len(".html")] + ".qa.json"
    payload = _load_json(qa_path)
    html_mtime_ns = _mtime_ns_or_none(path)
    qa_mtime_ns = _mtime_ns_or_none(qa_path)
    from agents.reports.signal_board_presentation import presentation_digest

    report_root = Path(path).resolve().parents[2]
    try:
        current_presentation_sha256 = presentation_digest(
            exact_client_name, root=report_root)
    except (OSError, TypeError, ValueError):
        current_presentation_sha256 = None
    presentation_current = bool(
        isinstance(payload, dict)
        and isinstance(payload.get("presentation_sha256"), str)
        and payload.get("presentation_sha256") == current_presentation_sha256
    )
    fresh = bool(
        html_mtime_ns is not None
        and qa_mtime_ns is not None
        and qa_mtime_ns >= html_mtime_ns
    )
    paired = bool(
        isinstance(payload, dict)
        and isinstance(payload.get("html_sha256"), str)
        and payload.get("html_sha256") == html_sha256(path)
    )
    try:
        from agents.reports.signal_board_release import (
            validate_certificate_schema,
        )
        validate_certificate_schema(payload)
        schema_valid = True
    except (TypeError, ValueError):
        schema_valid = False
    exact_identity = bool(
        isinstance(payload, dict)
        and payload.get("client_name") == exact_client_name
        and payload.get("slug") == slug
    )
    try:
        with open(path, encoding="utf-8") as f:
            html_text = f.read()
    except (OSError, UnicodeDecodeError):
        html_text = None
    expected_title = (
        f"<title>{escape(presentation_name)} · "
        "Federal Opportunity Pre-Assessment</title>"
    )
    presentation_valid = bool(
        isinstance(payload, dict)
        and payload.get("presentation_name") == presentation_name
        and isinstance(html_text, str)
        and html_text.count(expected_title) == 1
    )
    manifest = payload.get("federal_link_manifest") \
        if isinstance(payload, dict) else None
    manifest_shape_valid = isinstance(manifest, list) and all(
        isinstance(row, dict)
        and set(row) == {"url", "builder", "record_id", "reconciled"}
        and isinstance(row.get("url"), str) and bool(row.get("url"))
        and isinstance(row.get("builder"), str) and bool(row.get("builder"))
        and isinstance(row.get("record_id"), str)
        and bool(row.get("record_id"))
        and isinstance(row.get("reconciled"), bool)
        for row in manifest or []
    )
    manifest_matches = False
    if manifest_shape_valid and isinstance(html_text, str):
        try:
            from agents.reports.link_integrity import extract_link_occurrences
            from agents.reports.links import federal_link_domain
            occurrences = [
                {
                    "url": row.url,
                    "builder": row.builder,
                    "record_id": row.record_id,
                    "reconciled": row.reconciled,
                }
                for row in extract_link_occurrences(html_text)
                if federal_link_domain(row.url) is not None
            ]
            manifest_matches = occurrences == manifest
        except (OSError, UnicodeDecodeError):
            manifest_matches = False
    manifest_valid = manifest_shape_valid and manifest_matches
    manifest_release_valid = bool(
        manifest_valid
        and all(row["reconciled"] is True for row in manifest)
    )
    static_lints = payload.get("static_lints") \
        if isinstance(payload, dict) else None
    required_lints = {
        "signal_board", "sam_workspace_links",
        "federal_link_construction", "whitelabel",
        "client_bleed", "emdash",
    }
    lints_shape_valid = bool(
        isinstance(static_lints, dict)
        and set(static_lints) == required_lints
        and all(isinstance(row, dict)
                and set(row) == {"ok", "violations"}
                and isinstance(row.get("ok"), bool)
                and isinstance(row.get("violations"), list)
                for row in static_lints.values())
    )
    lints_valid = bool(
        lints_shape_valid
        and all(row["ok"] is True for row in static_lints.values())
    )
    clean_certificate = bool(
        isinstance(payload, dict)
        and payload.get("state") == "release"
        and payload.get("release_eligible") is True
        and payload.get("gate_verdict") == "- CLEAN (client-final)"
        and manifest_release_valid
        and lints_valid
    )
    blocked_certificate = bool(
        isinstance(payload, dict)
        and payload.get("state") == "do_not_send"
        and payload.get("release_eligible") is False
        and payload.get("gate_verdict") == (
            "- DO-NOT-SEND (violations below)")
        and lints_shape_valid
    )
    certificate_valid = bool(
        schema_valid
        and fresh and presentation_current
        and paired and exact_identity and presentation_valid
        and manifest_valid
        and (clean_certificate or blocked_certificate)
    )
    releasable = certificate_valid and clean_certificate
    if releasable:
        reason = "release"
    elif qa_mtime_ns is None:
        reason = "missing Signal Board QA certificate"
    elif not isinstance(payload, dict):
        reason = "unreadable Signal Board QA certificate"
    elif not schema_valid:
        reason = "Signal Board QA certificate schema is unsupported"
    elif not fresh:
        reason = "stale Signal Board QA certificate"
    elif not presentation_current:
        reason = (
            "presentation marks changed after the certified build; "
            "refresh the Federal Opportunity Pre-Assessment"
        )
    elif not exact_identity:
        reason = "Signal Board QA certifies a different client"
    elif not presentation_valid:
        reason = (
            "Signal Board QA/title does not match the current client "
            "presentation; re-press it"
        )
    elif not paired:
        reason = "Signal Board QA certifies a different build"
    elif certificate_valid and blocked_certificate:
        reason = "state is do_not_send"
    elif payload.get("state") not in {"release", "do_not_send"}:
        reason = f"state is {payload.get('state') or 'unknown'}"
    elif (payload.get("state") == "release") != (
            payload.get("release_eligible") is True):
        reason = "release ineligible"
    elif payload.get("gate_verdict") not in {
            "- CLEAN (client-final)",
            "- DO-NOT-SEND (violations below)",
    }:
        reason = "Signal Board gate verdict is invalid"
    elif not lints_shape_valid or (
            payload.get("state") == "release" and not lints_valid):
        reason = "Signal Board independent lint certificate is invalid"
    else:
        reason = "Signal Board federal-link manifest is invalid"
    return {
        "path": path,
        "qa_path": qa_path,
        "qa": payload,
        "qa_pass": releasable,
        "certificate_valid": certificate_valid,
        "presentation_current": presentation_current,
        "reason": reason,
        "updated": _mtime_or_none(path) or 0.0,
        "html_mtime": _mtime_or_none(path),
    }


def _candidates(slug: str, stem: str, report_dir: str,
                all_scope: bool, *, exact_client_name: str,
                presentation_name: str) -> list[dict]:
    """Every artifact that can claim to be this scope's assessment, each with
    clean/blocked state from its OWN family contract and its html mtime."""
    out: list[dict] = []
    stable = os.path.join(report_dir, f"{stem}.federal_opportunity_assessment.html")
    st_mtime = _mtime_or_none(stable)
    if st_mtime is not None:
        st = stable_status(stable)
        # mtime_ns captured AT SCAN TIME so the strict-freshness leg judges
        # the same build the clean/QA verdict judged; re-statting later let a
        # mid-request regeneration pass pointer freshness on a verdict formed
        # from the older build (Cycle 3 review finding, 2026-07-12).
        out.append({"path": stable, "clean": st["qa_pass"],
                    "reason": st["reason"], "updated": st_mtime,
                    "family": "stable", "qa_path": st["qa_path"],
                    "mtime_ns": _mtime_ns_or_none(stable)})
    explicit_bad = os.path.join(
        report_dir, f"{stem}.federal_opportunity_assessment.DO-NOT-SEND.html")
    eb_mtime = _mtime_or_none(explicit_bad)
    if eb_mtime is not None:
        out.append({"path": explicit_bad, "clean": False,
                    "reason": "DO-NOT-SEND filename", "updated": eb_mtime,
                    "family": "stable_bad", "qa_path": None,
                    "mtime_ns": _mtime_ns_or_none(explicit_bad)})
    view_clean = os.path.join(report_dir, f"{stem}.assessment.client.html")
    vc_mtime = _mtime_or_none(view_clean)
    if vc_mtime is not None:
        out.append({"path": view_clean, "clean": True,
                    "reason": "clean view filename (gated at build)",
                    "updated": vc_mtime, "family": "view", "qa_path": None,
                    "mtime_ns": _mtime_ns_or_none(view_clean)})
    view_bad = os.path.join(report_dir,
                            f"{stem}.assessment.client.DO-NOT-SEND.html")
    vb_mtime = _mtime_or_none(view_bad)
    if vb_mtime is not None:
        out.append({"path": view_bad, "clean": False,
                    "reason": "DO-NOT-SEND view filename", "updated": vb_mtime,
                    "family": "view_bad", "qa_path": None,
                    "mtime_ns": _mtime_ns_or_none(view_bad)})
    if all_scope:
        signal_board = os.path.join(
            report_dir, f"{slug}.federal_opportunity_signals.html")
        sb_mtime = _mtime_or_none(signal_board)
        if sb_mtime is not None:
            sb = signal_board_status(
                signal_board,
                exact_client_name=exact_client_name,
                presentation_name=presentation_name,
                slug=slug,
            )
            out.append({"path": signal_board, "clean": sb["qa_pass"],
                        "reason": sb["reason"], "updated": sb_mtime,
                        "family": "signal_board", "qa_path": sb["qa_path"],
                        "mtime_ns": _mtime_ns_or_none(signal_board)})
        signal_bad = os.path.join(
            report_dir,
            f"{slug}.federal_opportunity_signals.DO-NOT-SEND.html")
        sb_bad_mtime = _mtime_or_none(signal_bad)
        if sb_bad_mtime is not None:
            out.append({"path": signal_bad, "clean": False,
                        "reason": "DO-NOT-SEND Signal Board filename",
                        "updated": sb_bad_mtime,
                        "family": "signal_board_bad", "qa_path": None,
                        "mtime_ns": _mtime_ns_or_none(signal_bad)})
        legacy = os.path.join(report_dir, f"{slug}.capture_brief.html")
        lg_mtime = _mtime_or_none(legacy)
        if lg_mtime is not None:
            out.append({"path": legacy, "clean": True,
                        "reason": "legacy clean filename", "updated": lg_mtime,
                        "family": "legacy", "qa_path": None,
                        "mtime_ns": _mtime_ns_or_none(legacy)})
        legacy_bad = os.path.join(report_dir,
                                  f"{slug}.capture_brief.DO-NOT-SEND.html")
        lb_mtime = _mtime_or_none(legacy_bad)
        if lb_mtime is not None:
            out.append({"path": legacy_bad, "clean": False,
                        "reason": "legacy DO-NOT-SEND filename",
                        "updated": lb_mtime, "family": "legacy_bad",
                        "qa_path": None,
                        "mtime_ns": _mtime_ns_or_none(legacy_bad)})
    return out


def release_state(client_name: str, *, report_dir: str,
                  review_dir: Optional[str] = None) -> dict:
    """THE releasable verdict for a client's current gate scope.

    Newest html mtime wins across every family; a blocked candidate beats a
    clean one at the same instant; the Assess approval axis is folded in.
    Returns a dict the badge, shelf, download, PDF export, and /report banner
    all consume: {releasable, path, blocked_path, reason, approval_status,
    approval_problems, family, updated, preview_path}.
    """
    from agents.review import (
        _artifact_slug, canonical_client_name, gate_designator,
    )

    identity_problem = None
    try:
        exact_client_name = canonical_client_name(client_name, review_dir)
    except Exception as exc:  # noqa: BLE001 - identity ambiguity fails closed
        exact_client_name = client_name
        identity_problem = f"review client identity is invalid: {exc}"
    presentation_name = exact_client_name
    if identity_problem is None:
        try:
            from tools.capability import client_display_name
            presentation_name = client_display_name(exact_client_name)
        except Exception as exc:  # noqa: BLE001 - presentation drift fails closed
            identity_problem = (
                "client presentation identity is invalid: "
                f"{type(exc).__name__}: {str(exc)[:120]}"
            )
    slug = _artifact_slug(exact_client_name)
    try:
        designator = gate_designator(exact_client_name, review_dir)
    except Exception:  # noqa: BLE001 — unreadable packet: fail closed below
        designator = None
    # same construction as review.artifact_stem, resolved once per call
    stem = f"{slug}.{designator}" if designator else slug

    cands = _candidates(
        slug, stem, report_dir, all_scope=designator is None,
        exact_client_name=exact_client_name,
        presentation_name=presentation_name)
    if designator is None:
        # Internal release-selection law: once either Signal Board sibling
        # exists, that family owns the all-scope compatibility surface. Newer
        # retired assessment/capture artifacts remain shelf history and cannot
        # mask or supersede it. The external product slots are defined elsewhere.
        signal_cands = [
            candidate for candidate in cands
            if candidate["family"] in {"signal_board", "signal_board_bad"}
        ]
        if signal_cands:
            cands = signal_cands
    if not cands:
        return {"releasable": False, "path": None, "blocked_path": None,
                "reason": "no assessment on file", "approval_status": None,
                "approval_problems": [], "family": "missing", "updated": 0.0,
                "preview_path": None}

    # one time basis; blocked wins ties (fail closed)
    newest = max(cands, key=lambda c: (c["updated"], 0 if c["clean"] else 1))

    approval_status, approval_problems = None, []
    if identity_problem:
        approval_status = "invalid"
        approval_problems = [identity_problem]
    else:
        try:
            _, approval_status, approval_problems = assess_approval_for_release(
                exact_client_name, review_dir=review_dir)
        except Exception as e:  # noqa: BLE001 — gate machinery down: fail closed
            approval_status = "invalid"
            approval_problems = [f"approval gate unavailable: {e}"]

    strict_artifact_problem = None
    if approval_status == "approved":
        # A current strict pointer makes every older HTML a pre-cutover build,
        # even when that HTML once had a clean QA sidecar. The runner must
        # regenerate the exact scope after the pointer refresh before any
        # download surface can call it current. ABSENT preserves legacy
        # behavior. Run-id pairing (Cycle 4, 2026-07-12): mtime freshness
        # alone cannot prove WHICH run priced a build after restores, copies,
        # or same-instant writes, so a stable-family sidecar must also stamp
        # the exact current run id.
        try:
            from agents.assess.ledger import current_assess_pointer_path
            pointer = current_assess_pointer_path(
                exact_client_name, designator or "all")
            # Absence must mean what the approval leg's Path.exists() means
            # (ENOENT, ENOTDIR, ELOOP, EBADF all read as "no pointer"), or a
            # corrupt runs dir flips pointer-LESS clients from releasable to
            # blocked while approval stays dormant (Cycle 3 review finding,
            # 2026-07-12). A raise here (e.g. EACCES: a pointer may exist but
            # is unprovable) still falls to the outer fail-closed catch.
            try:
                pointer_mtime_ns = (pointer.stat().st_mtime_ns
                                    if pointer.exists() else None)
            except FileNotFoundError:
                pointer_mtime_ns = None
            if pointer_mtime_ns is not None:
                identity = _current_run_identity_memo(
                    exact_client_name, designator or "all", pointer,
                    review_dir)
                pointer_run_id = (identity or {}).get("run_id")
                # scan-time stat: the freshness verdict must judge the same
                # build the clean/QA verdict judged, never a mid-request
                # regeneration
                artifact_mtime_ns = newest.get("mtime_ns")
                if artifact_mtime_ns is None:
                    strict_artifact_problem = (
                        "current assessment artifact disappeared during release "
                        "validation")
                elif artifact_mtime_ns <= pointer_mtime_ns:
                    strict_artifact_problem = (
                        "assessment predates the current strict Assess run; "
                        "regenerate and QA this scope before release")
                elif not isinstance(pointer_run_id, str) or not pointer_run_id:
                    strict_artifact_problem = (
                        "current strict Assess pointer does not validate to "
                        "a run identity; the live lane fails closed")
                elif newest["family"] in {"stable", "signal_board"}:
                    qa_payload = (_load_json(newest["qa_path"])
                                  if newest.get("qa_path") else None)
                    stamped = (qa_payload.get("assess_run_id")
                               if isinstance(qa_payload, dict) else None)
                    if stamped != pointer_run_id:
                        strict_artifact_problem = (
                            "QA sidecar does not certify the current strict "
                            "Assess run; rebuild and QA this scope"
                            if stamped else
                            "QA sidecar carries no strict run stamp; rebuild "
                            "this scope under the current strict run")
        except Exception as exc:  # noqa: BLE001 - release freshness fails closed
            strict_artifact_problem = (
                "strict Assess artifact freshness is unavailable: "
                f"{type(exc).__name__}: {str(exc)[:120]}")

    if not newest["clean"]:
        reason = newest["reason"]
        releasable = False
    elif approval_status != "approved":
        reason = ("current Assess results are not approved for release: "
                  + "; ".join(approval_problems[:2] or [str(approval_status)]))
        releasable = False
    elif strict_artifact_problem:
        reason = strict_artifact_problem
        releasable = False
    else:
        reason = "release"
        releasable = True

    return {"releasable": releasable,
            "path": newest["path"] if releasable else None,
            "blocked_path": None if releasable else newest["path"],
            "reason": reason,
            "approval_status": approval_status,
            "approval_problems": approval_problems,
            "family": newest["family"],
            "updated": newest["updated"],
            "preview_path": newest["path"]}
