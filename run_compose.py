#!/usr/bin/env python3
"""C1 composer CLI: the sanctioned relevance output and stored sweep
become clients/<slug>/signal_board_content.json plus the INTERNAL
compose trail (markdown, adapter-validated).

    python3 run_compose.py --client <slug>

Exit 0: trail then content published, content loader-valid, exactly one
`[out:compose-trail] <path>` marker on stdout. Nonzero: exactly one
named JSON failure {stage, reason, fix_surface} on stderr; stderr is
the ONLY failure channel (the C3 refresh boundary captures and parses
it from the same invocation; no failure sidecar file exists), and the
prior content/trail pair is restored with atomic, verified writes.
Composition binds to the
chain's relevance receipt (data/state/relevance/<slug>.run.json), the
sweep must belong to the profile-owned display client exactly, and the
deterministic template seams are the production default: the sanctioned
no-flag invocation makes zero LLM calls.

Publication protocol (crash-consistent): both artifacts stage and
validate in memory first; the trail, whose FINAL line binds the exact
content sha256 (`pair content sha256 <sha>`), publishes first, then
the content commits, each via an atomic single-file replace. A crash
between the two publishes leaves a mixed pair that every pair consumer
rejects through that binding (assessment_chain.validate_compose_pair
verifies it against the on-disk content); content-only readers always
see one complete content generation and never consume the trail.
Recovery is a compose rerun, which republishes both artifacts as one
bound generation. Snapshotting distinguishes an absent prior artifact
from an unreadable one (unreadable refuses at the snapshot stage;
nothing a rollback could not restore is ever deleted), and a failed
restoration is named in the failure reason, never claimed clean.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ROOT = os.path.dirname(os.path.abspath(__file__))


def relevance_output_path(slug: str) -> str:
    return os.path.join(_ROOT, "data", "state", "relevance",
                        f"{slug}.calibration.csv")


#: prior artifact did not exist; distinct from "existed but unreadable"
_ABSENT = object()


def _snapshot(path: str):
    """Prior bytes for restoration, or the ABSENT sentinel when no file
    exists. An existing-but-unreadable prior artifact RAISES OSError:
    deleting a file that could not be read is not restoration."""
    if not os.path.exists(path):
        return _ABSENT
    with open(path, "rb") as f:
        return f.read()


def _restore(path: str, blob) -> None:
    """Atomic, verified restoration; errors PROPAGATE to the caller.
    ABSENT removes the path; bytes replace via a same-directory
    temporary file + os.replace and are read back for byte equality."""
    if blob is _ABSENT:
        if os.path.exists(path):
            os.remove(path)
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=".restore-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    with open(path, "rb") as f:
        if f.read() != blob:
            raise OSError(
                f"restored bytes at {path} do not match the prior snapshot")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True,
                        help="client slug; paths derive from it, identity "
                             "comes from the profile")
    parser.add_argument("--best-fit", type=int, default=3)
    parser.add_argument("--competitors", type=int, default=5)
    parser.add_argument("--teaming", type=int, default=4)
    parser.add_argument("--horizon", type=int, default=12)
    parser.add_argument("--llm-prose", action="store_true",
                        help="EXPERIMENTAL: compose prose seams via the Max "
                             "CLI with deterministic evidence-grounding "
                             "validation; the default is deterministic "
                             "templates with zero LLM calls")
    parser.add_argument("--offline", action="store_true",
                        help="alias of the default deterministic mode; "
                             "kept for compatibility")
    parser.add_argument("--today", default=None,
                        help="compose date YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    slug = args.client

    def fail(stage: str, reason: str, fix_surface: str) -> int:
        # stderr is the single failure channel: one schema-valid JSON
        # line per nonzero exit; consumers parse the current invocation
        payload = {"stage": stage, "reason": reason,
                   "fix_surface": fix_surface}
        sys.stderr.write(json.dumps(payload) + "\n")
        return 2

    try:
        return _compose(args, slug, fail)
    except Exception as exc:  # noqa: BLE001 - no raw tracebacks, ever
        return fail("unexpected",
                    f"{type(exc).__name__}: {str(exc)[:200]}",
                    "agents/reports/composer.py")


def _compose(args, slug: str, fail) -> int:
    from agents.assessment_chain import (
        relevance_receipt_valid, validate_calibration_csv)
    from agents.reports import composer
    from agents.reports.board_content import (
        SignalBoardContent, content_path, load_board_content)
    from agents.review import sweep_artifact_path
    from tools.api.recompete import load_calendar
    from tools.atomic_io import atomic_write_text

    try:
        today = (date.fromisoformat(args.today) if args.today
                 else date.today())
    except ValueError as exc:
        return fail("parse-date", f"bad --today value: {exc}",
                    "pass --today as YYYY-MM-DD")

    # identity: slug for paths, profile-owned display name in artifacts
    try:
        from tools.capability import _slug as canonical, load_profile
        profile = load_profile(slug)
        if profile is None or not profile.is_populated():
            raise RuntimeError(
                f"no populated capability profile for slug {slug!r}")
        display_name = profile.client_name
        if canonical(display_name) != canonical(slug):
            raise RuntimeError(
                f"profile client {display_name!r} does not match slug "
                f"{slug!r}")
    except Exception as exc:  # noqa: BLE001 - identity is a named gate
        return fail("load-client", str(exc),
                    f"clients/{slug}/profile.json (+ New Client)")

    try:
        # the sweep artifact is owned by the review/press family and
        # resolves through the profile-owned display name, never the
        # canonical slug
        sweep_path = sweep_artifact_path(display_name)
        with open(sweep_path, encoding="utf-8") as f:
            sweep = json.load(f)
    except FileNotFoundError as exc:
        return fail("load-sweep", f"no stored sweep for {slug}: {exc}",
                    "run the sweep first (run_searches.py or the chain)")
    except (OSError, ValueError) as exc:
        return fail("load-sweep", f"sweep unreadable: {exc}", str(exc))
    if sweep.get("client") != display_name:
        return fail(
            "load-sweep",
            f"stored sweep belongs to {sweep.get('client')!r}, not the "
            f"profile-owned client {display_name!r}",
            f"run_searches.py --client {display_name}")

    # exact client binding on the prior board content: path placement is
    # not identity proof; an unreadable prior artifact is a publication
    # concern, not an identity one, and is handled at snapshot time
    try:
        prior_board = load_board_content(
            slug, allow_legacy_machine_scale=True)
    except Exception:  # noqa: BLE001 - identity refusal needs a loadable payload
        prior_board = None
    # canonical comparison, matching the press-side law: a foreign
    # client's artifact always differs canonically, while an operator's
    # slug-preserving display rename never bricks the sanctioned mutator
    if prior_board is not None and \
            canonical(prior_board.client_name) != canonical(slug):
        return fail(
            "load-board",
            f"stored signal-board content belongs to "
            f"{prior_board.client_name!r}, not the profile-owned client "
            f"{display_name!r}; path placement is not identity proof",
            f"clients/{slug}/signal_board_content.json")

    # C1 relevance binding: the chain's receipt contract, not a sentinel
    rel_path = relevance_output_path(slug)
    repair = (f"python3 -m tools.relevance.calibrate --client "
              f"{display_name} --out data/state/relevance/"
              f"{slug}.calibration.csv, then the chain relevance step "
              f"(run_assessment.py --client {slug}) mints the receipt")
    # compose is the sanctioned board-content mutator, so the receipt's
    # board-content binding is relaxed for exactly that file; every other
    # binding (taxonomy, profile, scope, calibration, sweep) stays exact
    if not relevance_receipt_valid(slug, root=_ROOT,
                                   allow_board_content_change=True):
        return fail(
            "relevance",
            "relevance receipt is missing, stale, tampered, or bound to "
            "other evidence; C1 composes only from the sanctioned "
            "relevance state",
            repair)
    try:
        validate_calibration_csv(rel_path, display_name)
    except Exception as exc:  # noqa: BLE001 - CSV contract is named
        return fail("relevance",
                    f"calibration CSV failed production validation: {exc}",
                    repair)

    try:
        inputs = composer.load_client_inputs(slug)
    except RuntimeError as exc:
        return fail("load-client", str(exc),
                    "onboard the client (+ New Client)")
    # exact client binding on the capability taxonomy: its embedded
    # client_name must resolve to this client (a derived taxonomy carries
    # the requested identity by construction); path placement plus the
    # receipt's sha binding is not identity proof
    tax_client = str(getattr(inputs["taxonomy"], "client_name", "") or "")
    if canonical(tax_client) != canonical(slug):
        return fail(
            "load-client",
            f"capability taxonomy belongs to {tax_client!r}, not the "
            f"profile-owned client {display_name!r}; path placement is "
            "not identity proof",
            f"clients/{slug}/capability_taxonomy.json")

    # exact client binding on the recompete calendar: the payload embeds
    # its owner and a foreign calendar refuses named, never feeds horizon
    # the recompete calendar is owned by its own subsystem's slug
    # family and resolves through the display name
    calendar = load_calendar(display_name)
    if calendar is not None and calendar.get("client") != display_name:
        return fail(
            "load-calendar",
            f"recompete calendar belongs to {calendar.get('client')!r}, "
            f"not the profile-owned client {display_name!r}; path "
            "placement is not identity proof",
            f"run_recompete.py --client {display_name} (rebuild the "
            "calendar for this client)")

    try:
        best_fit = composer.select_best_fit(
            sweep, inputs["taxonomy"], inputs["scope"], n=args.best_fit)
        award_best_fit_pool = []
        if not best_fit:
            award_best_fit_pool = composer.select_award_best_fit(
                sweep, inputs["taxonomy"], inputs["scope"],
                client_name=display_name, today=today,
                n=args.best_fit + args.competitors)
            best_fit = award_best_fit_pool[:args.best_fit]
        footprints = composer.select_client_footprints(
            sweep, inputs["taxonomy"], inputs["scope"],
            client_name=display_name, today=today)
        competitors = composer.select_competitors(
            sweep, n=args.competitors, today=today, scope=inputs["scope"],
            client_name=display_name, taxonomy=inputs["taxonomy"],
            naics_boundary=profile.naics_boundary)
        if award_best_fit_pool:
            competitor_gids = {
                str(pick.gid or "").strip() for pick in competitors
                if str(pick.gid or "").strip()
            }
            best_fit = [
                pick for pick in award_best_fit_pool
                if str(pick.gid or "").strip() not in competitor_gids
            ][:args.best_fit]
        teaming_findings: list[str] = []
        teaming = composer.select_teaming(
            sweep, best_fit, competitors, n=args.teaming,
            scope=inputs["scope"], profile=load_profile(display_name),
            diagnostics=teaming_findings)
        candidate_acquisition_pathways = (
            [] if teaming else composer.select_acquisition_pathways(
                sweep, best_fit, competitors, n=args.teaming,
                scope=inputs["scope"], profile=load_profile(display_name),
                taxonomy=inputs["taxonomy"], client_name=display_name,
                today=today)
        )
        base_featured_award_identities = {
            str(identity).strip()
            for pick in [*best_fit, *competitors]
            for identity in (
                getattr(pick, "award_id", ""),
                getattr(pick, "gid", ""),
                getattr(pick, "source_id", "")
                if getattr(pick, "source_kind", "") == "usaspending-award"
                else "",
            )
            if str(identity).strip()
        }
        base_horizon = composer.select_horizon(
            sweep, calendar, today=today, n=args.horizon,
            scope=inputs["scope"], taxonomy=inputs["taxonomy"],
            featured_award_identities=base_featured_award_identities,
            client_name=display_name)
        acquisition_pathways = composer.reserve_distinct_forward_timing(
            candidate_acquisition_pathways, base_horizon)
        featured_award_identities = {
            *base_featured_award_identities,
            *{
                str(identity).strip()
                for pick in acquisition_pathways
                for identity in (
                    getattr(pick, "award_id", ""),
                    getattr(pick, "gid", ""),
                )
                if str(identity).strip()
            },
        }
        horizon = composer.select_horizon(
            sweep, calendar, today=today, n=args.horizon,
            scope=inputs["scope"], taxonomy=inputs["taxonomy"],
            featured_award_identities=featured_award_identities,
            client_name=display_name)
    except composer.ComposerSelectionError as exc:
        return fail("select", str(exc), exc.fix_surface)
    except Exception as exc:  # noqa: BLE001 - selection is a named stage
        return fail("select", f"selection failed: {exc}",
                    "agents/reports/composer.py selection rules")

    # machine-screened safety: a composed artifact is not operator
    # approval. No core-evidenced best-fit play = named safe stop before
    # any byte; generic teaming without plays never exists.
    if not best_fit:
        return fail(
            "select",
            "no core-evidenced SAM play or active award corridor under the "
            "machine-screened bar; refusing to compose until the taxonomy "
            "or current buyer-map evidence yields one",
            "the keyword/NAICS review checkpoint "
            f"(clients/{slug}/capability_taxonomy.json)")
    from tools.relevance.engine import relevance_basis_violations
    cards = [(f"best_fit:{p.source_id}", p.verdict) for p in best_fit]
    basis = relevance_basis_violations(cards, machine_screened=True)
    if basis:
        return fail("select",
                    "sanctioned relevance validation rejected the "
                    f"selection: {'; '.join(basis[:3])}",
                    "the keyword/NAICS review checkpoint")

    llm = bool(args.llm_prose) and not args.offline
    try:
        prose_trail: list = []
        content = composer.compose_content(
            display_name, sweep, best_fit=best_fit, competitors=competitors,
            teaming=teaming, acquisition_pathways=acquisition_pathways,
            horizon=horizon, today=today,
            offline=not llm, prose_trail=prose_trail,
            footprints=footprints)
        content = composer.build_hero(
            content, competitors, teaming, sweep,
            client_name=display_name, best_fit=best_fit,
            footprints=footprints,
            acquisition_pathways=acquisition_pathways, horizon=horizon)
        trail = composer.build_trail(
            display_name, best_fit=best_fit, competitors=competitors,
            teaming=teaming,
            acquisition_pathways=acquisition_pathways,
            horizon=horizon, prose_trail=prose_trail,
            today=today, scope=inputs["scope"],
            selection_findings=teaming_findings)
        trail_md = composer.render_trail_md(trail, display_name)
    except Exception as exc:  # noqa: BLE001 - compose is a named stage
        return fail("compose", f"composition failed: {exc}",
                    "agents/reports/composer.py")

    # stage + validate BEFORE publication; evidence requirements bind here
    try:
        staged = json.dumps(content, indent=2, ensure_ascii=False) + "\n"
        validated = SignalBoardContent.model_validate(json.loads(staged))
        if validated.client_name != display_name:
            raise ValueError(
                f"content client {validated.client_name!r} != profile "
                f"{display_name!r}")
        if getattr(validated, "composition_mode", "operator") != "machine":
            raise ValueError("machine-produced content must declare "
                             "composition_mode machine")
        composer.validate_machine_evidence(
            content, trail,
            taxonomy=inputs["taxonomy"],
            profile=load_profile(display_name),
            sweep=sweep,
            calendar=calendar,
        )
        composer.assert_trail_header(trail_md, display_name)
    except Exception as exc:  # noqa: BLE001
        return fail("validate", f"staged artifacts failed validation: {exc}",
                    "agents/reports/composer.py")

    out_path = content_path(slug)
    t_path = composer.trail_path(slug)
    try:
        prior_content = _snapshot(out_path)
        prior_trail = _snapshot(t_path)
    except OSError as exc:
        return fail(
            "snapshot",
            "a prior artifact exists but cannot be read; refusing to "
            f"publish over a pair that could not be restored: {exc}",
            out_path)

    def rollback():
        """Restore the prior pair (content first, then trail) with
        atomic, verified writes. Returns None on success, else the error
        text: a failed restoration is REPORTED, never claimed clean."""
        try:
            _restore(out_path, prior_content)
            _restore(t_path, prior_trail)
            return None
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"

    def fail_rolled_back(stage: str, reason: str, fix: str) -> int:
        restore_error = rollback()
        if restore_error:
            reason += ("; restoration of the prior pair ALSO failed and "
                       f"the pair on disk may be mixed: {restore_error}")
        return fail(stage, reason, fix)

    # the trail's final line binds the exact content generation published
    # beside it, so a crash between the two publishes leaves a DETECTABLE
    # mixed pair, never a silently accepted one
    content_sha = hashlib.sha256(staged.encode("utf-8")).hexdigest()
    trail_publish = trail_md + composer.pair_binding_line(content_sha) + "\n"
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        atomic_write_text(t_path, trail_publish)    # trail publishes first
    except OSError as exc:
        return fail_rolled_back(
            "write-trail", f"cannot publish trail: {exc}", t_path)
    try:
        atomic_write_text(out_path, staged)         # content is the commit
    except OSError as exc:
        return fail_rolled_back(
            "write-content", f"cannot publish content: {exc}", out_path)
    try:
        if load_board_content(slug) is None:
            raise ValueError("published content did not load back through "
                             "the board content loader")
    except Exception as exc:  # noqa: BLE001 - readback failure rolls back
        return fail_rolled_back("validate", str(exc), out_path)

    counts = trail["prose"]["counts"]
    print(f"[out:compose-trail] {t_path}")
    print(out_path)
    print(f"[compose] sections: best_fit={len(best_fit)} "
          f"competitors={len(competitors)} teaming={len(teaming)} "
          f"acquisition_pathways={len(acquisition_pathways)} "
          f"horizon={len(horizon)} · prose {counts}")
    for finding in trail["named_findings"]:
        print(f"[compose:finding] {finding}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
