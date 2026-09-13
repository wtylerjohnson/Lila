"""Attributed SAM lifecycle labels; no substring or narrative classification."""
from __future__ import annotations


HISTORICAL_LABELS = frozenset({
    "award notice", "award", "a", "awards", "award notification", "awarded", "award synopsis", "awarded contract",
})
_CURRENT_LABELS = {
    "solicitation": "solicitation", "o": "solicitation",
    "combined synopsis/solicitation": "combined", "k": "combined",
    "sources sought": "sources-sought", "r": "sources-sought",
    "presolicitation": "presolicitation", "p": "presolicitation",
    "preaward": "preaward", "pre-award": "preaward",
}


def notice_type_evidence(notice: dict) -> dict:
    """Native current type wins; baseType is an earlier lifecycle fallback.

    Conflicting current-type projections cannot establish current buying.
    A native current type differing from baseType is recorded, not assumed
    contradictory: SAM retains the original type across lifecycle updates.
    Unrecognized labels remain unknown rather than invented solicitations.
    """
    raw = notice.get("raw_payload")
    raw = raw if isinstance(raw, dict) else {}
    rows = []
    for prefix, data, keys in (
        ("raw_payload.", raw, ("type",)),
        ("", notice, ("notice_type", "type")),
        ("raw_payload.", raw, ("base_type", "baseType")),
        ("", notice, ("base_type", "baseType")),
    ):
        for key in keys:
            if key not in data:
                continue
            value = data[key]
            normalized = " ".join(value.casefold().split()) if isinstance(value, str) else ""
            rows.append({"field": prefix + key, "raw": value,
                         "normalized": normalized, "is_base_type": key in {"base_type", "baseType"},
                         "classification": "historical" if normalized in HISTORICAL_LABELS
                         else _CURRENT_LABELS.get(normalized, "unknown")})
    current = [r for r in rows if r["normalized"] and not r["is_base_type"]]
    usable = current or [r for r in rows if r["normalized"]]
    selected = usable[0] if usable else None
    # Equivalent supported aliases agree; distinct unknown labels do not.
    values = {r["classification"] if r["classification"] != "unknown" else r["normalized"] for r in usable}
    return {
        "version": "sam-lifecycle-v1", "fields": rows,
        "selected_field": selected["field"] if selected else None,
        "selected_raw": selected["raw"] if selected else None,
        "classification": selected["classification"] if selected else "unknown",
        "conflict": len(values) > 1, "missing": selected is None,
        "historical": any(r["classification"] == "historical" for r in usable),
        "policy": "native-current-type_then-projection_then-base-fallback",
    }
