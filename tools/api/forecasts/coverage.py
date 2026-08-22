"""Forecast coverage ledger — which agencies have a working source, which
don't, and when each last pulled. Absence is logged, never silent: a client
profile naming an agency with no registered forecast surface gets a coverage
gap entry, so the screening-stats line can say what was NOT screened.

Ledger: data/state/forecast_coverage.json
  {source: {agency, records, last_success, last_attempt, status, note}}
  {"_gaps": {agency: {clients: [...], note, first_logged}}}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.api.source_catalog import SOURCE_SPECS

_DEFAULT = Path(__file__).resolve().parents[3] / "data" / "state"

# Only verified forecast coverage is active. Identity, agency codes, and hints
# live in the central catalog so adding an adapter cannot leave this ledger
# claiming a different source universe than the runner.
#
# EFFECTIVE ENABLEMENT, NOT SHIP-TIME DEFAULT (audit 2026-07-30). dhs_apfs
# ships enabled_default=False and is armed at runtime by the operator's
# toggle, so filtering on enabled_default left COVERED_AGENCIES EMPTY in
# production: reports claimed nothing was forecast-screened while 786 DHS
# records were being screened and matched. The ledger now asks the same
# toggle machinery the adapter itself asks, so what coverage claims and
# what the runner does cannot disagree.
def _effectively_enabled(spec) -> bool:
    from tools.toggles import is_enabled

    return is_enabled(spec.identity, spec.enabled_default)


_COVERAGE_SPECS = tuple(
    spec for spec in SOURCE_SPECS
    if (
        spec.parent_identity == "forecasts"
        and spec.coverage_active
        and _effectively_enabled(spec)
    )
)
COVERED_AGENCIES = {
    agency: spec.identity
    for spec in _COVERAGE_SPECS
    for agency in spec.agency_codes
}
_COVERED_HINTS = {
    agency: spec.agency_hints
    for spec in _COVERAGE_SPECS
    for agency in spec.agency_codes
}


def _is_covered(target: str) -> bool:
    t = f" {(target or '').lower()} "
    for agency in COVERED_AGENCIES:
        if agency.lower() in t.split("/") or f" {agency.lower()} " in t:
            return True
        if any(h in t for h in _COVERED_HINTS.get(agency, ())):
            return True
    return False


def _path() -> Path:
    return Path(os.environ.get("LILA_FORECAST_COVERAGE",
                               str(_DEFAULT / "forecast_coverage.json")))


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(led: dict) -> None:
    os.makedirs(_path().parent, exist_ok=True)
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(led, f, indent=1, sort_keys=True)


def record_pull(source: str, agency: str, records: Optional[int], ok: bool,
                note: str = "", *, retrieved: Optional[int] = None,
                total_known: Optional[int] = None,
                complete: Optional[bool] = None,
                error: Optional[str] = None,
                elapsed_s: Optional[float] = None) -> None:
    """Ledger schema v2 (P3, 2026-07-11): every pull may carry an attempt
    manifest — retrieved vs total_known, completeness, the error, and wall
    time — so 'what was screened' is defensible per source, per pull. All
    v2 fields are optional; v1 callers are untouched."""
    led = _load()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = led.get(source) or {}
    row.update(agency=agency, last_attempt=now, status="ok" if ok else "error",
               note=note)
    attempt = {"at": now, "ok": ok}
    for k, v in (("retrieved", retrieved), ("total_known", total_known),
                 ("complete", complete), ("error", error),
                 ("elapsed_s", round(elapsed_s, 1) if elapsed_s is not None else None)):
        if v is not None:
            attempt[k] = v
    row["last_pull"] = attempt
    if ok:
        if records is not None:
            row["records"] = records
        row["last_success"] = now
    led[source] = row
    _save(led)


def log_gap(agency: str, client_name: str, note: str) -> None:
    """A profile names an agency with no forecast surface: log it once per
    agency, accumulating which clients hit the gap."""
    led = _load()
    gaps = led.setdefault("_gaps", {})
    g = gaps.get(agency) or {"clients": [], "note": note,
                             "first_logged": datetime.now(timezone.utc)
                             .isoformat(timespec="seconds")}
    if client_name not in g["clients"]:
        g["clients"].append(client_name)
    g["note"] = note
    gaps[agency] = g
    _save(led)


def gaps_for(agencies: list[str], client_name: str) -> list[str]:
    """Log + return coverage gaps for a client's target agencies. Targets are
    free-text strategy strings. A disabled or merely registered adapter never
    satisfies this gate; only an enabled source with reviewed agency coverage
    metadata can suppress a gap."""
    out = []
    for a in agencies or []:
        key = (a or "").strip()
        if key and not _is_covered(key):
            log_gap(key, client_name,
                    "no registered forecast adapter for this agency; "
                    "records not screened (DoD components publish via "
                    "scattered surfaces)")
            out.append(key)
    return out


def coverage_table() -> list[dict]:
    """agency -> source -> record count -> last successful pull (+ gaps)."""
    led = _load()
    rows = []
    for source, row in sorted(led.items()):
        if source == "_gaps":
            continue
        rows.append({"agency": row.get("agency", "?"), "source": source,
                     "records": row.get("records", 0),
                     "last_success": row.get("last_success"),
                     "status": row.get("status"), "note": row.get("note", "")})
    for agency, g in sorted((led.get("_gaps") or {}).items()):
        rows.append({"agency": agency, "source": "(none registered)",
                     "records": 0, "last_success": None, "status": "gap",
                     "note": f"{g.get('note')} · hit by: "
                             f"{', '.join(g.get('clients', []))}"})
    return rows
