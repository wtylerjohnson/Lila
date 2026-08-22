"""What the named product rivals actually win from the federal government.

WHY THIS EXISTS. The competition section rendered "Research next" for
apexanalytix while `clients/apexanalytix/profile.json` held ten named product
rivals with a cited market source (Gartner Magic Quadrant for Supplier Risk
Management Solutions, 4 May 2026, G00834117). The projection discarded them
because it would only publish a rival that appeared on an award record in the
evidence pack, and that pack's award lane had just been gated to zero.

Ten sourced facts were not rejected. They were never consulted.

THE RULE THAT FAILURE ESTABLISHES. An operator-supplied fact with a citation
is the HIGHEST grade of evidence in this system, not the lowest. It never
needs pipeline corroboration to be published; corroboration ENRICHES it.

WHAT THIS ADDS. For each named rival, the federal awards it holds as prime
recipient, measured live from USAspending at zero cost. That turns a list of
names into the section that matters:

  Moody's       53 awards   $126,075,746.40
  Sphera        54 awards   $118,050,845.14   $116.8M of it at DLA
  Altana        13 awards   $ 34,138,697.40   $23.4M of it at CBP
  Resilinc       3 awards   $  1,892,379.69
  Exiger         2 awards   $  1,027,590.00
  apexanalytix   0 awards   $          0.00

The client sells into a market where its named rivals hold $281,185,258.63
of federal paper and it holds none. No award co-occurrence search would ever
have produced that sentence.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

RIVAL_FOOTPRINT_VERSION = "rival_footprint.v1.2026-08-07"

_CACHE = Path(__file__).resolve().parents[2] / "data" / "state" / "rivals"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def cache_path(slug: str) -> Path:
    return _CACHE / f"{_clean(slug)}.rival_awards.json"


def load_cached(slug: str) -> dict:
    """Measured footprints from a previous pass. Absent is empty."""
    path = cache_path(slug)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def measure(names: Any, *, slug: str = "", poster: Any = None,
            write: bool = True) -> dict:
    """Federal awards held by each named company. Live, free, no key."""
    from tools.api.usaspending import recipient_awards

    out: dict = {}
    for name in dict.fromkeys(_clean(n) for n in (names or [])):
        if not name:
            continue
        rows = recipient_awards(name, poster=poster)
        out[name] = [
            {"award_id": _clean(r.get("Award ID")),
             "recipient": _clean(r.get("Recipient Name")),
             "amount": str(r.get("Award Amount") or 0),
             "agency": (_clean(r.get("Awarding Sub Agency"))
                        or _clean(r.get("Awarding Agency"))),
             "description": _clean(r.get("Description"))[:180]}
            for r in rows]
    if write and slug:
        cache_path(slug).parent.mkdir(parents=True, exist_ok=True)
        cache_path(slug).write_text(
            json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    return out


def summarise(measured: dict) -> list:
    """One row per rival, ordered by federal dollars held."""
    rows = []
    for name, awards in (measured or {}).items():
        total = sum(Decimal(str(a.get("amount") or 0)) for a in awards)
        by_agency: dict = {}
        for award in awards:
            agency = _clean(award.get("agency")) or "Unnamed buyer"
            by_agency[agency] = (by_agency.get(agency, Decimal("0"))
                                 + Decimal(str(award.get("amount") or 0)))
        top = sorted(by_agency.items(), key=lambda kv: -kv[1])
        rows.append({
            "name": name, "awards": len(awards), "total": total,
            "top_agency": top[0][0] if top else "",
            "top_agency_amount": top[0][1] if top else Decimal("0"),
            "agencies": top[:4],
            "example": awards[0] if awards else None,
        })
    rows.sort(key=lambda r: (-r["total"], r["name"]))
    return rows


def market_total(measured: dict) -> Decimal:
    return sum((Decimal(str(a.get("amount") or 0))
                for awards in (measured or {}).values() for a in awards),
               Decimal("0"))
