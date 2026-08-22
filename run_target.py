#!/usr/bin/env python3
"""Step 3: contact strategy — Claude reviews qualified findings, plans who to reach.

    python3 run_target.py --client "PlanetiQ" [--finder apollo-handoff]

Reads data/review/<client>.qualify.json (from run_qualify.py) + the approved
strategy, then: Claude identifies known POCs (from SAM notices), the additional
contact titles worth pursuing, and the target orgs (agency offices, incumbents,
primes) — and hands the plan to a ContactFinder. The default finder writes a
Cowork-ready Apollo handoff file; it does NOT contact anyone.

Needs ANTHROPIC_API_KEY. Review the plan before executing the handoff in Cowork.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.alerts import desktop_alert  # noqa: E402
from agents.decisions.contacts import build_contact_plan, generate_title_strategy  # noqa: E402
from agents.review import REVIEW_DIR, ReviewPacket, _slug, load_approved  # noqa: E402
from tools.crm.base import FINDERS  # noqa: E402
import tools.crm.apollo_handoff  # noqa: E402,F401 — registers the finder


def _load_qualify_report(client: str) -> dict:
    path = os.path.join(REVIEW_DIR, f"{_slug(client)}.qualify.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found — run run_qualify.py first.")
    with open(path) as f:
        return json.load(f)


def _render_plan_md(plan) -> str:
    lines = [f"# Contact plan — {plan.client_name}", "", plan.strategy_note, ""]
    if plan.known_pocs:
        lines.append("## Known POCs (verified, from source notices)")
        for p in plan.known_pocs:
            lines.append(
                f"- **{p.name or 'n/a'}** — {p.title or 'n/a'} ({p.org or 'n/a'}) "
                f"| {p.email or 'no email'} | {p.phone or 'no phone'} "
                f"| opp `{p.opportunity_id}`"
            )
        lines.append("")
    lines.append("## Recommended searches")
    for s in plan.searches:
        lines.append(
            f"- **{s.org_name}** ({s.org_type.value}) — titles: {', '.join(s.person_titles)}"
        )
        lines.append(f"  - why: {s.rationale}")
    lines += ["", f"**Review gate:** {plan.review_gate}"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--finder", default="apollo-handoff",
                    help=f"contact finder to use (registered: {[f.name for f in FINDERS.all()]})")
    ap.add_argument("--max-candidates", type=int, default=12)
    args = ap.parse_args()

    strategy = load_approved(args.client)  # Step-1 approval still gates everything downstream
    report = _load_qualify_report(args.client)

    # TARGET opener: deliberate the buying-committee titles from the Assess picture
    # BEFORE any org-by-org search planning.
    print(f"[3a] Claude deliberating buyer titles for {args.client} (from Assess evidence) ...",
          file=sys.stderr)
    titles = generate_title_strategy(
        args.client, report, strategy=strategy, max_candidates=args.max_candidates
    )
    print(f"     {len(titles.personas)} persona(s): "
          + ", ".join(p.title for p in titles.personas[:5])
          + ("..." if len(titles.personas) > 5 else ""), file=sys.stderr)
    os.makedirs(REVIEW_DIR, exist_ok=True)
    titles_path = os.path.join(REVIEW_DIR, f"{_slug(args.client)}.titles.json")
    with open(titles_path, "w") as f:
        f.write(titles.model_dump_json(indent=2))

    print("[3b] Claude building contact plan around those titles ...", file=sys.stderr)
    plan = build_contact_plan(
        args.client, report, strategy=strategy, max_candidates=args.max_candidates,
        titles=titles,
    )
    print(f"    {len(plan.known_pocs)} known POC(s), {len(plan.searches)} search(es) recommended",
          file=sys.stderr)

    # Persist the plan + a readable summary next to the other review artifacts.
    os.makedirs(REVIEW_DIR, exist_ok=True)
    plan_path = os.path.join(REVIEW_DIR, f"{_slug(args.client)}.contacts.json")
    with open(plan_path, "w") as f:
        f.write(plan.model_dump_json(indent=2))
    md_path = plan_path.replace(".json", ".md")
    with open(md_path, "w") as f:
        f.write(_render_plan_md(plan))

    # Vet every org the plan wants to approach: SAM registration + exclusion
    # check BEFORE any outreach. Failure-honest; never blocks the handoff.
    from tools.api.entity_vetting import EntityVettingSource
    org_names = [s.org_name for s in plan.searches]
    print(f"[3c] vetting {min(len(set(org_names)), 10)} target org(s) against "
          f"SAM Entity + Exclusions ...", file=sys.stderr)
    try:
        vetting = EntityVettingSource().vet_many(org_names)
        flags = [o for o, v in vetting["orgs"].items() if v["verdict"] == "EXCLUDED"]
        print(f"     {vetting['vetted_count']} vetted"
              + (f" — WARNING, EXCLUDED PARTIES: {', '.join(flags)}" if flags else ", none excluded"),
              file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        vetting = {"error": str(e), "orgs": {}}
        print(f"     vetting failed (non-blocking): {e}", file=sys.stderr)
    vet_path = os.path.join(REVIEW_DIR, f"{_slug(args.client)}.vetting.json")
    with open(vet_path, "w") as f:
        json.dump(vetting, f, indent=2)

    # Hand off to the selected finder (default: Apollo handoff file for Cowork).
    finder = FINDERS.get(args.finder)
    out = finder.deliver(plan)
    print(f"    [{finder.name}] -> {out}", file=sys.stderr)

    fake_packet = ReviewPacket.model_construct(client_name=args.client)
    desktop_alert(fake_packet, md_path)
    print(f"[done] contact plan -> {md_path}", file=sys.stderr)
    print(f"       handoff      -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
