"""Press Lead Gen orchestration (thin real path).

Documents and executes Build Plan steps 1-9. Opportunity assessment
stays the parent. LeadRow stays the child. This module does not
replace Assess, Market Map, or ``lila_release``.

    python -m agents.leadgen.press --assess <run.json>

Missing assess input fails closed. Notice-only input is refused. The
fail-closed default remains HOLD when four-leg evidence is thin. A
small evidenced subset may promote to WATCH / LEAD_T2 (at most one
LEAD_T1) through ``qualify_drafts``. Promotion never quota-fills.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from agents.assess.contracts import AssessRun
from tools.artifacts import atomic_write_json
from tools.atomic_io import atomic_write_text
from tools.slug import client_slug

from ._base import SCHEMA_VERSION, _FrozenContract
from .adapters.intake import IntakeCiteError, cite_dossier, cite_profile
from .contracts import LeadRow, OpportunityAssessment
from .enums import LEAD_TIER_LABELS, LeadTier
from .from_assess import (
    coerce_assess_run,
    coerce_target_actions,
    draft_lead_rows,
)
from .press_html import render_html
from .qualify import QUALIFIER_VERSION, overlays_for_promoted, qualify_drafts
from .traces import DecisionTrace

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_DIR = REPO_ROOT / "data" / "review"

MISSING_ASSESS = (
    "missing assess input: Press Lead Gen requires an AssessRun "
    "(or AssessRun-shaped dict). It will not invent leads from notices "
    "alone."
)
NOTICE_ONLY = (
    "notice-only input refused: Press Lead Gen will not invent "
    "leads from notices or sweep results. Pass an AssessRun (or "
    "AssessRun-shaped dict)."
)
NO_QUOTA_FILL = (
    "qualifier does not quota-fill; HOLD remains the fail-closed "
    "default when four-leg receipts are missing"
)
COVERAGE_PLACEHOLDER = (
    "Assess source coverage remains on AssessRun.coverage and any "
    "DecisionTrace rows from draft_lead_rows plus qualify_drafts. "
    "Press does not recompute the source-universe census."
)
TRACE_PLACEHOLDER = (
    "Decision traces from draft_lead_rows are retained and annotated "
    "when qualify_drafts promotes a row. Press does not rewrite "
    "Assess models."
)
MARKET_MAP_NOTE = (
    "Federal Market Map remains a separate deliverable. "
    "lila_release is untouched."
)

# Build Plan v1.0 is not in this checkout. These names are the locked
# product ruling plus the operator task for this stub.
BUILD_PLAN_STEPS: tuple[tuple[int, str, str], ...] = (
    (1, "load_ontology",
     ("Load client profile and optional intake dossier path. "
      "Step 1 CompanyDossier remains the ontology front door.")),
    (2, "discover_handoff",
     ("Hand off source-universe discovery to the existing search / "
      "sweep path. Do not reimplement discovery.")),
    (3, "qualify_handoff",
     "Hand off SAM qualify to the existing qualify path."),
    (4, "assess_handoff",
     ("Require an existing AssessRun. Opportunity assessment is "
      "parent. Do not re-run Assess.")),
    (5, "target_actions_handoff",
     ("Load an existing target_actions projection if supplied. "
      "Person-grain rows are not leads.")),
    (6, "draft_parents",
     "Mint OpportunityAssessment parents via draft_lead_rows."),
    (7, "draft_children",
     "Mint fail-closed LeadRow children via draft_lead_rows, then "
     "qualify a small evidenced subset."),
    (8, "publish_tiers",
     ("Publish a receipt listing rows by LeadTier. WATCH / LEAD_T2 "
      "may appear when four-leg receipts exist; at most one LEAD_T1.")),
    (9, "publish_coverage_trace",
     ("Retain WATCH / HOLD / REJECT receipts and coverage / "
      "decision-trace fields.")),
)


class PressLeadGenError(ValueError):
    """Fail-closed Press Lead Gen error."""


class PressStepStatus(str, Enum):
    """Per-step press status. Not a lead tier and not an Assess gate."""

    COMPLETE = "complete"
    HANDOFF = "handoff"
    SKIPPED = "skipped"
    FAILED = "failed"


class PressStepReceipt(_FrozenContract):
    """One Build Plan step as executed (or handed off) by press."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    step: int
    name: str
    status: PressStepStatus
    notes: str = ""
    artifact: str | None = None


class LeadTierBucket(_FrozenContract):
    """Draft ids grouped by ``lead_tier``. Active vs receipt is explicit."""

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    lead_tier: LeadTier
    label: str
    lead_ids: tuple[str, ...] = Field(default_factory=tuple)
    active: bool = False


class PressLeadGenReceipt(_FrozenContract):
    """Review artifact for Press Lead Gen.

    This is not a Market Map bundle and not an Assess run envelope.
    Primary persistence is branded HTML; JSON and optional Markdown are sidecars.
    ``stub`` is False when the real qualifier ran. Do not treat HOLD-only
    output as a stub receipt.
    """

    schema_version: Literal["leadgen.contracts.v1"] = SCHEMA_VERSION
    stub: bool = False
    qualifier: str = QUALIFIER_VERSION
    client_name: str = Field(min_length=1)
    client_slug: str = Field(min_length=1)
    assess_run_id: str = Field(min_length=1)
    as_of: datetime
    profile_path: str | None = None
    dossier_path: str | None = None
    dossier_schema_version: str | None = None
    identity_status: str | None = None
    steps: tuple[PressStepReceipt, ...]
    parents: tuple[OpportunityAssessment, ...]
    leads: tuple[LeadRow, ...]
    traces: tuple[DecisionTrace, ...]
    by_lead_tier: tuple[LeadTierBucket, ...]
    active_lead_t1: tuple[str, ...] = Field(default_factory=tuple)
    active_lead_t2: tuple[str, ...] = Field(default_factory=tuple)
    watch_receipts: tuple[str, ...] = Field(default_factory=tuple)
    hold_receipts: tuple[str, ...] = Field(default_factory=tuple)
    reject_receipts: tuple[str, ...] = Field(default_factory=tuple)
    coverage_placeholder: str = COVERAGE_PLACEHOLDER
    decision_trace_placeholder: str = TRACE_PLACEHOLDER
    market_map_untouched: Literal[True] = True
    eval_overlays: dict[str, dict[str, Any]] = Field(default_factory=dict)
    html_path: str | None = None
    json_path: str | None = None
    markdown_path: str | None = None


def leadgen_receipt_path(
    client_name: str, *, review_dir: str | Path,
) -> Path:
    """``data/review/<slug>.leadgen.json`` · human-gate receipt."""

    return Path(review_dir) / f"{client_slug(client_name)}.leadgen.json"


def leadgen_summary_path(
    client_name: str, *, review_dir: str | Path,
) -> Path:
    return Path(review_dir) / f"{client_slug(client_name)}.leadgen.md"


def leadgen_html_path(
    client_name: str, *, review_dir: str | Path,
) -> Path:
    """Desktop-friendly primary filename dated by the local press day."""
    return Path(review_dir).expanduser().resolve() / (
        f"{client_slug(client_name)}_Press_Lead_Gen_CLIENT_DELIVERABLE_"
        f"{datetime.now(timezone.utc).astimezone().date().isoformat()}.html"
    )


def _copy_html_to_desktop(client_name: str, html_path: str, html: str) -> None:
    """Mirror into an existing exact client folder; never interpret a path."""
    if client_name in {".", ".."} or Path(client_name).name != client_name:
        return
    folder = Path.home() / "Desktop" / client_name
    if folder.is_dir():
        destination = folder / Path(html_path).name
        if destination.resolve() != Path(html_path).resolve():
            try:
                atomic_write_text(str(destination), html)
            except OSError as exc:
                print(f"warning: Desktop copy failed ({exc}); primary HTML: "
                      f"{html_path}", file=sys.stderr)


def require_assess_input(
    assess: Mapping[str, Any] | Sequence[Any] | str | Path | None,
) -> Any:
    """Fail closed unless the caller supplied an AssessRun-shaped input.

    ``AssessLeadDrafts`` is not accepted here; that is an output of the
    mapper, not an Assess parent.
    """

    if assess is None:
        raise PressLeadGenError(MISSING_ASSESS)
    if isinstance(assess, AssessRun):
        return assess
    if isinstance(assess, (str, Path)):
        path = Path(assess)
        if not path.is_file():
            raise PressLeadGenError(
                f"missing assess input: assess path is not readable ({path})")
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PressLeadGenError(
                f"assess input is not valid JSON: {exc}") from exc
        return require_assess_input(payload)
    if _looks_like_notice_only(assess):
        raise PressLeadGenError(NOTICE_ONLY)
    try:
        return coerce_assess_run(assess)
    except (TypeError, ValidationError, ValueError) as exc:
        raise PressLeadGenError(
            "assess input is not an AssessRun: "
            f"{exc}. Notice-only input cannot mint leads.") from exc


def _looks_like_notice_only(payload: Any) -> bool:
    """True when the payload is notices / a sweep, not an AssessRun."""

    if isinstance(payload, Sequence) and not isinstance(
            payload, (str, bytes, Mapping)):
        return True
    if not isinstance(payload, Mapping):
        return False
    if _has_assess_identity(payload):
        return False
    keys = set(payload)
    notice_marks = {
        "notice_id", "NoticeId", "solicitation_number", "notices",
        "opportunities", "results",
    }
    return bool(keys & notice_marks)


def _has_assess_identity(payload: Mapping[str, Any]) -> bool:
    if payload.get("run_id") and (
            "live" in payload or "horizon" in payload
            or "partners" in payload):
        return True
    for key in ("assess_run", "run", "AssessRun"):
        inner = payload.get(key)
        if isinstance(inner, Mapping) and _has_assess_identity(inner):
            return True
    return False


def _load_target_actions(
    target_actions: Mapping[str, Any] | Sequence[Any]
    | str | Path | None,
) -> dict[str, Any]:
    if target_actions is None:
        return coerce_target_actions(None)
    if isinstance(target_actions, (str, Path)):
        path = Path(target_actions)
        if not path.is_file():
            raise PressLeadGenError(
                "target_actions path is not readable "
                f"({path})")
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PressLeadGenError(
                f"target_actions input is not valid JSON: {exc}") from exc
        return coerce_target_actions(payload)
    try:
        return coerce_target_actions(target_actions)
    except (TypeError, ValueError) as exc:
        raise PressLeadGenError(
            f"target_actions projection is not usable: {exc}") from exc


def _bind_dossier_cites(
    parents: tuple[OpportunityAssessment, ...],
    *,
    dossier_schema_version: str | None,
    identity_status: str | None,
) -> tuple[OpportunityAssessment, ...]:
    if not dossier_schema_version and not identity_status:
        return parents
    return tuple(
        parent.model_copy(update={
            "dossier_schema_version": dossier_schema_version,
            "identity_status": identity_status,
        })
        for parent in parents
    )


def _tier_buckets(
    leads: tuple[LeadRow, ...],
) -> tuple[LeadTierBucket, ...]:
    grouped: dict[LeadTier, list[str]] = {tier: [] for tier in LeadTier}
    for row in leads:
        grouped[row.lead_tier].append(row.lead_id)
    return tuple(
        LeadTierBucket(
            lead_tier=tier,
            label=LEAD_TIER_LABELS[tier],
            lead_ids=tuple(grouped[tier]),
            active=tier in (LeadTier.LEAD_T1, LeadTier.LEAD_T2),
        )
        for tier in LeadTier
    )


def _step(
    number: int, status: PressStepStatus, notes: str,
    *, artifact: str | None = None,
) -> PressStepReceipt:
    name = BUILD_PLAN_STEPS[number - 1][1]
    return PressStepReceipt(
        step=number, name=name, status=status, notes=notes,
        artifact=artifact,
    )


def run_press(
    *,
    assess: Any = None,
    target_actions: Any = None,
    client_name: str | None = None,
    dossier_path: str | Path | None = None,
    review_dir: str | Path | None = None,
    write_markdown: bool = False,
    as_of: datetime | None = None,
) -> PressLeadGenReceipt:
    """Execute Build Plan steps 1-9 and optionally persist.

    ``review_dir`` always writes branded CLIENT_DELIVERABLE HTML and a
    ``<slug>.leadgen.json`` sidecar (plus optional ``<slug>.leadgen.md``).
    Omit it for an in-memory receipt. HTML naming uses the local press day;
    the assessment clock remains visible in the report. The real
    qualifier always runs; ``stub`` is False on the receipt.
    """

    run = require_assess_input(assess)
    requested = " ".join(str(client_name or "").split())
    if requested and client_slug(requested) != client_slug(run.client_name):
        raise PressLeadGenError(
            f"client_name {requested!r} does not match AssessRun "
            f"client_name {run.client_name!r}")
    identity = run.client_name
    slug = client_slug(identity)
    clock = as_of or run.as_of
    if clock.tzinfo is None or clock.utcoffset() is None:
        clock = clock.replace(tzinfo=timezone.utc)

    profile = cite_profile(identity)
    try:
        dossier = cite_dossier(dossier_path)
    except IntakeCiteError as exc:
        raise PressLeadGenError(str(exc)) from exc
    step1_notes = []
    if profile["profile_path"]:
        step1_notes.append(f"capability profile loaded ({profile['profile_path']})")
    else:
        step1_notes.append(
            "capability profile not on disk; AssessRun identity stands")
    if dossier["dossier_path"]:
        step1_notes.append(
            f"intake dossier cited ({dossier['dossier_path']})")
    elif dossier_path:
        step1_notes.append("intake dossier path was provided and cited")
    else:
        step1_notes.append(
            "intake dossier path omitted; Step 1 remains optional")

    projection = _load_target_actions(target_actions)
    action_rows = list(projection.get("rows") or [])
    batch = draft_lead_rows(run, projection)
    batch = qualify_drafts(batch, run, projection)
    parents = _bind_dossier_cites(
        batch.parents,
        dossier_schema_version=dossier["dossier_schema_version"],
        identity_status=dossier["identity_status"],
    )
    leads = batch.leads
    overlays = overlays_for_promoted(batch)
    buckets = _tier_buckets(leads)
    by_tier = {bucket.lead_tier: bucket.lead_ids for bucket in buckets}

    steps = (
        _step(1, PressStepStatus.COMPLETE, "; ".join(step1_notes),
              artifact=dossier["dossier_path"] or profile["profile_path"]),
        _step(
            2, PressStepStatus.HANDOFF,
            "source-universe discovery stays on run_searches / the "
            "gate-designated sweep artifact. Stub does not launch a "
            "search.",
            artifact="data/cleaned/searches_<slug>.json",
        ),
        _step(
            3, PressStepStatus.HANDOFF,
            "SAM qualify stays on run_qualify / "
            "data/review/<slug>.qualify.json. Stub does not re-qualify "
            "notices.",
            artifact="data/review/<slug>.qualify.json",
        ),
        _step(
            4, PressStepStatus.HANDOFF,
            f"existing AssessRun {run.run_id} is the parent envelope. "
            "Opportunity assessment is not a lead.",
            artifact=run.run_id,
        ),
        _step(
            5, PressStepStatus.HANDOFF if action_rows
            else PressStepStatus.SKIPPED,
            (
                f"existing target_actions projection loaded "
                f"({len(action_rows)} row(s)); person-grain rows are "
                "not LeadRows."
                if action_rows else
                "target_actions omitted; empty projection is valid."
            ),
        ),
        _step(
            6, PressStepStatus.COMPLETE,
            f"draft_lead_rows minted {len(parents)} "
            "OpportunityAssessment parent(s).",
        ),
        _step(
            7, PressStepStatus.COMPLETE,
            f"draft_lead_rows minted {len(leads)} LeadRow child(ren); "
            f"{QUALIFIER_VERSION} ran. " + NO_QUOTA_FILL,
        ),
        _step(
            8, PressStepStatus.COMPLETE,
            "receipt lists rows by LeadTier. Active lead T1 / lead "
            "T2 stay empty unless four-leg receipts promoted them.",
        ),
        _step(
            9, PressStepStatus.COMPLETE,
            "WATCH / HOLD / REJECT receipts retained. Coverage and "
            "decision-trace fields stay honest. "
            + MARKET_MAP_NOTE,
        ),
    )

    html_path = None
    json_path = None
    markdown_path = None
    if review_dir is not None:
        review_dir = Path(review_dir).expanduser().resolve()
        html_path = str(leadgen_html_path(identity, review_dir=review_dir))
        json_path = str(leadgen_receipt_path(identity, review_dir=review_dir))
        if write_markdown:
            markdown_path = str(
                leadgen_summary_path(identity, review_dir=review_dir))
    receipt = PressLeadGenReceipt(
        client_name=identity,
        client_slug=slug,
        assess_run_id=run.run_id,
        as_of=clock,
        profile_path=profile["profile_path"],
        dossier_path=dossier["dossier_path"],
        dossier_schema_version=dossier["dossier_schema_version"],
        identity_status=dossier["identity_status"],
        steps=steps,
        parents=parents,
        leads=leads,
        traces=batch.traces,
        by_lead_tier=buckets,
        active_lead_t1=by_tier[LeadTier.LEAD_T1],
        active_lead_t2=by_tier[LeadTier.LEAD_T2],
        watch_receipts=by_tier[LeadTier.WATCH],
        hold_receipts=by_tier[LeadTier.HOLD],
        reject_receipts=by_tier[LeadTier.REJECT],
        eval_overlays=overlays,
        html_path=html_path,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    if html_path:
        html = render_html(receipt)
        atomic_write_text(html_path, html)
        _copy_html_to_desktop(identity, html_path, html)
    if json_path:
        atomic_write_json(json_path, receipt.model_dump(mode="json"))
    if markdown_path:
        atomic_write_text(markdown_path, render_markdown(receipt))
    return receipt


def render_markdown(receipt: PressLeadGenReceipt) -> str:
    """Operator-facing press summary. Internal review copy only."""

    lines = [
        f"# Press Lead Gen receipt · {receipt.client_name}",
        "",
        f"Qualifier {receipt.qualifier} ran. stub={receipt.stub}.",
        MARKET_MAP_NOTE,
        "",
        f"Assess run: {receipt.assess_run_id}",
        f"Parents: {len(receipt.parents)}",
        f"Lead rows: {len(receipt.leads)}",
        f"Active lead T1: {len(receipt.active_lead_t1)}",
        f"Active lead T2: {len(receipt.active_lead_t2)}",
        "",
        "## By lead tier",
        "",
    ]
    for bucket in receipt.by_lead_tier:
        posture = "active" if bucket.active else "receipt"
        lines.append(
            f"- {bucket.lead_tier.value} ({posture}): "
            f"{len(bucket.lead_ids)}"
        )
    lines.extend(["", "## Steps", ""])
    for step in receipt.steps:
        extra = f" · {step.artifact}" if step.artifact else ""
        lines.append(
            f"{step.step}. {step.name} · {step.status.value} · "
            f"{step.notes}{extra}"
        )
    lines.extend([
        "",
        "## Coverage placeholder",
        "",
        receipt.coverage_placeholder,
        "",
        "## Decision-trace placeholder",
        "",
        receipt.decision_trace_placeholder,
        "",
    ])
    if receipt.traces:
        lines.append("Retained traces:")
        for trace in receipt.traces:
            subject = trace.lead_id or trace.parent_assessment_id or ""
            note = " ".join(str(trace.notes or "").split())
            lines.append(f"- {trace.trace_id} · {subject} · {note}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Thin CLI for Press Lead Gen."""

    parser = argparse.ArgumentParser(
        description=(
            "Press Lead Gen (steps 1-9). Requires an AssessRun. "
            "Writes primary branded HTML and a JSON sidecar. Does not "
            "replace Market Map or lila_release."))
    parser.add_argument(
        "--assess", required=True,
        help="Path to an AssessRun JSON object (or a golden pack "
             "wrapping one under assess_run / run). Dump a current "
             "pointer with python -m agents.leadgen.export_assess "
             "--client \"Name\". Notice-only input is refused.")
    parser.add_argument(
        "--target-actions", default=None,
        help="Optional path to a target_actions projection JSON")
    parser.add_argument(
        "--client", default=None,
        help="Optional exact client name; must match the AssessRun")
    parser.add_argument(
        "--dossier", default=None,
        help="Optional Step 1 intake dossier JSON path (cite only)")
    parser.add_argument(
        "--review-dir", default=str(DEFAULT_REVIEW_DIR),
        help="Directory for primary CLIENT_DELIVERABLE HTML and JSON sidecar "
             "(default data/review)")
    parser.add_argument(
        "--markdown", action="store_true",
        help="Also write the optional <slug>.leadgen.md sidecar")
    args = parser.parse_args(argv)
    try:
        receipt = run_press(
            assess=args.assess,
            target_actions=args.target_actions,
            client_name=args.client,
            dossier_path=args.dossier,
            review_dir=args.review_dir,
            write_markdown=args.markdown,
        )
    except PressLeadGenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    json.dump(
        receipt.model_dump(mode="json"), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    if receipt.html_path:
        print(f"[out] {receipt.html_path}", file=sys.stderr)
    if receipt.json_path:
        print(f"[out] {receipt.json_path}", file=sys.stderr)
    if receipt.markdown_path:
        print(f"[out] {receipt.markdown_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
