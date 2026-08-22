"""SBA prime-contractor directory: normalize and measure the pack join.

Operator order 2026-08-05, Task 2. Directories are prime-side TARGETING
records, never opportunities; nothing here feeds press or sweep. The entity
normalizer is rollups.normalize_entity, reused, never forked.

Source (verified 2026-08-05): SBA "Report Builder FY24 Final rev2" XLSX
under legacy.sba.gov (www.sba.gov redirects drop the path: fetch legacy
directly). The MODERN format carries NO small-business liaison columns;
liaison fields persist as explicit nulls so the absence is a counted fact.
The GSA Subcontracting Directory for Small Businesses was NOT acquirable
on 2026-08-05: HTTP 404 at both historical paths and no inbound link from
the gsa.gov small-business hub; per standing law no third-party substitute
was used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from agents.golden_press.rollups import normalize_entity

DIRECTORY_PATH = "data/reference/subcontracting_primes.jsonl"

# FAIL-CLOSED FLOOR (extract-floor posture): FY24 carries 18,940 rows and
# 2,659 distinct primes; a fetch below this floor is a broken pull and
# never replaces the committed directory.
MIN_CREDIBLE_DIRECTORY_ROWS = 5_000

# Containment on a single generic token produced garbage on first
# measurement ("FOUR LLC" -> "FOUR COUNTY ELECTRIC POWER ASSOCIATION"):
# a containment key must carry at least one token OUTSIDE this set and at
# least 6 characters of distinctive content.
_GENERIC_TOKENS = frozenset({
    "data", "micro", "global", "international", "contracting", "federal",
    "technology", "technologies", "solutions", "systems", "services",
    "enterprise", "enterprises", "group", "consulting", "government",
    "national", "american", "america", "supply", "products", "computer",
    "computers", "network", "networks", "communications", "information",
})


def guard_directory_floor(rows: list[dict],
                          floor: int = MIN_CREDIBLE_DIRECTORY_ROWS) -> list[dict]:
    if len(rows) < floor:
        raise ValueError(
            f"subcontracting directory refused: {len(rows)} rows is below "
            f"the {floor}-row credibility floor; a broken fetch never "
            "replaces the committed directory")
    return rows


def load_directory(root: Path | str = ".") -> list[dict]:
    path = Path(root) / DIRECTORY_PATH
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return guard_directory_floor(rows)


def directory_keys(rows: Iterable[dict]) -> dict[str, dict]:
    """name_key AND parent_name_key -> one representative row."""
    keys: dict[str, dict] = {}
    for row in rows:
        for key in (row.get("name_key"), row.get("parent_name_key")):
            if key:
                keys.setdefault(key, row)
    return keys


def _distinctive_containment(key: str, candidate: str) -> bool:
    """Whole-word containment where the contained side carries at least one
    non-generic token; single generic-token overlap never matches."""
    shorter, longer = sorted((key, candidate), key=len)
    if f" {shorter} " not in f" {longer} ":
        return False
    tokens = [t for t in shorter.split() if t not in _GENERIC_TOKENS]
    return bool(tokens) and len("".join(tokens)) >= 6


def match_prime(prime: str, keys: dict[str, dict]) -> Optional[dict]:
    """{row, tier} or None. Tiers: exact | parent | containment."""
    key = normalize_entity(prime)
    if not key:
        return None
    hit = keys.get(key)
    if hit:
        tier = "exact" if hit.get("name_key") == key else "parent"
        return {"row": hit, "tier": tier, "matched_key": key}
    for candidate, row in keys.items():
        if _distinctive_containment(key, candidate):
            return {"row": row, "tier": "containment",
                    "matched_key": candidate}
    return None
