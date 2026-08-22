"""Pointer-gated strict-run refresh: ONE owner for every runner and gate hook.

The scope-specific current Assess pointer is the per-client cutover switch
and an OPERATOR decision (CONTRACT_SURFACES). materialize/persist mint that
pointer unconditionally, so every best-effort runner refresh was a latent
implicit cutover; the 2026-07-12 NETSCOUT DHS sweep proved it live. This
module makes refresh creation-free everywhere: an existing pointer refreshes
(operator approvals and fresh evidence stay bound to the current run), an
absent pointer skips loudly and the client keeps exact legacy behavior.

First activation is a deliberate operator command, never a side effect:

    python -m tools.assess_refresh --client "Name" --activate
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional


def _materialize(client_name: str, *, sweep_path=None, state_dir=None,
                 review_dir=None, expected_pointer=None) -> str:
    try:
        from agents.assess.ledger import materialize_current_assess_run
        kwargs: dict = {}
        if sweep_path is not None:
            kwargs["sweep_path"] = sweep_path
        if state_dir is not None:
            kwargs["state_dir"] = state_dir
        if review_dir is not None:
            kwargs["review_dir"] = review_dir
        if expected_pointer is not None:
            kwargs["expected_pointer"] = expected_pointer
        run, _path, diagnostics = materialize_current_assess_run(
            client_name, **kwargs)
        try:
            detail = (f" · {len(run.live.records)} live · "
                      f"{len(run.horizon.items)} horizon · "
                      f"{len(run.partners.items)} partner")
        except Exception:  # noqa: BLE001 - counts are log decoration only
            detail = ""
        note = f" · {len(diagnostics)} diagnostic(s)" if diagnostics else ""
        return f"refreshed {run.run_id}{detail}{note}"
    except Exception as e:  # noqa: BLE001 - nonfatal; INVALID holds lanes closed
        if "changed during refresh" in str(e):
            # the CAS repair (2026-07-12): the operator removed or replaced
            # the pointer mid-refresh; their decision stands, nothing written
            return (f"FAILED ({type(e).__name__}: {str(e)[:200]})")
        return (f"FAILED ({type(e).__name__}: {str(e)[:160]}); the current "
                "pointer is now stale against the fresh artifacts and the "
                "live lane holds closed until a successful refresh")


def refresh_current_assess_run_if_active(
    client_name: str,
    *,
    sweep_path=None,
    designator: Optional[str] = None,
    state_dir=None,
    review_dir=None,
) -> str:
    """Refresh the strict run ONLY where a cutover pointer already exists.

    Returns a log-ready outcome string and never raises. Absent pointer means
    the operator has not cut this scope over: nothing is created and the
    client keeps exact legacy behavior. A failed refresh is loud; the drifted
    pointer then resolves INVALID downstream, which can never release stale
    strict truth.
    """
    if designator is None:
        try:
            from agents.review import gate_designator
            designator = gate_designator(
                client_name,
                str(review_dir) if review_dir is not None else None) or "all"
        except Exception as e:  # noqa: BLE001 - unreadable gate: no refresh
            return f"skipped (gate designator unavailable: {type(e).__name__})"
    try:
        from agents.assess.ledger import current_assess_pointer_path
        pointer_path = current_assess_pointer_path(
            client_name, designator, state_dir=state_dir)
        try:
            pointer_snapshot = pointer_path.read_bytes()
        except OSError:
            pointer_snapshot = None
    except Exception as e:  # noqa: BLE001 - unresolvable designator: no pointer
        return f"skipped (pointer state unavailable: {type(e).__name__})"
    if pointer_snapshot is None:
        return ("absent (no strict pointer for this scope; refresh deferred "
                "to the operator's explicit cutover activation)")
    # The exact observed bytes ride to persistence as a compare-and-swap
    # expectation (2026-07-12): if the operator removes or replaces the
    # pointer while this refresh materializes, nothing is written and the
    # operator's rollback stands.
    return _materialize(client_name, sweep_path=sweep_path,
                        state_dir=state_dir, review_dir=review_dir,
                        expected_pointer={"bytes": pointer_snapshot})


def activate_current_assess_run(client_name: str, *, sweep_path=None,
                                state_dir=None, review_dir=None) -> str:
    """OPERATOR-ONLY first materialization: creates the cutover pointer.

    This is the sanctioned activation path (the operator's explicit act, on
    the CLI below or a future Control Room control). Runners and gate hooks
    never call this; they call the creation-free refresh above.
    """
    return _materialize(client_name, sweep_path=sweep_path,
                        state_dir=state_dir, review_dir=review_dir)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--sweep-path", default=None)
    ap.add_argument("--activate", action="store_true",
                    help="OPERATOR CUTOVER: first materialization for this "
                         "scope CREATES the current pointer and flips report "
                         "truth to the strict ledger. Without this flag the "
                         "command is creation-free.")
    args = ap.parse_args(argv)
    if args.activate:
        print("[activate] OPERATOR CUTOVER: materializing and creating the "
              "current pointer for this scope", file=sys.stderr)
        out = activate_current_assess_run(
            args.client, sweep_path=args.sweep_path)
    else:
        out = refresh_current_assess_run_if_active(
            args.client, sweep_path=args.sweep_path)
    print(f"[assess-ledger] {out}")
    return 0 if not out.startswith("FAILED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
