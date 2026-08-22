"""FCEB catalog owner: the CISA civilian-executive-branch list as a checkable
coverage surface.

WHY THIS EXISTS (2026-07-30). The engagement-scope civilian preset admits all
102 FCEB agencies, but 78 of them only through the fail-open rule ("ignorance
never excludes"): the code did not RECOGNIZE them, it merely failed to refuse
them. Coverage by accident cannot be asserted, tested, or reported. This
module makes the list a first-class reference so "we looked for every FCEB
agency" is a receipt with a number per agency, not a belief.

MATCHING IS EXACT, NEVER FUZZY. The store's agency vocabulary is small (55
distinct strings on 2026-07-30) and comes from the SAM extract, which is
stable. Token-subset matching was tried first and produced both a false
negative (IBWC, named "INTERNATIONAL BOUNDARY AND WATER COMMISSION:
US-MEXICO" in the store) and a latent collision class (Social Security
Administration vs Social Security Advisory Board; the two Endowments). So:
a deterministic normalizer, exact comparison, explicit aliases for shapes
the normalizer cannot derive, and every unmatched store agency REPORTED in
the census rather than guessed at. Unknown strings staying unresolved is the
same doctrine the scope code uses for toptier codes.

THIS MODULE DOES NOT TOUCH ENGAGEMENT SCOPE. Recognition here feeds the
census and the coverage tests only. Wiring FCEB recognition into
resolve_department would FLIP those 78 agencies from admitted-by-fail-open
to refused-by-enumeration under the civilian preset's 24-department list,
silently narrowing an operator-approved gate. The census proves coverage;
the scope decision stays the operator's.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CATALOG_PATH = os.path.join(_ROOT, "data", "reference", "fceb_agencies.json")
_SUPPORTED_VERSION = 1

# "TREASURY, DEPARTMENT OF THE" -> "DEPARTMENT OF THE TREASURY";
# "INTERNATIONAL TRADE COMMISSION, UNITED STATES" -> "UNITED STATES ...".
_INVERTED = re.compile(
    r"^(?P<body>.+?),\s*(?P<head>DEPARTMENT OF(?: THE)?|UNITED STATES)$")
_PAREN = re.compile(r"\([^)]*\)")


def _normalize(name: Any) -> str:
    """One canonical form for an agency name, applied to BOTH sides.

    Handles the extract's habits: comma-inverted department names, stray
    parentheticals ("(DUNS # 023599554)"), "U.S."/"US" for "UNITED STATES",
    and an optional leading "UNITED STATES" ("United States Department of
    Agriculture" is the CISA name; the store writes "AGRICULTURE, DEPARTMENT
    OF"). Anything this cannot make identical needs an explicit alias.
    """
    text = " ".join(str(name or "").split()).upper()
    text = _PAREN.sub(" ", text)
    text = text.replace("U.S.", "US ").replace(".", " ")
    text = " ".join(text.split())
    m = _INVERTED.match(text)
    if m:
        text = f"{m.group('head')} {m.group('body')}"
    words = text.split()
    words = ["UNITED", "STATES"] if words == ["US"] else words
    out: list[str] = []
    for w in words:
        out.extend(["UNITED", "STATES"] if w == "US" else [w])
    if out[:2] == ["UNITED", "STATES"]:
        out = out[2:] or out
    return " ".join(out)


# OPERATOR RULING 2026-07-30 ("if theres opps there than sure - why not"):
# civilian engagement scope means NON-MILITARY BUYERS, not strictly FCEB.
# These entities are outside the CISA list (other branches, trusts, the IC)
# but post real civilian opportunities; measured that day: 62 open notices
# across them, 5 open in tech NAICS, including a 541512 Integrated Library
# System at the US Courts and a Library of Congress optical-storage RFI.
# They stay ADMITTED in civilian engagements, and the census names them as
# approved buyers instead of leaving them to read as leakage. A store string
# in NO bucket lands in "unaccounted", which is a prompt for a decision,
# never an automatic admission onto this list.
NON_FCEB_CIVILIAN = {
    "Administrative Office of the US Courts": "judicial",
    "United States Government Publishing Office": "legislative",
    "Library of Congress": "legislative",
    "Architect of the Capitol": "legislative",
    "Government Accountability Office": "legislative",
    "Senate, The": "legislative",
    "Postal Service": "independent establishment",
    "Smithsonian Institution": "trust instrumentality",
    "National Gallery of Art": "trust instrumentality",
    "United States Holocaust Memorial Museum": "independent establishment",
    "Office of the Director of National Intelligence": "intelligence community",
}

# The one deliberately excluded department under a civilian engagement.
_DEFENSE_NAMES = {"DEPT OF DEFENSE", "DEPARTMENT OF DEFENSE"}


def classify_unmatched(name: Any) -> str:
    """The census bucket for a store agency string no FCEB entry claims."""
    text = " ".join(str(name or "").split())
    if not text:
        return "blank"
    if _normalize(text) in {_normalize(n) for n in _DEFENSE_NAMES}:
        return "excluded:defense"
    for approved, branch in NON_FCEB_CIVILIAN.items():
        if _normalize(approved) == _normalize(text):
            return f"approved_non_fceb:{branch}"
    return "unaccounted"


def load_catalog(path: str = _CATALOG_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("version") != _SUPPORTED_VERSION:
        raise ValueError(
            f"fceb_agencies.json version {doc.get('version')!r} unsupported "
            f"(expected {_SUPPORTED_VERSION})")
    agencies = doc["agencies"]
    seen: set[str] = set()
    for entry in agencies:
        if entry["acronym"] in seen:
            raise ValueError(f"duplicate FCEB acronym {entry['acronym']!r}")
        seen.add(entry["acronym"])
    return agencies


def build_index(catalog: Optional[list[dict]] = None) -> dict[str, str]:
    """normalized name/alias -> acronym. A collision inside the catalog is a
    catalog bug and refuses loudly rather than resolving arbitrarily."""
    index: dict[str, str] = {}
    for entry in (catalog if catalog is not None else load_catalog()):
        for raw in (entry["name"], *entry.get("aliases", [])):
            key = _normalize(raw)
            held = index.get(key)
            if held is not None and held != entry["acronym"]:
                raise ValueError(
                    f"FCEB catalog collision: {raw!r} normalizes onto both "
                    f"{held} and {entry['acronym']}")
            index[key] = entry["acronym"]
    return index


def match_agency(name: Any, index: dict[str, str]) -> Optional[str]:
    """The FCEB acronym for a store agency string, or None. None means NOT
    ON THE LIST (DoD, legislative branch, Smithsonian) or a naming shape the
    catalog has not seen; the census reports it either way."""
    if not str(name or "").strip():
        return None
    return index.get(_normalize(name))


def census(rows: Iterable[Any], *, catalog: Optional[list[dict]] = None,
           ) -> dict:
    """One row per FCEB agency over the given notices: a receipt, not a hunt.

    Every agency on the list gets a row even at zero, because "searched,
    nothing found" and "never looked" must be distinguishable. A notice is
    credited to an FCEB entry when its agency OR its subtier resolves there
    (OCC and FERC live only as subtiers), and both attributions are named.
    Store agency strings that resolve to no entry are returned under
    unmatched_agencies so nothing leaves the accounting silently.
    """
    catalog = catalog if catalog is not None else load_catalog()
    index = build_index(catalog)
    per: dict[str, dict] = {
        e["acronym"]: {"acronym": e["acronym"], "name": e["name"],
                       "notices": 0, "matched_agency_strings": [],
                       "matched_subtier_strings": []}
        for e in catalog}
    unmatched: dict[str, int] = {}
    scanned = 0
    for row in rows:
        scanned += 1
        agency = row["agency"] if "agency" in row.keys() else None
        subtier = row["subtier"] if "subtier" in row.keys() else None
        hit = match_agency(agency, index)
        sub_hit = match_agency(subtier, index)
        credited: set[str] = set()
        if hit:
            per[hit]["notices"] += 1
            credited.add(hit)
            name = " ".join(str(agency).split())
            if name not in per[hit]["matched_agency_strings"]:
                per[hit]["matched_agency_strings"].append(name)
        else:
            # The raw (possibly empty) string is the accounting key; the
            # "(blank)" label is display-only, so classification always sees
            # what the store actually held.
            key = " ".join(str(agency or "").split())
            unmatched[key] = unmatched.get(key, 0) + 1
        if sub_hit and sub_hit not in credited:
            per[sub_hit]["notices"] += 1
            name = " ".join(str(subtier).split())
            if name not in per[sub_hit]["matched_subtier_strings"]:
                per[sub_hit]["matched_subtier_strings"].append(name)
    agencies = sorted(per.values(),
                      key=lambda a: (-a["notices"], a["acronym"]))
    return {
        "source": "sam_notice_store",
        "notices_scanned": scanned,
        "fceb_total": len(per),
        "with_notices": sum(1 for a in agencies if a["notices"]),
        "zero_notices": sum(1 for a in agencies if not a["notices"]),
        "agencies": agencies,
        "unmatched_agencies": [
            {"agency": k or "(blank)", "notices": v,
             "class": classify_unmatched(k)}
            for k, v in sorted(unmatched.items(), key=lambda kv: -kv[1])],
        "unaccounted": sorted(
            k for k in unmatched if classify_unmatched(k) == "unaccounted"),
        "match_policy": ("exact after normalization; unmatched strings are "
                         "reported and classified (excluded defense, "
                         "operator-approved non-FCEB civilian buyer, blank, "
                         "or unaccounted), never guessed onto the list"),
    }


def main() -> int:  # pragma: no cover - thin CLI over census()
    from tools.notice_store import connect, last_ingest

    conn = connect()
    try:
        rows = conn.execute("SELECT agency, subtier FROM notices").fetchall()
        ingest = last_ingest(conn)
    finally:
        conn.close()
    report = census(rows)
    report["store_last_ingest"] = (ingest or {}).get("ingest_date")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
