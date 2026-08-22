"""Golden press orchestrator (GOLDEN_BUILD Phases 3-4).

approve -> sweep -> THIS: build the evidence pack live, RENDER the content
region from the pack deterministically, ask the model for prose only, splice
into the golden skeleton, mechanically validate, and write the report file
the Command Center serves via /report?path=.

Two doctrines govern this file (operator, 2026-07-27):

  DETERMINISTIC RENDERING. Code renders every figure, date, identifier and
  link; the model writes prose and nothing else. A model cannot transcribe a
  number wrongly if it never transcribes one.

  THE REPORT ALWAYS GETS PRODUCED. Nothing here blocks on a data defect. A
  validation failure ships the report plus a loudly-named .FAILED.html copy
  and the violation sidecar, and returns certified=False. Loud, never silent,
  and never a press that yields nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from agents.golden_press import compose as compose_mod
from agents.golden_press.records import EvidencePack
from agents.golden_press.press_time import local_press_date
from agents.golden_press.render import (
    composer_demotions,
    prose_slots,
    render_content_region,
)
from agents.golden_press.retrieval import build_evidence_pack
from agents.golden_press.skeleton import (
    add_band_nav,
    splice,
    split_golden,
)
from agents.golden_press.studio import compile_editable_studio
from agents.golden_press.validate import validate_press

GOLDEN_DESIGN_SOURCE = "fixtures/golden/riverbed_golden.html"
PROSE_SIDECAR_SCHEMA_VERSION = 1


class GoldenPressError(RuntimeError):
    """Operator-facing press failure; artifacts for forensics stay on disk."""


def _log(msg: str) -> None:
    print(f"     [golden:press] {msg}", file=sys.stderr, flush=True)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_targets_export(out_dir: Path, stem: str, pack, log) -> None:
    """The Apollo-ready export, beside the client artifact.

    An OPERATOR TOOL (contract amendment v1.4), never a certified artifact:
    nothing in the release path reads it, and a failure to write it never
    touches the report. It sits next to the client file so a sequence load
    is one file away from the brief that justified it.
    """
    path = out_dir / f"{stem}.apollo_targets.csv"
    try:
        from agents.golden_press import targets_store
        rows = targets_store.write_sequence_csv(
            path, getattr(pack, "targeting", None))
        log(f"Apollo-ready export: {rows} sequence row(s) -> {path.name} "
            "(operator tool, outside the certified artifact)")
    except Exception as exc:  # noqa: BLE001 - an operator tool never sinks a press
        log(f"Apollo-ready export skipped ({type(exc).__name__}: {exc}); the "
            "certified artifact is unaffected")


def _composer_pack_json(pack) -> str:
    """The composer's copy: full records, research, selection, sufficiency,
    lanes, scarcity; the verbatim query ledger stays in the sidecar only."""
    payload = json.loads(pack.model_dump_json())
    payload.pop("queries", None)
    return json.dumps(payload, indent=1, ensure_ascii=False)


def _pack_payload(pack: EvidencePack) -> dict:
    return json.loads(pack.model_dump_json())


def _payload_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prose_sidecar_payload(pack: EvidencePack, prose: dict) -> dict:
    """Versioned, pack-bound prose needed for a byte-faithful re-render.

    The sidecar intentionally contains no model transcript and no clock read.
    It is the composed prose plus the minimum deterministic binding needed to
    prove that a later re-render is using the prose written for this exact
    evidence pack.
    """
    pack_payload = _pack_payload(pack)
    allowed = {slot["key"] for slot in prose_slots(pack)}
    normalized = {
        key: value for key, value in sorted(prose.items())
        if key in allowed and isinstance(value, str)
    }
    return {
        "schema_version": PROSE_SIDECAR_SCHEMA_VERSION,
        "client_name": pack.client_name,
        "evidence_pack_sha256": _payload_sha256(pack_payload),
        "prose": normalized,
    }


def _load_prose_sidecar(path: Path, pack: EvidencePack) -> dict:
    """Load only prose that is bound to ``pack`` and still passes its rules."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoldenPressError(
            f"stored prose sidecar is missing: {path.name}") from exc
    except (OSError, ValueError) as exc:
        raise GoldenPressError(
            f"stored prose sidecar is unreadable: {path.name}") from exc
    if not isinstance(payload, dict) \
            or payload.get("schema_version") != PROSE_SIDECAR_SCHEMA_VERSION:
        raise GoldenPressError("stored prose sidecar has an unsupported schema")
    if payload.get("client_name") != pack.client_name:
        raise GoldenPressError("stored prose sidecar belongs to another client")
    expected_hash = _payload_sha256(_pack_payload(pack))
    if payload.get("evidence_pack_sha256") != expected_hash:
        raise GoldenPressError("stored prose sidecar belongs to another evidence pack")
    raw = payload.get("prose")
    if not isinstance(raw, dict):
        raise GoldenPressError("stored prose sidecar has no prose object")

    wanted = {slot["key"] for slot in prose_slots(pack)}
    kept: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in sorted(raw.items()):
        if key not in wanted:
            raise GoldenPressError(
                f"stored prose sidecar contains an unknown slot: {key}")
        # WITH THE PACK, exactly as compose judged it. Reloading without it
        # applies the no-figure rule to prose the figure ruling (2026-07-30)
        # deliberately admitted, so every re-render of a current client died
        # on its own stored prose: "invalid prose for slot: band_calendar".
        usable = compose_mod._prose_usable(value, pack)
        if usable is None:
            raise GoldenPressError(
                f"stored prose sidecar contains invalid prose for slot: {key}")
        fingerprint = " ".join(usable.casefold().split())
        if fingerprint in seen:
            raise GoldenPressError(
                f"stored prose sidecar repeats prose in slot: {key}")
        seen.add(fingerprint)
        kept[key] = usable
    return kept


def _press_stamp(pack: EvidencePack) -> str:
    """The pack date, never the wall clock, so replay keeps the same chrome."""
    return local_press_date(pack.generated_at)


def _render_saved_inputs(
    client: str,
    *,
    slug: str,
    root: Path,
    pack: EvidencePack,
    prose: dict,
) -> tuple[str, str, dict, dict]:
    """Render and Studio-compile one pack/prose pair without retrieval.

    The model-facing work is already over. The Studio compiler runs on the
    complete spliced document, preserves every evidence/content surface, and
    adds only the deterministic editing runtime. Both initial press and replay
    use this seam, so they cannot drift into different deliverable formats.
    """
    # A replay adopts current pure decision rules over the sealed records.
    # This fixes old rule outputs without retrieval, mutation, or a model call.
    from agents.golden_press.decision_rules import build_decisions
    stamped = _press_stamp(pack)
    decision_date = date.fromisoformat(stamped) if stamped else None
    stored_decisions = pack.decisions or {}
    refreshed_decisions = build_decisions(pack, today=decision_date)
    # R2 may carry verbatim rows from the durable notice store, which is
    # deliberately not reopened during replay. Preserve that sealed surface;
    # R1/R3/R4 remain fully reproducible from the pack itself.
    if stored_decisions.get("r2"):
        refreshed_decisions["r2"] = stored_decisions["r2"]
    pack = pack.model_copy(update={"decisions": refreshed_decisions})
    # Targeting rides the same replay contract as the decision rules: the
    # pure specs re-derive from the refreshed rows, and the SEALED
    # enrichment (contacts + receipt already on the pack) is carried
    # forward verbatim rather than reopening the targets store. A replay
    # therefore makes zero live calls and zero file lookups for people.
    if pack.targeting is not None:
        from agents.golden_press.targeting_rules import build_targeting
        sealed = {"contacts": pack.targeting.get("contacts") or [],
                  "receipt": pack.targeting.get("enrichment_receipt")}
        try:
            pack = pack.model_copy(update={
                "targeting": build_targeting(pack, contacts=sealed)})
        except Exception:  # noqa: BLE001 - replay keeps the stored specs
            pass
    golden_path = root / GOLDEN_DESIGN_SOURCE
    golden_html = golden_path.read_text(encoding="utf-8")
    skeleton, _golden_content = split_golden(golden_html)
    if slug != "riverbed":
        from agents.golden_press.skeleton import brand_skeleton
        skeleton = brand_skeleton(
            skeleton, client_name=client, slug=slug, stamp=_press_stamp(pack))
    skeleton = add_band_nav(skeleton, with_events=bool(pack.events))
    content = render_content_region(pack, prose=prose)
    spliced = splice(skeleton, content)
    pack_hash = _payload_sha256(_pack_payload(pack))[:12]
    stamp = _press_stamp(pack)
    display = " ".join(str(pack.client_name or client).split()) or client
    filename_client = re.sub(r"[^A-Za-z0-9]+", "_", display).strip("_")
    studio = compile_editable_studio(
        spliced,
        client_name=display,
        stamp=stamp,
        report_id=f"{slug}-golden-studio-{stamp or 'undated'}-{pack_hash}",
        download_name=(
            f"{filename_client}_Federal_Opportunity_Pre_Assessment_"
            f"EDITABLE_STUDIO_{stamp or 'undated'}.html"),
        title=f"{display} · Federal Opportunity Pre-Assessment · Editable Studio",
        inject_runtime=False,
    )
    return content, studio.html, validate_press(content, studio.html, pack), studio.receipt


def _term_discovery(client: str, slug: str, root: Path,
                    log: Callable[[str], None]) -> Optional[dict]:
    """PRE-PRESS vocabulary discovery. Proposes; it does not decide.

    WHY IT RUNS EVERY PRESS (operator, 2026-08-06: "wire it into the pre
    press"). A frame written in vendor marketing language misses what a
    contracting officer writes, and the miss is invisible: the press returns
    a confident zero and reads as an empty market. apexanalytix's eight
    approved terms matched zero of 330,641 notices while the government was
    publishing an RFI for Audit Remediation Services at DIA.

    WHY IT ONLY PROPOSES. Capability terms are the ENGAGEMENT boundary, and
    the standing rule is that gate values stay operator-owned: data can tell
    us what the government calls a thing, never what this client sells. So
    this writes a review artifact and says so loudly in the log. A proposed
    term reaches a search only after the operator moves it into the profile,
    exactly as the keyword workshop already works.

    Free by construction: local store only, zero model calls, zero credits,
    zero network. A failure here is disclosed and never sinks the press.
    """
    try:
        from agents.golden_press.term_discovery import (
            propose_for_profile, render_review)
        from tools.notice_store import connect
    except Exception as exc:                              # noqa: BLE001
        log(f"term discovery unavailable ({type(exc).__name__}); the press "
            f"continues on the approved frame")
        return None
    try:
        profile = json.loads(
            (root / "clients" / slug / "profile.json").read_text(
                encoding="utf-8"))
    except (OSError, ValueError):
        log("term discovery: no client profile on disk; skipped")
        return None
    conn = None
    try:
        conn = connect()
        proposal = propose_for_profile(profile, conn)
    except Exception as exc:                              # noqa: BLE001
        log(f"term discovery failed ({type(exc).__name__}: {exc}); the press "
            f"continues on the approved frame")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                             # noqa: BLE001
                pass

    receipt = proposal.get("receipt") or {}
    candidates = proposal.get("candidates") or []
    out_dir = root / "data" / "review"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from tools.artifacts import atomic_write_json
        atomic_write_json(out_dir / f"{slug}.term_discovery.json", proposal)
        (out_dir / f"{slug}.term_discovery.md").write_text(
            render_review(proposal), encoding="utf-8")
    except Exception as exc:                              # noqa: BLE001
        log(f"term discovery: proposals computed but not written ({exc})")

    approved = [t for t in (profile.get("discovered_terms_approved") or [])
                if str(t).strip()]
    log(f"term discovery: seed set {receipt.get('seed_notices', 0)} notices "
        f"from NAICS {receipt.get('naics_seed_codes') or 'none'} over "
        f"{receipt.get('store_size', 0):,} stored; "
        f"{len(candidates)} candidate term(s) PROPOSED, {len(approved)} "
        f"previously approved and in the search; zero model calls, zero "
        f"credits")
    if candidates:
        top = ", ".join(f"{c['term']!r} ({c['store_hits']} notices, "
                        f"{c['lift']}x)" for c in candidates[:5])
        log(f"  strongest candidates: {top}")
        log(f"  NOT SEARCHED until approved. Review "
            f"data/review/{slug}.term_discovery.md, then move terms into "
            f"clients/{slug}/profile.json discovered_terms_approved")
    return proposal


def _bridge_entities(strategy, slug: str, root: Path,
                     log: Callable[[str], None]) -> tuple[object, str]:
    """2026-07-24 bridge, loud and disclosed: a packet approved before
    Phase 1 carries no research_entities; if the golden-build dev research
    artifact exists, its entities drive retrieval. A fresh intake (today's
    golden path) writes entities into the packet and this bridge is inert."""
    if getattr(strategy, "research_entities", None):
        return strategy, "packet"

    # THE SANCTIONED DURABLE SURFACE (2026-08-04). A packet approved before
    # the entities era cannot be edited in place: revise() refuses an
    # APPROVED strategy, and re-approval is an operator gate. The client
    # profile is the real, durable, operator-owned surface, and retrieval
    # already reads it for the NAICS boundary. Entities declared there beat
    # the dev artifact, so a client never depends on a development bridge.
    from agents.decisions.client_files import client_dir
    # client_dir's `root` REPLACES the clients base, it is not the repo root.
    profile_path = Path(client_dir(slug, root=str(root / "clients"))) / "profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # noqa: BLE001
            profile, _ = {}, log(
                f"client profile unreadable ({type(exc).__name__}); "
                f"falling through to the dev bridge")
        rows = profile.get("research_entities") or []
        if rows:
            # LOUD-TOLERANT READER (2026-08-07). A legacy profile row can
            # carry a kind the contract has since narrowed ('client' sank
            # the entire Riverbed press at validation). A reader skips the
            # row, names it in the log, and leaves the profile untouched;
            # dying here costs the operator the whole deliverable over one
            # stale row.
            from pydantic import ValidationError

            from agents.decisions.schemas import ResearchEntity
            entities, skipped = [], []
            for row in rows:
                try:
                    entities.append(ResearchEntity.model_validate(row))
                except ValidationError:
                    skipped.append(f"{row.get('name', '?')} "
                                   f"(kind={row.get('kind', '?')})")
            if skipped:
                log(f"profile entities: skipped {len(skipped)} row(s) whose "
                    f"kind the contract does not accept: "
                    f"{', '.join(skipped[:6])}; the profile is unchanged")
            strategy = strategy.model_copy(
                update={"research_entities": entities})
            kinds: dict = {}
            for entity in entities:
                kinds[entity.kind] = kinds.get(entity.kind, 0) + 1
            log(f"entities from the client profile: {len(entities)} rows "
                f"({kinds}) from {profile_path.name}; the approved "
                f"packet is unchanged on disk")
            return strategy, f"client profile ({profile_path.name})"

        # CLIENT IDENTITY (2026-08-06). A profile with no research_entities
        # but a named_competitors list is exactly the shape that zeroed
        # apexanalytix: the client and its own products sat in the competitor
        # list, so nothing resolved client-side and the decision layer went
        # silently empty. The audit splits that list into client, product and
        # competitor kinds and RECEIPTS every reclassification, so the client
        # is never searched as its own rival.
        if profile.get("named_competitors_and_incumbents"):
            from agents.decisions.schemas import ResearchEntity
            from agents.golden_press.client_identity import (
                audit_client_identity)
            audit = audit_client_identity(profile)
            # The audit marks the client's own rows kind='client' precisely
            # so they are NEVER searched as rivals; the research-entity
            # contract does not carry that kind, by design. They stay in
            # the audit receipt and out of retrieval.
            researchable = [e for e in audit["entities"]
                            if e.get("kind") in ("product", "competitor",
                                                 "reseller")]
            held_out = len(audit["entities"]) - len(researchable)
            if held_out:
                log(f"  client-kind row(s) held out of retrieval by design: "
                    f"{held_out}")
            entities = [ResearchEntity.model_validate(e)
                        for e in researchable]
            strategy = strategy.model_copy(
                update={"research_entities": entities})
            r = audit["receipt"]
            log(f"CLIENT IDENTITY: derived {r['counts']} entities from "
                f"{profile_path.name}")
            if r["reclassified_client"] or r["reclassified_product"]:
                log(f"  reclassified out of the competitor list: "
                    f"client={r['reclassified_client']} "
                    f"product={r['reclassified_product']}")
            if r["needs_operator_decision"]:
                log(f"  NEEDS OPERATOR DECISION (searched as rivals until "
                    f"confirmed): "
                    f"{[x['name'] for x in r['needs_operator_decision']]}")
            return strategy, f"client identity audit ({profile_path.name})"

    dev_path = root / "data" / "state" / "golden_build" / f"{slug}.research.raw.json"
    if not dev_path.exists():
        log("packet has no research entities and no dev artifact exists; "
            "L2 runs on the client name alone")
        return strategy, "packet (no entities)"
    raw = json.loads(dev_path.read_text(encoding="utf-8"))
    entities = (raw.get("strategy") or {}).get("research_entities") or []
    if not entities:
        return strategy, "packet (no entities)"
    from agents.decisions.schemas import ResearchEntity
    strategy = strategy.model_copy(update={
        "research_entities": [ResearchEntity.model_validate(e)
                              for e in entities]})
    log(f"ENTITY BRIDGE: packet predates Phase 1; using {len(entities)} "
        f"research entities from {dev_path} (disclosed in the pack)")
    return strategy, f"golden_build dev research artifact ({dev_path.name})"


def _deliver_to_desktop(
    html_path: Path,
    *,
    client: str,
    certified: bool,
    label: Optional[str] = None,
    log: Callable[[str], None] = _log,
) -> Optional[str]:
    """Copy a CERTIFIED press into the operator's client folder.

    The golden press became the one deliverable but never delivered: the last
    file in ~/Desktop/Riverbed was a 2026-07-21 legacy Signal Board press, so
    every golden press since then landed only under data/state and the operator
    never saw it.

    Naming follows run_signal_board.py. A CERTIFIED press takes the plain
    dated name and replaces its own earlier copy. An uncertified press is also
    parked in the folder (operator ruling 2026-07-30, superseding the
    certified-only gate) under the same name plus an incrementing DRAFT number,
    so a draft is unmistakably a draft sitting next to the clean file. A
    delivery failure is logged and never costs the caller the report.
    """

    # FAIL-CLOSED LAW (repress tasking, 2026-08-03): promotion is atomic
    # and happens ONLY after every gate passes (structural validation,
    # vocabulary, suite, zero-SAM instrumentation, browser render, visual
    # parity). The press itself therefore never promotes when held, and an
    # UNCERTIFIED press never reaches the Desktop at all; drafts stay in
    # data/state. This supersedes the 2026-07-30 draft-parking ruling for
    # the client folder.
    if os.environ.get("LILA_PRESS_HOLD_DELIVERY", "").strip() == "1":
        log("delivery HELD (LILA_PRESS_HOLD_DELIVERY): promotion runs "
            "after the browser-render and parity gates, never inside the "
            "press")
        return None
    if not certified:
        log("uncertified press: NOT delivered; the draft stays in "
            "data/state (fail-closed promotion law, 2026-08-03)")
        return None
    try:
        root = Path(os.environ.get("LILA_DESKTOP_ROOT",
                                   os.path.expanduser("~/Desktop")))
        folder = root / str(client)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = date.today().isoformat()
        base = f"{client} · Federal Opportunity Pre-Assessment · {stamp}"
        # A run at a scope the engagement did not set gets its own name. The
        # certified path OVERWRITES its dated file, so without this a wider
        # run would silently replace the engagement's own deliverable with a
        # report covering agencies the operator excluded.
        if label:
            base = f"{base} · {label}"
        if certified:
            # The certified press owns the plain name and overwrites its own
            # earlier copy: one clean file per day, never a pile of near-twins.
            pretty = folder / f"{base}.html"
        else:
            # OPERATOR RULING 2026-07-30: park every draft in the client folder
            # too. Drafts keep the same name plus an incrementing draft number,
            # so an uncertified press is visibly a draft sitting beside the
            # clean file and can never be mistaken for it.
            number = 1
            while (folder / f"{base} · DRAFT {number}.html").exists():
                number += 1
            pretty = folder / f"{base} · DRAFT {number}.html"
        shutil.copyfile(html_path, pretty)
        log(f"delivered: {pretty}")
        print(f"[deliver] {pretty}", flush=True)
        return str(pretty)
    except OSError as exc:
        log(f"client-folder delivery failed ({type(exc).__name__}: {exc}); "
            f"the certified report is still at {html_path}")
        return None


def golden_press(
    client: str,
    *,
    root: Optional[Path] = None,
    state_root: Optional[Path] = None,
    scope_override: Optional[str] = None,
    run: Callable[..., str] = None,
    log: Callable[[str], None] = _log,
) -> dict:
    """Build pack -> render -> prose -> validate -> write. Returns the
    artifact paths, the verdict, and certified=False when the report shipped
    with violations. A data defect never costs the caller the report."""
    root = root or Path(__file__).resolve().parents[2]
    from agents.assessment_chain import canonical_slug
    from agents.review import load_packet

    slug = canonical_slug(client)
    packet = load_packet(client)
    strategy = packet.strategy
    log(f"packet loaded: status={getattr(packet.status, 'value', packet.status)}, "
        f"entities={len(getattr(strategy, 'research_entities', []) or [])}")
    strategy, entities_source = _bridge_entities(strategy, slug, root, log)

    sweep = None
    sweep_path = root / "data" / "cleaned" / f"searches_{slug}.json"
    if sweep_path.exists():
        sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    from tools.relevance.scope import load_engagement_scope
    scope = load_engagement_scope(client)
    # RUN-SCOPED ONLY, and never a gate edit (2026-07-30). The engagement
    # scope encodes a decision data cannot make, so this reads the operator's
    # stored gate and leaves it exactly as found: the override lives for one
    # press, is stated in the log, rides in the pack, and labels the artifact
    # so a wider-scope run can never be mistaken for the engagement's own
    # deliverable. An unrecognised preset refuses rather than guessing.
    if scope_override:
        from tools.relevance.scope import EngagementScope
        stored = scope.resolved_basis if scope else "UNSCOPED"
        # Built the same way the config file builds one, so an unknown preset
        # raises here instead of silently pressing at the wrong scope.
        scope = EngagementScope(preset=scope_override)
        log(f"SCOPE OVERRIDE (this run only): {scope.resolved_basis}; the "
            f"stored gate {stored} is unchanged on disk")
    log(f"scope: {scope.resolved_basis if scope else 'UNSCOPED'}")

    _term_discovery(client, slug, root, log)

    # PROACTIVE COMPETITIVE INTELLIGENCE BUILD (operator order 2026-08-05).
    # Discovery, identity expansion, the full lane matrix, recursive
    # discovery to saturation, adjudication, and the account picture all
    # complete BEFORE any composition. Each round rebuilds through the one
    # existing pack builder, so the final pack is exactly what a press that
    # had known every competitor from the start would have produced.
    from agents.golden_press.competitive import competitive_build
    profile_dict = None
    try:
        profile_dict = json.loads(
            (root / "clients" / slug / "profile.json").read_text(
                encoding="utf-8"))
    except (OSError, ValueError):
        pass
    competitive = competitive_build(
        strategy, sweep=sweep, profile=profile_dict, scope=scope, root=root,
        log=log,
        # the module-global stays the ONE patchable builder seam
        build_pack=build_evidence_pack,
        sam_degraded_note=("gate-designated sweep reused for the notice "
                           "lane (metered SAM quota is never spent here)"))
    pack = competitive["pack"]
    strategy = competitive["strategy"]
    # The render-facing slice rides the pack itself so a saved-inputs replay
    # reproduces the competitive band byte-for-byte with no re-research.
    pack.research["competitive"] = {
        "completeness": competitive["completeness"],
        "accounts": competitive["accounts"],
        "frame_confirmed": competitive["completeness"][
            "confirmed_competitors"],
        "lanes_searched": sorted({
            str(c.get("lane")) for c in
            competitive["search_ledger"]["cells"]
            if c.get("status") == "covered" and c.get("competitor") != "*"}),
        "rounds_run": competitive["search_ledger"]["rounds_run"],
    }
    pack.research["entities_source"] = entities_source
    if scope_override:
        # Disclosed in the pack, not just the log: an artifact that outlives
        # its console must be able to say what scope produced it.
        pack.research["scope_override"] = {
            "preset": scope_override,
            "resolved": scope.resolved_basis if scope else "UNSCOPED",
            "stored_gate_unchanged": True,
            "note": ("run-scoped override for this press only; the "
                     "engagement's stored scope gate was not edited"),
        }

    # EVENTS_LANE (2026-07-27). Two sources, one band, one record shape.
    #
    #   tier A (2026-07-27) · the daily extract: today's whole active
    #       universe with full description text, zero metered SAM quota.
    #   tier B (2026-07-30) · the accumulated notice store: industry days
    #       screened by the stricter industry-day doctrine, which excludes
    #       site visits and pre-proposal conferences as procurement mechanics
    #       rather than gatherings. The store is the right source for these
    #       because 6.4% of notices vanish from the extract every three days,
    #       and the store is the thing that accumulates them.
    #
    # Every URL in both tiers is verified live; anything that fails is skipped
    # and disclosed, never approximated. The tiers fail independently, so one
    # dying leaves the other standing, and a total lane failure never sinks
    # the press: events are additive.
    events_found: list = []
    screen: dict = {}
    try:
        from agents.golden_press.events import (
            harvest_tier_a, load_extract_rows, verify_urls,
        )
        from tools.api.sam_extract import _latest_path

        extract_path = _latest_path()
        if extract_path is None:
            log("events lane: no daily extract on disk; tier A reports empty")
            screen["tier_a"] = "no extract available"
        else:
            candidates, stats = harvest_tier_a(
                load_extract_rows(str(extract_path)), pack)
            verified, skipped = verify_urls(candidates)
            events_found.extend(verified)
            screen.update({
                "tier": "A", "source": "sam_extract",
                "extract_file": extract_path.name,
                "metered_quota_spent": 0,
                **stats, "verified": len(verified), "skipped": skipped,
            })
            log(f"events lane: {stats['event_language']} event-language "
                f"notices screened, {len(verified)} verified events kept, "
                f"{len(skipped)} skipped for failing live verification")
    except Exception as exc:  # noqa: BLE001 - the lane is additive
        log(f"events lane tier A failed ({type(exc).__name__}: {exc}); "
            "pressing without extract events")
        screen["tier_a_error"] = str(exc)[:200]

    try:
        from agents.golden_press.events import verify_urls
        from agents.golden_press.industry_days import (
            drop_already_shipped, harvest_from_store,
        )
        from tools.notice_store import connect as _store_connect

        _conn = _store_connect()
        try:
            store_rows = _conn.execute("SELECT * FROM notices").fetchall()
        finally:
            _conn.close()
        candidates, receipt = harvest_from_store(store_rows, pack, scope=scope)
        fresh = drop_already_shipped(candidates, events_found)
        receipt["already_in_tier_a"] = len(candidates) - len(fresh)
        verified_days, skipped_days = verify_urls(fresh)
        events_found.extend(verified_days)
        receipt.update({"verified": len(verified_days),
                        "skipped": skipped_days})
        screen["industry_days"] = receipt
        log(f"industry days: {receipt['matched_event_phrase']} event-phrase "
            f"matches over {receipt['screened']} stored notices, "
            f"{receipt['distinct_after_dedupe']} distinct and still future, "
            f"{receipt['client_relevant']} bound to a pack fact, "
            f"{len(verified_days)} verified and kept")
    except Exception as exc:  # noqa: BLE001 - the lane is additive
        log(f"industry days failed ({type(exc).__name__}: {exc}); "
            "pressing with tier A events only")
        screen["industry_days"] = {"error": str(exc)[:200]}

    # tier C (2026-07-30): the curated conference roster - AUSA, AFCEA,
    # HIMSS and peers, the events a client should be AT that never post as
    # SAM notices because nobody procures a conference. The roster was
    # fetched, live-verified, and stored on 2026-07-28 and then NOTHING ever
    # read it: six verified conferences sat on disk while every report
    # shipped without them. Zero LLM here: discovery/refresh stays a
    # separate operator-scheduled step; this consumes the stored roster,
    # gates it by engagement scope, and re-verifies URLs LIVE like every
    # other tier. Stale roster keys are disclosed, never silently served.
    try:
        from agents.golden_press.events import verify_urls
        from agents.golden_press.events_conferences import (
            harvest_tier_c, load_roster, stale_keys,
        )
        # imported here, not inherited from tier B's block: a tier B failure
        # before its imports must not cost tier C a NameError
        from agents.golden_press.industry_days import drop_already_shipped

        roster = load_roster(str(root))
        conf_candidates, conf_stats = harvest_tier_c(
            pack, roster=roster, root=str(root), scope=scope)
        fresh_confs = drop_already_shipped(conf_candidates, events_found)
        verified_confs, skipped_confs = verify_urls(fresh_confs)
        events_found.extend(verified_confs)
        screen["conferences"] = {
            **conf_stats, "verified": len(verified_confs),
            "skipped": skipped_confs,
            "stale_roster_keys": stale_keys(roster),
        }
        log(f"conferences: {conf_stats['roster_rows']} on the roster, "
            f"{conf_stats['client_relevant']} bound to a pack fact, "
            f"{conf_stats['out_of_scope']} out of engagement scope, "
            f"{len(verified_confs)} verified live and kept")
    except Exception as exc:  # noqa: BLE001 - the lane is additive
        log(f"conference tier failed ({type(exc).__name__}: {exc}); "
            "pressing without conferences")
        screen["conferences"] = {"error": str(exc)[:200]}

    events_found.sort(key=lambda e: (e.event_start or e.registration_deadline
                                     or "9999", e.name))
    pack.events = [json.loads(e.model_dump_json()) for e in events_found]
    pack.events_screen = screen

    # NOTICE-STORE FRESHNESS. Read-only, best-effort, and never fatal: an
    # unreadable store leaves the field None and the press continues. The
    # point is that a lane reading a stale store cannot look identical to a
    # lane reading a current one.
    try:
        from tools.notice_store import connect as _ns_connect, last_ingest
        _conn = _ns_connect()
        _li = last_ingest(_conn)
        _rows = _conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"]
        _conn.close()
        pack.notice_store = ({"rows": _rows, **{k: _li[k] for k in (
            "ingest_date", "rows_in_file", "rows_new", "gone_since_prev",
            "suspect", "suspect_reason")}} if _li else
            {"rows": _rows, "ingest_date": None,
             "suspect_reason": "store present but never ingested"})
        log(f"notice store: {_rows:,} notices, last ingest "
            f"{pack.notice_store.get('ingest_date')}")
    except Exception as exc:  # noqa: BLE001 - freshness never fails a press
        log(f"notice store unreadable ({type(exc).__name__}); "
            "pressing without a freshness stamp")
        pack.notice_store = None

    # DECISION RULES (2026-08-03). Deterministic account decisions over the
    # finished pack plus the durable store: R1 head-to-head paper, R2
    # displacement with verbatim sentence receipts, R3 vehicle-expiry
    # context, R4 qualify-only adjacency. Zero LLM, zero network, zero
    # metered quota; stored ON the pack so replay re-renders the same band
    # and the validator derives its dates and ids. An unreadable store
    # leaves R2 with its stated store-unavailable receipt; rules never sink
    # the press.
    try:
        from agents.golden_press.decision_rules import build_decisions
        decision_store_rows = None
        try:
            from tools.notice_store import connect as _dr_connect
            _conn = _dr_connect()
            try:
                decision_store_rows = _conn.execute(
                    "SELECT notice_id, title, notice_type, agency, subtier, "
                    "office, posted, deadline, url, description_prefix "
                    "FROM notices").fetchall()
            finally:
                _conn.close()
        except Exception as exc:  # noqa: BLE001 - R2 discloses the gap
            log(f"decision rules: store unreadable "
                f"({type(exc).__name__}); R2 runs on pack records only")
        pack.decisions = build_decisions(pack, decision_store_rows)
        log(f"decision rules: {len(pack.decisions['r1']['cards'])} "
            f"head-to-head cards, "
            f"{len(pack.decisions['r2']['alerts'])} displacement alerts, "
            f"{len(pack.decisions['r3']['contexts'])} vehicle contexts, "
            f"{len(pack.decisions['r4']['rows'])} qualify adjacencies; "
            f"{len(pack.decisions['r1']['receipt']['undated_not_evaluated'])}"
            f" undated award(s) not evaluated")
    except Exception as exc:  # noqa: BLE001 - loud, never silent, never fatal
        log(f"decision rules FAILED ({type(exc).__name__}: {exc}); the band "
            "will state that rules were not computed")
        pack.decisions = None

    # TARGETING RULES (contract amendment v1.4, 2026-08-05). One targeting
    # spec per R1 card, R2 alert and R4 qualify row, plus whatever the
    # DURABLE TARGETS STORE already holds for this client. ZERO LIVE CALLS:
    # the supply step is a separate sweep-class runner (run_targets.py) and
    # the press only ever reads its file, exactly as the L1 lane reads the
    # notice store instead of calling sam.gov. Stored on the pack so a
    # saved-inputs replay re-renders Band 09 from the same bytes.
    try:
        from agents.golden_press.targeting_rules import build_targeting
        from agents.golden_press import targets_store
        stored_contacts = targets_store.load(slug, root)
        pack.targeting = build_targeting(pack, contacts=stored_contacts)
        _receipt = pack.targeting["receipt"]
        log(f"targeting rules: {_receipt['specs_built']} spec(s) over "
            f"{sum(_receipt['rows_read'].values())} decision row(s); "
            f"{len(pack.targeting['contacts'])} stored contact(s), "
            + ("enrichment receipt on file"
               if pack.targeting["enrichment_receipt"]
               else "supply step not run for this client")
            + "; zero live calls at press time by construction")
    except Exception as exc:  # noqa: BLE001 - loud, never silent, never fatal
        log(f"targeting rules FAILED ({type(exc).__name__}: {exc}); the band "
            "will state that the specs were not computed")
        pack.targeting = None

    golden_path = root / GOLDEN_DESIGN_SOURCE
    golden_html = golden_path.read_text(encoding="utf-8")
    if slug != "riverbed":
        log(f"skeleton chrome branded for {client!r} (title, meta, report "
            "id, download name, brand word; header logo slot neutralized "
            "for the operator's mark)")
    pack_json = _composer_pack_json(pack)
    log(f"pack: {len(pack.records)} records -> composer payload "
        f"{len(pack_json):,} chars; the content region is rendered from this "
        "pack by code, so the golden region is no longer passed as a format "
        "contract and no assets need eliding")

    out_dir = Path(state_root) if state_root else (
        root / "data" / "state" / "candidate_review_v1" / slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    # A wider-scope run gets its OWN artifact family. The QA sidecar and
    # release state key on this stem, so letting an all-federal press
    # overwrite the engagement's own file could put a report covering
    # agencies the operator excluded behind a later release decision.
    stem = (f"{slug}.golden_report.scope_{scope_override}" if scope_override
            else f"{slug}.golden_report")

    def _draft_sink(step: str, text: str) -> None:
        _atomic_write_text(out_dir / f"{stem}.draft-{step}.html", text)

    kwargs = {"log": log, "draft_sink": _draft_sink}
    if run is not None:
        kwargs["run"] = run

    # DETERMINISTIC RENDERING (2026-07-27). Code renders every data-bearing
    # element from the pack; the model writes prose only. The prior path
    # asked one call to hand-write ~185 KB of HTML carrying several hundred
    # exact figures, then regenerate the whole document when any single one
    # was wrong: three consecutive presses died in that loop at the 2,400s
    # timeout. Prose is optional here, so a failed or refused prose call
    # costs the report its writing, never its evidence.
    slots = prose_slots(pack)
    prose, transcript = compose_mod.compose_prose(
        pack_json, slots, pack=pack, **kwargs)
    demotions = composer_demotions(pack, prose)
    if demotions:
        log(f"composer attested {len(demotions)} wrong-domain rival "
            f"record(s): {', '.join(sorted(demotions))} - demoted from "
            "rival seats, retained in the evidence dock")
        transcript.append({"step": "composer-demotions",
                           "records": [
                               {"record_id": rid, "reason": reason[:200]}
                               for rid, reason in sorted(demotions.items())
                           ]})
    content, spliced, verdict, studio_receipt = _render_saved_inputs(
        client, slug=slug, root=root, pack=pack, prose=prose)
    _draft_sink("render", content)
    log(f"rendered {len(content):,} chars deterministically from "
        f"{len(pack.records)} records; {len(prose)}/{len(slots)} prose slots "
        f"written, the rest on deterministic fallback text")
    log(f"editable Studio: {len(studio_receipt['source']['edit_ids'])} "
        f"editable fields, {len(studio_receipt['source']['logo_ids'])} "
        f"logo/seal slots, "
        f"{sum(n for _url, n in studio_receipt['source']['hrefs'])} links "
        "preserved")
    transcript.append({"step": "render", "chars": len(content),
                       "records": len(pack.records),
                       "prose_slots_filled": len(prose),
                       "prose_slots_total": len(slots)})
    if not verdict["ok"]:
        # Every rule the validator carries is unfirable from this path by
        # construction, so a violation here is a RENDERER defect, not a
        # model transcription error, and no amount of re-asking a model
        # fixes it. It is recorded loudly and the report still ships.
        log(f"validator: {len(verdict['violations'])} violations from the "
            "deterministic renderer. These are unfirable by construction, "
            "so this is a renderer regression: "
            + "; ".join(sorted({v["rule"] for v in verdict["violations"]}))[:300])
        transcript.append({"step": "validator-violations",
                           "source": "deterministic-renderer",
                           "violations": verdict["violations"]})

    from tools.artifacts import atomic_write_json
    pack_payload = _pack_payload(pack)
    atomic_write_json(out_dir / f"{stem}.evidence_pack.json", pack_payload)
    atomic_write_json(out_dir / f"{stem}.prose.json",
                      _prose_sidecar_payload(pack, prose))
    atomic_write_json(out_dir / f"{stem}.critique.json",
                      {"transcript": transcript})
    # G3 (2026-08-03): the press date is captured once (the pack's own
    # generated_at, stamped at press invocation) and persisted; re-renders
    # of a saved press reuse the stored value, never the wall clock.
    verdict = dict(verdict)
    verdict["press_date"] = _press_stamp(pack)
    atomic_write_json(out_dir / f"{stem}.validation.json", verdict)
    atomic_write_json(out_dir / f"{stem}.studio.json", studio_receipt)

    # Competitive Intelligence Build sidecars: the record of the proactive
    # research that preceded composition. Stable-ordered for replay.
    atomic_write_json(out_dir / f"{stem}.competitive_market_definition.json",
                      competitive["market_definition"])
    atomic_write_json(out_dir / f"{stem}.competitive_discovery.json",
                      competitive["discovery"])
    atomic_write_json(out_dir / f"{stem}.competitive_frame.json",
                      competitive["frame"])
    atomic_write_json(out_dir / f"{stem}.competitive_search_ledger.json",
                      competitive["search_ledger"])
    atomic_write_json(out_dir / f"{stem}.competitive_review.json",
                      competitive["review"])
    atomic_write_json(out_dir / f"{stem}.competitive_completeness.json",
                      competitive["completeness"])

    # The relevance calibrator's disagreement rows used to die as an
    # advisory CSV nobody consumed. Each press now attaches them as term
    # PROPOSALS (proposes only, operator disposes): rows where the engine
    # and the pipeline disagree are exactly where the capability
    # vocabulary needs an operator decision.
    calibration_csv = root / "data" / "state" / "relevance" / \
        f"{slug}.calibration.csv"
    if calibration_csv.exists():
        import csv as _csv
        with open(calibration_csv, encoding="utf-8") as handle:
            rows = [row for row in _csv.DictReader(handle)
                    if (row.get("disagreement") or "").strip()]
        kept_rows = rows[:200]
        by_kind: dict[str, int] = {}
        for row in rows:
            kind = row["disagreement"].strip()
            by_kind[kind] = by_kind.get(kind, 0) + 1
        atomic_write_json(out_dir / f"{stem}.term_proposals.json", {
            "source_csv": str(calibration_csv),
            "disagreement_counts": by_kind,
            "rows_total": len(rows),
            "rows_attached": len(kept_rows),
            "rows": [{
                "record_ref": row.get("record_ref"),
                "kind": row.get("kind"),
                "title": (row.get("title") or "")[:140],
                "disagreement": row.get("disagreement"),
                "engine_score": row.get("engine_score"),
                "matched_spans": row.get("matched_spans"),
            } for row in kept_rows],
        })
        dropped = len(rows) - len(kept_rows)
        log(f"calibration: {len(rows)} disagreement row(s) attached as "
            f"term proposals ({', '.join(f'{k}={n}' for k, n in sorted(by_kind.items()))})"
            + (f"; {dropped} beyond the 200-row attachment cap" if dropped
               else ""))

    recall = None
    if slug == "riverbed":
        from agents.golden_press.recall import score_recall
        recall = score_recall(pack.records, golden_html)
        atomic_write_json(out_dir / f"{stem}.recall.json", recall)
        log(f"recall vs golden dock: {recall['matched_count']}/"
            f"{recall['golden_total']}")

    # Operator-marked ideal (2026-08-04): when a golden target file exists
    # for this client, every press scores presence AND seating against it,
    # so "closer to the ideal" is a number in the receipts, not a feeling.
    from agents.golden_press.recall import (
        load_golden_targets,
        score_against_targets,
    )
    targets = load_golden_targets(root, slug)
    if targets:
        from agents.golden_press.render import shape
        seated_core = {
            r.record_id
            for r in shape(pack, demoted=frozenset(demotions))["core"]
        }
        golden_score = score_against_targets(
            pack.records, seated_core, targets)
        atomic_write_json(out_dir / f"{stem}.golden_score.json",
                          golden_score)
        log(f"golden targets: {golden_score['present_count']}/"
            f"{golden_score['targets_total']} present, "
            f"{golden_score['lead_count']}/{golden_score['lead_total']} "
            "holding a core seat"
            + ("; missing: " + ", ".join(golden_score["missing"][:6])
               if golden_score["missing"] else ""))

    # OPERATOR DOCTRINE (2026-07-27): "No blocking. Degrade instead. The
    # report always gets produced." A validation failure used to raise, so a
    # single unprovable token cost the operator the entire deliverable. It
    # now ships the report AND a loudly-named FAILED copy alongside it, with
    # the violation sidecar. Nothing is silent: the log names every rule, the
    # returned verdict carries ok=False, and the caller can gate on it.
    if not verdict["ok"]:
        failed_path = out_dir / f"{stem}.FAILED.html"
        _atomic_write_text(failed_path, spliced)
        print(f"[out:golden-report-FAILED] {failed_path}", flush=True)
        log(f"SHIPPING WITH {len(verdict['violations'])} VIOLATIONS. "
            f"Sidecar {out_dir / (stem + '.validation.json')}; flagged copy "
            f"{failed_path}. The report is produced because withholding it "
            "helps nobody, but it is NOT certified.")

    html_path = out_dir / f"{stem}.html"
    _atomic_write_text(html_path, spliced)
    # CONTRACT §3 (2026-08-03): the CLIENT build is generated from press
    # state by the export path, never authored separately. Studio
    # affordances (scripts, edit/studio attributes, hidden sections) are
    # physically removed; the aesthetic tokens+stylesheet already travel
    # inside the content region, so the client file is self-contained.
    from agents.golden_press.validate import client_export
    display_name = " ".join(str(pack.client_name or client).split())
    client_html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, "
        "initial-scale=1\">"
        f"<title>{display_name} · Federal Opportunity Pre-Assessment"
        "</title></head><body><main>"
        + client_export(content) + "</main></body></html>")
    client_path = out_dir / f"{stem}.client.html"
    _atomic_write_text(client_path, client_html)
    print(f"[out:golden-report-client] {client_path}", flush=True)
    _write_targets_export(out_dir, stem, pack, log)
    try:
        rel = html_path.relative_to(root)
    except ValueError:  # custom state_root outside the repo (tests)
        rel = html_path
    cc_link = "/report?path=" + urllib.parse.quote(str(rel))
    print(f"[out:golden-report] {html_path}", flush=True)
    log(f"Command Center link: {cc_link}")
    delivered = _deliver_to_desktop(
        html_path, client=pack.client_name or client,
        certified=bool(verdict["ok"]),
        label=(f"ALL-FEDERAL SCOPE ({scope_override})" if scope_override
               else None),
        log=log)

    # FEDERAL MARKET MAP (2026-08-07). A second RENDER of the same finished
    # pack through the typed projection and its deterministic renderer,
    # writing its own artifact family beside the golden one. Additive and
    # fail-soft by law: a Market Map defect is logged and recorded in the
    # receipt, and the golden deliverable ships exactly as it always did.
    # Zero LLM and zero metered quota. The renderer itself is zero-network;
    # on a LIVE press only, the preflight below may cache the current client's
    # public mark from the website already supplied at intake. Saved-input
    # replay never runs this preflight and remains deterministic.
    market_map: Optional[dict] = None
    if not scope_override:  # a scope-labelled run keeps one artifact family
        try:
            from agents.golden_press.market_map_press import (
                deliver as _mm_deliver, hydrate_market_map_client_mark,
                press_market_map,
            )
            mark_state = hydrate_market_map_client_mark(
                client=pack.client_name or client, slug=slug, root=root,
                log=log)
            log(f"market map client identity: {mark_state}")
            market_map = press_market_map(
                pack=pack, client=client, slug=slug, root=root,
                out_dir=out_dir, stamp=_press_stamp(pack),
                profile=profile_dict, log=log)
            market_map["delivered_path"] = _mm_deliver(
                market_map, client=pack.client_name or client, log=log,
                deliver_fn=_deliver_to_desktop)
            print(f"[out:market-map] {market_map['html_path']}", flush=True)
        except Exception as exc:  # noqa: BLE001 - never sinks the golden press
            log(f"market map press FAILED ({type(exc).__name__}: {exc}); "
                "the golden deliverable is unaffected")
            market_map = {"certified": False, "error": str(exc)[:300]}

    return {
        "html_path": str(html_path),
        "client_path": str(client_path),
        "cc_link": cc_link,
        "certified": bool(verdict["ok"]),
        "delivered_path": delivered,
        "validation": verdict,
        "recall": recall,
        "market_map": market_map,
        "pack_records": len(pack.records),
        "sidecars": {
            "evidence_pack": str(out_dir / f"{stem}.evidence_pack.json"),
            "prose": str(out_dir / f"{stem}.prose.json"),
            "critique": str(out_dir / f"{stem}.critique.json"),
            "validation": str(out_dir / f"{stem}.validation.json"),
            "studio": str(out_dir / f"{stem}.studio.json"),
        },
    }


def rerender_golden_press(
    client: str,
    *,
    root: Optional[Path] = None,
    state_root: Optional[Path] = None,
    log: Callable[[str], None] = _log,
) -> dict:
    """Re-render the sealed report from its pack and prose sidecars only.

    This path performs no retrieval, live verification, or model call. The
    prose sidecar is mandatory and hash-bound to the exact evidence pack, so a
    re-render either reproduces the composed narrative or fails named instead
    of silently flattening the report to fallback prose.
    """
    root = root or Path(__file__).resolve().parents[2]
    from agents.assessment_chain import canonical_slug

    slug = canonical_slug(client)
    out_dir = Path(state_root) if state_root else (
        root / "data" / "state" / "candidate_review_v1" / slug)
    stem = f"{slug}.golden_report"
    pack_path = out_dir / f"{stem}.evidence_pack.json"
    prose_path = out_dir / f"{stem}.prose.json"
    try:
        pack = EvidencePack.model_validate_json(
            pack_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoldenPressError(
            f"stored evidence pack is missing: {pack_path.name}") from exc
    except (OSError, ValueError) as exc:
        raise GoldenPressError(
            f"stored evidence pack is unreadable: {pack_path.name}") from exc
    prose = _load_prose_sidecar(prose_path, pack)
    content, spliced, verdict, studio_receipt = _render_saved_inputs(
        client, slug=slug, root=root, pack=pack, prose=prose)

    from tools.artifacts import atomic_write_json
    validation_path = out_dir / f"{stem}.validation.json"
    studio_path = out_dir / f"{stem}.studio.json"
    verdict = dict(verdict)
    verdict["press_date"] = _press_stamp(pack)
    atomic_write_json(validation_path, verdict)
    atomic_write_json(studio_path, studio_receipt)
    failed_path = out_dir / f"{stem}.FAILED.html"
    if not verdict["ok"]:
        _atomic_write_text(failed_path, spliced)
    html_path = out_dir / f"{stem}.html"
    _atomic_write_text(html_path, spliced)
    # CONTRACT §3: the client build is generated from press state by the
    # export path, and a re-render IS new press state. Leaving the prior
    # client file in place handed the operator a certified verdict stapled
    # to a stale client artifact still carrying the defect the re-render
    # fixed (witnessed 2026-08-05, the bare-0002 build).
    from agents.golden_press.validate import client_export
    display_name = " ".join(str(pack.client_name or client).split())
    client_html = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, "
        "initial-scale=1\">"
        f"<title>{display_name} · Federal Opportunity Pre-Assessment"
        "</title></head><body><main>"
        + client_export(content) + "</main></body></html>")
    client_path = out_dir / f"{stem}.client.html"
    _atomic_write_text(client_path, client_html)
    print(f"[out:golden-report-client] {client_path}", flush=True)
    _write_targets_export(out_dir, stem, pack, log)
    try:
        rel = html_path.relative_to(root)
    except ValueError:
        rel = html_path
    cc_link = "/report?path=" + urllib.parse.quote(str(rel))
    print(f"[out:golden-report] {html_path}", flush=True)
    log(f"re-rendered from {pack_path.name} + {prose_path.name}; "
        f"no retrieval or model call; Command Center link: {cc_link}")
    return {
        "html_path": str(html_path),
        "client_path": str(client_path),
        "cc_link": cc_link,
        "certified": bool(verdict["ok"]),
        "validation": verdict,
        "pack_records": len(pack.records),
        "sidecars": {
            "evidence_pack": str(pack_path),
            "prose": str(prose_path),
            "validation": str(validation_path),
            "studio": str(studio_path),
        },
    }
