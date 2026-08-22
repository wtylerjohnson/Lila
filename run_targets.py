#!/usr/bin/env python3
"""Targeting supply step: resolve Band 09's specs into the targets store.

    python3 run_targets.py --client "NetScout" --dry-run
    python3 run_targets.py --client "NetScout" --cap 25

SWEEP CLASS, NEVER PRESS CLASS (REPORT_CONTRACT.md amendment v1.4). The
press renders Band 09 from the durable targets store and calls nobody. This
runner is what fills that store, on the operator's schedule, under the
operator's switch, exactly as the daily extract fills the notice store that
the L1 lane reads.

WHAT IT READS. The client's stored evidence pack, which already carries the
deterministic targeting specs the report printed. Nothing is re-derived
from a model and nothing new is retrieved from a federal source: the specs
are the report's own, so the people resolved here can only ever hang off a
record the report already cites.

GATES, IN ORDER, ALL LOUD.
  1. the Assess to Target operator door (data/review/<slug>.target_approval
     .json). Outreach is physically locked behind an explicit, journaled
     operator decision, and Apollo is named in that lock. This runner reads
     that gate and refuses when it is closed. It NEVER writes it.
  2. LILA_ENABLE_APOLLO, which defaults off.
  3. APOLLO_API_KEY in the environment. No code path here places a key.

--dry-run prints the exact searches the step would send and calls nothing,
so the plan can be reviewed at zero spend. It reveals no identity, so it
runs whether or not the switch is on.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

from agents.assessment_chain import canonical_slug  # noqa: E402
from agents.golden_press import targets_store  # noqa: E402
from agents.golden_press.records import EvidencePack  # noqa: E402
from agents.golden_press.targeting_rules import build_targeting  # noqa: E402
from tools import apollo_targets  # noqa: E402
from tools.apollo_credits import CreditRefusal  # noqa: E402

_ROOT = Path(__file__).resolve().parent


def pack_path(slug: str, root: Path, state_root: str = "") -> Path:
    base = (Path(state_root) if state_root
            else root / "data" / "state" / "candidate_review_v1" / slug)
    return base / f"{slug}.golden_report.evidence_pack.json"


def load_specs(slug: str, root: Path, state_root: str = "") -> tuple:
    """(specs, pack). Loud when the client has never been pressed: there is
    nothing to target before there is a report to target from.

    A pack stored before the targeting rules carries no specs, and one
    stored before a decision-rule revision carries the OLD row shape. Both
    are repaired the way a replay repairs them (press._render_saved_inputs):
    the pure rules re-derive over the sealed records at the pack's own press
    date, and R2's store-backed rows are preserved because the durable
    notice store is deliberately not reopened here. Measured 2026-08-05:
    reading the stored rows verbatim produced 0 specs on all seven live
    packs while the refreshed rows produce 53, because those packs predate
    the current R1 evidence shape.
    """
    from agents.golden_press.decision_rules import build_decisions
    from agents.golden_press.press import _press_stamp

    path = pack_path(slug, root, state_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(
            f"no evidence pack at {path}. Press the report first; the "
            f"targeting specs come from the report's own decision rows "
            f"({exc.__class__.__name__}).")
    except ValueError as exc:
        raise SystemExit(f"evidence pack at {path} is unreadable: {exc}")
    pack = EvidencePack.model_validate(payload)
    if pack.targeting:
        return list(pack.targeting.get("specs") or []), pack
    stamped = _press_stamp(pack)
    refreshed = build_decisions(
        pack, today=date.fromisoformat(stamped) if stamped else None)
    if (pack.decisions or {}).get("r2"):
        refreshed["r2"] = pack.decisions["r2"]
    pack.decisions = refreshed
    return list(build_targeting(pack).get("specs") or []), pack


def check_target_gate(client: str) -> None:
    """The Assess to Target operator door, read and never written.

    CONTRACT SURFACE (docs/CONTRACT_SURFACES.md, Assess -> Target operator
    door): the outreach lane, Apollo explicitly included, is locked behind
    an unrevoked proceed-to-Target decision AND a currently valid Assess
    approval. This step spends credits to resolve real people, so it sits
    behind that same door rather than beside it.
    """
    from agents.review import target_gate_status

    unlocked, problems = target_gate_status(client)
    if not unlocked:
        raise SystemExit(
            "Target is locked for this client, so the targeting supply step "
            "refuses:\n  - " + "\n  - ".join(problems)
            + "\nApprove the assessment and proceed to Target from the "
              "Review step in the Command Center. This runner never writes "
              "that decision.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Targeting supply step (Band 09)")
    ap.add_argument("--client", required=True)
    ap.add_argument("--cap", type=int,
                    default=apollo_targets.DEFAULT_CALL_CAP,
                    help="maximum Apollo search calls this run")
    ap.add_argument("--per-account", type=int, default=5,
                    help="people to resolve per buying account (depth). "
                         "COVERAGE IS NOT AFFECTED: every account is always "
                         "searched. This only sets how deep each one goes.")
    ap.add_argument("--state-root", default="",
                    help="override the press state directory (tests)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the planned searches and call nothing")
    ap.add_argument("--no-enrich", action="store_true",
                    help="search only; skip the billable work-email match")
    args = ap.parse_args()

    slug = canonical_slug(args.client)
    specs, _pack = load_specs(slug, _ROOT, args.state_root)
    # COVERAGE IS COMPLETE BY DEFAULT (operator ruling 2026-08-06). A pass
    # covers EVERY buying account the report cites. Nothing here trims the
    # spec list to fit a budget: the plan is costed whole, the cost is
    # alerted if it is large, and the operator decides. Depth per account is
    # the only dial, and it never removes an account.
    accounts = apollo_targets.dry_run(specs, per_page=args.per_account) \
        if specs else {"planned_calls": 0}
    print(f"[targets] {args.client}: {len(specs)} targeting spec(s) across "
          f"{accounts['planned_calls']} buying account(s) · FULL COVERAGE, "
          f"{args.per_account} per account", flush=True)
    if not specs:
        print("[targets] nothing to resolve: the report carries no targeting "
              "spec, so there is no organisation to search. Nothing written.",
              flush=True)
        return 0

    if args.dry_run:
        plan = apollo_targets.dry_run(specs, per_page=args.per_account)
        print(json.dumps(plan, indent=2))
        print(f"[targets] DRY RUN: {plan['planned_calls']} call(s) would be "
              f"sent to {plan['endpoint']}. Nothing was called and nothing "
              f"was written.", flush=True)
        return 0

    check_target_gate(args.client)
    try:
        payload = apollo_targets.resolve(
            specs, call_cap=args.cap, per_page=args.per_account,
            enrich_emails=not args.no_enrich)
    except apollo_targets.ApolloError as exc:
        print(f"[targets] REFUSED: {exc}", file=sys.stderr, flush=True)
        return 2
    except CreditRefusal as exc:
        print(f"[targets] REFUSED ON COST: {exc}", file=sys.stderr, flush=True)
        return 3

    receipt = payload["receipt"]
    path = targets_store.write(slug, payload, _ROOT)
    print(f"[out:targets-store] {path}", flush=True)
    print(f"[targets] {receipt['spend_note']}", flush=True)
    if receipt.get("truncated"):
        print(f"[targets] CAPPED: {receipt['truncated_note']}", flush=True)
    spend = receipt.get("spend") or {}
    print(f"[targets] emails resolved: {receipt.get('emails_resolved', 0)} "
          f"of {receipt['contacts_written']} stored contact(s) · "
          f"{receipt.get('enrich_calls', 0)} enrichment call(s)", flush=True)
    print(f"[targets] credits: projected {spend.get('credits_projected')} · "
          f"actual {spend.get('credits_spent')} · balance "
          f"{spend.get('balance_before')} -> {spend.get('balance_after')}",
          flush=True)
    print(f"[targets] {receipt['contacts_written']} contact(s) stored with "
          f"apollo provenance retrieved {receipt['retrieved_at']}. Re-press "
          f"the report to render them in Band 09.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
