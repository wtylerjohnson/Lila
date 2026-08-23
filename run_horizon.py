#!/usr/bin/env python3
"""Developing Horizon — compose, refine, approve. The dialogue gate's CLI.

    python3 run_horizon.py --client "Osprey Flight Solutions"              # compose a draft
    python3 run_horizon.py --client "..." --refine "sharpen the CBP item"  # one refine round
    python3 run_horizon.py --client "..." --approve                        # human sign-off
    python3 run_horizon.py --client "..." --show                           # current state

Compose and refine run through the DecisionEngine (Max-plan routed) against a
deterministic fact bank built from the client's sweep artifact; validation
rejects any signal citing a source outside that bank. Only an APPROVED set
renders into the deliverable — the FINAL PRODUCT build loads it by status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.env import load_env  # noqa: E402

load_env()

ROOT = os.path.dirname(os.path.abspath(__file__))


def _slug(name: str) -> str:
    from tools.slug import client_slug
    return client_slug(name)


def _print_state(payload: dict) -> None:
    s = payload.get("set") or {}
    print(f"[horizon] schema=v{payload.get('schema_version', 1)} · "
          f"status={payload.get('status')} · "
          f"{len(s.get('items') or [])} items · "
          f"{len(payload.get('rounds') or [])} refine rounds", file=sys.stderr)
    for it in s.get("items") or []:
        print(f"  [{it.get('confidence', '?'):8}] {it.get('title', '')[:64]} "
              f"· {it.get('where', '')[:40]} · window: {it.get('window', '')}",
              file=sys.stderr)
    if s.get("refine_note"):
        print(f"  note: {s['refine_note']}", file=sys.stderr)


def _refresh_assess_ledger(client_name: str) -> None:
    """Best-effort projection; Horizon itself remains available on failure."""
    # Creation-free (2026-07-12): refresh an existing strict run only; the
    # cutover pointer is operator-owned (tools/assess_refresh.py).
    from tools.assess_refresh import refresh_current_assess_run_if_active
    print("[assess-ledger] "
          + refresh_current_assess_run_if_active(client_name),
          file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--refine", metavar="INSTRUCTION",
                    help="one refine round under this operator instruction")
    ap.add_argument("--approve", action="store_true",
                    help="human sign-off: only an approved set ever renders")
    ap.add_argument("--show", action="store_true",
                    help="print the current draft/approved state and exit")
    ap.add_argument("--recompose", action="store_true",
                    help="required to overwrite an existing draft/approved set "
                         "(refine rounds are discarded)")
    args = ap.parse_args()

    from agents.reports.horizon import (
        HorizonValidationError, approve_horizon, compose_horizon, load_horizon,
        current_horizon_binding, horizon_binding_problems, new_draft,
        refine_horizon, save_horizon,
    )

    payload = load_horizon(args.client)

    if args.show:
        if not payload:
            print("[horizon] no draft exists — compose first", file=sys.stderr)
            return 2
        _print_state(payload)
        for problem in horizon_binding_problems(payload):
            print(f"[horizon] CURRENT BINDING: {problem}", file=sys.stderr)
        return 0

    if args.approve:
        if not payload:
            print("[horizon] nothing to approve — compose first", file=sys.stderr)
            return 2
        try:
            approved = approve_horizon(payload)
        except HorizonValidationError as exc:
            for problem in exc.problems:
                print(f"[horizon] APPROVAL REJECTED: {problem}", file=sys.stderr)
            print("[horizon] the draft remains unapproved; base assessment "
                  "generation remains available without this layer",
                  file=sys.stderr)
            return 3
        save_horizon(approved)
        _refresh_assess_ledger(args.client)
        count = len(payload["set"].get("items") or [])
        print(f"[horizon] APPROVED · {count} items; zero items means no Horizon "
              "section in the next brief build", file=sys.stderr)
        return 0

    if args.refine:
        if not payload:
            print("[horizon] nothing to refine — compose first", file=sys.stderr)
            return 2
        print(f"[refine] {args.refine!r}", file=sys.stderr)
        try:
            payload = refine_horizon(payload, args.refine)
        except HorizonValidationError as exc:
            for problem in exc.problems:
                print(f"[refine] REJECTED: {problem}", file=sys.stderr)
            print("[refine] recompose under the current scope/profile before "
                  "editing; base assessment generation remains available",
                  file=sys.stderr)
            return 3
        save_horizon(payload)
        _refresh_assess_ledger(args.client)
        last = payload["rounds"][-1]
        if last["applied"]:
            print(f"[refine] APPLIED{' — ' + last['note'] if last['note'] else ''}",
                  file=sys.stderr)
        else:
            print(f"[refine] {last['note']}", file=sys.stderr)
        _print_state(payload)
        return 0 if last["applied"] else 3

    # compose: build the fact bank from the sweep artifact and draft the set
    if payload and not args.recompose:
        print(f"[horizon] a {payload.get('status')} set already exists with "
              f"{len(payload.get('rounds') or [])} refine rounds — pass "
              "--recompose to overwrite it", file=sys.stderr)
        return 2
    from agents.review import sweep_artifact_path
    sp = sweep_artifact_path(args.client)  # L19: gate-designated artifact
    if not os.path.exists(sp):
        print(f"[horizon] no sweep artifact at {sp} — run the search step first",
              file=sys.stderr)
        return 2
    with open(sp, encoding="utf-8") as f:
        sweep = json.load(f) or {}
    results = sweep.get("results") or {}
    try:
        binding = current_horizon_binding(args.client, sweep_path=sp,
                                          sweep=sweep)
    except Exception as exc:  # noqa: BLE001 - cannot bind means no valid draft
        print(f"[horizon] cannot bind draft to current run: {exc}",
              file=sys.stderr)
        return 2

    from agents.reports.horizon import build_fact_bank
    sweep_retrieved_at = sweep.get("generated_at")
    try:
        from datetime import datetime
        parsed_retrieval = datetime.fromisoformat(
            str(sweep_retrieved_at).replace("Z", "+00:00"))
        if parsed_retrieval.tzinfo is None or parsed_retrieval.utcoffset() is None:
            raise ValueError("timezone is missing")
    except (TypeError, ValueError):
        print("[horizon] sweep generated_at is missing, invalid, or timezone-naive; "
              "re-run the search so evidence time is deterministic",
              file=sys.stderr)
        return 2
    bank_preview = build_fact_bank(
        results, retrieved_at=sweep_retrieved_at)
    print(f"[facts] {len(bank_preview)} citable horizon facts "
          f"({', '.join(sorted({f['kind'] for f in bank_preview}))})",
          file=sys.stderr)
    if not bank_preview:
        print("[horizon] fact bank is empty; recording a zero-item draft "
              "without an LLM call", file=sys.stderr)
    else:
        print("[compose] drafting horizon items from the fact bank ...",
              file=sys.stderr)
    hset, bank, problems = compose_horizon(
        args.client, results, bank=bank_preview)
    if problems:
        for p in problems:
            print(f"[QA] FAIL: {p}", file=sys.stderr)
        print("[horizon] compose failed validation twice — NOT saved; "
              "fix the inputs and rerun", file=sys.stderr)
        return 2
    payload = new_draft(args.client, hset, bank, binding=binding)
    save_horizon(payload)
    _refresh_assess_ledger(args.client)
    print("[horizon] v2 DRAFT saved — refine or approve at the gate "
          "(a draft never renders)", file=sys.stderr)
    _print_state(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
