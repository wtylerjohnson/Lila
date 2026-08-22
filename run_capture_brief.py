#!/usr/bin/env python3
"""Build the client-approved capture brief (locked GTM Group HTML format).
Client-facing name: Federal Opportunity Assessment.

    python3 run_capture_brief.py --client "Recorded Future"

Chain: FactPack -> compose (Max-plan Claude, strict schema) -> deterministic QA
gates -> deterministic render. Output:
    data/reports/<slug>.federal_opportunity_assessment.html
    ~/Desktop/GTM - Recorded Future/<Client>_Federal_Opportunity_Assessment_<date>.html
(Loaders also accept legacy <slug>.capture_brief*.html artifacts.)

The pipeline ALWAYS renders a report (graceful QA, 2026-07-09): rule
failures auto-fix, suppress, or flag the failing element, never the
document. Two states, one command: DRAFT (watermark band + QA appendix;
any open flag, or --release not passed) and RELEASE (clean client PDF,
QA log to sidecars). DO-NOT-SEND filenames are retired.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import sys
from datetime import date, datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.reports.capture_brief import (  # noqa: E402
    compose_capture_brief,
    qa_capture_brief,
    reconcile_section01_strict,
    render_capture_brief,
)
from agents.reports.facts import build_fact_pack  # noqa: E402
from agents.reports.link_integrity import (  # noqa: E402
    agency_doc_link_inputs,
    run_client_link_gate,
)
from agents.review import _slug  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ROOT, "data", "reports")


def _is_link_integrity_rule(rule: str) -> bool:
    """Link release gates are source truth, never operator-adjudicatable."""
    return (rule == "sam_workspace_link"
            or rule.startswith(("federal_link_", "external_link_")))


def client_folder(client: str) -> str:
    """Each client gets their own Desktop folder; created on first report."""
    return os.path.expanduser(f"~/Desktop/{client}")


from tools.atomic_io import atomic_write_text as _atomic_write_text  # noqa: E402


def _draft_banner(page: str) -> str:
    """Mark a deterministic fallback visibly and mechanically as DRAFT."""
    banner = (
        '<div data-assessment-fallback="1" style="background:#7b241c;color:#fff;'
        'padding:14px 22px;font:700 14px/1.4 Arial,sans-serif;letter-spacing:.04em">'
        'DRAFT · DETERMINISTIC FALLBACK · DO NOT RELEASE'
        '<span style="display:block;font-weight:400;letter-spacing:0">'
        'The analysis layer was unavailable. This assessment contains the '
        'deterministic evidence view and requires a new reviewed build before release.'
        '</span></div>')
    return page.replace("<body>", f"<body>{banner}", 1) \
        if "<body>" in page else banner + page


def _write_deterministic_draft(client: str, searches_path: str,
                               family: str, reason: str,
                               approval_problems: Optional[list[str]] = None) -> int:
    """Fail open from optional AI layers into an unreleaseable assessment.

    The deterministic AssessmentDocument is the compatibility surface. If its
    rich renderer also fails, a minimal DRAFT still records the failure. QA is
    written before HTML so a replacement failure can never leave a fallback
    wearing a prior RELEASE sidecar.
    """
    safe_reason = " ".join(str(reason).split())[:500]
    out = os.path.join(REPORT_DIR, family + ".html")
    qa_base = os.path.join(REPORT_DIR, family + ".qa")
    render_note = ""
    try:
        with open(searches_path, encoding="utf-8") as f:
            searches = json.load(f)
        if not isinstance(searches, dict) or not isinstance(searches.get("results"), dict):
            raise ValueError("sweep artifact has no results object")
        from agents.reports.document import build_document
        from agents.reports.views import render_assessment
        doc = build_document(client, searches=searches, qualify={})
        page = _draft_banner(render_assessment(doc, "client"))
    except Exception as e:  # noqa: BLE001 - even deterministic rendering must fail open
        render_note = f"; deterministic renderer also failed: {type(e).__name__}: {e}"
        page = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
            f"<title>{html.escape(client)} · Federal Opportunity Assessment · DRAFT</title>"
            "</head><body><div data-assessment-fallback=\"1\">"
            "<h1>DRAFT · Federal Opportunity Assessment · DO NOT RELEASE</h1>"
            f"<h2>{html.escape(client)}</h2>"
            "<p>The deterministic assessment renderer was unavailable. The prior "
            "assessment, if any, was not partially overwritten.</p></div></body></html>")

    from agents.reports.links import (
        format_workspace_link_issue, normalize_sam_workspace_links,
    )
    normalized = normalize_sam_workspace_links(page)
    page = normalized.html
    link_issues = normalized.remaining
    for issue in link_issues:
        print("[fallback] sam_workspace_link HARD FAIL: "
              + format_workspace_link_issue(issue), file=sys.stderr)

    action_reason = safe_reason + render_note[:300]
    actions = [{
        "rule": "AI_FALLBACK", "action": "flag", "location": "document",
        "reason": action_reason,
    }]
    if approval_problems:
        actions.append({
            "rule": "ASSESS_APPROVAL", "action": "flag",
            "location": "document",
            "reason": "; ".join(approval_problems[:3]),
        })
    import hashlib as _hashlib
    actions.extend({
        "rule": "sam_workspace_link", "action": "flag",
        "location": issue.section,
        "reason": format_workspace_link_issue(issue),
    } for issue in link_issues)
    qa_payload = {
        "state": "draft",
        "fallback": True,
        "release_eligible": False,
        "lane_only_teaming": False,
        "html_sha256": _hashlib.sha256(page.encode("utf-8")).hexdigest(),
        "actions": actions,
    }
    qa_md = ("# Assessment QA\n\n"
             "**State:** DRAFT\n\n"
             "**Release eligible:** no\n\n"
             "The optional analysis layer failed. A deterministic fallback was "
             f"generated instead.\n\nReason: {action_reason}\n")
    try:
        _atomic_write_text(qa_base + ".json",
                           json.dumps(qa_payload, indent=2, ensure_ascii=False))
        _atomic_write_text(qa_base + ".md", qa_md)
        _atomic_write_text(out, page)
    except Exception as e:  # noqa: BLE001 - prior artifact must remain intact
        print(f"[fallback] atomic write FAILED ({e}); prior assessment, if any, "
              f"remains intact at {out}", file=sys.stderr)
        return 2
    # a prior release build may have left <fam>.pdf beside the html; the
    # main approval-blocked path removes it and the fallback must too, or a
    # stale RELEASE pdf survives next to this DRAFT
    stale_pdf = os.path.splitext(out)[0] + ".pdf"
    if os.path.exists(stale_pdf):
        try:
            os.remove(stale_pdf)
            print(f"[fallback] removed stale release artifact {stale_pdf}",
                  file=sys.stderr)
        except OSError as e:
            print(f"[fallback] could not remove stale pdf ({e}); treat "
                  f"{stale_pdf} as superseded", file=sys.stderr)
    print(f"[fallback] DRAFT deterministic assessment -> {out}", file=sys.stderr)
    print("[fallback] release disabled; rerun through the Command Center after "
          "the analysis layer recovers", file=sys.stderr)
    return 2 if link_issues else 0


def _strict_projection_and_token(client_name: str, searches_path: str,
                                 profile) -> tuple:
    """ONE strict resolution per press (Cycle 4, 2026-07-12).

    Returns (projection or None, identity token or None). The token threads
    through every compose cache key, the resume fingerprint, and the QA
    sidecar stamp; the projection feeds fit traces and Section-01
    reconciliation, so one press observes one strict state everywhere.
    None token == legacy client: every key and artifact byte stays exactly
    as before the strict cutover machinery existed.
    """
    from agents.reports.compose_cache import identity_token, unresolved_token
    try:
        with open(searches_path, encoding="utf-8") as f:
            searches = json.load(f)
        if not isinstance(searches, dict):
            raise ValueError("sweep artifact root is not an object")
        from agents.assess.live_report import resolve_current_live_report
        projection = resolve_current_live_report(
            client_name, searches, profile)
        return projection, identity_token(projection)
    except Exception:  # noqa: BLE001 - the token owns the fail-closed rule
        designator = None
        try:
            from agents.review import gate_designator
            designator = gate_designator(client_name)
        except Exception:  # noqa: BLE001 - unreadable gate: check the all lane
            designator = None
        return None, unresolved_token(client_name, designator or "all")


def _report_fit_traces(profile, results: dict, strict_live,
                       strict_token) -> dict:
    """Per-notice inspectable fit provenance for the opportunity cards.

    Under strict report truth (Cycle 4, 2026-07-12) the ledger selects which
    notices carry a trace and legacy triage is never consulted; the trace
    math stays the same deterministic capability match, run on each strict
    record's exact raw posting row. Any non-None strict token other than a
    CURRENT run also disables the legacy rebuild: a press that cannot prove
    legacy truth must not decorate cards from a triage layer the ledger may
    have superseded (INVALID and unresolved yield no traces; the live lane
    is held closed and reconciliation flags any composed live entry).
    """
    if profile is None:
        return {}
    traces: dict = {}
    try:
        from tools.capability import fit_trace as _fit_trace
        if strict_live is not None:
            from agents.assess.live_report import LiveReportState
            if strict_live.state == LiveReportState.CURRENT:
                for projected in strict_live.notices:
                    record = projected.record
                    reason = (
                        "human-reviewed requirement span: "
                        + record.requirement_excerpt[:160]
                        if record.requirement_excerpt
                        else "verified live SAM notice record")
                    traces[record.notice_id] = _fit_trace(
                        profile, projected.current, reason)
                return traces
        if strict_token is not None:
            return {}
        sam_by_id = {n.get("source_id"): n
                     for n in results.get("sam.gov") or []}
        for nid, verdict in (results.get("triage") or {}).items():
            if (verdict or {}).get("verdict") in ("pursue", "monitor") \
                    and nid in sam_by_id:
                traces[nid] = _fit_trace(
                    profile, sam_by_id[nid], (verdict or {}).get("reason") or "")
    except Exception as e:  # noqa: BLE001 — provenance never blocks
        print(f"[qa] fit traces unavailable ({e})", file=sys.stderr)
    return traces


def _draft_fingerprint(pack, strict_token) -> list:
    """Resume-never-rebuy identity for the saved draft. The strict token
    appends ONLY when present, so every pre-cutover saved draft still
    resumes; a strict-truth flip composes fresh instead of patching prose
    priced under the other evidence truth."""
    base = [len(pack.facts), (pack.as_of or date.today()).isoformat()]
    return base + ([strict_token] if strict_token else [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--without-horizon", action="store_true",
                    help="build while deliberately omitting any approved "
                         "Horizon set; missing, draft, empty, or invalid "
                         "Horizon work is already nonblocking")
    ap.add_argument("--pdf", action="store_true",
                    help="write a .pdf beside the .html when every gate passes "
                         "(the PDF boundary runs the FULL stack incl. counts)")
    ap.add_argument("--compose-split", action="store_true",
                    help="sectioned compose (P2): narrative/news/boards as "
                         "three cached schema-enforced calls; unchanged "
                         "sections never re-pay their LLM cost")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any saved unfinished draft and re-run the "
                         "full review + compose (default resumes: a failed "
                         "run's draft is patched, never re-bought)")
    ap.add_argument("--release", action="store_true",
                    help="the operator's release switch: with zero open QA "
                         "flags and arbiter consensus, render the clean "
                         "RELEASE (no watermark, no appendix). Without it, "
                         "or with any flag open, the build is a DRAFT — "
                         "which always renders.")
    ap.add_argument("--offline", action="store_true",
                    help="skip external HTTP checks; list those links for "
                         "manual review in the INTERNAL sidecar")
    args = ap.parse_args()

    # Resolve the gate-designated artifact family before any optional analysis
    # runs. Scope failures remain loud; once the correct evidence universe is
    # known, every later failure can still produce a DRAFT in that same lane.
    from agents.reports.capture_brief import CaptureBriefContent
    from agents.review import artifact_stem as _stem19, sweep_artifact_path
    slug = _slug(args.client)
    searches_path = sweep_artifact_path(args.client)
    _fam = _stem19(args.client, "federal_opportunity_assessment")
    draft_path = os.path.join(REPORT_DIR, _fam + ".draft.json")
    initial_approval_problems: list[str] = []
    if args.release:
        from agents.assess.approval import assess_approval_for_release
        _, initial_approval_status, initial_approval_problems = \
            assess_approval_for_release(args.client)
        if initial_approval_status == "approved":
            initial_approval_problems = []

    def _fallback(stage: str, exc: Exception) -> int:
        detail = f"{stage} failed: {type(exc).__name__}: {exc}"
        print(f"[{stage}] FAILED ({exc}) — generating the deterministic "
              "assessment as DRAFT", file=sys.stderr)
        return _write_deterministic_draft(
            args.client, searches_path, _fam, detail,
            approval_problems=initial_approval_problems)

    print(f"[facts] building FactPack for {args.client} ...", file=sys.stderr)
    try:
        pack = build_fact_pack(args.client)
    except Exception as e:  # noqa: BLE001 — deterministic evidence still renders
        return _fallback("facts", e)
    print(f"[facts] {len(pack.facts)} facts, {len(pack.warnings)} warnings", file=sys.stderr)

    # ── RESUME, NEVER RE-BUY (2026-07-09): a failed run persists its draft
    # after every audit round. When the fact pack is unchanged, the next press
    # SKIPS the review and compose (the expensive stages) and patches the
    # saved draft to consensus. --fresh forces a full rebuild.
    import json as _json  # local alias retained for the orchestration below
    # ── STRICT RUN IDENTITY (Cycle 4, 2026-07-12): resolved ONCE per press.
    # The same projection/token pair feeds the resume fingerprint, every
    # compose cache key, the fit-trace source, Section-01 reconciliation,
    # and the QA sidecar stamp, so cached prose and the release gate always
    # agree on WHICH evidence truth priced this build.
    from tools.capability import load_profile as _load_profile
    try:
        _strict_profile = _load_profile(args.client)
    except Exception:  # noqa: BLE001 - resolver treats it as unbindable
        _strict_profile = None
    strict_live, strict_token = _strict_projection_and_token(
        args.client, searches_path, _strict_profile)
    if strict_token:
        print(f"[strict] compose identity {strict_token}", file=sys.stderr)
    fingerprint = _draft_fingerprint(pack, strict_token)
    resume = None
    if not args.fresh and os.path.exists(draft_path):
        try:
            with open(draft_path, encoding="utf-8") as f:
                saved = _json.load(f)
            if saved.get("pack_fingerprint") == fingerprint:
                # Validate before we commit to skipping the expensive stages.
                CaptureBriefContent(**saved["content"])
                resume = saved
                print(f"[resume] saved draft found (passed={saved.get('passed')}, "
                      f"{saved.get('violations', '?')} violation(s) at last audit) "
                      "— review + compose SKIPPED. --fresh forces a full rebuild.",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — a bad cache means a fresh build
            print(f"[resume] saved draft unreadable ({e}) — composing fresh",
                  file=sys.stderr)

    # ── FULL-PICTURE REVIEW: everything the pipeline produced, weighed at once.
    # The brief is the FINAL PRODUCT — this is where the reasoning weight goes.
    dossiers = None
    results: dict = {}
    everything: dict = {}
    if os.path.exists(searches_path):
        try:  # a corrupt artifact degrades to a thin build, never a crash
            with open(searches_path, encoding="utf-8") as f:
                results = _json.load(f).get("results") or {}
        except Exception as e:  # noqa: BLE001
            results = {}
            print(f"[facts] sweep artifact unreadable ({e}) — thin build",
                  file=sys.stderr)
        try:
            from agents.decisions.research_picture import distill
            everything["source_sweep"] = distill(results)
        except Exception as e:  # noqa: BLE001 — synthesis input is optional
            print(f"[review] sweep distill failed ({e}) — review sees the "
                  "fact pack only", file=sys.stderr)
        pic = results.get("research_picture")
        if isinstance(pic, dict) and not pic.get("error"):
            everything["research_picture"] = pic
        dz = results.get("dossiers") or {}
        dossiers = dz.get("records") or None
        if dossiers:
            everything["pursuit_dossiers"] = dossiers
    qual = os.path.join(ROOT, "data", "review", f"{slug}.qualify.json")
    if os.path.exists(qual):
        with open(qual, encoding="utf-8") as f:
            q = _json.load(f)
        everything["qualify_summary"] = {
            "candidate_count": q.get("candidate_count"),
            "verified_count": q.get("verified_count"),
        }

    # the client's own footprint: INTERNAL judgment fuel for the review —
    # never FactPack facts, never client copy. We don't tell the client what
    # they already know; we let it sharpen what we tell them that they don't.
    if os.path.exists(searches_path):
        try:
            from agents.reports.facts import client_footprint
            fp = client_footprint(results)
            if fp:
                everything["client_footprint"] = fp
                print(f"[footprint] {len(fp['awards'])} client awards inform the "
                      "review (internal only, never restated)", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — judgment fuel, never a wall
            print(f"[footprint] skipped ({e})", file=sys.stderr)

    # Horizon is an OPTIONAL, operator-approved layer. Report generation never
    # composes or approves it. Every non-approved or invalid state degrades to a
    # base assessment, so Horizon can improve a report but can never block one.
    horizon = None
    if args.without_horizon:
        print("[horizon] explicitly omitted for this build", file=sys.stderr)
    else:
        try:
            from agents.reports.horizon import horizon_for_report
            horizon, hz_state, hz_problems = horizon_for_report(args.client)
        except Exception as e:  # noqa: BLE001 — optional layer, never a wall
            horizon, hz_state, hz_problems = None, "invalid", [str(e)]
        hz_items = (horizon or {}).get("items") or []
        if hz_state == "approved" and hz_items:
            everything["developing_horizon"] = {
                "note": ("human-approved forming-opportunity map, cited signals, "
                         "labeled projections; in a zero-live cycle LEAD with these"),
                "items": [{k: it.get(k) for k in
                           ("title", "where", "pattern", "projection", "window",
                            "confidence")}
                          for it in hz_items],
            }
            print(f"[horizon] {len(hz_items)} approved items feed the review + "
                  "render", file=sys.stderr)
        elif hz_state == "approved":
            print("[horizon] approved zero-item result; base assessment continues "
                  "without a Horizon section", file=sys.stderr)
        elif hz_state == "draft":
            print("[horizon] draft exists but is not approved; base assessment "
                  "continues without the Horizon layer", file=sys.stderr)
        elif hz_state == "invalid":
            detail = "; ".join(hz_problems[:3]) or "validation failed"
            print(f"[horizon] invalid or unreadable ({detail}); base assessment "
                  "continues without the Horizon layer", file=sys.stderr)
        else:
            print("[horizon] no approved set on file; base assessment continues "
                  "without the Horizon layer", file=sys.stderr)

    # the review anchors its directive to fact ids, so it must see the SAME
    # citable universe the composer gets — everything it pushes without an
    # F# forces an invention downstream (three failed Osprey builds, 2026-07-09)
    if everything:
        everything["fact_pack"] = pack.context()
    # TRUTH PURGE (2026-08-03): web-search assertions are quarantined to the
    # internal surfaces before anything is digested or composed. Quarantined,
    # not deleted: the picture stays at data/review/<slug>.research_picture.md
    # and in the Control Room; it no longer shapes client copy.
    from agents.reports.capture_brief import quarantine_web_assertions
    everything, _quarantined = quarantine_web_assertions(everything)
    if _quarantined:
        print("[review] web-search assertions quarantined to internal "
              "surfaces (" + ", ".join(_quarantined) + "); the compose "
              "chain never sees them", file=sys.stderr)
    early_flags: list[tuple[str, str]] = []
    early_advisories: list[tuple[str, str]] = []
    # a sweep whose scope screen gutted the haul flags the report (2026-07-10)
    try:
        with open(searches_path, encoding="utf-8") as f:
            _cw = (_json.load(f).get("search_scope") or {}).get("cut_warning")
        if _cw:
            early_flags.append(("SCOPE", _cw))
    except Exception:  # noqa: BLE001
        pass
    # ── CLIENT-INVARIANCE REGRESSION PROOF (2026-07-09): runs on EVERY
    # report build. Capability-verified findings shared with a synthetic
    # dummy profile came from the lane layer and are not client
    # intelligence; each leak is a flag (graceful, never a block).
    from tools.capability import load_profile as _load_profile
    lane_only_now = False  # meta-check state, recorded in the qa sidecar
    _profile = _load_profile(args.client)
    if _profile is None:
        early_flags.append(("R12", "no capability profile at clients/<slug>/"
                            "profile.json: teaming and recompete evidence is "
                            "lane-level only and client-invariant"))
    else:
        try:
            from agents.reports.facts import client_invariance_check
            for msg in client_invariance_check(results, _profile):
                early_flags.append(("R12", msg))
        except Exception as e:  # noqa: BLE001 — the proof itself never blocks
            early_flags.append(("R12", f"invariance check errored ({e})"))
        # META-CHECK (2026-07-09): teaming graded on lane evidence alone for
        # two consecutive builds gets auto-flagged. The prior build's qa
        # sidecar records the condition; two in a row is the trigger.
        try:
            lane_only_now = not any(
                f.text.startswith("CAPABILITY-VERIFIED teaming door")
                for f in pack.facts)
            qa_prev_path = os.path.join(
                REPORT_DIR, _stem19(args.client, "federal_opportunity_assessment") + ".qa.json")
            prev_lane_only = False
            if os.path.exists(qa_prev_path):
                with open(qa_prev_path, encoding="utf-8") as f:
                    prev_lane_only = bool(_json.load(f).get("lane_only_teaming"))
            if lane_only_now and prev_lane_only:
                # ADVISORY, not a release gate (2026-07-10): this is an
                # internal evidence-roadmap item — expected for OEM clients
                # whose demand rides vehicles (L9) — and no regeneration can
                # clear it, so as a flag it would block release forever. It
                # records as a suppress-class action: sidecar + appendix,
                # never the gate.
                early_advisories.append(
                    ("META", "evidence upgrade needed: teaming has rested on "
                             "lane-level or zero-match evidence for two "
                             "consecutive builds"))
        except Exception:  # noqa: BLE001 — meta-check never blocks
            lane_only_now = False
    review = None
    if resume is not None:
        pass  # patching a saved draft needs no directive — the fix list rules
    else:
        print(f"[review] Sonnet weighing the full picture "
              f"({', '.join(everything) or 'fact pack only'}) ...", file=sys.stderr)
        from agents.reports.capture_brief import compose_full_picture
        try:
            if everything and args.compose_split:
                from agents.reports.capture_brief import cached_full_picture_review
                from agents.review import gate_designator as _gd_rev
                review, _rhit = cached_full_picture_review(
                    args.client, scope=_gd_rev(args.client) or "all",
                    everything=everything, strict_identity=strict_token)
                print(f"[review] full-picture "
                      f"{'HIT (cached)' if _rhit else 'composed fresh'}",
                      file=sys.stderr)
            else:
                review = compose_full_picture(args.client, everything) if everything else None
        except Exception as e:  # noqa: BLE001 — degrade, never block the render
            review = None
            early_flags.append(
                ("REVIEW", f"full-picture review errored ({str(e)[:120]}); "
                           "composed without a directive"))
            print(f"[review] FAILED ({e}) — composing without a directive; "
                  "the build renders as DRAFT with this flagged", file=sys.stderr)
    if review is not None:
        print(f"[review] THESIS: {review.thesis_directive}", file=sys.stderr)
        print(f"[review] {len(review.storyline_connections)} cross-stage connections · "
              f"{len(review.must_include)} must-includes · "
              f"{len(review.do_not_claim)} do-not-claims", file=sys.stderr)
        # the composer's strongest input must survive the run: a DO-NOT-SEND
        # is undiagnosable when the directive that shaped the draft is gone
        # (2026-07-09 Osprey fail — only the thesis line hit stderr)
        review_path = os.path.join(ROOT, "data", "review",
                                   f"{slug}.full_picture.json")
        try:
            _atomic_write_text(
                review_path,
                _json.dumps(review.model_dump(mode="json"), indent=2),
            )
            print(f"[review] directive -> {review_path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — persistence is not the report
            early_flags.append(
                ("REVIEW", f"review directive could not be persisted ({e})"))
            print(f"[review] directive write failed ({e}) — continuing as "
                  "DRAFT", file=sys.stderr)

    # scoped sweep -> scoped compose: the artifact's scope (set at the gate)
    # shapes the copy; all-scope composes plain, never says "all agencies"
    scope_directive = None
    scope_tag = ""
    # L19: the GATE is the scope authority. Artifacts that predate the
    # focus-lineage fix carry search_scope null even when scoped; deriving
    # from the gate keeps the tag, the Desktop name, and the compose
    # directive correct regardless of artifact vintage.
    _sc = {}
    try:
        from agents.review import load_packet as _lp
        _gate_agencies = _lp(args.client).scope_agencies()
        if _gate_agencies:
            _sc = {"agencies": [{"abbr": a["abbr"], "name": a["name"]}
                                for a in _gate_agencies]}
    except Exception:  # noqa: BLE001 — packet-less direct runs fall through
        _sc = {}
    if not _sc.get("agencies") and os.path.exists(searches_path):
        with open(searches_path, encoding="utf-8") as f:
            _sc = (_json.load(f) or {}).get("search_scope") or {}
    if True:
        if _sc.get("agencies"):
            names = ", ".join(f"{a['name']} ({a['abbr']})" for a in _sc["agencies"])
            scope_tag = "_" + "_".join(a["abbr"] for a in _sc["agencies"]) + "_Focus"
            scope_directive = (f"AGENCY-SCOPED assessment: this sweep was "
                               f"scoped to {names}. Write the thesis and stats "
                               f"for that lane; never generalize to the whole "
                               f"federal market.")
            print(f"[scope] agency-scoped sweep: {scope_tag.strip('_')}",
                  file=sys.stderr)

    if resume is not None:
        content = CaptureBriefContent(**resume["content"])
        print("[compose] SKIPPED — resuming the saved draft into the patch loop",
              file=sys.stderr)
    else:
        print("[compose] Max-plan Claude filling the locked schema under the review directive ...",
              file=sys.stderr)
        try:
            if args.compose_split:
                from agents.reports.capture_brief import compose_capture_brief_sectioned
                from agents.review import gate_designator as _gd19
                import time as _ptime
                _t0 = _ptime.monotonic()
                content, _hits = compose_capture_brief_sectioned(
                    pack, review=review, directive=scope_directive,
                    client=args.client,
                    scope=_gd19(args.client) or "all",
                    strict_identity=strict_token)
                print("[compose] sectioned: "
                      + ", ".join(f"{k}={'HIT' if v else 'miss'}"
                                  for k, v in _hits.items())
                      + f" ({_ptime.monotonic() - _t0:.1f}s)", file=sys.stderr)
            else:
                content = compose_capture_brief(
                    pack, review=review, directive=scope_directive)
        except Exception as e:  # noqa: BLE001 — prose is optional to existence
            return _fallback("compose", e)

        # Money-in-motion floor: retry once, then flag. A pack with >=3 cited dollar
        # facts must yield budget bars; one compose retry with an explicit directive,
        # and a second miss becomes a QA problem (DO-NOT-SEND, no client copy).
        from agents.reports.capture_brief import BUDGET_BARS_MIN_DOLLAR_FACTS, dollar_fact_count
        n_dollar = dollar_fact_count(pack)
        if not content.budget_bars and n_dollar >= BUDGET_BARS_MIN_DOLLAR_FACTS:
            print(f"[compose] RETRY: {n_dollar} dollar facts in the pack but no budget "
                  f"bars composed — re-running with an explicit directive", file=sys.stderr)
            _retry_directive = (
                f"MISS ON FIRST PASS: the fact pack contains {n_dollar} cited dollar "
                "facts but you returned no budget_bars. Populate budget_bars with "
                "3-5 of the largest cited dollar facts (money-in-motion chart).")
            try:
                if args.compose_split:
                    from agents.reports.capture_brief import compose_capture_brief_sectioned
                    from agents.review import gate_designator as _gd19
                    # the directive busts ONLY the narrative section's cache;
                    # news and boards hit — the retry re-pays one section
                    content, _hits = compose_capture_brief_sectioned(
                        pack, review=review, directive=_retry_directive,
                        client=args.client,
                        scope=_gd19(args.client) or "all",
                        strict_identity=strict_token)
                    print("[compose] sectioned retry: "
                          + ", ".join(f"{k}={'HIT' if v else 'miss'}"
                                      for k, v in _hits.items()), file=sys.stderr)
                else:
                    content = compose_capture_brief(
                        pack, review=review, directive=_retry_directive)
            except Exception as e:  # noqa: BLE001 — first composition still stands
                early_flags.append(
                    ("COMPOSE", "budget-bar retry errored; retained the first "
                     f"composition ({str(e)[:120]})"))
                print(f"[compose] budget-bar retry FAILED ({e}) — retaining "
                      "the first composition and forcing DRAFT", file=sys.stderr)

    # ── ARBITER PANEL: independent judges audit the composed draft against
    # the FactPack (primary Anthropic critic + any active cross-vendor
    # arbiter; the OpenAI antagonist activates via .env, zero code). One
    # violation-fed recompose, then the panel's word is final: a failed
    # consensus stamps the build. Verdicts are INTERNAL (stderr + sidecar).
    from agents.decisions.assessment_arbiters import arbitrate_assessment
    arbiter_trail = None

    def _audit_round(m, a):
        return {"passed": m.passed, "note": m.note,
                "violations": [v.model_dump(mode="json") for v in m.violations],
                "judges": {k: x.passed for k, x in a.items()}}

    def _save_draft(cnt, m):
        """Persist after EVERY audit round: a failed or killed run resumes
        from here — review + compose are never re-bought."""
        _atomic_write_text(
            draft_path,
            _json.dumps({"passed": m.passed,
                         "violations": len(m.violations),
                         "pack_fingerprint": fingerprint,
                         "content": cnt.model_dump(mode="json")}),
        )

    class _FixVerified(Exception):
        """Control flow: the promote path met consensus via the fix list."""

    prior_trail_path = os.path.join(
        REPORT_DIR, _stem19(args.client, "federal_opportunity_assessment") + ".arbiters.json")
    try:
        # ── PROMOTE PATH (2026-07-10): a fresh full audit of a large draft
        # finds NEW marginal nits every pass, so zero-flags-by-rerolling
        # never terminates. On resume with a prior fix list, verify THOSE
        # violations are resolved; the panel's word is final means its fix
        # list is the contract. Fresh compositions still get the full audit.
        if resume is not None and os.path.exists(prior_trail_path):
            with open(prior_trail_path, encoding="utf-8") as f:
                _prior = _json.load(f)
            _pv = _prior.get("violations") or []
            if _pv:
                from agents.decisions.assessment_arbiters import verify_fixes
                print(f"[arbiters] promote path: verifying {len(_pv)} "
                      "previously raised violation(s) against the edited "
                      "draft (targeted check, no fresh full audit) ...",
                      file=sys.stderr)
                _ok, _unresolved = verify_fixes(pack, content, _pv)
                if _ok:
                    arbiter_trail = {
                        "passed": True,
                        "note": (f"targeted verification: {len(_pv)} prior "
                                 "violation(s) confirmed resolved"),
                        "violations": [], "judges": {"fix-verify": True},
                        "rounds": _prior.get("rounds") or []}
                    from types import SimpleNamespace as _NS
                    _save_draft(content, _NS(passed=True, violations=[]))
                    raise _FixVerified()
                for _u in _unresolved:
                    print(f"[arbiters] unresolved: {_u}", file=sys.stderr)
                print("[arbiters] fix list not fully resolved — full audit "
                      "path", file=sys.stderr)
        merged, audits, fix_directive = arbitrate_assessment(pack, content)
        names = ", ".join(audits)
        print(f"[arbiters] {names} -> "
              f"{'PASS' if merged.passed else 'FAIL'}"
              f" ({len(merged.violations)} violation(s))", file=sys.stderr)
        rounds = [_audit_round(merged, audits)]
        best = (len(merged.violations), content, merged)
        _save_draft(content, merged)
        # CONVERGENCE LOOP, not a single retry: valid research must be able to
        # yield a report. Repair rounds PATCH the standing draft (fast tier,
        # only the quoted lines change) rather than re-composing from scratch:
        # a full recompose burns minutes re-reasoning over the whole pack and
        # rewrites every sentence, surfacing new wording slips each round
        # (2026-07-09 Osprey: 18 -> 3 with two of round two's misses brand
        # new). Fidelity stays absolute: the panel audits every round, and a
        # round that stops shrinking ends the loop with the stamp intact.
        from agents.reports.capture_brief import patch_capture_brief
        MAX_PATCHES = 3
        heavy = False  # plateau escalation: fast tier first, heavy tier once
        while fix_directive and len(rounds) <= MAX_PATCHES:
            print(f"[arbiters] patch {len(rounds)}/{MAX_PATCHES}"
                  f"{' (heavy tier)' if heavy else ''} "
                  "under the panel's fix list ...", file=sys.stderr)
            if heavy:
                from agents.decisions.engine import DecisionEngine as _Heavy
                fix_directive += (" NOTE: these violations SURVIVED a previous "
                                  "patch round. Apply each fix EXACTLY as "
                                  "written, verbatim where replacement text is "
                                  "supplied.")
                content = patch_capture_brief(content, fix_directive,
                                              engine=_Heavy())
            else:
                content = patch_capture_brief(content, fix_directive)
            prev_count = len(merged.violations)
            merged, audits, fix_directive = arbitrate_assessment(pack, content)
            rounds.append(_audit_round(merged, audits))
            print(f"[arbiters] re-audit -> "
                  f"{'PASS' if merged.passed else str(len(merged.violations)) + ' violation(s) remain'}",
                  file=sys.stderr)
            if len(merged.violations) < best[0]:
                best = (len(merged.violations), content, merged)
            _save_draft(best[1], best[2])
            if merged.passed:
                continue  # loop exits: fix_directive is None on a pass
            if len(merged.violations) > prev_count:
                # BLOWUP: a bad patch round can never destroy a good draft —
                # revert to the fewest-violations state and stop
                content, merged = best[1], best[2]
                print("[arbiters] violations increased — reverting to the "
                      f"best draft ({best[0]} violation(s)); the panel's "
                      "word is final", file=sys.stderr)
                break
            if len(merged.violations) == prev_count:
                # PLATEAU: the fast patcher isn't landing these fixes — not a
                # degrading draft (2026-07-09: 6 -> 2 -> 2 quit two trivial
                # wording fixes from consensus). Escalate to the heavy tier
                # once; a plateau AT the heavy tier is final.
                if heavy:
                    content, merged = best[1], best[2]
                    print("[arbiters] plateau held at the heavy tier — "
                          f"best draft stands ({best[0]} violation(s)); "
                          "the panel's word is final", file=sys.stderr)
                    break
                heavy = True
                print("[arbiters] plateau at the fast tier — escalating the "
                      "next patch to the heavy engine", file=sys.stderr)
        # the trail keeps EVERY round: the re-audit used to overwrite round
        # one, hiding whether a recompose fixed or introduced violations. Top
        # level mirrors the SHIPPED draft's audit (post keep-best revert).
        arbiter_trail = {"passed": merged.passed, "note": merged.note,
                         "violations": [v.model_dump(mode="json")
                                        for v in merged.violations],
                         "judges": rounds[-1]["judges"], "rounds": rounds}
    except _FixVerified:
        print("[arbiters] fix list VERIFIED RESOLVED — consensus met by "
              "construction; no fresh dice roll", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — panel outage fails LOUD, build continues stamped-aware
        print(f"[arbiters] panel errored ({e}) — treating as non-pass",
              file=sys.stderr)
        arbiter_trail = {"passed": False, "note": f"panel errored: {e}",
                         "violations": [], "judges": {}}

    from agents.reports.capture_brief import zero_stat_problems
    from agents.reports.document import build_document

    # ── COLD-READ EDITORIAL PASS: one revision made with the whole document
    # in view (story spine, redundancy, dead weight, momentum). It can only
    # IMPROVE the build, never block it: the revision ships solely if it
    # holds arbiter consensus AND every deterministic gate; any slip and the
    # audited draft stands. Fresh context — the editor sees only what the
    # client will see.
    already_edited = bool(resume and resume.get("passed"))
    if already_edited:
        print("[editor] SKIPPED — resumed draft already carries its editorial "
              "pass", file=sys.stderr)
    if arbiter_trail and arbiter_trail.get("passed") and not already_edited:
        from agents.reports.capture_brief import edit_capture_brief
        print("[editor] cold-read pass over the whole assessment ...",
              file=sys.stderr)
        try:
            _eres = edit_capture_brief(content)
            edited = _eres.revised
            for _axis, _note in (_eres.value_assessment or {}).items():
                print(f"[editor] value · {_axis}: {_note[:140]}",
                      file=sys.stderr)
            e_merged, _, _ = arbitrate_assessment(pack, edited)
            ok = e_merged.passed
            if ok:
                base_q = set(qa_capture_brief(content, pack=pack))
                ok = set(qa_capture_brief(edited, pack=pack)) <= base_q
            if ok:
                e_counts = build_document(args.client, content=edited).counts()
                ok = not zero_stat_problems(edited, e_counts)
            if ok:
                content = edited
                _save_draft(content, e_merged)  # the edit survives a crash
                print("[editor] revision holds every gate — accepted",
                      file=sys.stderr)
            else:
                print("[editor] revision slipped a gate — the audited draft "
                      "stands (editor never blocks)", file=sys.stderr)
            arbiter_trail["editor"] = {
                "accepted": ok,
                "value_assessment": _eres.value_assessment}
        except Exception as e:  # noqa: BLE001 — editorial is best-effort by design
            print(f"[editor] pass errored ({e}) — the audited draft stands",
                  file=sys.stderr)
            arbiter_trail["editor"] = {"accepted": False, "error": str(e)}

    # ── GRACEFUL QA (operator spec 2026-07-09): remediate claims, NEVER
    # block the render. Every rule auto-fixes, suppresses, or flags the
    # failing ELEMENT; a rule that throws becomes a flag and the element
    # renders unmodified. The document ALWAYS renders.
    from agents.reports.remediation import remediate
    try:
        counts = build_document(args.client, content=content).counts()
    except Exception as e:  # noqa: BLE001 — counts degrade, tokens still lint
        counts = {}
        early_flags.append(("QA", f"ground-truth counts unavailable ({e}); "
                                  "COUNT tokens resolve to 0 and lint as flags"))
    content, qa_report = remediate(content, pack, counts)
    for rule, msg in early_flags:
        qa_report.add(rule, "flag", "document", msg)
    # SCOPE CONTAINMENT (2026-07-12): a focus-gated build whose document
    # carries a foreign agency on any actionable surface is a blocking flag
    # (forces DRAFT / DO-NOT-SEND), whatever path minted the row. This is the
    # capture path the leaked NETSCOUT DHS press took.
    #
    # FAIL CLOSED (Cycle 5 review P1): resolve the sweep's focus FIRST. If the
    # sweep is agency-scoped and the containment check itself throws, the
    # build cannot be trusted clean, so it takes a blocking flag rather than
    # silently shipping unchecked. An all-scope sweep has no gate to enforce,
    # so a check error there is non-blocking (logged only).
    _scope_sweep = None
    _scope_is_focus = False
    try:
        with open(searches_path, encoding="utf-8") as _sf:
            _scope_sweep = _json.load(_sf)
        _scope_is_focus = (
            (_scope_sweep.get("search_scope") or {}).get("mode") == "focus")
    except Exception as _e:  # noqa: BLE001 - unreadable sweep handled below
        print(f"[scope] sweep unreadable for containment ({_e})", file=sys.stderr)
    try:
        from tools.agency_scope import document_focus_violations
        _scope_doc = build_document(args.client, searches=_scope_sweep or {},
                                    content=content)
        for _bad in document_focus_violations(_scope_doc, _scope_sweep or {}):
            qa_report.add("SCOPE_CONTAINMENT", "flag", "document",
                          f"out-of-gate agency on an actionable surface: {_bad}")
    except Exception as _e:  # noqa: BLE001 - a check failure fails CLOSED for scoped builds
        print(f"[scope] containment check FAILED ({_e})", file=sys.stderr)
        if _scope_is_focus or _scope_sweep is None:
            qa_report.add(
                "SCOPE_CONTAINMENT", "flag", "document",
                "agency-scope containment check could not run "
                f"({type(_e).__name__}); build held as DRAFT until it passes")
    for rule, msg in early_advisories:
        qa_report.add(rule, "suppress", "document", msg)
    for a in qa_report.actions:
        print(f"[qa] {a.rule} {a.action} at {a.location}: {a.reason}",
              file=sys.stderr)
    if arbiter_trail and not arbiter_trail.get("passed"):
        for v in (arbiter_trail.get("violations") or []):
            qa_report.add("ARB", "flag", v.get("section", "document"),
                          (v.get("fix") or v.get("issue")
                           or "unresolved arbiter violation")[:180])
    # residual detector findings remediation could not classify -> flags
    for p in qa_capture_brief(content, pack=pack) + zero_stat_problems(content, counts):
        qa_report.add("QA", "flag", "document", p)
    # Cycle 4 (2026-07-12): Section-01 truth must join the strict NOTICE
    # allowlist exactly; a composed live card the current ledger does not
    # carry is a blocking flag, never client copy.
    for p in reconcile_section01_strict(content, strict_live):
        qa_report.add("S01_STRICT", "flag", "document", p)

    if dossiers:
        print(f"[depth] folding {len(dossiers)} pursuit dossiers into the brief",
              file=sys.stderr)
    fc = {}
    if os.path.exists(searches_path):
        with open(searches_path, encoding="utf-8") as f:
            fc = (_json.load(f).get("results") or {}).get("forecast_signals") or {}
    forecasts = fc.get("matched") or None
    if forecasts:
        print(f"[signals] {len(forecasts)} forecast signals -> Early Signals section "
              f"(agency-stated intent, never live opportunities)", file=sys.stderr)
    buyer_map = results.get("incumbent_buyer_map")
    # SHOW THE WORK (2026-07-10): per-notice fit traces for every card.
    # Cycle 4: under strict report truth the ledger picks the notices and
    # legacy triage is never consulted (see _report_fit_traces).
    fit_traces = _report_fit_traces(_profile, results, strict_live,
                                    strict_token)

    def _render(**kw):
        """The render NEVER kills the build: on failure the optional layers
        shed and the failure is flagged; the last resort is an emergency
        minimal document. A report ALWAYS exists."""
        try:
            page = render_capture_brief(content, dossiers=dossiers,
                                        forecasts=forecasts,
                                        forecast_delta=fc.get("delta"),
                                        counts=counts, horizon=horizon,
                                        forecast_total=fc.get("total_records"),
                                        buyer_map=buyer_map,
                                        fit_traces=fit_traces, **kw)
        except Exception as e:  # noqa: BLE001
            qa_report.add("R10", "flag", "render",
                          f"full render failed ({str(e)[:120]}); optional "
                          "layers shed")
            try:
                page = render_capture_brief(content, counts=counts, **kw)
            except Exception as e2:  # noqa: BLE001
                qa_report.add("R10", "flag", "render",
                              f"minimal render failed ({str(e2)[:120]}); "
                              "emergency document emitted")
                import html as _h
                page = ("<html><head><title>Federal Opportunity Assessment"
                        "</title></head><body><h1>"
                        + _h.escape(args.client)
                        + " · Federal Opportunity Assessment (EMERGENCY "
                          "RENDER: template failed, content preserved)</h1>"
                          "<pre style='white-space:pre-wrap'>"
                        + _h.escape(content.model_dump_json(indent=2)[:400000])
                        + "</pre></body></html>")
        from agents.reports.links import normalize_sam_workspace_links
        normalized = normalize_sam_workspace_links(page)
        return normalized.html

    # base render (no band) for the deterministic gate lints; their
    # violations become FLAGS, never blocks — and the lint stack itself is
    # behind the same safety valve as every QA rule
    html = _render()
    from agents.reports.links import (
        find_sam_workspace_links, format_workspace_link_issue,
    )
    link_gate_issues = find_sam_workspace_links(html)
    for issue in link_gate_issues:
        detail = format_workspace_link_issue(issue)
        print(f"[qa] sam_workspace_link HARD FAIL: {detail}", file=sys.stderr)
        qa_report.add("sam_workspace_link", "flag", issue.section, detail)

    # The shared post-render gate sees the exact normalized client HTML that
    # the state machine is about to promote. SAM and USAspending links are
    # proved from their builder/reconciliation attributes without HTTP;
    # every other outbound URL is GET-checked unless this press is offline.
    manual_link_checks: list[str] = []
    link_claim_warnings: list[str] = []
    try:
        agency_doc_links, agency_doc_figures = agency_doc_link_inputs(pack.facts)
        client_link_gate = run_client_link_gate(
            html,
            enabled=not args.offline,
            extra_links=agency_doc_links,
            figures=agency_doc_figures,
        )
        manual_link_checks = list(client_link_gate.manual_checks)
        link_claim_warnings = list(client_link_gate.claim_warnings)
        for violation in client_link_gate.violations:
            print(f"[qa] {violation.rule} HARD FAIL: {violation.detail}",
                  file=sys.stderr)
            qa_report.add(violation.rule, "flag", "document",
                          violation.detail)
    except Exception as e:  # noqa: BLE001 - gate failure holds release closed
        detail = ("client link-integrity gate could not run "
                  f"({type(e).__name__}: {str(e)[:160]})")
        print(f"[qa] external_link_gate_error HARD FAIL: {detail}",
              file=sys.stderr)
        qa_report.add("external_link_gate_error", "flag", "document", detail)
    from agents.reports.lint import (
        lint_brief_identity, lint_client_bleed, lint_client_terminology,
        lint_contact_rendering, lint_count_tokens, lint_emdash,
        lint_entity_lineage, lint_federal_link_construction,
        lint_notice_tier_claims, lint_screen_census, lint_forecast_context,
        lint_horizon, lint_svg_geometry, lint_sam_workspace_links,
        lint_whitelabel,
    )
    from agents.reports.verification import (
        cited_fact_ids, data_current_violations, freshness_violations,
        load_resolutions,
    )
    try:
        # Per-figure freshness gate (2026-07-16): every cited figure proves
        # WHEN it was pulled, at render time, before the state machine runs.
        # A stale, unknown-freshness, or disputed-and-unresolved figure is a
        # flag, which forces DRAFT exactly like every other gate violation.
        _cited = cited_fact_ids(html)
        _render_now = datetime.now(timezone.utc)
        directly_reported = {issue.url for issue in link_gate_issues}
        sam_link_lint = [
            violation
            for violation in lint_sam_workspace_links(html).violations
            if violation.excerpt not in directly_reported
        ]
        gate_violations = (
            sam_link_lint
            + lint_federal_link_construction(html).violations
            + lint_svg_geometry(html).violations
            + lint_forecast_context(html).violations
            + lint_contact_rendering(html).violations
            + lint_brief_identity(html).violations
            + lint_client_terminology(html).violations
            + lint_count_tokens(html).violations
            + lint_horizon(html).violations
            + lint_client_bleed(html, args.client).violations
            + lint_emdash(html).violations
            + lint_entity_lineage(html).violations
            + lint_notice_tier_claims(html).violations
            + lint_screen_census(html).violations
            + lint_whitelabel(html).violations
            + freshness_violations(
                pack, _cited, now=_render_now,
                resolutions=load_resolutions(args.client))
            + data_current_violations(pack, _cited, pack.as_of)
        )
    except Exception as e:  # noqa: BLE001 — a lint bug never breaks a report
        gate_violations = []
        qa_report.add("R10", "flag", "document",
                      f"gate lint stack errored ({e}); rendered unmodified")
    for viol in gate_violations:
        print(f"[qa] {viol.rule} flag: {viol.detail}", file=sys.stderr)
        qa_report.add(viol.rule, "flag", "document", viol.detail)

    # ── OPERATOR ADJUDICATION (2026-07-10): FLAG means "needs a human" —
    # this is that human's mechanism, not a bypass. A dismissal recorded in
    # data/review/<slug>.flag_decisions.json converts the matching flag to a
    # logged suppress with the operator's note attached; the decision is
    # permanent in the QA sidecar.
    try:
        _fd_path = os.path.join(ROOT, "data", "review",
                                f"{slug}.flag_decisions.json")
        if os.path.exists(_fd_path):
            with open(_fd_path, encoding="utf-8") as f:
                _decisions = (_json.load(f) or {}).get("dismiss") or []
            for a in qa_report.actions:
                if a.action != "flag":
                    continue
                if _is_link_integrity_rule(a.rule):
                    continue  # release-link failures are never adjudicatable
                for dec in _decisions:
                    m = str(dec.get("match") or "")
                    if m and m.lower() in a.reason.lower():
                        a.action = "suppress"
                        a.reason += (" [operator-adjudicated: "
                                     f"{str(dec.get('note') or 'accepted')[:80]}]")
                        print(f"[qa] flag adjudicated by operator: "
                              f"{a.rule} — {a.reason[:100]}", file=sys.stderr)
                        break
    except Exception as e:  # noqa: BLE001 — adjudication never blocks
        print(f"[qa] flag decisions unreadable ({e})", file=sys.stderr)

    # ── TWO STATES, ONE COMMAND. RELEASE requires a current human approval
    # bound to this exact scope/sweep/profile, zero unresolved flags, arbiter
    # consensus, and the operator's --release switch. Validate at promotion
    # time as well as at the UI boundary: a direct CLI run stays available but
    # can only produce a visibly flagged DRAFT when approval is absent/stale.
    assess_release_approved = True
    assess_approval_problems: list[str] = []
    if args.release:
        from agents.assess.approval import assess_approval_for_release
        _, approval_status, assess_approval_problems = \
            assess_approval_for_release(args.client)
        assess_release_approved = approval_status == "approved"
        if not assess_release_approved:
            reason = ("; ".join(assess_approval_problems[:3])
                      or "current Assess results are not approved")
            qa_report.add("ASSESS_APPROVAL", "flag", "document", reason)
            print(f"[gate] ASSESS APPROVAL BLOCKED — {reason}", file=sys.stderr)
    consensus = bool(arbiter_trail and arbiter_trail.get("passed"))
    hard_link_issues = any(
        action.action == "flag" and _is_link_integrity_rule(action.rule)
        for action in qa_report.actions
    )
    state = ("release" if args.release and assess_release_approved
             and consensus and not qa_report.has_flags
             else "draft")
    if state == "draft":
        why = ("current Assess approval missing or stale"
               if args.release and not assess_release_approved
               else "open flags" if qa_report.has_flags
               else "arbiter consensus failed" if not consensus
               else "release switch off (pass --release)")
        print(f"[state] DRAFT — {why}", file=sys.stderr)
    else:
        print("[state] RELEASE — zero flags, consensus held, release switch on",
              file=sys.stderr)
    html = _render(qa=qa_report, state=state)

    os.makedirs(REPORT_DIR, exist_ok=True)
    # L19: one stem for the whole artifact family — a scoped press can never
    # overwrite the all-scope deliverable or cross-read its sidecars
    if arbiter_trail is not None:
        trail_path = os.path.join(
            REPORT_DIR, _fam + ".arbiters.json")
        try:
            _atomic_write_text(
                trail_path, _json.dumps(arbiter_trail, indent=2))
            print(f"[arbiters] trail -> {trail_path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — assessment still renders
            qa_report.add("ARB", "flag", "document",
                          f"arbiter trail could not be persisted ({e})")
            state = "draft"
            html = _render(qa=qa_report, state="draft")
            print(f"[arbiters] trail write failed ({e}) — forcing DRAFT",
                  file=sys.stderr)
    # ONE filename in both states — state lives in the document band and the
    # qa sidecar, never the filename. DO-NOT-SEND is retired.
    out = os.path.join(REPORT_DIR, _fam + ".html")
    try:
        _atomic_write_text(out, html)
    except Exception as e:  # noqa: BLE001 — preserve the previous assessment
        print(f"[out] atomic write FAILED ({e}); prior assessment, if any, "
              f"remains intact at {out}", file=sys.stderr)
        return 2
    print(f"[out] {out} ({state.upper()})", file=sys.stderr)
    stale = os.path.join(REPORT_DIR, _fam + ".DO-NOT-SEND.html")
    if os.path.exists(stale):
        try:
            os.remove(stale)
        except OSError as e:
            print(f"[out] stale compatibility artifact not removed ({e})",
                  file=sys.stderr)

    pdf = None
    approval_blocked = args.release and not assess_release_approved
    if hard_link_issues:
        stale_pdf = os.path.splitext(out)[0] + ".pdf"
        if os.path.exists(stale_pdf):
            try:
                os.remove(stale_pdf)
                print(f"[pdf] removed stale release artifact {stale_pdf}",
                      file=sys.stderr)
            except OSError as e:
                qa_report.add("external_link_gate_error", "flag", "document",
                              f"stale release PDF could not be removed: {e}")
        if args.pdf:
            print("[pdf] skipped — client link-integrity hard gate failed",
                  file=sys.stderr)
    elif approval_blocked:
        # A newly blocked DRAFT must not leave an older release PDF beside the
        # same stable HTML name where it could be mistaken for this build.
        stale_pdf = os.path.splitext(out)[0] + ".pdf"
        if os.path.exists(stale_pdf):
            try:
                os.remove(stale_pdf)
                print(f"[pdf] removed stale release artifact {stale_pdf}",
                      file=sys.stderr)
            except OSError as e:
                qa_report.add("ASSESS_APPROVAL", "flag", "document",
                              f"stale release PDF could not be removed: {e}")
        if args.pdf:
            print("[pdf] skipped — current Assess approval is required; the "
                  "DRAFT HTML remains available", file=sys.stderr)
    elif args.pdf:
        if state == "release":
            # belt and braces: the release boundary re-runs the full gate
            # stack; a refusal here means a gate gap, so fall back to DRAFT
            from agents.reports.lint import LintViolation
            from tools.export_pdf import export_deliverable
            try:
                pdf, refused = export_deliverable(out)
            except Exception as e:  # noqa: BLE001 — exporter crash != refusal
                pdf, refused = None, [LintViolation(
                    rule="pdf_export_error",
                    detail=f"release exporter errored: {str(e)[:140]}")]
            if pdf:
                print(f"[pdf] {pdf} (RELEASE — clean, no appendix)", file=sys.stderr)
            else:
                qa_report.add(refused[0].rule, "flag", "document",
                              f"release export refused: {refused[0].detail[:160]}")
                state = "draft"
                html = _render(qa=qa_report, state="draft")
                try:
                    _atomic_write_text(out, html)
                except Exception as e:  # noqa: BLE001
                    print(f"[pdf] DRAFT downgrade write failed ({e}); prior "
                          "artifact remains intact", file=sys.stderr)
                    return 2
                print(f"[pdf] release refused ({refused[0].rule}) — "
                      "re-rendered as DRAFT", file=sys.stderr)
        if state == "draft":
            # a DRAFT always exports: watermark band + QA appendix ride
            # inside the document; headless Chrome adds no browser chrome.
            # A PDF failure never kills the build — the HTML is the report.
            try:
                from tools.export_pdf import export_pdf as _export_pdf
                pdf = _export_pdf(out)
                print(f"[pdf] {pdf} (DRAFT — watermark + QA appendix)",
                      file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"[pdf] export failed non-fatally ({e}) — the HTML "
                      "report stands", file=sys.stderr)

    # QA record written LAST so the recorded state is the FINAL state (the
    # release boundary can downgrade to draft); sidecars exist in both
    # states — the appendix page only ever appears inside DRAFT documents
    qa_base = os.path.join(REPORT_DIR, _fam + ".qa")
    # build pairing (2026-07-11): the sidecar certifies EXACTLY the html it
    # was written beside. Concurrent builds interleave their separate atomic
    # writes; a sidecar whose html_sha256 does not match the html on disk is
    # a cross-build mispair and must never read as releasable.
    import hashlib as _hashlib
    qa_payload = {"state": state,
                  "lane_only_teaming": lane_only_now,
                  "html_sha256": _hashlib.sha256(html.encode("utf-8")).hexdigest(),
                  "actions": [a.model_dump(mode="json")
                              for a in qa_report.actions]}
    if strict_token:
        # Cycle 4 (2026-07-12): certify WHICH strict evidence truth priced
        # this build. release_state refuses a stable-family stamp that is not
        # the current pointer's run id; the sentinel tokens (strict:invalid,
        # strict:unresolved) can never match one, so a build pressed against
        # a broken strict state can never ship. Legacy sidecars carry no key
        # and stay byte-identical.
        qa_payload["assess_run_id"] = strict_token
    try:
        _atomic_write_text(
            qa_base + ".json", _json.dumps(qa_payload, indent=2))
        _atomic_write_text(qa_base + ".md", qa_report.to_markdown())
    except Exception as e:  # noqa: BLE001 — HTML remains the assessment
        if state == "release":
            qa_report.add("QA", "flag", "document",
                          f"release QA sidecar could not be persisted ({e})")
            state = "draft"
            html = _render(qa=qa_report, state="draft")
            try:
                _atomic_write_text(out, html)
            except Exception as write_error:  # noqa: BLE001
                print(f"[qa] release downgrade write failed ({write_error}); "
                      "prior artifact remains intact", file=sys.stderr)
                return 2
            if pdf and os.path.exists(pdf):
                try:
                    os.remove(pdf)
                    pdf = None
                except OSError as remove_error:
                    print(f"[qa] stale release PDF could not be removed "
                          f"({remove_error})", file=sys.stderr)
        print(f"[qa] sidecar write failed non-fatally ({e}); assessment "
              f"stands at {out}", file=sys.stderr)
    print(f"[qa] {len(qa_report.actions)} action(s) "
          f"({len(qa_report.flags)} flag(s)) -> {qa_base}.json", file=sys.stderr)
    # THE INTERNAL FILE (client-file doctrine 2026-07-10): everything deleted
    # or converted out of the client file lives here in full, with forward
    # actions. Written beside the artifact, never copied to the client folder.
    try:
        from agents.reports.internal_file import build_internal_md
        _int_path = os.path.join(REPORT_DIR, _fam + ".internal.md")
        _atomic_write_text(
            _int_path,
            build_internal_md(args.client, pack, qa_report,
                              arbiter_trail, results,
                              link_warnings=[],
                              manual_link_checks=manual_link_checks,
                              link_claim_warnings=link_claim_warnings),
        )
        print(f"[internal] trail -> {_int_path}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — the internal file never blocks
        print(f"[internal] write failed non-fatally ({e})", file=sys.stderr)

    if state == "release":
        try:  # the folder copy is delivery, not existence: never a crash
            folder = client_folder(args.client)
            os.makedirs(folder, exist_ok=True)
            pretty = os.path.join(
                folder,
                f"{args.client.replace(' ', '_')}_Federal_Opportunity_Assessment"
                f"{scope_tag}_{date.today().isoformat()}.html",
            )
            shutil.copy(out, pretty)
            print(f"[out] {pretty}", file=sys.stderr)
            if pdf:
                shutil.copy(pdf, os.path.splitext(pretty)[0] + ".pdf")
                print(f"[pdf] {os.path.splitext(pretty)[0] + '.pdf'}", file=sys.stderr)
            print(f"RELEASE: assessment is client-clean -> Desktop/{args.client}/",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[out] Desktop copy failed non-fatally ({e}) — the RELEASE "
                  f"artifact stands at {out}", file=sys.stderr)
    else:
        print("DRAFT rendered end-to-end (a report ALWAYS ships). Resolve the "
              "flags in the QA appendix by fixing source data/copy, then press "
              "with --release.", file=sys.stderr)
        if not consensus:
            print("The draft is SAVED: pressing again resumes at the patch loop "
                  "(review + compose are not re-bought). --fresh discards it.",
                  file=sys.stderr)
    return 2 if hard_link_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
