#!/usr/bin/env python3
"""Press the Federal Opportunity Pre-Assessment, client-final, reproducibly.

    python3 run_signal_board.py --client "Insignary" --replay --render-date 2026-07-13

One command renders the board with zero post-render human editing: seals
and logos embed at render, links are normalized to public form, and the
default output IS the deliverable (no editor script, no toolbar, no
residue; no editor variant exists in the generator).

--replay renders exclusively from the stored sweep and the stored
operator-approved content artifact: zero LLM calls, zero live API calls,
entity-resolution telemetry isolated, and both report clocks pinned to
--render-date. Same stored inputs + same render date = byte-identical
output (the SHA256 prints on completion).

Gates (all fail closed, none weakened): figure reconciliation against the
stored sweep (docs/VERIFICATION_POLICY.md), per-figure freshness at the
pinned render date, data-current divergence BEFORE render, residual
workspace links, board conformance, white-label, em dash, client bleed.
A gated press writes .DO-NOT-SEND.html plus the INTERNAL sidecar and
exits nonzero; it never reaches the Desktop folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ROOT, "data", "reports")


def _slug(name: str) -> str:
    from tools.slug import client_slug
    return client_slug(name)


def _fmt_board_date(d: date) -> str:
    return f"{d.day:02d} {d.strftime('%b').upper()} {d.year}"


def _press(args) -> int:
    from tools.atomic_io import atomic_write_text as _atomic_write_text

    from agents.reports.board_content import (
        load_board_content, load_decision_makers,
        reconcile_federal_link_records, reconcile_figures,
        reconciliation_error, validate_decision_maker_routes,
    )
    from agents.reports.link_integrity import (
        agency_doc_claim_warnings, audit_client_links,
        dead_link_violations, manual_link_check_lines,
    )
    from agents.reports.lint import (
        lint_client_bleed, lint_emdash, lint_federal_link_construction,
        lint_sam_workspace_links, lint_whitelabel,
    )
    from agents.reports.links import (
        format_workspace_link_issue, normalize_sam_workspace_links,
    )
    from agents.reports.document import build_document
    from agents.reports.signal_board import (
        build_model, canonical_federal_link_manifest, lint_signal_board,
        render_signal_board,
    )
    from agents.reports.signal_board_presentation import presentation_digest
    from agents.reports.verification import FRESHNESS_MAX_AGE_DAYS
    from agents.review import sweep_artifact_path

    try:
        presentation_sha256 = presentation_digest(args.client, root=ROOT)
    except (OSError, TypeError, ValueError) as exc:
        print(
            "[press] REFUSED: presentation marks are invalid: "
            f"{type(exc).__name__}: {str(exc)[:180]}",
            file=sys.stderr,
        )
        return 2

    if args.render_date:
        render_date = date.fromisoformat(args.render_date)
    elif args.replay:
        print("[press] --replay requires --render-date (a replay is a pinned, "
              "byte-identical render)", file=sys.stderr)
        return 2
    else:
        render_date = date.today()

    sweep_path = sweep_artifact_path(args.client)
    if not os.path.exists(sweep_path):
        print(f"[press] no stored sweep at {sweep_path}; run the search step "
              "through the Command Center first", file=sys.stderr)
        return 2
    with open(sweep_path, encoding="utf-8") as f:
        sweep = json.load(f)
    print(f"[press] stored sweep {sweep_path} "
          f"(generated_at {sweep.get('generated_at') or 'ABSENT'})",
          file=sys.stderr)

    from tools.api.recompete import load_calendar
    recompete_calendar = load_calendar(args.client)

    from tools.capability import _slug as _canonical
    content = load_board_content(args.client)
    if content is not None and \
            _canonical(content.client_name) != _canonical(args.client):
        # exact client binding: path placement is not identity proof, and
        # a foreign artifact must never render into this client's board
        print(f"[press] REFUSED: stored board content belongs to "
              f"{content.client_name!r}, not {args.client!r}; path "
              "placement is not identity proof", file=sys.stderr)
        return 2
    if content is not None:
        try:
            validate_decision_maker_routes(
                content,
                expected_people=load_decision_makers(args.client),
            )
        except ValueError as exc:
            print(
                "[press] REFUSED: decision-maker route integrity failed: "
                f"{exc}", file=sys.stderr)
            return 2
        if getattr(content, "composition_mode", "operator") == "machine":
            from agents.assessment_chain import validate_current_compose_pair
            from agents.reports.composer import (
                load_client_inputs,
                validate_machine_client_relevance_contract,
                validate_machine_executive_summary_contract,
                validate_machine_horizon_contract,
                validate_machine_teaming_contract,
            )
            from tools.capability import load_profile
            profile = load_profile(args.client)
            if profile is None or not profile.is_populated():
                print(
                    "[press] REFUSED: machine content has no profile-owned "
                    "exact client identity", file=sys.stderr)
                return 2
            profile_client = profile.client_name
            if _canonical(profile_client) != _canonical(args.client):
                print(
                    "[press] REFUSED: machine content profile belongs to "
                    f"{profile_client!r}, not {args.client!r}; path "
                    "placement is not identity proof", file=sys.stderr)
                return 2
            if sweep.get("client") != profile_client:
                print(
                    "[press] REFUSED: stored sweep belongs to "
                    f"{sweep.get('client')!r}, not the profile-owned client "
                    f"{profile_client!r}; path placement is not identity "
                    "proof",
                    file=sys.stderr,
                )
                return 2
            if (recompete_calendar is not None
                    and recompete_calendar.get("client") != profile_client):
                print(
                    "[press] REFUSED: recompete calendar belongs to "
                    f"{recompete_calendar.get('client')!r}, not the "
                    f"profile-owned client {profile_client!r}; path "
                    "placement is not identity proof",
                    file=sys.stderr,
                )
                return 2
            try:
                content, compose_trail_md = validate_current_compose_pair(
                    ROOT, _canonical(args.client), profile_client,
                    return_trail_text=True)
            except ValueError as exc:
                print(
                    "[press] REFUSED: machine compose pair failed its "
                    f"trail binding: {exc}", file=sys.stderr)
                return 2
            try:
                validate_machine_teaming_contract(content)
            except ValueError as exc:
                print(
                    "[press] REFUSED: machine Section 03 route evidence "
                    f"failed: {exc}", file=sys.stderr)
                return 2
            try:
                validate_machine_horizon_contract(
                    content, trail_md=compose_trail_md)
            except ValueError as exc:
                print(
                    "[press] REFUSED: machine prospective horizon evidence "
                    f"failed: {exc}", file=sys.stderr)
                return 2
            try:
                relevance_inputs = load_client_inputs(profile_client)
                validate_machine_client_relevance_contract(
                    content,
                    trail_md=compose_trail_md,
                    taxonomy=relevance_inputs["taxonomy"],
                    profile=profile,
                    sweep=sweep,
                    calendar=recompete_calendar,
                    require_current_sources=True,
                )
            except (RuntimeError, ValueError) as exc:
                print(
                    "[press] REFUSED: machine client relevance evidence "
                    f"failed: {exc}", file=sys.stderr)
                return 2
            try:
                validate_machine_executive_summary_contract(
                    content, sweep=sweep)
            except ValueError as exc:
                print(
                    "[press] REFUSED: machine executive summary evidence "
                    f"failed: {exc}", file=sys.stderr)
                return 2
            print(f"[press] machine-composed content artifact loaded "
                  f"({len(content.figures)} registered figures); "
                  "machine-screened relevance law applies",
                  file=sys.stderr)
        else:
            print(f"[press] operator-approved content artifact loaded "
                  f"({len(content.figures)} registered figures)",
                  file=sys.stderr)
    else:
        print("[press] no content artifact; derived-only board", file=sys.stderr)

    violations: list[str] = []
    render_moment = datetime(render_date.year, render_date.month,
                             render_date.day, 23, 59,
                             tzinfo=timezone.utc)

    # figure reconciliation: content vs the stored sweep, record-scoped
    reconciliation = None
    if content is not None:
        reconciliation = reconcile_figures(content, sweep)
        err = reconciliation_error(reconciliation)
        if err:
            violations.append(err)
        print(f"[reconcile] backed={len(reconciliation.backed)} "
              f"attested={len(reconciliation.attested)} "
              f"unbacked={len(reconciliation.unbacked)} "
              f"unregistered={len(reconciliation.unregistered)}",
              file=sys.stderr)

    # per-figure freshness at the render date (the Phase 1 gate, no special
    # cases: stale or unknown blocks the same way)
    if content is not None:
        for fig in content.figures:
            if fig.retrieved_at is None:
                if not fig.analyst_attested:
                    violations.append(
                        f"FRESHNESS_UNKNOWN: {fig.text} has no retrieval "
                        f"time and no attestation")
                continue
            age = (render_moment - fig.retrieved_at).total_seconds() / 86400.0
            if age > FRESHNESS_MAX_AGE_DAYS:
                violations.append(
                    f"FRESHNESS_STALE: {fig.text} retrieved "
                    f"{fig.retrieved_at.date().isoformat()}, "
                    f"{int(age)} days before the render date "
                    f"(limit {FRESHNESS_MAX_AGE_DAYS})")

    link_reconciliation = None
    if content is not None:
        link_reconciliation = reconcile_federal_link_records(
            content, sweep, now=render_moment)
        violations += [violation.detail
                       for violation in link_reconciliation.violations]
        print(f"[link records] reconciled="
              f"{sum(r.reconciled for r in link_reconciliation.records)} "
              f"blocked="
              f"{sum(not r.reconciled for r in link_reconciliation.records)}",
              file=sys.stderr)

    doc = build_document(args.client, searches=sweep, qualify={},
                         as_of=render_date)
    # recompete-aware theses: the calendar (when the recompete step has run
    # for this client) plus the sweep's forecast lane feed every teaming
    # play's successor-event join; a missing calendar surfaces as CALENDAR
    # GAP notes in the INTERNAL sidecar, never as silence
    recompete_inputs = {
        "calendar": recompete_calendar,
        "forecasts": ((sweep.get("results") or {})
                      .get("forecast_signals") or {}).get("matched") or [],
    }
    try:
        model = build_model(doc, report_date=_fmt_board_date(render_date),
                            content=content, recompete=recompete_inputs,
                            federal_link_status=(
                                link_reconciliation.status_map
                                if link_reconciliation else None))
    except ValueError as e:
        # data-currency divergence fails BEFORE render: no artifact at all
        print(f"[press] REFUSED before render: {e}", file=sys.stderr)
        return 2

    # Main-first merged gate order (capability-relevance + link integrity):
    # build/reconcile the structured model, run the main relevance engine on
    # that model and stored evidence, render final bytes, normalize/audit every
    # link, then apply conformance lints before stamping or promotion. Neither
    # gate can authorize or bypass the other.
    #
    # relevance trail (2026-07-17): every rendered opportunity is scored
    # against the client taxonomy over the stored-sweep evidence that cites
    # its records. Operator-approved content is the operator's inclusion
    # judgment: scores render in the INTERNAL trail (zero-evidence rows
    # visibly so). A machine-screened board could not render a zero-evidence
    # card at all; code membership alone never renders.
    relevance_trail = []
    try:
        from tools.relevance.engine import (
            relevance_basis_violations, score_opportunity_evidence,
        )
        from tools.relevance.taxonomy import derived_taxonomy, load_taxonomy
        from tools.relevance.scope import in_scope, load_engagement_scope
        engagement_scope = load_engagement_scope(args.client)
        taxonomy = load_taxonomy(args.client) or derived_taxonomy(args.client)
        machine_content = (content is not None and getattr(
            content, "composition_mode", "operator") == "machine")
        if machine_content and taxonomy is None:
            # machine-origin law: machine-composed content without a
            # judgeable taxonomy fails CLOSED, never silently skips
            violations.append(
                "MACHINE_ORIGIN: no capability taxonomy or profile for "
                f"{args.client}; machine-composed content cannot be "
                "judged under the machine-screened law and fails closed")
        if machine_content and taxonomy is not None:
            # the machine-screened law covers EVERY machine band, and
            # every machine card must carry its own bound evidence: a
            # stable source identity, quote, kind, the agency used for
            # scope judgment, and a positive scope verdict. A blank or
            # unknown agency never satisfies the law merely because the
            # generic scope engine returns unresolved.
            for band, rows in (("best_fit", content.best_fit),
                               ("competitors", content.competitors),
                               ("acquisition_pathways",
                                content.acquisition_pathways),
                               ("teaming", content.teaming),
                               ("horizon", content.horizon)):
                for row in rows:
                    ident = str(row.get("title") or row.get("partner")
                                or row.get("label") or "untitled")
                    evidence = row.get("machine_evidence")
                    if not (isinstance(evidence, dict)
                            and evidence.get("source_identity")
                            and evidence.get("source_kind")
                            and evidence.get("quote")
                            and evidence.get("in_scope") is True
                            and evidence.get("scope_basis")):
                        violations.append(
                            f"MACHINE_EVIDENCE: {band} row {ident!r} "
                            "lacks its bound evidence (identity, kind, "
                            "quote, or positive scope proof)")
                        continue
                    agency = str(evidence.get("agency") or "")
                    if engagement_scope is None:
                        continue
                    if not agency:
                        violations.append(
                            f"MACHINE_EVIDENCE: {band} row {ident!r} "
                            "carries a blank agency; blank agency cannot "
                            "satisfy the machine-screened scope law")
                        continue
                    inside, basis = in_scope({"agency": agency},
                                             engagement_scope)
                    if not inside:
                        violations.append(
                            f"OFF_SCOPE: machine-composed {band} row "
                            f"{ident!r} is outside the engagement scope "
                            f"({basis})")
                    elif "unresolved" in basis:
                        violations.append(
                            f"MACHINE_EVIDENCE: {band} row {ident!r} "
                            f"agency {agency!r} cannot resolve against "
                            "the engagement scope; an unknown agency "
                            "cannot satisfy the machine-screened law")
        if taxonomy is not None:
            _id_re = re.compile(r"\b[0-9A-Z]{2}[0-9A-Z]{6,16}\b")
            cards = []
            for card in model.get("best_fit") or []:
                ids = set()
                if machine_content:
                    # machine-composed cards carry structured identities;
                    # use them directly, never regex over labels
                    for ev in card.get("evidence") or []:
                        for key in ("sam_notice_guid",
                                    "award_generated_id"):
                            if ev.get(key):
                                ids.add(str(ev[key]))
                    if card.get("sam_notice_guid"):
                        ids.add(str(card["sam_notice_guid"]))
                else:
                    for ev in card.get("evidence") or []:
                        ids |= set(_id_re.findall(str(ev.get("label") or "")))
                        ids |= set(_id_re.findall(str(ev.get("url") or "")))
                    ids |= set(_id_re.findall(str(card.get("url") or "")))
                verdict = score_opportunity_evidence(sweep, sorted(ids),
                                                     taxonomy)
                inside, basis = in_scope(
                    {"agency": card.get("agency_name") or ""},
                    engagement_scope)
                if not inside:
                    from tools.relevance.engine import (
                        OFF_CODE_STRONG_THRESHOLD as _STRONG,
                    )
                    strong = bool(verdict.core_terms) and \
                        verdict.score >= _STRONG
                    verdict = verdict.model_copy(update={
                        "off_scope": True, "off_scope_signal": strong,
                        "scope_basis": basis, "relevant": False})
                else:
                    verdict = verdict.model_copy(
                        update={"scope_basis": basis})
                cards.append((card.get("title") or "untitled", verdict))
            # producer-origin truth: absent content OR machine-composed
            # content is judged machine-screened; only operator-authored
            # content keeps the hand-approved standing (legacy default)
            machine_screened = content is None or machine_content
            violations += relevance_basis_violations(
                cards, machine_screened=machine_screened)
            for label, verdict in cards:
                relevance_trail.append((label, verdict, machine_screened))
    except Exception as e:  # noqa: BLE001 - trail failure is loud, never silent
        violations.append(f"RELEVANCE_TRAIL: scoring failed "
                          f"({type(e).__name__}: {str(e)[:120]})")

    html = render_signal_board(model)
    try:
        presentation_after = presentation_digest(args.client, root=ROOT)
    except (OSError, TypeError, ValueError) as exc:
        presentation_after = None
        violations.append(
            "PRESENTATION_SNAPSHOT: presentation marks became invalid "
            f"during press ({type(exc).__name__}: {str(exc)[:140]})"
        )
    if presentation_after != presentation_sha256:
        violations.append(
            "PRESENTATION_SNAPSHOT: presentation marks changed during press; "
            "run the press again"
        )
    federal_link_manifest = canonical_federal_link_manifest(model)

    normalized = normalize_sam_workspace_links(html)
    html = normalized.html
    for rewrite in normalized.rewrites:
        print(f"[links] workspace -> public: {rewrite.source_url}",
              file=sys.stderr)
    violations += [format_workspace_link_issue(x) for x in normalized.remaining]

    agency_doc_links = [
        (figure.source_url, "Figure provenance")
        for figure in (content.figures if content is not None else [])
        if (getattr(figure.source_system, "value", figure.source_system)
            == "agency_doc" and figure.source_url)
    ]
    link_audit = audit_client_links(
        html,
        enabled=not args.replay and not getattr(args, "offline", False),
        extra_links=agency_doc_links,
        expected_federal_links=federal_link_manifest,
    )
    violations += [violation.detail
                   for violation in dead_link_violations(link_audit)]
    manual_link_checks = manual_link_check_lines(link_audit)
    claim_warnings = agency_doc_claim_warnings(
        content.figures if content is not None else [], link_audit)

    ok, board_violations = lint_signal_board(html)
    violations += board_violations
    for lint in (lint_whitelabel, lint_emdash, lint_sam_workspace_links):
        violations += [v.detail for v in lint(html).violations]
    violations += [v.detail for v in lint_federal_link_construction(
        html, expected_links=federal_link_manifest).violations]
    violations += [v.detail
                   for v in lint_client_bleed(html, args.client).violations]

    slug = _slug(args.client)
    stamp = "" if not violations else ".DO-NOT-SEND"
    out = args.out or os.path.join(
        REPORT_DIR, f"{slug}.federal_opportunity_signals{stamp}.html")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    _atomic_write_text(out, html)
    if not args.out:
        # a press supersedes its other-stamp sibling: a stale clean file must
        # never sit beside a newer DO-NOT-SEND, and vice versa
        sibling = os.path.join(
            REPORT_DIR, f"{slug}.federal_opportunity_signals"
                        f"{'.DO-NOT-SEND' if not stamp else ''}.html")
        if os.path.exists(sibling):
            os.remove(sibling)
            print(f"[out] superseded stale sibling {sibling}", file=sys.stderr)
    sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
    print(f"[out] {out}", file=sys.stderr)
    print(f"sha256 {sha}")

    # INTERNAL sidecar: provenance notes, reconciliation, gate verdicts.
    # Never copied to the client folder.
    internal = [f"# INTERNAL · {args.client} · Signal Board press trail",
                f"_render date {render_date.isoformat()} · replay="
                f"{bool(args.replay)} · never leaves the shop_", "",
                "## Presentation snapshot",
                f"- SHA256 {presentation_sha256}", "",
                "## Gate verdict",
                "- CLEAN (client-final)" if not violations else
                "- DO-NOT-SEND (violations below)", ""]
    if violations:
        internal.append("## Violations")
        internal += [f"- {v}" for v in violations]
        internal.append("")
    if reconciliation is not None:
        internal.append("## Figure reconciliation (stored sweep)")
        internal += [f"- BACKED {t}" for t in reconciliation.backed]
        internal += [f"- ATTESTED {t}" for t in reconciliation.attested]
        internal += [f"- UNBACKED {t}" for t in reconciliation.unbacked]
        internal.append("")
    if link_reconciliation is not None:
        internal.append("## Primary-record link reconciliation")
        internal += [
            f"- {'RECONCILED' if record.reconciled else 'BLOCKED'} "
            f"{record.ref.builder}:{record.ref.record_id} · {record.proof}"
            for record in link_reconciliation.records
        ]
        internal.append("")
    if link_audit:
        internal.append("## Link integrity audit")
        internal += [
            f"- {result.classification.value} · {result.url} · "
            f"{', '.join(result.sections)} · {result.proof}"
            for result in link_audit
        ]
        internal.append("")
    if manual_link_checks:
        internal.append("## MANUAL LINK CHECK")
        internal += [f"- {line}" for line in manual_link_checks]
        internal.append("")
    if claim_warnings:
        internal.append("## AGENCY_DOC claim-visibility warnings")
        internal += [f"- {warning}" for warning in claim_warnings]
        internal.append("")
    if model.get("internal_notes"):
        internal.append("## Internal notes (brand marks, recompete coverage)")
        internal += [f"- {n}" for n in model["internal_notes"]]
        internal.append("")
    if relevance_trail:
        tax_v = relevance_trail[0][1].taxonomy_version
        tax_c = relevance_trail[0][1].taxonomy_client
        internal.append(f"## Relevance trail · taxonomy v{tax_v} ({tax_c})")
        internal.append("_Why each opportunity is here, in quoted record "
                        "text. Codes contribute zero relevance._")
        for label, verdict, machine in relevance_trail:
            basis = ("machine-screened" if machine
                     else "operator-included content")
            internal.append(
                f"- **{label}** · score {verdict.score} · core "
                f"{verdict.core_terms or 'NONE'} · adjacent "
                f"{verdict.adjacent_terms or 'none'} · {basis}"
                + (" · OFF-CODE SIGNAL" if verdict.off_code else "")
                + (" · OFF-SCOPE SIGNAL (expansion signal, never "
                   "deliverable candidacy)" if verdict.off_scope_signal
                   else (" · off-scope" if verdict.off_scope else ""))
                + (f" · scope {verdict.scope_basis}"
                   if (verdict.scope_basis == "UNSCOPED"
                       or verdict.scope_basis.startswith("preset:")) else ""))
            for span in verdict.spans[:6]:
                internal.append(f"  - [{span.tier}/{span.field}] "
                                f"\"{span.context.strip()}\"")
            if not verdict.spans:
                internal.append(
                    "  - no capability span in stored records: included on "
                    "operator judgment; calibration surfaces this row")
            for k in verdict.killed[:3]:
                internal.append(f"  - killed: {k}")
        internal.append("")
    _atomic_write_text(
        os.path.join(REPORT_DIR,
                     f"{slug}.federal_opportunity_signals.internal.md"),
        "\n".join(internal) + "\n")

    if violations:
        for v in violations:
            print(f"[QA] FAIL: {v.splitlines()[0]}", file=sys.stderr)
        print(f"[press] {len(violations)} gate violation(s); artifact is "
              f"DO-NOT-SEND and never reaches the Desktop", file=sys.stderr)
        return 2

    if not args.replay:
        folder = os.path.expanduser(f"~/Desktop/{args.client}")
        os.makedirs(folder, exist_ok=True)
        pretty = os.path.join(
            folder, f"{args.client} · Federal Opportunity Pre-Assessment · "
                    f"{render_date.isoformat()}.html")
        shutil.copyfile(out, pretty)
        print(f"[deliver] {pretty}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Client-final Federal Opportunity Pre-Assessment press.")
    ap.add_argument("--client", required=True)
    ap.add_argument("--replay", action="store_true",
                    help="render exclusively from stored inputs: zero LLM, "
                         "zero live calls, byte-identical for a given "
                         "--render-date")
    ap.add_argument("--render-date",
                    help="ISO date pinning both report clocks (required "
                         "with --replay)")
    ap.add_argument("--out", help="explicit output path (default "
                                  "data/reports/<slug>."
                                  "federal_opportunity_signals.html)")
    ap.add_argument("--offline", action="store_true",
                    help="enumerate external links without network checks; "
                         "each is listed under MANUAL LINK CHECK")
    args = ap.parse_args()

    if args.replay:
        # replay is hermetic: entity-resolution telemetry cannot grow the
        # operator's unresolved.log (parity-tool pattern)
        prior = os.environ.get("LILA_ENTITIES_DIR")
        with tempfile.TemporaryDirectory(prefix="lila-board-replay-") as tmp:
            os.environ["LILA_ENTITIES_DIR"] = tmp
            try:
                return _press(args)
            finally:
                if prior is None:
                    os.environ.pop("LILA_ENTITIES_DIR", None)
                else:
                    os.environ["LILA_ENTITIES_DIR"] = prior
    return _press(args)


if __name__ == "__main__":
    sys.exit(main())
