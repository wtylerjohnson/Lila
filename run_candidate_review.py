"""Candidate Review v1 driver (Chunk 6, Pass E).

Consumes the promoted watch generation and produces the certified, editable
Candidate Review document in one run:

    load generation -> verify leads -> author seeds -> build inventory
    -> remap alias ids -> vehicle coverage -> calendar -> compose
    -> render -> certify -> persist -> emit one marker

It consumes the watch generation; it never subsumes collection.  The sanctioned
no-flag invocation makes ZERO model calls and zero network calls (both
verification fetchers and the seed-author adapter default to ``None``), and
produces a valid, thin document.  Providers are injected for enrichment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

_ROOT = Path(__file__).resolve().parent
_STAGE = "candidate-review-document"


def _client_id(client: str) -> str:
    """Canonical client id, shared with the watch's mint (one client, one
    candidate-review directory); legacy hyphen dirs stay readable through
    the same fallback."""
    from run_candidate_review_watch import _candidate_client_id
    return _candidate_client_id(client)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _subprocess_environment() -> dict:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(_ROOT)] + ([environment["PYTHONPATH"]]
                        if environment.get("PYTHONPATH") else []))
    if not press_live_sam_enabled():
        # ZERO-SAM PRESS: a child sweep's sam.gov lane falls back to the
        # LIVE API when the daily extract is broken (it is, upstream, since
        # 2026-07-29), so the press hands children no SAM keys at all; the
        # keyless path is a typed, disclosed NOT_RUN, never a crash.
        environment.pop("SAM_GOV_API_KEY", None)
        environment.pop("SAM_GOV_API_KEY_2", None)
    return environment


def _relevance_overlay(
    root_path: Path, sweep_path: Path,
) -> tempfile.TemporaryDirectory:
    """Bind calibration to exactly the Candidate Review input root.

    The calibrator resolves repository-relative inputs from its imported code
    root.  Candidate Review supports an explicit alternate ``root`` (including
    isolated tests and staged presses), so running it directly could score an
    unrelated ignored sweep from the code checkout and then bind the receipt
    to a different artifact.  This overlay keeps executable modules on the
    current code revision while resolving clients and data from ``root_path``;
    its cleaned directory contains only the gate-designated sweep.
    """

    parent = root_path / "data" / "state" / "candidate_review_v1"
    parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix=".relevance-", dir=parent)
    overlay = Path(temporary.name)
    try:
        for name in ("tools", "agents"):
            os.symlink(_ROOT / name, overlay / name, target_is_directory=True)
        os.symlink(
            root_path / "clients", overlay / "clients", target_is_directory=True)
        overlay_data = overlay / "data"
        overlay_data.mkdir()
        cleaned = overlay_data / "cleaned"
        cleaned.mkdir()
        os.symlink(sweep_path, cleaned / sweep_path.name)
        source_data = root_path / "data"
        for child in source_data.iterdir():
            if child.name == "cleaned":
                continue
            os.symlink(
                child,
                overlay_data / child.name,
                target_is_directory=child.is_dir(),
            )
    except Exception:
        temporary.cleanup()
        raise
    return temporary


def press_live_sam_enabled() -> bool:
    """ZERO-SAM PRESS (2026-08-03). One flag gates every live-SAM-capable
    path in the press: the watch's live provider runtime, and the SAM keys
    handed to a child sweep. Default OFF: the apexanalytix press spent 28
    live calls against a permanent 20/day pool; the store and the extract
    serve everything a press needs. LILA_PRESS_LIVE_SAM=on restores the old
    behavior explicitly."""
    return os.environ.get("LILA_PRESS_LIVE_SAM", "off").strip().lower() in (
        "1", "on", "true", "yes")


def _ensure_current_sweep(root_path: Path, client: str) -> None:
    """Refresh the gate-designated sweep when the approved packet postdates it.

    The ordained flow is approve -> search -> report. The search leg here is
    the ZERO-LLM sweep (SAM plus the free lanes; triage and picture skipped),
    quota-guarded by the sources themselves, so the one-click press stays
    credit-free. A missing sweep (a fresh client) runs the same first search.
    The freshness predicate is the watch's own, imported, so the two can
    never drift.
    """

    import subprocess

    from agents.assessment_chain import canonical_slug
    from agents.review import sweep_artifact_path

    slug = canonical_slug(client)
    packet_path = root_path / "data" / "review" / f"{slug}.review.json"
    if not packet_path.is_file():
        raise ValueError(f"no approved Analyst packet at {packet_path.name}")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    client_name = packet.get("client_name") or client
    sweep_path = Path(sweep_artifact_path(
        client_name, review_dir=str(root_path / "data" / "review")))

    fresh = False
    decided_raw = packet.get("decided_at")
    if sweep_path.is_file() and decided_raw:
        try:
            from run_candidate_review_watch import (
                _aware_datetime,
                _source_data_as_of,
            )
            sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
            generated = _aware_datetime(
                sweep.get("generated_at"), label="sweep generated_at")
            as_of = _source_data_as_of(
                sweep.get("results") or {}, sweep_generated_at=generated)
            decided = _aware_datetime(
                decided_raw, label="packet decided_at")
            fresh = decided <= as_of
        except Exception:  # noqa: BLE001 - unreadable means refresh
            fresh = False
    def _repull_if_starved() -> None:
        # The scoped award re-pull (USAspending, quota-free) lands the
        # board's cited award records in the stored sweep; without them the
        # watch's parent-IDV identity lanes starve. Best effort by design.
        try:
            sweep_now = json.loads(sweep_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if (sweep_now.get("results") or {}).get("award_repulls"):
            return
        repull = subprocess.run(
            [sys.executable, "-m", "tools.api.award_repull",
             "--client", client_name],
            cwd=_ROOT, env=_subprocess_environment(),
            capture_output=True, text=True, check=False)
        if repull.returncode != 0:
            sys.stderr.write(
                "[press] award re-pull skipped: "
                + ((repull.stdout or "") + (repull.stderr or ""))
                .strip()[-200:] + "\n")

    if fresh:
        if root_path.resolve() == _ROOT:
            _repull_if_starved()
        return
    if root_path.resolve() != _ROOT:
        raise ValueError(
            "the sweep is stale against the approved packet and the sweep "
            "refresh runs only at the repo root")
    # ZERO-SAM PRESS (operator spec, 2026-08-03): with the flag off, the
    # press never launches the metered sweep fan-out. The stale (or absent)
    # sweep is disclosed loudly and the press continues on the durable
    # store, the extract, and the unmetered lanes; refreshing the sweep
    # stays the operator's separate, explicitly metered search step.
    from tools.api.sam_quota import press_live_sam_enabled
    if not press_live_sam_enabled():
        state = "stale against the approved packet" if sweep_path.is_file() \
            else "absent"
        sys.stderr.write(
            f"[press] ZERO-SAM: sweep artifact is {state}; the metered "
            "sweep refresh is skipped (LILA_PRESS_LIVE_SAM is off). The "
            "press continues on the durable notice store and unmetered "
            "lanes.\n")
        if root_path.resolve() == _ROOT:
            _repull_if_starved()
        return
    command = [sys.executable, "run_searches.py", "--client", client_name,
               "--skip", "triage", "picture"]
    completed = subprocess.run(
        command, cwd=_ROOT, env=_subprocess_environment(),
        capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        output = (completed.stdout or "") + (completed.stderr or "")
        raise ValueError(
            f"sweep refresh failed named: {output.strip()[-300:]}")
    _repull_if_starved()


def _ensure_relevance_receipt(root_path: Path, client: str) -> None:
    """Mint the deterministic relevance receipt when missing or stale.

    The watch hard-requires a current receipt. The calibrator is the real
    engine (zero LLM, zero network, advisory CSV); running it here keeps
    the one-click press self-sufficient for any approved client instead of
    failing named until a legacy refresh runs. The receipt binds the exact
    sanctioned command shape its validator pins.
    """

    import subprocess
    from datetime import datetime, timezone

    from agents.assessment_chain import (
        _relevance_input_binding,
        canonical_slug,
        relevance_receipt_valid,
        write_relevance_receipt,
    )
    from agents.review import sweep_artifact_path

    slug = canonical_slug(client)
    if relevance_receipt_valid(
            slug, root=root_path,
            allow_board_content_change=True, allow_sweep_change=True):
        return
    packet_path = root_path / "data" / "review" / f"{slug}.review.json"
    if not packet_path.is_file():
        raise ValueError(
            f"no approved Analyst packet at {packet_path.name}")
    client_name = (json.loads(packet_path.read_text(encoding="utf-8"))
                   .get("client_name") or client)
    calibration_path = (root_path / "data" / "state" / "relevance"
                        / f"{slug}.calibration.csv")
    calibration_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "tools.relevance.calibrate",
               "--client", client_name, "--out", str(calibration_path)]
    started_at = datetime.now(timezone.utc).isoformat()
    sweep_path = sweep_artifact_path(
        client_name, review_dir=str(root_path / "data" / "review"))
    with _relevance_overlay(root_path, Path(sweep_path)) as overlay_name:
        completed = subprocess.run(
            command, cwd=overlay_name, env=_subprocess_environment(),
            capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if completed.returncode != 0:
        raise ValueError(
            "relevance calibration failed named: "
            f"{output.strip()[-300:]}")
    summaries = [
        line.strip() for line in output.splitlines()
        if line.strip().startswith(f"[calibrate] {client_name}:")
    ]
    usable = [
        line for line in summaries if "skipped" not in line.casefold()
    ]
    if len(usable) != 1:
        detail = summaries[-1] if summaries else output.strip()[-200:]
        raise ValueError(
            f"calibration produced no usable run for {client_name}: {detail}")
    write_relevance_receipt(
        root_path, slug, client_name,
        command=command[1:],
        sweep_path=Path(sweep_path),
        started_at=started_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
        inputs=_relevance_input_binding(root_path, slug),
        calibration_path=calibration_path,
        summary=usable[-1])


def run_candidate_review(
    client: str,
    *,
    root: Path | str = _ROOT,
    state_root: Path | str | None = None,
    fetch_event=None,
    fetch_vehicle=None,
    adapter=None,
    released: bool = False,
    certified_at: Optional[datetime] = None,
    strict: bool = False,
    with_watch: bool = False,
    live: bool = False,
) -> Path:
    from agents.candidate_review_v1.alias_remap import (
        remap_accepted_evidence,
        remap_all,
        remap_evidence_ids,
    )
    from agents.candidate_review_v1.authoring import author_candidate_review
    from agents.candidate_review_v1.calendar_engine import build_calendar
    from agents.candidate_review_v1.candidate_engine import build_candidate_inventory
    from agents.candidate_review_v1.composer import compose_candidate_review_document
    from agents.candidate_review_v1.contracts import (
        CONTENT_BUDGETS,
        CandidateReviewDocument,
    )
    from agents.candidate_review_v1.document_release import (
        certify_candidate_review_release,
        write_candidate_review_release,
    )
    from agents.candidate_review_v1.event_research import EventDiscoveryResult
    from agents.candidate_review_v1.persistence import (
        default_generation_state_root,
        load_current_generation,
    )
    from agents.candidate_review_v1.renderer import render_candidate_review
    from agents.candidate_review_v1.vehicle_watch import (
        VehicleCollectionResult,
        project_vehicle_watch_coverage,
    )
    from agents.candidate_review_v1.verification import verify_research

    root_path = Path(root)
    effective_state_root = (
        Path(state_root) if state_root is not None
        else default_generation_state_root(root_path))
    client_id = _client_id(client)

    # STEP 1 (optional) - refresh the watch generation in-process first, so
    # one Command Center job carries collection AND press with a same-run
    # as_of (the 24h notice window stays open for current notices).
    if with_watch:
        _ensure_current_sweep(root_path, client)
        _ensure_relevance_receipt(root_path, client)
        from run_candidate_review_watch import run_watch_generation
        if live and not press_live_sam_enabled():
            print("[press] LILA_PRESS_LIVE_SAM is off: --live overridden; "
                  "the watch runs its replay/offline lanes and the store "
                  "serves everything", file=sys.stderr)
            live = False
        run_watch_generation(
            client_id, root=root_path, state_root=effective_state_root,
            live=live)

    # STEP 2 - load the promoted watch generation.
    generation = load_current_generation(client_id, state_root=effective_state_root)
    if generation is None:
        raise ValueError(
            "no current Candidate Review generation; run "
            f"run_candidate_review_watch.py --client {client_id} first")

    # STEP 3 - rehydrate typed inputs.
    binding = generation.receipt.binding
    as_of = datetime.fromisoformat(
        generation.manifests["pipeline-inputs"]["as_of"])
    event_result = EventDiscoveryResult.model_validate(
        generation.artifacts["event-discovery-result"])
    vehicle_result = VehicleCollectionResult.model_validate(
        generation.artifacts["vehicle-collection-result"])
    event_manifest = event_result.manifest
    vehicle_manifest = vehicle_result.manifest
    if event_manifest.binding != binding or vehicle_manifest.binding != binding:
        raise ValueError("generation manifest binding does not match the receipt")
    if event_manifest.window_start != as_of.date():
        raise ValueError("event manifest window does not open on the run as-of")
    if vehicle_manifest.as_of != as_of:
        raise ValueError("vehicle manifest as-of does not match the run as-of")

    client_dir = effective_state_root / client_id
    document_json = client_dir / f"{client_id}.candidate_review.document.json"
    prior_document = None
    if document_json.exists():
        prior_document = CandidateReviewDocument.model_validate_json(
            document_json.read_text(encoding="utf-8"))

    # STEP 4b - a live watch generation persisted its collection-time fetches;
    # replay them so verified_at stays inside the same-run 24h window and the
    # press spends zero network. Explicit fetchers always win; the no-artifact
    # default stays byte-identical (both None, typed drops).
    if fetch_event is None and fetch_vehicle is None:
        from agents.candidate_review_v1.generation_fetchers import (
            generation_bound_fetchers,
        )
        fetch_event, fetch_vehicle = generation_bound_fetchers(generation)

    # STEP 5 - verify leads into official evidence (injected seam #1).
    verified = verify_research(
        binding=binding, as_of=as_of, event_result=event_result,
        vehicle_result=vehicle_result, fetch_event=fetch_event,
        fetch_vehicle=fetch_vehicle,
        trusted_organizer_domains=event_manifest.trusted_organizer_domains)

    # STEP 6 - author seeds + narrative (injected seam #2).
    authored = author_candidate_review(
        binding=binding, as_of=as_of, evidence=verified.evidence,
        anchor_offers=verified.anchor_offers, adapter=adapter,
        max_candidates=CONTENT_BUDGETS["candidates"])

    # STEP 7 - build the candidate inventory.
    candidate_build = build_candidate_inventory(
        binding=binding, as_of=as_of, evidence=verified.evidence,
        seeds=authored.seeds, max_candidates=CONTENT_BUDGETS["candidates"])

    # STEP 8 - remap evidence ids onto the canonical universe.  The engine
    # collapses same-identity evidence and remaps its own seeds; everything
    # else minted before canonicalization is remapped here or the composer
    # silently drops it (the hollow-report failure).
    alias = candidate_build.evidence_alias_map
    canonical_by_id = {
        item.evidence_id: item for item in candidate_build.evidence}
    event_seeds = remap_all(verified.event_seeds, alias)
    vehicle_signals = remap_all(verified.vehicle_signals, alias)
    vehicle_watch_records = remap_all(verified.vehicle_watch_records, alias)
    pattern_claims = remap_all(authored.pattern_claims, alias)
    market_signals = remap_all(authored.market_signals, alias)
    past_awards = remap_all(authored.past_awards, alias)
    search_concepts = (
        remap_evidence_ids(authored.search_concepts, alias)
        if authored.search_concepts is not None else None)
    execution_framework = (
        remap_evidence_ids(authored.execution_framework, alias)
        if authored.execution_framework is not None else None)
    accepted_by_query = remap_accepted_evidence(
        verified.accepted_vehicle_evidence_by_query, alias, canonical_by_id)

    # STEP 9 - vehicle coverage.
    vehicle_coverage = project_vehicle_watch_coverage(
        vehicle_result, accepted_evidence_by_query=accepted_by_query or None)

    # STEP 10 - calendar.
    calendar = build_calendar(
        binding=binding, as_of=as_of, candidates=candidate_build.candidates,
        evidence=candidate_build.evidence, event_seeds=event_seeds,
        manifest=event_manifest, attempts=event_result.attempts,
        trusted_organizer_domains=event_manifest.trusted_organizer_domains,
        vehicle_signals=vehicle_signals,
        maximum_items=CONTENT_BUDGETS["calendar_events"])

    # STEP 11 - compose.
    composition = compose_candidate_review_document(
        binding=binding,
        document_id=f"crv1-{binding.client_id}-{binding.run_id}",
        as_of=as_of, generated_at=as_of, candidate_build=candidate_build,
        evidence=(), vehicle_signals=calendar.vehicle_signals,
        vehicle_watch_records=vehicle_watch_records,
        calendar_events=calendar.events_for_document,
        pattern_claims=pattern_claims,
        market_signals=market_signals,
        past_awards=past_awards,
        search_concepts=search_concepts,
        execution_framework=execution_framework,
        coverage=(*vehicle_coverage, *calendar.coverage_receipt.rows),
        previous_document=prior_document, strict=strict)
    document = composition.document

    # STEP 12-14 - render, certify, persist.
    html = render_candidate_review(document)
    certificate = certify_candidate_review_release(
        document, html,
        certified_at=certified_at or datetime.now(timezone.utc),
        released=released)
    qa_path = write_candidate_review_release(
        document, html, certificate, root=effective_state_root)
    _atomic_write_text(document_json, document.model_dump_json())
    return qa_path.resolve()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compose, render, and certify a Candidate Review v1 document")
    parser.add_argument("--client", required=True)
    parser.add_argument("--state-root", default=None)
    parser.add_argument(
        "--scope-override", default=None,
        help="Press at a DIFFERENT engagement scope for this run only "
             "(e.g. all_federal). The operator's stored scope gate is read "
             "and left exactly as found; the override is logged, recorded in "
             "the pack, and labels both the artifact family and the Desktop "
             "filename so a wider-scope run can never be mistaken for the "
             "engagement's own deliverable. Opt-in, never a default.")
    parser.add_argument("--author-seeds", action="store_true",
                        help="EXPERIMENTAL: opt in to the Max-plan seed-author lane")
    parser.add_argument("--offline", action="store_true",
                        help="explicit alias of the default zero-LLM path")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--with-watch", action="store_true",
                        help="refresh the watch generation first, one process")
    parser.add_argument("--live", action="store_true",
                        help="the watch refresh runs its live lanes "
                             "(quota-guarded); requires --with-watch")
    parser.add_argument("--golden-only", action="store_true",
                        help="press ONLY the golden deliverable from the "
                             "approved packet and unmetered evidence lanes; "
                             "skips the candidate-review document press and "
                             "its watch-generation requirement (the "
                             "zero-SAM re-press path, 2026-08-03)")
    parser.add_argument("--golden", action="store_true",
                        help="force the STEP 15 golden press on a bare "
                             "diagnostic invocation")
    parser.add_argument("--no-golden", action="store_true",
                        help="skip the STEP 15 golden press (diagnostics "
                             "only; the golden press is the deliverable)")
    args = parser.parse_args(argv)

    adapter = None
    if args.author_seeds and not args.offline:
        from agents.candidate_review_v1.authoring import SeedAuthorLaneNotImplemented
        _ = SeedAuthorLaneNotImplemented  # the lane raises until Pass D lands
        # (kept explicit so --author-seeds fails named, not silently)
        raise SystemExit(_failure(args.client,
                                  "seed-author lane is not implemented yet"))

    if args.golden_only:
        # The zero-SAM re-press path (2026-08-03): the golden deliverable
        # presses directly from the approved packet, the durable store, and
        # the unmetered lanes. No watch generation is required and none is
        # consulted; clients without one (Red Hat) stay pressable.
        try:
            from agents.golden_press.press import golden_press
            result = golden_press(
                args.client, scope_override=args.scope_override)
        except Exception as exc:  # noqa: BLE001 - one sanitized failure object
            sys.stderr.write(_failure(
                args.client,
                f"golden press: {type(exc).__name__}: {str(exc)[:400]}")
                + "\n")
            return 2
        sys.stdout.write(
            f"[golden] Command Center link: {result['cc_link']}\n")
        return 0

    try:
        qa_path = run_candidate_review(
            args.client, state_root=args.state_root, adapter=adapter,
            released=bool(args.release), strict=bool(args.strict),
            with_watch=bool(args.with_watch), live=bool(args.live))
    except Exception as exc:  # noqa: BLE001 - one sanitized failure object
        sys.stderr.write(_failure(args.client, f"{type(exc).__name__}: {str(exc)[:400]}") + "\n")
        return 2
    sys.stdout.write(f"[out:{_STAGE}] {qa_path}\n")

    # STEP 15 - the GOLDEN press (GOLDEN_BUILD Phase 3-4, 2026-07-24): the
    # frontier-model composition into the inherited golden skeleton is the
    # flow's deliverable. It runs on the Command Center's dispatch form
    # (--with-watch --live) and on an explicit --golden; the sanctioned bare
    # invocation stays ZERO model calls (driver contract). The deterministic
    # composition above still runs for its projections and DRAFT. A golden
    # failure is LOUD and fails the job; the deterministic artifacts remain
    # on disk for forensics.
    golden_wanted = args.golden or (args.with_watch and args.live)
    if args.no_golden or not golden_wanted:
        return 0
    try:
        from agents.golden_press.press import golden_press
        result = golden_press(args.client, scope_override=args.scope_override)
    except Exception as exc:  # noqa: BLE001 - one sanitized failure object
        sys.stderr.write(_failure(
            args.client,
            f"golden press: {type(exc).__name__}: {str(exc)[:400]}") + "\n")
        return 2
    sys.stdout.write(f"[golden] Command Center link: {result['cc_link']}\n")
    return 0


def _failure(client: str, reason: str) -> str:
    return json.dumps({
        "stage": _STAGE,
        "reason": reason,
        "fix_surface": (
            f"run_candidate_review.py --client {client} "
            "(requires a current Candidate Review watch generation)"),
    }, sort_keys=True, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
