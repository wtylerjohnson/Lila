"""Federal agency reference — canonical names, abbreviations, match aliases.

Serves two jobs: the dashboard's agency autocomplete (type "cbp", complete to
U.S. Customs and Border Protection) and the agency-pass scoper (match a chosen
agency against notice fullParentPathName / award awarding_agency strings,
which name agencies in wildly inconsistent forms).

Curated, not exhaustive: the agencies federal capture work actually touches.
Aliases are lowercase substrings; a record matches when ANY alias appears in
the target string. Components carry their parent so a DEPARTMENT pass (DHS)
also captures its components (CBP, TSA, ...) via the parent relationship.
"""

from __future__ import annotations

from typing import Optional

# name: canonical display · abbr: chip/short form · parent: department abbr
# aliases: lowercase substrings matched against agency strings in records
AGENCIES: list[dict] = [
    {"name": "Department of Homeland Security", "abbr": "DHS", "parent": None,
     "aliases": ["homeland security"]},
    {"name": "U.S. Customs and Border Protection", "abbr": "CBP", "parent": "DHS",
     "aliases": ["customs and border protection", "cbp"]},
    {"name": "Transportation Security Administration", "abbr": "TSA", "parent": "DHS",
     "aliases": ["transportation security administration", "tsa"]},
    {"name": "U.S. Immigration and Customs Enforcement", "abbr": "ICE", "parent": "DHS",
     "aliases": ["immigration and customs enforcement"]},
    {"name": "U.S. Coast Guard", "abbr": "USCG", "parent": "DHS",
     "aliases": ["coast guard"]},
    {"name": "Cybersecurity and Infrastructure Security Agency", "abbr": "CISA", "parent": "DHS",
     "aliases": ["cybersecurity and infrastructure security"]},
    {"name": "Federal Emergency Management Agency", "abbr": "FEMA", "parent": "DHS",
     "aliases": ["federal emergency management"]},
    {"name": "U.S. Citizenship and Immigration Services", "abbr": "USCIS", "parent": "DHS",
     "aliases": ["citizenship and immigration"]},
    {"name": "U.S. Secret Service", "abbr": "USSS", "parent": "DHS",
     "aliases": ["secret service"]},
    {"name": "Federal Law Enforcement Training Centers", "abbr": "FLETC", "parent": "DHS",
     "aliases": ["law enforcement training center"]},
    {"name": "Department of Defense", "abbr": "DoD", "parent": None,
     "aliases": ["dept of defense", "department of defense"]},
    {"name": "Department of the Air Force", "abbr": "USAF", "parent": "DoD",
     "aliases": ["air force"]},
    {"name": "Department of the Army", "abbr": "Army", "parent": "DoD",
     "aliases": ["dept of the army", "department of the army", "army national guard"]},
    {"name": "Department of the Navy", "abbr": "Navy", "parent": "DoD",
     "aliases": ["dept of the navy", "department of the navy", "marine corps"]},
    {"name": "U.S. Special Operations Command", "abbr": "SOCOM", "parent": "DoD",
     "aliases": ["special operations command", "socom", "ussocom"]},
    {"name": "Defense Information Systems Agency", "abbr": "DISA", "parent": "DoD",
     "aliases": ["defense information systems"]},
    {"name": "Missile Defense Agency", "abbr": "MDA", "parent": "DoD",
     "aliases": ["missile defense agency"]},
    {"name": "Washington Headquarters Services", "abbr": "WHS", "parent": "DoD",
     "aliases": ["washington headquarters"]},
    {"name": "Defense Logistics Agency", "abbr": "DLA", "parent": "DoD",
     "aliases": ["defense logistics"]},
    {"name": "Defense Advanced Research Projects Agency", "abbr": "DARPA", "parent": "DoD",
     "aliases": ["advanced research projects agency", "darpa"]},
    {"name": "National Geospatial-Intelligence Agency", "abbr": "NGA", "parent": "DoD",
     "aliases": ["geospatial-intelligence"]},
    {"name": "Department of Transportation", "abbr": "DOT", "parent": None,
     "aliases": ["transportation, department", "department of transportation"]},
    {"name": "Federal Aviation Administration", "abbr": "FAA", "parent": "DOT",
     "aliases": ["federal aviation administration", "faa"]},
    {"name": "General Services Administration", "abbr": "GSA", "parent": None,
     "aliases": ["general services administration"]},
    {"name": "Federal Acquisition Service", "abbr": "FAS", "parent": "GSA",
     "aliases": ["federal acquisition service"]},
    {"name": "Department of State", "abbr": "DOS", "parent": None,
     "aliases": ["department of state", "state, department"]},
    {"name": "Department of Justice", "abbr": "DOJ", "parent": None,
     "aliases": ["department of justice", "justice, department"]},
    {"name": "Federal Bureau of Investigation", "abbr": "FBI", "parent": "DOJ",
     "aliases": ["bureau of investigation"]},
    {"name": "Department of Veterans Affairs", "abbr": "VA", "parent": None,
     "aliases": ["veterans affairs"]},
    {"name": "Department of Health and Human Services", "abbr": "HHS", "parent": None,
     "aliases": ["health and human services"]},
    {"name": "Department of Energy", "abbr": "DOE", "parent": None,
     "aliases": ["department of energy", "energy, department"]},
    {"name": "Department of the Interior", "abbr": "DOI", "parent": None,
     "aliases": ["interior, department", "department of the interior"]},
    {"name": "Department of Commerce", "abbr": "DOC", "parent": None,
     "aliases": ["department of commerce", "commerce, department"]},
    {"name": "National Oceanic and Atmospheric Administration", "abbr": "NOAA", "parent": "DOC",
     "aliases": ["oceanic and atmospheric"]},
    {"name": "U.S. Patent and Trademark Office", "abbr": "USPTO", "parent": "DOC",
     "aliases": ["patent and trademark", "uspto"]},
    {"name": "Department of the Treasury", "abbr": "Treasury", "parent": None,
     "aliases": ["treasury"]},
    {"name": "Internal Revenue Service", "abbr": "IRS", "parent": "Treasury",
     "aliases": ["internal revenue"]},
    {"name": "Department of Agriculture", "abbr": "USDA", "parent": None,
     "aliases": ["department of agriculture", "agriculture, department"]},
    {"name": "Department of Labor", "abbr": "DOL", "parent": None,
     "aliases": ["department of labor", "labor, department"]},
    {"name": "Department of Education", "abbr": "ED", "parent": None,
     "aliases": ["department of education", "education, department"]},
    {"name": "National Aeronautics and Space Administration", "abbr": "NASA", "parent": None,
     "aliases": ["aeronautics and space"]},
    {"name": "Environmental Protection Agency", "abbr": "EPA", "parent": None,
     "aliases": ["environmental protection"]},
    {"name": "Agency for International Development", "abbr": "USAID", "parent": None,
     "aliases": ["international development"]},
    {"name": "Small Business Administration", "abbr": "SBA", "parent": None,
     "aliases": ["small business administration"]},
    {"name": "National Institutes of Health", "abbr": "NIH", "parent": "HHS",
     "aliases": ["national institutes of health"]},
    {"name": "Centers for Disease Control and Prevention", "abbr": "CDC", "parent": "HHS",
     "aliases": ["disease control"]},
    {"name": "Centers for Medicare and Medicaid Services", "abbr": "CMS", "parent": "HHS",
     "aliases": ["medicare and medicaid"]},
]

# the dashboard's prefill chips, most-searched first (operator preference)
DEFAULT_CHIPS = ["DHS", "DoD", "CBP", "TSA", "FAA", "GSA"]


def find(query: str) -> Optional[dict]:
    """Resolve a user string ('cbp', 'CBP', 'Customs and Border Protection')
    to one agency record, abbr match first, then name/alias prefix."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for a in AGENCIES:
        if q == a["abbr"].lower() or q == a["name"].lower():
            return a
    for a in AGENCIES:
        if a["name"].lower().startswith(q) or any(al.startswith(q) for al in a["aliases"]):
            return a
    for a in AGENCIES:
        if q in a["name"].lower() or any(q in al for al in a["aliases"]):
            return a
    return None


def suggest(query: str, limit: int = 8) -> list[dict]:
    """Ranked autocomplete matches: abbr-prefix, name-prefix, then contains."""
    q = (query or "").strip().lower()
    if not q:
        return [a for a in AGENCIES if a["abbr"] in DEFAULT_CHIPS][:limit]
    tiers: list[list[dict]] = [[], [], []]
    for a in AGENCIES:
        hay = [a["abbr"].lower(), a["name"].lower(), *a["aliases"]]
        if a["abbr"].lower().startswith(q):
            tiers[0].append(a)
        elif any(h.startswith(q) for h in hay):
            tiers[1].append(a)
        elif any(q in h for h in hay):
            tiers[2].append(a)
    out = [a for tier in tiers for a in tier]
    return out[:limit]


def matches_record(agency_string: str, agency: dict,
                   include_components: bool = True) -> bool:
    """Does a record's agency string (fullParentPathName, awarding_agency,
    awarding_sub_agency ...) belong to this agency? A department pass also
    captures its components."""
    s = (agency_string or "").lower()
    if not s:
        return False
    if any(al in s for al in agency["aliases"]):
        return True
    if include_components and agency["parent"] is None:
        return any(any(al in s for al in child["aliases"])
                   for child in AGENCIES if child.get("parent") == agency["abbr"])
    return False
