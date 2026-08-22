#!/usr/bin/env python3
"""Agency-focused pass: scope the research to ONE agency and ship a report.

    python3 run_agency_report.py --client "Osprey Flight Solutions" --agency CBP
    python3 run_agency_report.py --client "..." --agency "Department of Homeland Security"
    python3 run_agency_report.py --client "..." --agency FAA --no-compose

How it scopes (zero SAM quota, no re-triage): the canonical sweep artifact
already holds every screened notice WITH its triage verdict, so the pass
deterministically filters agency-keyed layers (notices, triage, recompete
calendar, forecast lines, news) to the chosen agency, pulls a FRESH
agency-scoped USAspending market slice per NAICS lane, and builds the
assessment from that scoped artifact through the full gate stack. A
department pass (DHS) captures its components (CBP, TSA, ...).

Output: data/reports/<slug>.agency_<abbr>.assessment.client[.DO-NOT-SEND].html
plus the Desktop client folder when gate-clean.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.reports.link_integrity import (  # noqa: E402
    render_link_integrity_internal_md,
    run_client_link_gate,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ROOT, "data", "reports")
_CONTENT_CACHE_VERSION = 1


class _ScopeContainmentError(Exception):
    """A foreign agency reached a scoped deliverable surface (fail-closed)."""


@dataclass(frozen=True)
class _BlockingViolation:
    rule: str
    detail: str


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _jsonable(value: Any) -> Any:
    """Stable JSON payload for Pydantic models and ordinary containers."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(value).items()
                if not k.startswith("_")}
    return value


def _content_fingerprint(*, client: str, agency: dict, scoped_artifact: dict,
                         strategy: Any, profile: Any,
                         strict_run_id: Any = None) -> str:
    """Strong identity for every input that can change agency-focused prose."""
    payload = {
        "version": _CONTENT_CACHE_VERSION,
        "client": client,
        "agency": {k: agency.get(k) for k in ("name", "abbr", "parent")},
        "scoped_artifact": scoped_artifact,
        "approved_strategy": _jsonable(strategy),
        "capability_profile": _jsonable(profile),
    }
    if strict_run_id:
        # Cycle 4 (2026-07-12): under strict report truth the ledger, not the
        # raw sweep, decides the live lane, so identical sweep bytes with a
        # different current run must re-price prose. Key ABSENT when legacy:
        # pre-cutover fingerprints stay byte-identical.
        payload["strict_run_id"] = strict_run_id
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _content_cache_path(slug: str, agency_slug: str) -> str:
    return os.path.join(ROOT, "data", "state", "agency_report_content",
                        f"{slug}.agency_{agency_slug}.content.json")


from tools.atomic_io import atomic_write_text as _atomic_write_text  # noqa: E402


def _load_cached_content(path: str, fingerprint: str):
    """Return a schema-valid cached composition for this exact input set."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("version") != _CONTENT_CACHE_VERSION:
            print(f"[compose-cache] version mismatch at {path}; recomposing",
                  file=sys.stderr)
            return None
        if payload.get("fingerprint") != fingerprint:
            print("[compose-cache] inputs changed; recomposing", file=sys.stderr)
            return None
        from agents.reports.capture_brief import CaptureBriefContent
        content = CaptureBriefContent.model_validate(payload.get("content"))
        print(f"[compose-cache] exact input match; reusing {path}",
              file=sys.stderr)
        return content
    except Exception as e:  # noqa: BLE001 - bad cache must never block a report
        print(f"[compose-cache] invalid ({e}); recomposing", file=sys.stderr)
        return None


def _save_cached_content(path: str, fingerprint: str, content: Any) -> None:
    payload = {
        "version": _CONTENT_CACHE_VERSION,
        "fingerprint": fingerprint,
        "content": content.model_dump(mode="json"),
    }
    _atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def _invalidate_content_cache(path: str, reason: str) -> None:
    if not path or not os.path.exists(path):
        return
    try:
        os.remove(path)
        print(f"[compose-cache] invalidated ({reason}) -> {path}",
              file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - invalidation cannot block fallback
        print(f"[compose-cache] invalidation FAILED ({e}); cache will be "
              "ignored for this run", file=sys.stderr)


def _rematerialize_strict_run(client_name: str, designator: str,
                              scoped_path: str) -> str:
    """Refresh the strict Assess run AFTER the scoped sweep bytes changed.

    Delegates to the ONE pointer-gated, creation-free owner
    (tools/assess_refresh.py): a client without a current pointer for this
    scope keeps exact legacy behavior; activating a pointer is the operator's
    cutover decision, never a runner side effect. Failures are loud but
    nonfatal, and the stale pointer then holds the live lanes closed
    downstream (INVALID), so a failed refresh can never release stale strict
    truth.
    """
    from tools.assess_refresh import refresh_current_assess_run_if_active
    return refresh_current_assess_run_if_active(
        client_name, sweep_path=scoped_path, designator=designator)


def _emergency_assessment_html(client: str, agency: dict,
                               scoped_artifact: dict) -> str:
    """Last-resort deterministic artifact. Always stamped DO-NOT-SEND."""
    results = (scoped_artifact.get("results") or {}
               if isinstance(scoped_artifact, dict) else {})
    notices = [n for n in (results.get("sam.gov") or [])
               if isinstance(n, dict) and n.get("source_id")
               and str(n.get("source") or "").strip().lower()
               in ("", "sam.gov")]
    triage = results.get("triage") or {}
    notice_ids = {n["source_id"] for n in notices}
    notice_count = len(notices)
    screened_count = (sum(1 for sid in triage if sid in notice_ids)
                      if isinstance(triage, dict) else 0)
    client_text = html_lib.escape(client)
    agency_text = html_lib.escape(
        f"{agency.get('name') or 'Agency'} ({agency.get('abbr') or 'focus'})")
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{client_text} · Federal Opportunity Assessment</title>
<style>body{{font-family:Arial,sans-serif;margin:48px;color:#17283f}}
.blocked{{background:#8b1e2d;color:white;padding:12px 16px;font-weight:700}}
main{{max-width:860px;margin:auto}} li{{margin:8px 0}}</style></head><body><main>
<div class="blocked">DO NOT SEND · DETERMINISTIC SAFETY RENDER</div>
<h1>{client_text}</h1><h2>Federal Opportunity Assessment · {agency_text}</h2>
<p>The scoped evidence reached the final safety renderer. Review the job log
and rebuild before release.</p>
<ul><li>{notice_count} scoped notice records retained</li>
<li>{screened_count} screening decisions retained</li>
<li>Assessment date: {date.today().isoformat()}</li></ul>
</main></body></html>"""


from tools.agency_scope import scope_results  # noqa: E402  (one owner, 2026-07-12)


def refresh_market_slice(scoped: dict, agency: dict, naics: list[str],
                         keywords: list) -> None:
    """Fresh agency-scoped USAspending market evidence per lane. USAspending
    filters at DEPARTMENT tier, so a component pass sizes the market at its
    parent department with the basis saying so."""
    from tools.agencies import AGENCIES
    from tools.api.usaspending import UsaSpendingSource, addressable_terms
    dept = agency
    if agency.get("parent"):
        dept = next(a for a in AGENCIES if a["abbr"] == agency["parent"])
    src = UsaSpendingSource()
    terms = addressable_terms(keywords)
    bundles = []
    for code in naics[:6]:
        try:
            ev = src.market_evidence(code, agency=dept["name"],
                                     keywords=terms or None)
        except Exception as e:  # noqa: BLE001 — a lane failure keeps the rest
            print(f"[lane {code}] market slice FAILED: {e}", file=sys.stderr)
            continue
        ev["scope_note"] = (f"market sized at {dept['name']} (department tier)"
                            + (f" for the {agency['abbr']} pass"
                               if dept is not agency else ""))
        bundles.append(ev)
        print(f"[lane {code}] {agency['abbr']} slice: "
              f"{(ev.get('summary') or {}).get('award_count', 0)} awards",
              file=sys.stderr)
    if bundles or "usaspending.gov" not in scoped:
        scoped["usaspending.gov"] = bundles
    else:
        print("[market] every fresh lane failed; retaining the scoped sweep's "
              "existing market evidence", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--agency", required=True,
                    help="abbr or name; resolved against the agency reference")
    ap.add_argument("--no-compose", action="store_true",
                    help="skip the LLM prose layer (joins only)")
    ap.add_argument("--offline", action="store_true",
                    help="skip external link requests; list them for manual check")
    args = ap.parse_args()

    from tools.agencies import find, suggest
    agency = find(args.agency)
    if not agency:
        opts = ", ".join(f"{a['abbr']} ({a['name']})"
                         for a in suggest(args.agency, 5))
        print(f"[agency] '{args.agency}' not recognized. Close matches: "
              f"{opts or 'none'}", file=sys.stderr)
        return 2
    print(f"[agency] {agency['name']} ({agency['abbr']})"
          + (f" · component of {agency['parent']}" if agency["parent"] else
             " · department pass includes components"), file=sys.stderr)

    slug = _slug(args.client)
    # L19 convergence: a filter-first sweep already produced the scoped
    # artifact (searches_<slug>.agency_<a>.json carrying search_scope) — use
    # it directly, zero re-projection. Legacy full-market artifacts still
    # project through scope_results below.
    direct = os.path.join(ROOT, "data", "cleaned",
                          f"searches_{slug}.agency_{agency['abbr'].lower()}.json")
    if os.path.exists(direct):
        try:
            with open(direct, encoding="utf-8") as f:
                candidate = json.load(f)
            if not isinstance(candidate, dict):
                raise ValueError("scoped sweep root is not an object")
        except Exception as e:  # noqa: BLE001 - canonical fallback may still work
            print(f"[agency] scoped sweep unreadable ({e}); trying the "
                  "all-scope artifact", file=sys.stderr)
            candidate = None
        if candidate is not None:
            if (candidate.get("search_scope") or {}).get("mode") == "focus":
                print(f"[agency] filter-first sweep found at {direct}; "
                      "using it directly (no projection)", file=sys.stderr)
                canonical = candidate
                scoped_results = canonical.get("results") or {}
                stats = {"direct": 1}
            else:
                candidate = None
    else:
        candidate = None
    if candidate is None:
        src_path = os.path.join(ROOT, "data", "cleaned", f"searches_{slug}.json")
        if not os.path.exists(src_path):
            print(f"[agency] no sweep artifact at {src_path}; run the search step "
                  "first", file=sys.stderr)
            return 2
        try:
            with open(src_path, encoding="utf-8") as f:
                canonical = json.load(f)
            if not isinstance(canonical, dict):
                raise ValueError("all-scope sweep root is not an object")
        except Exception as e:  # noqa: BLE001 - no trustworthy input to assess
            print(f"[agency] all-scope sweep unreadable ({e}); run the search "
                  "step again", file=sys.stderr)
            return 2
        scoped_results, stats = scope_results(canonical.get("results") or {}, agency)
    print("[scope] " + " · ".join(f"{k} {v}" for k, v in stats.items()),
          file=sys.stderr)

    from agents.review import load_approved
    strategy = load_approved(args.client)  # gate: approved strategy required
    try:
        refresh_market_slice(
            scoped_results, agency, strategy.inferred_naics,
            [k.model_dump() if hasattr(k, "model_dump") else k
             for k in strategy.keywords])
    except Exception as e:  # noqa: BLE001 - enrichment is optional, report is not
        print(f"[market] refresh FAILED ({type(e).__name__}: {e}); retaining "
              "the scoped sweep's existing market evidence", file=sys.stderr)

    a_slug = agency["abbr"].lower()
    scoped_artifact = {"client": canonical.get("client") or args.client,
                       "agency_focus": {"abbr": agency["abbr"],
                                        "name": agency["name"]},
                       # Preserve the filter-first lineage when this artifact
                       # replaces its own source. Without it, retry #2 rejects
                       # the direct artifact and wrongly demands an all-scope one.
                       "search_scope": (
                           canonical.get("search_scope")
                           if (canonical.get("search_scope") or {}).get("mode")
                           == "focus"
                           else {"agencies": [{"abbr": agency["abbr"],
                                                "name": agency["name"]}],
                                 "mode": "focus"}),
                       "generated_at": canonical.get("generated_at"),
                       "results": scoped_results}
    scoped_path = os.path.join(ROOT, "data", "cleaned",
                               f"searches_{slug}.agency_{a_slug}.json")
    scoped_write_ok = False
    try:
        _atomic_write_text(
            scoped_path, json.dumps(scoped_artifact, indent=2, default=str))
        scoped_write_ok = True
        print(f"[scope] artifact -> {scoped_path}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - in-memory scope can still render
        print(f"[scope] artifact write FAILED ({e}); continuing from the "
              "in-memory scoped evidence", file=sys.stderr)

    # This is the runner's FINAL scoped write; a current strict run bound to
    # the prior bytes must be rematerialized here, BEFORE any html exists, so
    # a clean report always postdates its pointer (release freshness leg).
    if scoped_write_ok:
        outcome = _rematerialize_strict_run(
            scoped_artifact.get("client") or args.client,
            f"agency_{a_slug}", scoped_path)
        print(f"[strict] run rematerialization: {outcome}", file=sys.stderr)

    content = None
    cache_path = ""
    fingerprint = ""
    cache_hit = False
    prose_degraded: list[_BlockingViolation] = []
    if not args.no_compose:
        try:
            from agents.reports.capture_brief import compose_capture_brief
            from agents.reports.compose_cache import compose_identity_token
            from agents.reports.facts import build_fact_pack
            from tools.capability import load_profile
            profile = load_profile(args.client)
            if profile is None:
                prose_degraded.append(_BlockingViolation(
                    "agency_profile_missing",
                    "capability profile missing; optional prose is not releasable"))
            strict_token = compose_identity_token(
                scoped_artifact.get("client") or args.client, scoped_artifact,
                profile, designator=f"agency_{a_slug}")
            if strict_token:
                print(f"[strict] compose identity {strict_token}",
                      file=sys.stderr)
            fingerprint = _content_fingerprint(
                client=args.client, agency=agency,
                scoped_artifact=scoped_artifact, strategy=strategy,
                profile=profile, strict_run_id=strict_token)
            cache_path = _content_cache_path(slug, a_slug)
            content = _load_cached_content(cache_path, fingerprint)
            cache_hit = content is not None
            if content is None:
                pack = build_fact_pack(args.client, searches=scoped_artifact,
                                       profile=profile)
                print(f"[facts] {len(pack.facts)} agency-scoped facts, "
                      f"{len(pack.warnings)} warnings", file=sys.stderr)
                print("[compose] filling the locked schema (agency-focused) ...",
                      file=sys.stderr)
                content = compose_capture_brief(
                    pack,
                    directive=(f"AGENCY-FOCUSED assessment: every fact is scoped "
                               f"to {agency['name']} ({agency['abbr']}). Write "
                               "the thesis and stats for that agency lane "
                               "specifically; never generalize to the whole "
                               "federal market."))
        except Exception as e:  # noqa: BLE001 - prose is optional, report is not
            content = None
            cache_hit = False
            prose_degraded.append(_BlockingViolation(
                "agency_prose_degraded",
                f"optional prose unavailable ({type(e).__name__}: {str(e)[:160]})"))
            print(f"[compose] FAILED ({type(e).__name__}: {e}); continuing "
                  "with the deterministic agency assessment and no prose "
                  "layer; output is DO-NOT-SEND", file=sys.stderr)

    def _build_and_render(prose):
        from agents.reports.document import build_document
        from agents.reports.views import render_assessment
        document = build_document(args.client, searches=scoped_artifact,
                                  content=prose)
        for gap in document.gaps:
            print(f"[gaps] {gap}", file=sys.stderr)
        # SCOPE CONTAINMENT (2026-07-12): the last gate before a scoped
        # deliverable renders. A foreign agency on any actionable surface is
        # a blocking violation, whatever path minted it.
        from tools.agency_scope import document_focus_violations
        for bad in document_focus_violations(document, scoped_artifact):
            raise _ScopeContainmentError(bad)
        rendered = render_assessment(
            document, "client",
            focus_label=f"{agency['name']} ({agency['abbr']})")
        from agents.reports.links import normalize_sam_workspace_links
        return normalize_sam_workspace_links(rendered).html

    link_gate_pack = None

    def _lint(rendered: str) -> list:
        nonlocal link_gate_pack
        from agents.reports.links import (
            find_sam_workspace_links, format_workspace_link_issue,
        )
        workspace_links = find_sam_workspace_links(rendered)
        if workspace_links:
            return [_BlockingViolation(
                "sam_workspace_link", format_workspace_link_issue(issue))
                for issue in workspace_links]
        try:
            from datetime import datetime, timezone

            from agents.reports.facts import build_fact_pack as _bfp
            from agents.reports.lint import (
                lint_brief_identity, lint_client_bleed,
                lint_client_terminology, lint_contact_rendering, lint_counts,
                lint_emdash, lint_entity_lineage,
                lint_federal_link_construction, lint_notice_tier_claims,
                lint_sam_workspace_links, lint_screen_census, lint_whitelabel,
            )
            from agents.reports.verification import (
                cited_fact_ids, data_current_violations,
                freshness_violations, load_resolutions,
            )
            # per-figure freshness (2026-07-16): the gate pack rebuilds from
            # the exact scoped artifact (deterministic, no LLM) so cached or
            # prose-shed presses still prove every cited figure's pull time
            gate_pack = _bfp(args.client, searches=scoped_artifact)
            link_gate_pack = gate_pack
            _cited = cited_fact_ids(rendered)
            return (lint_sam_workspace_links(rendered).violations
                    + lint_federal_link_construction(rendered).violations
                    + lint_brief_identity(rendered).violations
                    + lint_contact_rendering(rendered).violations
                    + lint_client_terminology(rendered).violations
                    + lint_counts(rendered).violations
                    + lint_client_bleed(rendered, args.client).violations
                    + lint_emdash(rendered).violations
                    + lint_entity_lineage(rendered).violations
                    + lint_notice_tier_claims(rendered).violations
                    + lint_screen_census(rendered).violations
                    + lint_whitelabel(rendered).violations
                    + freshness_violations(
                        gate_pack, _cited, now=datetime.now(timezone.utc),
                        resolutions=load_resolutions(args.client))
                    + data_current_violations(
                        gate_pack, _cited, gate_pack.as_of))
        except Exception as e:  # noqa: BLE001 - lint outage blocks release, not HTML
            print(f"[QA] lint stack FAILED ({type(e).__name__}: {e}); "
                  "output is DO-NOT-SEND", file=sys.stderr)
            return [_BlockingViolation(
                "agency_lint_error",
                f"lint stack failed ({type(e).__name__}: {str(e)[:160]})")]

    render_failure = None
    _scope_blocked = False
    try:
        html = _build_and_render(content)
    except _ScopeContainmentError as e:
        print(f"[SCOPE] BLOCKED: out-of-gate agency on an actionable surface "
              f"({e}); refusing to render a scoped deliverable", file=sys.stderr)
        html = _emergency_assessment_html(args.client, agency, scoped_artifact)
        content = None
        violations = [_BlockingViolation("agency_scope_containment",
                                         f"out-of-gate agency: {e}")]
        render_failure = None
        _scope_blocked = True
    except Exception as e:  # noqa: BLE001 - retry without optional prose
        render_failure = e
        if content is not None:
            print(f"[render] prose render FAILED ({type(e).__name__}: {e}); "
                  "retrying the deterministic assessment", file=sys.stderr)
            if cache_hit:
                _invalidate_content_cache(cache_path, "cached prose failed render")
            content = None
            cache_hit = False
            prose_degraded.append(_BlockingViolation(
                "agency_prose_render_failed",
                f"prose render failed ({type(e).__name__}: {str(e)[:160]})"))
            try:
                html = _build_and_render(None)
                render_failure = None
            except Exception as retry_error:  # noqa: BLE001 - final safety renderer
                render_failure = retry_error

    if _scope_blocked:
        pass  # violations already set to the containment block
    elif render_failure is not None:
        print(f"[render] deterministic render FAILED "
              f"({type(render_failure).__name__}: {render_failure}); emitting "
              "the DO-NOT-SEND safety document", file=sys.stderr)
        html = _emergency_assessment_html(args.client, agency, scoped_artifact)
        content = None
        violations = [_BlockingViolation(
            "agency_render_error",
            f"deterministic render failed ({type(render_failure).__name__}: "
            f"{str(render_failure)[:160]})")]
    else:
        violations = _lint(html)
        # A cached or fresh prose layer that fails QA cannot pin retries to the
        # same bad content. Shed it now and build the deterministic assessment.
        if content is not None and violations:
            print("[QA] prose layer failed gates; retrying without prose and "
                  "invalidating any cached copy", file=sys.stderr)
            if cache_hit:
                _invalidate_content_cache(cache_path, "cached prose failed QA")
            content = None
            cache_hit = False
            prose_degraded.append(_BlockingViolation(
                "agency_prose_qa_failed",
                "optional prose failed the report gate stack"))
            try:
                html = _build_and_render(None)
                violations = _lint(html)
            except Exception as e:  # noqa: BLE001 - final safety renderer
                print(f"[render] deterministic retry FAILED "
                      f"({type(e).__name__}: {e}); emitting the DO-NOT-SEND "
                      "safety document", file=sys.stderr)
                html = _emergency_assessment_html(
                    args.client, agency, scoped_artifact)
                violations = [_BlockingViolation(
                    "agency_render_error",
                    f"deterministic retry failed ({type(e).__name__}: "
                    f"{str(e)[:160]})")]

    violations = list(violations) + prose_degraded

    # Promote to cache only after the exact composed content rendered and
    # passed every gate. A schema-valid but unrenderable/QA-failing draft must
    # never become a permanent retry trap.
    if content is not None and not violations and not cache_hit \
            and cache_path and fingerprint:
        try:
            _save_cached_content(cache_path, fingerprint, content)
            print(f"[compose-cache] saved -> {cache_path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - cache is an optimization
            print(f"[compose-cache] write FAILED ({e}); report build continues",
                  file=sys.stderr)

    # The agency artifact is itself a release evidence universe. Revalidate at
    # promotion time against its final in-memory contents (including refreshed
    # market evidence), its agency-scoped basename, the current gate scope, and
    # the complete current capability profile. Direct runs still generate, but
    # an unreviewed derivative is necessarily DO-NOT-SEND.
    approval_violations: list[_BlockingViolation] = []
    try:
        from agents.assess.approval import (
            assess_approval_for_release, current_assess_binding,
        )
        final_binding = current_assess_binding(
            args.client, sweep_path=scoped_path, sweep=scoped_artifact)
        _, approval_status, approval_problems = assess_approval_for_release(
            args.client, current_binding=final_binding)
    except Exception as e:  # noqa: BLE001 - release authorization fails closed
        approval_status = "invalid"
        approval_problems = [f"Assess approval validation failed: {e}"]
    if approval_status != "approved":
        approval_violations.append(_BlockingViolation(
            "assess_approval",
            "; ".join(approval_problems[:3])
            or "current agency Assess results are not approved"))

    violations = list(violations) + approval_violations

    link_outcome = run_client_link_gate(
        html,
        enabled=not args.offline,
        figures=(link_gate_pack.facts if link_gate_pack is not None else ()),
    )
    violations += list(link_outcome.violations)

    stamp = "" if not violations else ".DO-NOT-SEND"
    for v in violations:
        print(f"[QA] FAIL: {v.rule} · {v.detail}", file=sys.stderr)

    artifact_base = os.path.join(
        REPORT_DIR, f"{slug}.agency_{a_slug}.assessment.client")
    out = artifact_base + f"{stamp}.html"
    try:
        _atomic_write_text(out, html)
    except Exception as e:  # noqa: BLE001 - prior artifact must survive intact
        print(f"[out] atomic write FAILED ({e}); prior report, if any, remains "
              f"intact at {out}", file=sys.stderr)
        return 2
    print(f"[out] {out}", file=sys.stderr)

    sidecar = artifact_base + ".link-integrity.internal.md"
    try:
        if link_outcome.manual_checks or link_outcome.claim_warnings:
            _atomic_write_text(
                sidecar,
                render_link_integrity_internal_md(
                    f"{args.client} · {agency['abbr']} agency assessment",
                    link_outcome),
            )
            print(f"[links] INTERNAL sidecar -> {sidecar}", file=sys.stderr)
        elif os.path.exists(sidecar):
            os.remove(sidecar)
    except OSError as exc:
        print(f"[links] INTERNAL sidecar unavailable ({exc})", file=sys.stderr)

    if not violations:
        try:
            folder = os.path.expanduser(f"~/Desktop/{args.client}")
            pretty = os.path.join(
                folder,
                f"{args.client.replace(' ', '_')}_{agency['abbr']}_Focus_"
                f"{date.today().isoformat()}.html")
            _atomic_write_text(pretty, html)
            print(f"[out] {pretty}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - repo artifact already stands
            print(f"[out] Desktop delivery FAILED non-fatally ({e}); report "
                  f"remains available at {out}", file=sys.stderr)
    return 0 if not violations else 2


if __name__ == "__main__":
    raise SystemExit(main())
