"""Agency seal resolution for deterministic rendering.

A seal is INHERITED DESIGN, not evidence. It identifies the buying agency on
a card and asserts nothing about the record, so it can be reused across
clients without carrying a claim from one report into another.

What is deliberately NOT here: vendor and competitor wordmarks. Those are
client-specific. Cataloguing them would put one client's competitors into
another client's report, which is exactly the hardcoding the operator ruled
out ("Gigamon: no action, no hardcoding", 2026-07-24).

Resolution never guesses. A record whose agency is not in the catalog gets
no seal and the card renders in its no-image form, which the inherited CSS
already styles.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any, Optional

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "agency_seals.json"

_WORD = re.compile(r"[^a-z0-9]+")


@functools.lru_cache(maxsize=1)
def load_catalog(path: Optional[str] = None) -> dict:
    """The seal catalog, or an empty one. A missing catalog degrades the
    look of a report; it never fails a press."""
    p = Path(path) if path else CATALOG_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seals": {}, "aliases": {}}
    return {"seals": data.get("seals") or {},
            "aliases": data.get("aliases") or {}}


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.split(str(text or "").lower()) if t]


def resolve(*names: Any, catalog: Optional[dict] = None) -> Optional[str]:
    """The seal data URI for the first name that resolves, else None.

    Pass the most specific name first (sub-agency, then agency). Matching is
    by whole-name containment and then by WHOLE-TOKEN alias: token equality,
    never substring, so "service" can never match the alias "ice" and pin a
    Homeland Security seal onto an unrelated buyer.
    """
    cat = catalog or load_catalog()
    seals, aliases = cat["seals"], cat["aliases"]
    if not seals:
        return None
    for name in names:
        if not name:
            continue
        norm = " ".join(_tokens(name))
        if not norm:
            continue
        if norm in seals:
            return seals[norm]
        for key in seals:
            if key in norm:
                return seals[key]
        for token in _tokens(name):
            parent = aliases.get(token)
            if parent and parent in seals:
                return seals[parent]
    return None


def seal_for(record: Any, catalog: Optional[dict] = None) -> Optional[str]:
    """Seal for a pack record: sub-agency first, then agency."""
    return resolve(getattr(record, "sub_agency", None),
                   getattr(record, "agency", None), catalog=catalog)
