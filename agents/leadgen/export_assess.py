"""Read-only AssessRun dump for Press Lead Gen.

Press (``python -m agents.leadgen.press --assess``) fails closed without
an AssessRun. This module does not invent one. It copies the current
immutable ledger pointer, or validates a caller-supplied file.

``run_assessment.py`` / ``AssessmentChain`` do not write AssessRun JSON.
Market Map packs, worksheets, notice lists, and ``source_records`` dumps
are not AssessRun. First materialization of a completed assessment is
the existing operator cutover:

    python -m tools.assess_refresh --client "Name" --activate

This exporter never calls ``materialize_current_assess_run``.

    python -m agents.leadgen.export_assess --client "Arista Networks"
    python -m agents.leadgen.export_assess --client "Arista Networks" \\
        --output /tmp/arista.assess_run.json
    python -m agents.leadgen.export_assess --client "Arista Networks" \\
        --from-file path/to/file.json --output /tmp/arista.assess_run.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from agents.assess.contracts import AssessRun
from agents.assess.ledger import (
    current_assess_pointer_path,
    load_current_assess_run,
)
from tools.artifacts import atomic_write_json
from tools.slug import client_slug

from .from_assess import coerce_assess_run
from .press import (
    NOTICE_ONLY,
    _has_assess_identity,
    _looks_like_notice_only,
)

EXPORT_KIND = "leadgen.assess_export.v1"
CHAIN_DOES_NOT_WRITE = (
    "run_assessment.py / AssessmentChain do not write AssessRun JSON "
    "under data/state/assess_runs/"
)
MARKET_MAP_REFUSED = (
    "Market Map / worksheet pack refused: that family is not an "
    "AssessRun. Press will not invent leads from a Market Map pack."
)
SOURCE_RECORDS_REFUSED = (
    "source_records input refused: this exporter will not invent "
    "AssessRun live/horizon records from a source_records dump. "
    "Every live Assess record needs primary_source evidence URLs "
    "from the sanctioned ledger. Materialize a REAL AssessRun via "
    'python -m tools.assess_refresh --client "Name" --activate'
)
LEADGEN_RECEIPT_REFUSED = (
    "press/draft LeadRow receipt refused: pass an AssessRun, not "
    "leadgen output. This exporter does not invent a parent envelope."
)
INVENTED_LIVE = (
    "invented or notice-only live record refused: every live "
    "Assess record must carry a primary_source evidence URL. "
    "This exporter does not mint notices."
)
DEMO_TESTCO = (
    "Testco-shaped demo AssessRun refused for client {requested!r}. "
    "A file such as arista_demo.assess_run.json is not a real Arista "
    "AssessRun. Press will not invent leads from a demo fixture."
)
CLIENT_MISMATCH = (
    "AssessRun client_name {actual!r} does not match requested "
    "client {requested!r}"
)
MISSING_ASSESS_RUNS_WRITE = (
    "exporter will not write into data/state/assess_runs/"
)

_MARKET_MAP_MARKS = {
    "certified_graph",
    "external_product",
    "lila_federal_market_map",
    "market_map",
    "product_family",
    "worksheet",
    "worksheet_pack",
    "worksheets",
}


class ExportAssessError(ValueError):
    """Fail-closed read-only AssessRun export error."""


def export_current_assess_run(
    client_name: str,
    *,
    scope: str | None = None,
    state_dir: str | Path | None = None,
    review_dir: str | Path | None = None,
    from_file: str | Path | None = None,
) -> dict[str, Any]:
    """Return a press-ready AssessRun envelope. Read-only. No I/O writes."""

    requested = " ".join(str(client_name or "").split())
    if not requested:
        raise ExportAssessError("client_name is required")
    if from_file is not None:
        return _export_from_file(requested, Path(from_file))
    designator = _resolve_scope(requested, scope, review_dir)
    return _export_from_pointer(
        requested, designator, state_dir=state_dir,
    )


def _export_from_pointer(
    client_name: str,
    designator: str,
    *,
    state_dir: str | Path | None,
) -> dict[str, Any]:
    pointer_path = current_assess_pointer_path(
        client_name, designator, state_dir=state_dir)
    payload = load_current_assess_run(
        client_name, designator, state_dir=state_dir)
    if payload is None:
        raise ExportAssessError(
            _missing_pointer_message(
                client_name, designator, pointer_path,
            )
        )
    run = _coerce_validated_run(payload)
    _assert_client_match(run, client_name)
    _assert_live_primary_urls(run)
    artifact_name = _pointer_artifact_name(pointer_path)
    if artifact_name:
        source_path = str(pointer_path.parent / artifact_name)
    else:
        source_path = str(pointer_path)
    return _envelope(
        run,
        source="current_pointer",
        source_path=source_path,
        scope_designator=designator,
        note=(
            "verbatim dump of the current AssessRun pointer; not a new "
            "assessment; " + CHAIN_DOES_NOT_WRITE
        ),
    )


def _pointer_artifact_name(pointer_path: Path) -> str | None:
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(pointer, Mapping):
        return None
    name = pointer.get("artifact")
    if isinstance(name, str) and name and Path(name).name == name:
        return name
    return None


def _export_from_file(client_name: str, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ExportAssessError(f"assess path is not readable ({path})")
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExportAssessError(
            f"assess input is not valid JSON: {exc}"
        ) from exc
    _refuse_non_assess(raw)
    run = _coerce_validated_run(raw)
    _assert_client_match(run, client_name, source_path=path)
    _assert_live_primary_urls(run)
    return _envelope(
        run,
        source="from_file",
        source_path=str(path),
        scope_designator=_scope_designator(run),
        note=(
            "validated dump of a caller-supplied AssessRun file; "
            "not a new assessment; " + CHAIN_DOES_NOT_WRITE
        ),
    )


def _refuse_non_assess(payload: Any) -> None:
    if isinstance(payload, Sequence) and not isinstance(
            payload, (str, bytes, Mapping)):
        raise ExportAssessError(NOTICE_ONLY)
    if not isinstance(payload, Mapping):
        raise ExportAssessError(
            "assess input is not an AssessRun JSON object"
        )
    if _looks_like_market_map(payload):
        raise ExportAssessError(MARKET_MAP_REFUSED)
    if _looks_like_source_records(payload):
        raise ExportAssessError(SOURCE_RECORDS_REFUSED)
    if _looks_like_leadgen_receipt(payload):
        raise ExportAssessError(LEADGEN_RECEIPT_REFUSED)
    if _looks_like_notice_only(payload):
        raise ExportAssessError(NOTICE_ONLY)


def _looks_like_market_map(payload: Mapping[str, Any]) -> bool:
    if _has_assess_identity(payload):
        return False
    keys = set(payload)
    if keys & _MARKET_MAP_MARKS:
        return True
    family = str(payload.get("family") or payload.get("product_family") or "")
    return family.casefold() == "lila_federal_market_map"


def _looks_like_source_records(payload: Mapping[str, Any]) -> bool:
    if _has_assess_identity(payload):
        return False
    records = payload.get("source_records")
    return isinstance(records, Sequence) and not isinstance(
        records, (str, bytes, Mapping))


def _looks_like_leadgen_receipt(payload: Mapping[str, Any]) -> bool:
    if _has_assess_identity(payload):
        return False
    return "parents" in payload and "leads" in payload


def _coerce_validated_run(payload: Any) -> AssessRun:
    try:
        run = coerce_assess_run(payload)
    except (TypeError, ValidationError, ValueError) as exc:
        message = str(exc).casefold()
        if "primary" in message or "notice" in message or "sam.gov" in message:
            raise ExportAssessError(INVENTED_LIVE + f" ({exc})") from exc
        raise ExportAssessError(
            "assess input is not an AssessRun: "
            f"{exc}. Notice-only input cannot mint leads."
        ) from exc
    return run


def _assert_client_match(
    run: AssessRun,
    requested: str,
    *,
    source_path: Path | None = None,
) -> None:
    if client_slug(run.client_name) == client_slug(requested):
        return
    if (
        client_slug(run.client_name) == "testco"
        and client_slug(requested) == "arista_networks"
    ):
        raise ExportAssessError(DEMO_TESTCO.format(requested=requested))
    extra = f" ({source_path})" if source_path is not None else ""
    raise ExportAssessError(
        CLIENT_MISMATCH.format(
            actual=run.client_name, requested=requested,
        ) + extra
    )


def _assert_live_primary_urls(run: AssessRun) -> None:
    for record in run.live.records:
        urls = [
            str(item.source_url)
            for item in record.authoritative_evidence
            if item.primary_source
            and str(item.source_url).startswith("https://")
        ]
        if not urls:
            raise ExportAssessError(
                INVENTED_LIVE + f" ({record.record_id})"
            )


def _scope_designator(run: AssessRun) -> str:
    agencies = tuple(
        " ".join(str(getattr(item, "abbr", "") or "").split()).casefold()
        for item in run.scope.agencies
    )
    agencies = tuple(item for item in agencies if item)
    if len(agencies) == 1:
        return f"agency_{agencies[0]}"
    return "all"


def _resolve_scope(
    client_name: str,
    scope: str | None,
    review_dir: str | Path | None,
) -> str:
    requested = " ".join(str(scope or "").split())
    if requested:
        return requested
    try:
        from agents.review import gate_designator
        review = str(review_dir) if review_dir is not None else None
        return gate_designator(client_name, review) or "all"
    except Exception as exc:
        raise ExportAssessError(
            "Assess scope designator unavailable: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _missing_pointer_message(
    client_name: str,
    designator: str,
    pointer_path: Path,
) -> str:
    quoted = json.dumps(client_name)
    activate = (
        f"python -m tools.assess_refresh --client {quoted} --activate"
    )
    dump = (
        "python -m agents.leadgen.export_assess --client "
        f"{quoted} --output /tmp/assess_run.json"
    )
    press = (
        "python -m agents.leadgen.press --assess /tmp/assess_run.json "
        "--markdown"
    )
    eval_cmd = (
        "python -m agents.leadgen.eval.score --pack "
        f"data/review/{client_slug(client_name)}.leadgen.json"
    )
    if pointer_path.is_file():
        refresh = f"python -m tools.assess_refresh --client {quoted}"
        return (
            f"current Assess pointer exists for {client_name!r} "
            f"(scope={designator}) but the artifact failed validation. "
            f"{CHAIN_DOES_NOT_WRITE}. Do not pass a Market Map pack, "
            "worksheet, notice list, or Testco-shaped demo fixture to "
            "press. Refresh the existing pointer (creation-free):\n"
            f"  {refresh}\n"
            "Then dump it read-only:\n"
            f"  {dump}"
        )
    return (
        f"no current AssessRun for {client_name!r} (scope={designator}). "
        "Press Lead Gen will not invent leads from Market Map packs, "
        "worksheets, notices, or source_records. "
        f"{CHAIN_DOES_NOT_WRITE}. "
        "If this client already has a completed sweep (and optional "
        "qualify/horizon artifacts), materialize the REAL ledger once "
        "(operator cutover; writes the current pointer):\n"
        f"  {activate}\n"
        "Then dump it read-only for press:\n"
        f"  {dump}\n"
        "Then press + skeptic eval:\n"
        f"  {press}\n"
        f"  {eval_cmd}"
    )


def _envelope(
    run: AssessRun,
    *,
    source: Literal["current_pointer", "from_file"],
    source_path: str,
    scope_designator: str,
    note: str,
) -> dict[str, Any]:
    return {
        "export_kind": EXPORT_KIND,
        "read_only": True,
        "source": source,
        "source_path": source_path,
        "client_name": run.client_name,
        "scope_designator": scope_designator,
        "note": note,
        "run": run.model_dump(mode="json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only dump of an existing AssessRun for Press Lead Gen. "
            "Does not run AssessmentChain, does not invent leads, and "
            "does not call materialize_current_assess_run."
        ),
    )
    parser.add_argument(
        "--client", required=True,
        help="Exact engagement client name (for example 'Arista Networks')",
    )
    parser.add_argument(
        "--scope", default=None,
        help="Assess scope designator (default: current gate, else all)",
    )
    parser.add_argument(
        "--from-file", default=None,
        help="Validate and dump an existing JSON file instead of the "
             "current pointer. Notice-only / Market Map / Testco demo "
             "files are refused.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Write the press-ready envelope here. Default: stdout",
    )
    parser.add_argument(
        "--state-dir", default=None,
        help="Override data/state/assess_runs (tests / diagnostics)",
    )
    parser.add_argument(
        "--review-dir", default=None,
        help="Override data/review when resolving the gate designator",
    )
    args = parser.parse_args(argv)
    try:
        payload = export_current_assess_run(
            args.client,
            scope=args.scope,
            state_dir=args.state_dir,
            review_dir=args.review_dir,
            from_file=args.from_file,
        )
    except ExportAssessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        out = Path(args.output)
        if "assess_runs" in out.parts:
            print(f"error: {MISSING_ASSESS_RUNS_WRITE}", file=sys.stderr)
            return 2
        atomic_write_json(str(out), payload)
        print(f"[out] {out}", file=sys.stderr)
        print(
            f"[assess-export] {payload['client_name']} "
            f"{payload['run']['run_id']} ({payload['source']})",
            file=sys.stderr,
        )
        return 0
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
