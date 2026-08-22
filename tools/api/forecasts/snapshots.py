"""Forecast snapshots + deltas — the revision itself is a signal.

Agencies revise forecasts quarterly-ish. A line slipping from Q2 to Q4, or
vanishing entirely, tells a capture strategist something no single pull can.
Snapshots persist per client under data/state/forecast_snapshots/<slug>/ and
diff on (source, source_id).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

from agents.schemas import ForecastRecord
from tools.api.forecasts.store import record_payload

_DEFAULT = Path(__file__).resolve().parents[3] / "data" / "state" / "forecast_snapshots"


def _root() -> Path:
    return Path(os.environ.get("LILA_FORECAST_SNAP_DIR", str(_DEFAULT)))


def _key(r: dict) -> str:
    return f"{r.get('source')}::{r.get('source_id')}"


def save_snapshot(slug: str, records: list[ForecastRecord],
                  as_of: Optional[date] = None) -> str:
    d = _root() / slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{(as_of or date.today()).isoformat()}.json"
    path.write_text(json.dumps(
        [record_payload(r) for r in records]))
    return str(path)


def load_latest_snapshot(slug: str, before: Optional[date] = None) -> Optional[list[dict]]:
    """Newest snapshot strictly BEFORE `before` (default: today) — the baseline
    a fresh pull diffs against."""
    d = _root() / slug
    if not d.exists():
        return None
    cutoff = (before or date.today()).isoformat()
    older = sorted(p for p in d.glob("*.json") if p.stem < cutoff)
    if not older:
        return None
    return json.loads(older[-1].read_text())


def diff_snapshots(previous: list[dict], current: list[dict]) -> dict:
    """{'new': [...], 'disappeared': [...], 'moved': [...]} — each entry carries
    id + title; 'moved' adds from/to on anticipated timing."""
    prev = {_key(r): r for r in previous}
    cur = {_key(r): r for r in current}

    def brief(r: dict) -> dict:
        return {"id": r.get("source_id"), "title": r.get("title"),
                "url": r.get("url")}

    new = [brief(cur[k]) for k in cur.keys() - prev.keys()]
    disappeared = [brief(prev[k]) for k in prev.keys() - cur.keys()]
    moved = []
    for k in cur.keys() & prev.keys():
        was = prev[k].get("anticipated_solicitation")
        now = cur[k].get("anticipated_solicitation")
        if was != now:
            moved.append({**brief(cur[k]), "from": was, "to": now})
    return {"new": new, "disappeared": disappeared, "moved": moved}
