"""Persistence facade for one completed Refresh Press.

The snapshot module owns artifact projection and the delta module owns
classification/rendering.  This facade owns only their transaction order:
find the newest strictly older snapshot, build the comparison, write the
delta artifacts, and promote the current snapshot last as the next baseline.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional


_ROOT = Path(__file__).resolve().parents[1]


def _newest_older_snapshot(
    client_dir: Path,
    *,
    current_filename: str,
    press_timestamp: datetime,
):
    """Load the newest valid snapshot strictly older than this press."""
    from agents.press_snapshot import PressSnapshot

    candidates = []
    if not client_dir.exists():
        return None
    for path in sorted(client_dir.glob("*.json")):
        if path.name.endswith(".delta.json") or path.name >= current_filename:
            continue
        try:
            snapshot = PressSnapshot.model_validate_json(
                path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"prior press snapshot {path.name} is unreadable: {exc}") \
                from exc
        if snapshot.press_timestamp < press_timestamp:
            candidates.append(snapshot)
    return (max(candidates, key=lambda row: row.press_timestamp)
            if candidates else None)


def persist_refresh_press(
    client_name: str,
    *,
    press_timestamp: datetime,
    verification: Mapping[str, Any],
    root: str | Path = _ROOT,
    state_root: Optional[str | Path] = None,
) -> dict[str, Path]:
    """Build and persist snapshot, delta JSON, and INTERNAL markdown."""
    from agents.press_delta import (
        baseline_delta,
        canonical_json,
        compute_delta,
        render_internal_markdown,
    )
    from agents.press_snapshot import (
        build_press_snapshot,
        snapshot_filename,
        write_press_snapshot,
    )
    from tools.atomic_io import atomic_write_text

    root = Path(root)
    state_base = Path(state_root or root / "data" / "state" / "deltas")
    current = build_press_snapshot(
        client_name,
        press_timestamp=press_timestamp,
        root=root,
        verification=dict(verification),
    )
    snapshot_name = snapshot_filename(press_timestamp)
    client_dir = state_base / current.slug
    previous = _newest_older_snapshot(
        client_dir,
        current_filename=snapshot_name,
        press_timestamp=current.press_timestamp,
    )

    if previous is None:
        delta = baseline_delta(current)
    else:
        delta = compute_delta(
            previous,
            current,
            dispute_observer=lambda _drift: dict(verification),
        )

    stamp = snapshot_name[: -len(".json")]
    delta_path = client_dir / f"{stamp}.delta.json"
    summary_path = client_dir / f"{stamp}.internal.md"
    atomic_write_text(str(delta_path), canonical_json(delta))
    atomic_write_text(str(summary_path), render_internal_markdown(delta))
    # Promote the snapshot last.  A later refresh cannot consume a baseline
    # whose matching delta artifacts did not finish writing.
    snapshot_path = write_press_snapshot(current, state_root=state_base)
    return {
        "snapshot_path": snapshot_path,
        "delta_path": delta_path,
        "summary_path": summary_path,
    }


def load_latest_delta_feed(
    client_name: str,
    *,
    state_root: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Return the newest persisted delta in the home-ticker feed contract."""
    from agents.press_delta import delta_feed
    from agents.press_snapshot import client_slug

    base = Path(state_root or _ROOT / "data" / "state" / "deltas")
    client_dir = base / client_slug(client_name)
    candidates = sorted(client_dir.glob("*.delta.json"))
    if not candidates:
        return {"items": [], "total": 0, "suppressed": 0}
    newest = candidates[-1]
    try:
        payload = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"latest press delta {newest.name} is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"latest press delta {newest.name} is not an object")
    return delta_feed(payload)


__all__ = ["load_latest_delta_feed", "persist_refresh_press"]
