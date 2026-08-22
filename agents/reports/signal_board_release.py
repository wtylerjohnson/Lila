"""Hash-bound release certificate for one pressed Signal Board.

The press runner owns the render and its INTERNAL verdict.  This module is
the independent hand-off from that verdict to release surfaces: it reads the
exact pressed bytes, repeats the deterministic client-safety lints, records
the canonical federal-link occurrences, and binds all of that evidence to
the HTML SHA-256.  A dashboard or download boundary can therefore consume a
certificate without trusting attributes in an arbitrary HTML file.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
from typing import Any

from agents.reports.link_integrity import extract_link_occurrences
from agents.reports.links import federal_link_domain
from agents.reports.lint import (
    lint_client_bleed,
    lint_emdash,
    lint_federal_link_construction,
    lint_sam_workspace_links,
    lint_whitelabel,
)
from agents.reports.signal_board import lint_signal_board
from tools.atomic_io import atomic_write_text
from agents.press_snapshot import client_slug


_CLEAN = "- CLEAN (client-final)"
_BLOCKED = "- DO-NOT-SEND (violations below)"
CERTIFICATE_SCHEMA_VERSION = 2
LINT_CONTRACT_VERSION = 1
CERTIFICATE_REQUIRED_FIELDS = frozenset({
    "schema_version", "lint_contract_version", "client_name",
    "presentation_name", "slug", "state", "release_eligible",
    "html_sha256", "presentation_sha256", "press_timestamp", "gate_verdict",
    "static_lints",
    "federal_link_manifest",
})
CERTIFICATE_OPTIONAL_FIELDS = frozenset({"assess_run_id"})


def validate_certificate_schema(payload: Any) -> None:
    """Validate the exact, versioned Signal Board QA envelope.

    Semantic binding to the HTML is release.py's responsibility; this is the
    certificate producer's one schema owner, shared by every consumer.
    """
    if not isinstance(payload, dict):
        raise ValueError("Signal Board QA certificate root is not an object")
    keys = set(payload)
    if (not CERTIFICATE_REQUIRED_FIELDS.issubset(keys)
            or keys - CERTIFICATE_REQUIRED_FIELDS - CERTIFICATE_OPTIONAL_FIELDS):
        raise ValueError("Signal Board QA certificate fields are not exact")
    if payload.get("schema_version") != CERTIFICATE_SCHEMA_VERSION:
        raise ValueError("Signal Board QA certificate schema is unsupported")
    if payload.get("lint_contract_version") != LINT_CONTRACT_VERSION:
        raise ValueError("Signal Board QA lint contract is unsupported")
    strings = (
        "client_name", "presentation_name", "slug", "html_sha256",
        "presentation_sha256", "press_timestamp", "gate_verdict",
    )
    if any(not isinstance(payload.get(name), str) or not payload[name]
           for name in strings):
        raise ValueError("Signal Board QA certificate identity is incomplete")
    for field in ("html_sha256", "presentation_sha256"):
        digest = payload[field]
        if (len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise ValueError("Signal Board QA certificate hash is invalid")
    try:
        timestamp = datetime.fromisoformat(
            payload["press_timestamp"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "Signal Board QA certificate timestamp is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Signal Board QA certificate timestamp is naive")
    if not isinstance(payload.get("release_eligible"), bool):
        raise ValueError("Signal Board QA release_eligible must be boolean")
    if not isinstance(payload.get("state"), str) or not payload["state"]:
        raise ValueError("Signal Board QA state is invalid")
    run_id = payload.get("assess_run_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id):
        raise ValueError("Signal Board QA assess_run_id is invalid")


def _lint_payload(result: Any) -> dict[str, Any]:
    """Normalize both Signal Board's tuple lint and standard LintResult."""
    if isinstance(result, tuple) and len(result) == 2:
        ok, violations = result
        return {"ok": bool(ok), "violations": [str(v) for v in violations]}
    violations = []
    for violation in getattr(result, "violations", ()):
        if hasattr(violation, "model_dump"):
            violations.append(violation.model_dump())
        else:
            violations.append(str(violation))
    return {"ok": bool(getattr(result, "ok", False)),
            "violations": violations}


def _static_lint_results(html_text: str, client_name: str) -> dict[str, dict]:
    """Re-run the deterministic release floor over the exact pressed bytes."""
    checks = {
        "signal_board": lint_signal_board(html_text),
        "sam_workspace_links": lint_sam_workspace_links(html_text),
        "federal_link_construction": lint_federal_link_construction(html_text),
        "whitelabel": lint_whitelabel(html_text),
        "client_bleed": lint_client_bleed(html_text, client_name=client_name),
        "emdash": lint_emdash(html_text),
    }
    return {name: _lint_payload(result) for name, result in checks.items()}


def _strict_assess_run_id(client_name: str) -> str | None:
    """Return only a fully validated current all-scope Assess run identity."""
    try:
        from agents.assess import current_run_identity
        identity = current_run_identity(client_name, "all")
    except Exception:  # noqa: BLE001 - release.py independently fails closed
        return None
    run_id = (identity or {}).get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def _exact_client_name(client_name: str) -> str:
    """Resolve the review-owned display identity for client-facing bytes."""
    from agents.review import canonical_client_name
    return canonical_client_name(client_name)


def _presentation_client_name(client_name: str) -> str:
    """Profile-owned capitalization; never a second release identity."""
    from tools.capability import client_display_name
    return client_display_name(client_name)


def _verdict(sidecar_text: str, client_name: str) -> str:
    lines = sidecar_text.splitlines()
    expected_header = f"# INTERNAL · {client_name} · Signal Board press trail"
    if not lines or lines[0] != expected_header:
        raise ValueError("INTERNAL sidecar has the wrong Signal Board header")
    if lines.count("## Gate verdict") != 1:
        raise ValueError("INTERNAL sidecar must contain exactly one gate verdict heading")
    verdicts = [line for line in lines if line in {_CLEAN, _BLOCKED}]
    if len(verdicts) != 1:
        raise ValueError("INTERNAL sidecar must contain exactly one recognized gate verdict")
    return verdicts[0]


def _presentation_snapshot(sidecar_text: str) -> str:
    lines = sidecar_text.splitlines()
    if lines.count("## Presentation snapshot") != 1:
        raise ValueError(
            "INTERNAL sidecar must contain one presentation snapshot")
    rows = [line.removeprefix("- SHA256 ") for line in lines
            if line.startswith("- SHA256 ")]
    if (len(rows) != 1 or len(rows[0]) != 64
            or any(ch not in "0123456789abcdef" for ch in rows[0])):
        raise ValueError("INTERNAL presentation snapshot hash is invalid")
    return rows[0]


def _repo_root(report_dir: Path) -> Path:
    resolved = report_dir.resolve()
    if resolved.name == "reports" and resolved.parent.name == "data":
        return resolved.parents[1]
    return resolved


def certify_signal_board(
    client_name: str,
    *,
    report_dir: str | Path,
    press_timestamp: datetime,
) -> Path:
    """Certify the exact artifact selected by the fresh INTERNAL verdict.

    CLEAN bytes must pass every repeated static lint.  A DO-NOT-SEND render
    still receives a hash-bound negative certificate so preview surfaces can
    explain the current state without ever treating it as releasable.
    """
    report_root = Path(report_dir)
    if press_timestamp.tzinfo is None or press_timestamp.utcoffset() is None:
        raise ValueError("press timestamp must be timezone-aware")
    slug = client_slug(client_name)
    exact_client_name = _exact_client_name(client_name)
    if client_slug(exact_client_name) != slug:
        raise ValueError("review client identity does not match the press slug")
    stem = report_root / f"{slug}.federal_opportunity_signals"
    internal_path = Path(f"{stem}.internal.md")
    try:
        sidecar_text = internal_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"fresh INTERNAL sidecar is unreadable: {exc}") from exc
    verdict = _verdict(sidecar_text, client_name)
    presentation_sha256 = _presentation_snapshot(sidecar_text)
    clean = verdict == _CLEAN
    html_path = Path(f"{stem}{'' if clean else '.DO-NOT-SEND'}.html")
    try:
        html_bytes = html_path.read_bytes()
        html_text = html_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"pressed Signal Board is unreadable: {exc}") from exc

    presentation_name = _presentation_client_name(exact_client_name)
    expected_title = (
        f"<title>{escape(presentation_name)} · "
        "Federal Opportunity Pre-Assessment</title>"
    )
    if html_text.count(expected_title) != 1:
        raise ValueError("pressed Signal Board title does not match the exact client")

    from agents.reports.signal_board_presentation import presentation_digest

    current_presentation = presentation_digest(
        exact_client_name, root=_repo_root(report_root))
    if current_presentation != presentation_sha256:
        raise ValueError(
            "presentation marks changed during or after the Signal Board press")

    static_lints = _static_lint_results(html_text, exact_client_name)
    failed = sorted(name for name, result in static_lints.items()
                    if not result.get("ok"))
    if clean and failed:
        raise ValueError(
            "CLEAN Signal Board failed independent release lint(s): "
            + ", ".join(failed)
        )

    manifest = [
        {
            "url": occurrence.url,
            "builder": occurrence.builder,
            "record_id": occurrence.record_id,
            "reconciled": occurrence.reconciled,
        }
        for occurrence in extract_link_occurrences(html_text)
        if federal_link_domain(occurrence.url) is not None
    ]
    payload: dict[str, Any] = {
        "schema_version": CERTIFICATE_SCHEMA_VERSION,
        "lint_contract_version": LINT_CONTRACT_VERSION,
        "client_name": exact_client_name,
        "presentation_name": presentation_name,
        "slug": slug,
        "state": "release" if clean else "do_not_send",
        "release_eligible": clean,
        "html_sha256": hashlib.sha256(html_bytes).hexdigest(),
        "presentation_sha256": presentation_sha256,
        "press_timestamp": press_timestamp.isoformat().replace("+00:00", "Z"),
        "gate_verdict": verdict,
        "static_lints": static_lints,
        "federal_link_manifest": manifest,
    }
    run_id = _strict_assess_run_id(exact_client_name)
    if run_id is not None:
        payload["assess_run_id"] = run_id

    qa_path = Path(f"{stem}.qa.json")
    atomic_write_text(qa_path, json.dumps(
        payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return qa_path
