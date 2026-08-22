"""The INTERNAL file (client-file doctrine, 2026-07-10).

Nothing that grades our own prior work appears in the client file: findings
that did not verify carry no chip, row, caveat, or footnote there. They are
deleted from client copy and they live HERE, in full, with the forward
action each negative converts into. One file per assessment build, written
beside the client artifact, never copied to the client folder.
"""

from __future__ import annotations

from datetime import date
from typing import Optional


def build_internal_md(client_name: str, pack, qa_report,
                      arbiter_trail: Optional[dict],
                      results: Optional[dict] = None,
                      link_warnings: Optional[list[str]] = None,
                      manual_link_checks: Optional[list[str]] = None,
                      link_claim_warnings: Optional[list[str]] = None) -> str:
    lines = [
        f"# INTERNAL · {client_name} · Assessment build trail",
        f"_{date.today().isoformat()} · never leaves the shop; the client "
        "file carries verified claims and forward motion only_", ""]

    # 1. everything deleted or converted out of the client file
    removed = [a for a in qa_report.actions
               if a.action in ("suppress", "flag")]
    lines.append("## Removed or held from the client file")
    if removed:
        for a in removed:
            lines.append(f"- **{a.rule}** [{a.action}] at `{a.location}`: "
                         f"{a.reason}")
            if a.before:
                lines.append(f"  - text: {a.before}")
    else:
        lines.append("- nothing removed: the composed file was doctrine-clean")
    lines.append("")

    # 2. internal-only evidence: lane fallbacks and verified negatives, each
    # with the forward action it converts into
    lines.append("## Internal-only findings and their forward actions")
    internal_facts = [
        f for f in pack.facts
        if "LANE-LEVEL EVIDENCE ONLY" in f.text.upper()
        or f.text.startswith("FINDING:")]
    if internal_facts:
        for f in internal_facts:
            lines.append(f"- `{f.id}` {f.text}")
            if f.text.startswith("FINDING:"):
                lines.append("  - forward action: stand up a live watch on "
                             "the lanes/postings behind this screen; the "
                             "client file states the watch, never the zero")
            else:
                lines.append("  - forward action: upgrade to description-"
                             "level evidence before this claim enters a "
                             "client file")
    else:
        lines.append("- none this build")
    lines.append("")

    # 2b. per-figure provenance (2026-07-16): the client file never carries
    # retrieval vocabulary; THIS file is where the trail lives. One line per
    # fact: system, record, pull time (or the UNKNOWN_FRESHNESS marker), and
    # any open dispute.
    lines.append("## Figure provenance (per-figure freshness)")
    if pack.facts:
        for f in pack.facts:
            system = f.source_system.value if f.source_system else "unmapped"
            when = (f.retrieved_at.isoformat()
                    if f.retrieved_at is not None
                    else (f.freshness or "no retrieval time"))
            rec = f" record {f.source_record_id}" if f.source_record_id else ""
            flag = " · DISPUTED (unresolved blocks release)" if f.disputed else ""
            lines.append(f"- `{f.id}` {system}{rec} · retrieved {when}{flag}")
    else:
        lines.append("- no facts in this build")
    lines.append("")

    # 3. sweep-side internal notes
    r = results or {}
    scope_note = ((r.get("_search_scope") or {}).get("cut_warning")
                  if isinstance(r.get("_search_scope"), dict) else None)
    if scope_note:
        lines += ["## Scope", f"- {scope_note}", ""]
    try:
        from tools.vehicles import load_vehicles
        pending = [v["name"] for v in load_vehicles().get("vehicles", [])
                   if not v.get("verified")]
        if pending:
            lines += ["## Vehicle catalog, pending verification (internal)",
                      f"- {', '.join(pending)}", ""]
    except Exception:  # noqa: BLE001 — reference data optional here
        pass

    if link_warnings:
        lines.append("## SAM public-link verification warnings")
        lines.extend(f"- {warning}" for warning in link_warnings)
        lines.append("")

    if manual_link_checks:
        lines.append("## MANUAL LINK CHECK")
        lines.extend(f"- {warning}" for warning in manual_link_checks)
        lines.append("")

    if link_claim_warnings:
        lines.append("## AGENCY_DOC claim-visibility warnings")
        lines.extend(f"- {warning}" for warning in link_claim_warnings)
        lines.append("")

    # 4. the editor's value verdict (well presented / relevant / new to
    # the client / actionable) — the judgment behind the final polish
    ed = (arbiter_trail or {}).get("editor") or {}
    if ed.get("value_assessment"):
        lines.append("## Editor value assessment")
        lines.append(f"- revision accepted: {ed.get('accepted')}")
        for axis, note in ed["value_assessment"].items():
            lines.append(f"- **{axis}**: {note}")
        lines.append("")

    # 5. panel summary
    if arbiter_trail:
        lines.append("## Arbiter trail")
        lines.append(f"- consensus: {'PASS' if arbiter_trail.get('passed') else 'FAIL'}"
                     f" · {arbiter_trail.get('note') or ''}")
        rounds = arbiter_trail.get("rounds") or []
        if rounds:
            lines.append(f"- rounds: {[len(x.get('violations') or []) for x in rounds]}")
        lines.append("")
    return "\n".join(lines) + "\n"
