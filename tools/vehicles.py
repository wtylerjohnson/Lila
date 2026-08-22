"""Standing contract-vehicle considerations, appended to every client report.

Requested by GTM Group (Keith Johnson, Jul 2 2026): each report carries a
vehicle-considerations section. The catalog lives in data/reference/vehicles.json.

The section is DETERMINISTIC — rendered from the catalog, not composed by the
LLM — so it can't hallucinate ceilings and is appended after the critic/lint
gates rather than run through them. Entries the catalog marks unverified are
listed separately and explicitly, because a wrong agency attribution in front
of a client is worse than an honest 'pending verification'.
"""

from __future__ import annotations

import json
from pathlib import Path

_CATALOG = Path(__file__).resolve().parents[1] / "data" / "reference" / "vehicles.json"


def load_vehicles() -> dict:
    with open(_CATALOG, encoding="utf-8") as f:
        return json.load(f)


def vehicles_section_body() -> str:
    data = load_vehicles()
    verified = [v for v in data["vehicles"] if v.get("verified")]
    pending = [v for v in data["vehicles"] if not v.get("verified")]

    lines: list[str] = [
        "Standing assessment of the highest-value federal contract vehicles and the "
        "client's most direct path onto each. Catalog updated "
        f"{data['_meta']['updated']}.",
        "",
        "**Confirmed vehicles**",
        "",
        "| Vehicle | Agency | Ceiling | Client path |",
        "|---|---|---|---|",
    ]
    for v in verified:
        lines.append(
            f"| {v['name']} | {v['agency']} | {v['ceiling']} | {v['software_vendor_path']} |"
        )
    if pending:
        lines += [
            "",
            "**Tracked, pending verification** (not for client citation until confirmed): "
            + ", ".join(v["name"] for v in pending)
            + ".",
        ]
    return "\n".join(lines)


def vehicles_heading() -> str:
    return "Contract vehicle considerations"
