#!/usr/bin/env python3
"""Run intelligence-graph verification in ordered, receipted stages.

The runner keeps fast feedback local to the module that changed.  A full
strict suite runs once per milestone commit, while release requires separate
visual-QA receipts for every certified surface from that same commit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable


PLAN_VERSION = "graph-stages-v1"
STAGE_ORDER = ("graph", "jtg", "renderer", "milestone", "release")
REQUIRED_VISUAL_SURFACES = (
    "desktop",
    "tablet",
    "mobile",
    "print",
    "standalone_html",
    "links_marks",
)
STAGE_TESTS: dict[str, tuple[str, ...]] = {
    "graph": (
        "tests/test_intelligence_graph_workflow.py",
        "tests/test_intelligence_graph_interface_coverage.py",
        "tests/test_intelligence_graph_properties.py",
        "tests/test_graph_failure_fixtures.py",
        "tests/test_evidence_pack_v2.py",
        "tests/test_evidence_route.py",
        "tests/test_intelligence_graph_cache.py",
    ),
    "jtg": (
        "tests/test_jtg_opportunity_capture.py",
        "tests/test_evidence_pack_v2.py",
        "tests/test_graph_failure_fixtures.py",
    ),
    "renderer": (
        "tests/test_market_map_render.py",
        "tests/test_market_map_press.py",
    ),
    "milestone": ("tests",),
    "release": (
        "tests/test_cross_client_leakage.py",
        "tests/test_market_map.py",
        "tests/test_market_map_render.py",
        "tests/test_market_map_press.py",
    ),
}
PREREQUISITE = {
    "jtg": "graph",
    "renderer": "jtg",
    "milestone": "renderer",
    "release": "milestone",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _receipt_dir(root: Path) -> Path:
    override = os.environ.get("LILA_GRAPH_TEST_RECEIPT_DIR")
    return Path(override) if override else (
        root / "data" / "state" / "test_receipts" / "graph_workflow")


def receipt_path(root: Path, stage: str, head: str) -> Path:
    return _receipt_dir(root) / f"{head}.{stage}.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_visual_receipts(
        paths: Iterable[str | Path], *, expected_head: str) -> list[dict]:
    receipts = [_read_json(Path(path)) for path in paths]
    passed_surfaces = {
        row.get("surface") for row in receipts
        if row.get("status") == "passed" and row.get("git_head") == expected_head
    }
    missing = set(REQUIRED_VISUAL_SURFACES) - passed_surfaces
    if missing:
        raise ValueError(
            "release requires passed same-commit visual receipts for: " +
            ", ".join(sorted(missing)))
    return receipts


def _require_prerequisite(root: Path, stage: str, head: str) -> Path | None:
    prerequisite = PREREQUISITE.get(stage)
    if not prerequisite:
        return None
    path = receipt_path(root, prerequisite, head)
    if not path.exists():
        raise RuntimeError(
            f"{stage} requires a same-commit {prerequisite} receipt: {path}")
    payload = _read_json(path)
    if payload.get("status") != "passed" or payload.get("git_head") != head:
        raise RuntimeError(
            f"{stage} requires a passed same-commit {prerequisite} receipt")
    return path


def run_stage(
        root: Path, stage: str, *, force: bool = False,
        visual_receipts: Iterable[str | Path] = ()) -> tuple[int, Path, bool]:
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown stage: {stage}")
    head = _git_head(root)
    path = receipt_path(root, stage, head)
    if path.exists() and not force:
        previous = _read_json(path)
        return (0 if previous.get("status") == "passed" else 1, path, True)

    prerequisite = _require_prerequisite(root, stage, head)
    visual_payloads: list[dict] = []
    if stage == "release":
        visual_payloads = validate_visual_receipts(
            visual_receipts, expected_head=head)

    command = [sys.executable, "-m", "pytest", "-q", *STAGE_TESTS[stage]]
    environment = dict(os.environ)
    environment["LILA_SUITE_STRICT"] = "1"
    environment["LILA_SUITE_OFFLINE"] = "1"
    started = _utc_now()
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    receipt = {
        "plan_version": PLAN_VERSION,
        "stage": stage,
        "git_head": head,
        "status": "passed" if completed.returncode == 0 else "failed",
        "started_at": started,
        "completed_at": _utc_now(),
        "command": command,
        "test_paths": list(STAGE_TESTS[stage]),
        "strict": True,
        "offline": True,
        "prerequisite_receipt": str(prerequisite) if prerequisite else None,
        "visual_receipts": [row.get("receipt_id") or row.get("surface")
                            for row in visual_payloads],
        "output_tail": completed.stdout[-12000:],
    }
    _atomic_json(path, receipt)
    sys.stdout.write(completed.stdout)
    return completed.returncode, path, False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGE_ORDER, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--visual-receipt", action="append", default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        code, path, reused = run_stage(
            root,
            args.stage,
            force=args.force,
            visual_receipts=args.visual_receipt,
        )
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"receipt: {path}")
    print("result reused for this commit" if reused else "result measured")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
