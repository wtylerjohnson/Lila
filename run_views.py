#!/usr/bin/env python3
"""Build the three-view Federal Opportunity Assessment (client / sales / internal).

    python3 run_views.py --client "Thinklogical"                 # all three views
    python3 run_views.py --client "Thinklogical" --view sales    # one view
    python3 run_views.py --client "Thinklogical" --no-compose    # joins only, no LLM prose

One document, three renderers: the underlying AssessmentDocument is assembled
once (deterministic joins + one LLM compose for prose), then each view renders
from it. Every view passes the full hard-gate stack — identity assets, graded
contacts, client terminology, count reconciliation — or ships stamped
DO-NOT-SEND. The internal view never copies to the client Desktop folder.

Output: data/reports/<slug>.assessment.<view>[.DO-NOT-SEND].html
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.reports.document import build_document  # noqa: E402
from agents.reports.link_integrity import (  # noqa: E402
    render_link_integrity_internal_md,
    run_client_link_gate,
)
from agents.reports.links import normalize_sam_workspace_links  # noqa: E402
from agents.reports.lint import (  # noqa: E402
    LintViolation,
    lint_brief_identity, lint_client_bleed, lint_client_terminology,
    lint_contact_rendering, lint_counts, lint_emdash, lint_entity_lineage,
    lint_federal_link_construction, lint_notice_tier_claims,
    lint_sam_workspace_links, lint_screen_census, lint_whitelabel,
)
from agents.reports.views import VIEWS, render_assessment  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ROOT, "data", "reports")


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _compose_content(client: str):
    """The one LLM compose, reusing the assessment chain (pack -> review ->
    compose). Returns None when the inputs for compose are missing."""
    from agents.reports.capture_brief import (
        compose_capture_brief, compose_full_picture,
    )
    from agents.reports.facts import build_fact_pack
    import json as _json

    pack = build_fact_pack(client)
    print(f"[facts] {len(pack.facts)} facts, {len(pack.warnings)} warnings", file=sys.stderr)
    everything = {}
    from agents.review import sweep_artifact_path
    sp = sweep_artifact_path(client)  # L19: gate-designated artifact
    if os.path.exists(sp):
        results = (_json.load(open(sp)) or {}).get("results") or {}
        from agents.decisions.research_picture import distill
        everything = {
            "source_sweep": distill(results),
            "research_picture": results.get("research_picture"),
            "pursuit_dossiers": (results.get("dossiers") or {}).get("records"),
        }
    review = None
    if everything:
        print("[review] weighing the full picture ...", file=sys.stderr)
        review = compose_full_picture(client, everything)
    print("[compose] filling the locked schema ...", file=sys.stderr)
    return compose_capture_brief(pack, review=review)


def main() -> int:
    ap = argparse.ArgumentParser(description="Three-view opportunity assessment.")
    ap.add_argument("--client", required=True)
    ap.add_argument("--view", choices=[*VIEWS, "all"], default="all")
    ap.add_argument("--no-compose", action="store_true",
                    help="skip the LLM prose layer (joins only)")
    ap.add_argument("--pdf", action="store_true",
                    help="write a .pdf beside every gate-clean .html "
                         "(a DO-NOT-SEND view never gets a PDF)")
    ap.add_argument("--offline", action="store_true",
                    help="skip external link requests; list them for manual check")
    args = ap.parse_args()

    content = None
    compose_failure_detail = None
    if not args.no_compose:
        try:
            content = _compose_content(args.client)
        except Exception as exc:  # noqa: BLE001 - prose is optional, report is not
            compose_failure_detail = (
                f"optional report composition failed ({type(exc).__name__}: "
                f"{str(exc)[:240]})"
            )
            print(
                f"[compose] FAILED ({type(exc).__name__}: {exc}); continuing "
                "with the deterministic assessment and no prose layer; all "
                "selected views are DO-NOT-SEND",
                file=sys.stderr,
            )

    doc = build_document(args.client, content=content)
    for gap in doc.gaps:
        print(f"[gaps] {gap}", file=sys.stderr)

    # scope designator from the ARTIFACT (set at sweep time by the gate's
    # scope choice): agency-scoped sweeps render the focus label; all-scope
    # renders plain — "Federal Opportunity Assessment" IS the all-agencies
    # statement and never says "all agencies".
    import json as _json
    focus_label = None
    from agents.review import sweep_artifact_path as _sap
    sp = _sap(args.client)  # L19: the label derives from the designated artifact
    if os.path.exists(sp):
        sc = (_json.load(open(sp)) or {}).get("search_scope") or {}
        if sc.get("agencies"):
            focus_label = " + ".join(a["abbr"] for a in sc["agencies"])
            print(f"[scope] agency-scoped sweep: {focus_label}", file=sys.stderr)

    views = list(VIEWS) if args.view == "all" else [args.view]
    os.makedirs(REPORT_DIR, exist_ok=True)
    exit_code = 0

    # per-figure freshness (2026-07-16): one deterministic gate pack for all
    # selected views; a pack-build failure holds the gate closed rather than
    # letting figures render unproven
    gate_pack = None
    gate_pack_error = None
    try:
        from agents.reports.facts import build_fact_pack as _bfp
        gate_pack = _bfp(args.client)
    except Exception as exc:  # noqa: BLE001 - fail closed, never fail open
        gate_pack_error = f"{type(exc).__name__}: {str(exc)[:160]}"

    for view in views:
        html = render_assessment(doc, view, focus_label=focus_label)
        links = normalize_sam_workspace_links(html)
        html = links.html
        violations = (lint_sam_workspace_links(html).violations
                      + lint_federal_link_construction(html).violations
                      + lint_brief_identity(html).violations
                      + lint_contact_rendering(html).violations
                      + lint_client_terminology(html).violations
                      + lint_counts(html).violations
                      + lint_client_bleed(html, args.client).violations
                      + lint_emdash(html).violations
                      + lint_entity_lineage(html).violations
                      + lint_notice_tier_claims(html).violations
                      + lint_screen_census(html).violations
                      + lint_whitelabel(html).violations)
        link_outcome = None
        if view in {"client", "sales"}:
            link_outcome = run_client_link_gate(
                html,
                enabled=not args.offline,
                figures=(gate_pack.facts if gate_pack is not None else ()),
            )
            violations += list(link_outcome.violations)
        from datetime import datetime, timezone

        from agents.reports.verification import (
            cited_fact_ids, data_current_violations, freshness_violations,
            load_resolutions,
        )
        if gate_pack is not None:
            _cited = cited_fact_ids(html)
            violations += freshness_violations(
                gate_pack, _cited, now=datetime.now(timezone.utc),
                resolutions=load_resolutions(args.client))
            violations += data_current_violations(
                gate_pack, _cited, gate_pack.as_of)
        elif cited_fact_ids(html):
            violations.append(LintViolation(
                rule="FRESHNESS_UNKNOWN",
                detail=(f"figure freshness could not be proven (gate pack "
                        f"failed: {gate_pack_error}); cited figures cannot "
                        f"render unproven")))
        if compose_failure_detail:
            violations.append(LintViolation(
                rule="compose_failure", detail=compose_failure_detail))
        # Sales remains the gated teaser. Only the full client Assess view is a
        # release surface, and direct CLI generation must stay available even
        # when its human approval is missing or stale.
        if view == "client":
            from agents.assess.approval import assess_approval_for_release
            _, approval_status, approval_problems = \
                assess_approval_for_release(args.client)
            if approval_status != "approved":
                detail = ("; ".join(approval_problems[:3])
                          or "current Assess results are not approved")
                violations.append(LintViolation(
                    rule="assess_approval", detail=detail))
        stamp = "" if not violations else ".DO-NOT-SEND"
        for v in violations:
            print(f"[QA:{view}] FAIL: {v.rule} — {v.detail}", file=sys.stderr)
        from agents.review import artifact_stem as _stem19
        artifact_base = os.path.join(
            REPORT_DIR, _stem19(args.client, "assessment") + f".{view}")
        out = artifact_base + f"{stamp}.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[out:{view}] {out}", file=sys.stderr)
        sidecar = artifact_base + ".link-integrity.internal.md"
        try:
            if link_outcome and (
                    link_outcome.manual_checks or link_outcome.claim_warnings):
                with open(sidecar, "w", encoding="utf-8") as f:
                    f.write(render_link_integrity_internal_md(
                        f"{args.client} · assessment · {view}", link_outcome))
                print(f"[links:{view}] INTERNAL sidecar -> {sidecar}",
                      file=sys.stderr)
            elif link_outcome and os.path.exists(sidecar):
                os.remove(sidecar)
        except OSError as exc:
            print(f"[links:{view}] INTERNAL sidecar unavailable ({exc})",
                  file=sys.stderr)

        pdf = None
        if args.pdf and view == "internal":
            # ratified 2026-07-06: internal is a DO-NOT-SEND artifact — no
            # print control, no PDF sibling (export_deliverable would refuse
            # it anyway on the DO NOT SEND self-label)
            print("[pdf:internal] skipped — internal never gets a PDF",
                  file=sys.stderr)
        elif args.pdf:
            # HARD RULE: gates before PDF — a stamped view never gets one
            from tools.export_pdf import export_deliverable
            pdf, refused = export_deliverable(out)
            if pdf:
                print(f"[pdf:{view}] {pdf}", file=sys.stderr)
            else:
                print(f"[pdf:{view}] REFUSED — {refused[0].rule}: gates must "
                      "pass before any PDF exists", file=sys.stderr)

        if view == "client" and not violations and not args.no_compose:
            folder = os.path.expanduser(f"~/Desktop/{args.client}")
            os.makedirs(folder, exist_ok=True)
            pretty = os.path.join(
                folder,
                f"{args.client.replace(' ', '_')}_Federal_Opportunity_Assessment_{date.today().isoformat()}.html")
            shutil.copy(out, pretty)
            print(f"[out:{view}] {pretty}", file=sys.stderr)
            if pdf:
                shutil.copy(pdf, os.path.splitext(pretty)[0] + ".pdf")
                print(f"[pdf:{view}] {os.path.splitext(pretty)[0] + '.pdf'}",
                      file=sys.stderr)
        if violations:
            exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
