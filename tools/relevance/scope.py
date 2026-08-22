"""Engagement scope: the agency universe as a second exclude-only boundary.

A DoD record in a DHS-scoped engagement is out of scope regardless of
capability score. Boundaries exclude; they never include; a strong
out-of-scope hit passes flagged OFF-SCOPE SIGNAL (internal vocabulary,
surfaced as expansion signal, never deliverable candidacy).

Absent config means NO scope filter, marked UNSCOPED in every output;
scope is never guessed. Normalization resolves department codes, subtier
names, and configured aliases through the curated agency catalog; a
record whose agency fields resolve to nothing is treated as in scope
(ignorance is not foreignness, matching the scope-containment doctrine).
"""
from __future__ import annotations

import json
import os
import re
from typing import NamedTuple, Optional

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    model_serializer,
    model_validator,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCOPE_PRESETS_PATH = os.path.join(
    _ROOT, "data", "reference", "scope_presets.json")
_SUPPORTED_SCOPE_PRESET_VERSION = 1
_REQUIRED_SCOPE_PRESETS = {"civilian", "defense", "all_federal"}

_AGENCY_FIELDS = ("agency", "awarding_agency", "awarding_sub_agency",
                  "department", "subtier", "office", "component")
_TOPTIER_CODE_FIELDS = ("toptier_code", "awarding_toptier_code",
                        "awarding_toptier_agency_code")
_PRESET_ONLY_DEPARTMENT_NAMES = {
    "department of housing and urban development": "HUD",
    "national science foundation": "NSF",
    "nuclear regulatory commission": "NRC",
    "office of personnel management": "OPM",
    "social security administration": "SSA",
}

#: The single code-normalization authority for engagement scope. Three-digit
#: (and DOL's four-digit) keys are USAspending toptier codes; the remaining
#: four-digit keys are the awarding-toptier encodings found in USAspending
#: generated award ids (CONT_AWD_<piid>_<awarding toptier>_...). Unknown
#: codes stay unresolved rather than being padded, truncated, or guessed.
TOPTIER_CODE_DEPARTMENTS = {
    "012": "USDA",
    "013": "DOC",
    "014": "DOI",
    "015": "DOJ",
    "019": "DOS",
    "020": "Treasury",
    "024": "OPM",
    "028": "SSA",
    "031": "NRC",
    "036": "VA",
    "047": "GSA",
    "049": "NSF",
    "068": "EPA",
    "069": "DOT",
    "070": "DHS",
    "072": "USAID",
    "073": "SBA",
    "075": "HHS",
    "080": "NASA",
    "086": "HUD",
    "089": "DOE",
    "091": "ED",
    "097": "DoD",
    "1344": "DOC",   # USPTO orders (verified: 1333BJ... awards)
    "1406": "DOI",   # verified live: 140D0426F0204 awarding DOI
    "1601": "DOL",
    "3600": "VA",    # verified: 36C10B25F0304
    "4732": "GSA",   # GSA FAS
    "7003": "DHS",   # USCIS (verified: 70SBUR22F00000113)
    "7014": "DHS",   # CBP (verified: 70B04C25F00001054)
    "9700": "DoD",   # verified: HR001124C0488
}
_PRESET_DEPARTMENT_ABBREVIATIONS = {
    department.lower(): department
    for department in TOPTIER_CODE_DEPARTMENTS.values()
}

_GENERATED_ID_RE = re.compile(r"CONT_(?:AWD|IDV)_[^_]+_(\d{4})_")


class ScopePresetResolution(NamedTuple):
    """Deterministic expansion of one named preset and its code edits."""

    name: str
    version: int
    unbounded: bool
    codes: tuple[str, ...]
    departments: tuple[str, ...]
    excluded_departments: tuple[str, ...]
    basis: str


def _scope_preset_catalog() -> tuple[int, dict]:
    """Load and validate the versioned preset reference.

    Human-readable ``_comment`` values are deliberately ignored: only each
    entry's code is operational, and every code must resolve through
    ``TOPTIER_CODE_DEPARTMENTS`` above.
    """
    with open(_SCOPE_PRESETS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    version = raw.get("version") if isinstance(raw, dict) else None
    if (type(version) is not int  # bool and 1.0 are not schema versions
            or version != _SUPPORTED_SCOPE_PRESET_VERSION):
        raise ValueError(
            "unsupported scope preset version "
            f"{version!r}; expected v{_SUPPORTED_SCOPE_PRESET_VERSION}")
    presets = raw.get("presets")
    if not isinstance(presets, dict):
        raise ValueError("scope preset reference must contain a presets object")
    names = set(presets)
    if names != _REQUIRED_SCOPE_PRESETS:
        raise ValueError(
            "scope preset reference must define exactly "
            f"{sorted(_REQUIRED_SCOPE_PRESETS)}; found {sorted(names)}")
    validated = {}
    for name, body in presets.items():
        if not isinstance(body, dict) or not isinstance(
                body.get("unbounded"), bool):
            raise ValueError(f"scope preset {name!r} needs boolean unbounded")
        entries = body.get("codes")
        if not isinstance(entries, list):
            raise ValueError(f"scope preset {name!r} needs a codes list")
        codes = []
        for entry in entries:
            code = entry.get("code") if isinstance(entry, dict) else None
            if not isinstance(code, str) or not code:
                raise ValueError(
                    f"scope preset {name!r} has a non-string code entry")
            if code not in TOPTIER_CODE_DEPARTMENTS:
                raise ValueError(
                    f"scope preset {name!r} references unknown code {code!r}")
            if code in codes:
                raise ValueError(
                    f"scope preset {name!r} repeats code {code!r}")
            codes.append(code)
        unbounded = body["unbounded"]
        if unbounded and codes:
            raise ValueError(
                f"unbounded scope preset {name!r} must not list codes")
        if not unbounded and not codes:
            raise ValueError(
                f"bounded scope preset {name!r} must list at least one code")
        validated[name] = {"unbounded": unbounded, "codes": tuple(codes)}
    return version, validated


def _validate_edit_codes(codes: list[str], label: str) -> tuple[str, ...]:
    if len(codes) != len(set(codes)):
        raise ValueError(f"engagement_scope {label} contains duplicate codes")
    unknown = [code for code in codes
               if code not in TOPTIER_CODE_DEPARTMENTS]
    if unknown:
        raise ValueError(
            f"engagement_scope {label} references unknown code(s): "
            + ", ".join(repr(code) for code in unknown))
    return tuple(codes)


def resolve_scope_preset(name: str, add: Optional[list[str]] = None,
                         remove: Optional[list[str]] = None,
                         ) -> ScopePresetResolution:
    """Expand ``name``, then apply ADD, then REMOVE (REMOVE always wins).

    Code edits normalize to departments through the module map, so aliases
    cannot create two semantic copies of the same department. An unbounded
    preset remains all-federal; removals become an explicit deny set.
    """
    version, presets = _scope_preset_catalog()
    if name not in presets:
        raise ValueError(
            f"unknown engagement_scope preset {name!r}; expected one of "
            + ", ".join(sorted(presets)))
    add_codes = _validate_edit_codes(list(add or []), "add")
    remove_codes = _validate_edit_codes(list(remove or []), "remove")
    preset = presets[name]
    base_codes = list(preset["codes"])
    for code in add_codes:
        if code not in base_codes:
            base_codes.append(code)
    removed_departments = {
        TOPTIER_CODE_DEPARTMENTS[code] for code in remove_codes}
    if preset["unbounded"]:
        # ADD cannot widen a universal set. It remains visible in provenance,
        # while REMOVE becomes the explicit deny set below.
        effective_codes = ()
        departments = ()
    else:
        effective_codes = tuple(
            code for code in base_codes
            if TOPTIER_CODE_DEPARTMENTS[code] not in removed_departments)
        departments = tuple(dict.fromkeys(
            TOPTIER_CODE_DEPARTMENTS[code] for code in effective_codes))
    if not preset["unbounded"] and not departments:
        raise ValueError(
            f"engagement_scope preset {name!r} resolves to an empty boundary")
    edits = [*(f"+{code}" for code in sorted(add_codes)),
             *(f"-{code}" for code in sorted(remove_codes))]
    basis = f"preset:{name}@v{version}"
    if edits:
        basis += " (" + " ".join(edits) + ")"
    excluded = (tuple(sorted(removed_departments))
                if preset["unbounded"] else ())
    return ScopePresetResolution(
        name=name, version=version, unbounded=preset["unbounded"],
        codes=effective_codes, departments=departments,
        excluded_departments=excluded, basis=basis)


class EngagementScope(BaseModel):
    """The in-scope agency universe for one engagement."""

    departments: list[str] = Field(
        default_factory=list,
        description="in-scope department abbrs per the curated catalog "
                    "(DHS, DoD, VA, ...)")
    subtiers: list[str] = Field(
        default_factory=list,
        description="explicitly in-scope subtier names when narrower than "
                    "a department")
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="normalization map: raw agency string -> department "
                    "abbr (department codes, subtier names, common aliases)")
    preset: Optional[str] = Field(
        default=None,
        description="versioned named boundary from scope_presets.json")
    add: list[str] = Field(
        default_factory=list,
        description="toptier codes added after preset expansion")
    remove: list[str] = Field(
        default_factory=list,
        description="toptier codes removed last; REMOVE wins")

    _preset_resolution: Optional[ScopePresetResolution] = PrivateAttr(
        default=None)

    @model_validator(mode="after")
    def _resolve_named_preset(self):
        if self.preset is None:
            if self.add or self.remove:
                raise ValueError(
                    "engagement_scope add/remove require a named preset")
            return self
        if self.departments or self.subtiers or self.aliases:
            raise ValueError(
                "engagement_scope preset cannot be mixed with explicit "
                "departments, subtiers, or aliases")
        self._preset_resolution = resolve_scope_preset(
            self.preset, self.add, self.remove)
        return self

    @model_serializer(mode="wrap")
    def _serialize_compatibly(self, handler):
        """Keep the pre-preset JSON shape byte-identical on Pydantic 2.6+."""
        data = handler(self)
        if self.preset is None:
            data.pop("preset", None)
        if not self.add:
            data.pop("add", None)
        if not self.remove:
            data.pop("remove", None)
        return data

    @property
    def resolved_codes(self) -> tuple[str, ...]:
        """Effective code sequence; empty for legacy or unbounded scopes."""
        if self._preset_resolution is None:
            return ()
        return self._preset_resolution.codes

    @property
    def resolved_departments(self) -> tuple[str, ...]:
        """Effective department allowlist, preserving reference order."""
        if self._preset_resolution is None:
            return tuple(self.departments)
        return self._preset_resolution.departments

    @property
    def excluded_departments(self) -> tuple[str, ...]:
        if self._preset_resolution is None:
            return ()
        return self._preset_resolution.excluded_departments

    @property
    def unbounded(self) -> bool:
        return bool(self._preset_resolution
                    and self._preset_resolution.unbounded)

    @property
    def resolved_basis(self) -> str:
        """Stable provenance stamp for output surfaces."""
        if self._preset_resolution is None:
            return "config"
        return self._preset_resolution.basis


def record_department(record: dict) -> Optional[str]:
    """Resolve a record's department through the curated agency catalog;
    None when no field affirmatively resolves (never guessed)."""
    from tools.agency_scope import _department_abbrs
    texts = []
    for field in _AGENCY_FIELDS:
        v = record.get(field)
        if isinstance(v, str) and v.strip():
            texts.append(v)
    raw = record.get("raw_payload")
    if isinstance(raw, dict):
        for field in _AGENCY_FIELDS:
            v = raw.get(field)
            if isinstance(v, str) and v.strip():
                texts.append(v)
    for text in texts:
        depts = _department_abbrs(text)
        if depts:
            return sorted(depts)[0]
    # a USAspending generated id encodes the awarding toptier: rows without
    # agency fields (buyer-map records) still resolve deterministically
    for key in ("url", "generated_id", "source"):
        v = record.get(key)
        if isinstance(v, str):
            m = _GENERATED_ID_RE.search(v)
            # 1601 is the sole four-digit canonical code introduced with
            # presets. Excluding it here preserves the exact pre-preset
            # generated-id behavior without duplicating the authoritative
            # code map in a second allowlist.
            if (m and m.group(1) in TOPTIER_CODE_DEPARTMENTS
                    and m.group(1) != "1601"):
                return TOPTIER_CODE_DEPARTMENTS[m.group(1)]
    return None


def _preset_record_department(record: dict) -> Optional[str]:
    """Preset-only completion of the legacy agency resolver.

    Existing explicit scopes retain their exact historical decisions. Named
    presets additionally understand canonical toptier fields and the five CFO
    Act departments not yet present in the shared agency catalog.
    """
    raw = record.get("raw_payload")
    sources = (record, raw if isinstance(raw, dict) else {})
    for source in sources:
        for field in _TOPTIER_CODE_FIELDS:
            code = source.get(field)
            if isinstance(code, str) and code in TOPTIER_CODE_DEPARTMENTS:
                return TOPTIER_CODE_DEPARTMENTS[code]
    for source in sources:
        for field in _AGENCY_FIELDS:
            value = source.get(field)
            if not isinstance(value, str):
                continue
            low = value.strip().lower()
            abbreviation = _PRESET_DEPARTMENT_ABBREVIATIONS.get(low)
            if abbreviation is not None:
                return abbreviation
            for name, department in _PRESET_ONLY_DEPARTMENT_NAMES.items():
                if name in low:
                    return department
    for key in ("url", "generated_id", "source"):
        value = record.get(key)
        if isinstance(value, str):
            match = _GENERATED_ID_RE.search(value)
            if match and match.group(1) in TOPTIER_CODE_DEPARTMENTS:
                return TOPTIER_CODE_DEPARTMENTS[match.group(1)]
    return None


def resolve_department(record: dict,
                       scope: Optional[EngagementScope]) -> Optional[str]:
    """Department resolution with the scope's alias map applied first."""
    if scope and scope.aliases:
        for field in _AGENCY_FIELDS:
            v = record.get(field)
            if isinstance(v, str) and v.strip() in scope.aliases:
                return scope.aliases[v.strip()]
        raw = record.get("raw_payload")
        if isinstance(raw, dict):
            for field in _AGENCY_FIELDS:
                v = raw.get(field)
                if isinstance(v, str) and v.strip() in scope.aliases:
                    return scope.aliases[v.strip()]
    if scope is not None and scope.preset is not None:
        # An explicit toptier code is stronger than conflicting display text;
        # preset-only completion checks it before the legacy text resolver.
        department = _preset_record_department(record)
        if department is not None:
            return department
    return record_department(record)


def in_scope(record: dict, scope: Optional[EngagementScope]) -> tuple[bool, str]:
    """(inside, basis). No scope config -> (True, 'UNSCOPED'). An
    unresolvable agency -> (True, 'unresolved'): ignorance never excludes.
    Named presets prefix every decision with their resolved version stamp."""
    def stamped(decision: str = "") -> str:
        if scope is None or scope.preset is None:
            return decision
        return (f"{scope.resolved_basis} · {decision}"
                if decision else scope.resolved_basis)

    if scope is None:
        return True, "UNSCOPED"
    if scope.unbounded:
        if not scope.excluded_departments:
            return True, stamped()
        dept = resolve_department(record, scope)
        if dept is None:
            return True, stamped("unresolved")
        if dept in scope.excluded_departments:
            return False, stamped(f"off-scope:{dept}")
        return True, stamped(f"in-scope:{dept}")
    departments = scope.resolved_departments
    if not (departments or scope.subtiers):
        return True, "UNSCOPED"
    dept = resolve_department(record, scope)
    if dept is None:
        return True, stamped("unresolved")
    if dept in departments:
        return True, stamped(f"in-scope:{dept}")
    for field in _AGENCY_FIELDS:
        v = record.get(field)
        if isinstance(v, str) and any(
                s.lower() in v.lower() for s in scope.subtiers):
            return True, stamped(f"in-scope-subtier:{v.strip()[:40]}")
    return False, stamped(f"off-scope:{dept}")


def load_engagement_scope(client_name: str) -> Optional[EngagementScope]:
    """Config precedence: the taxonomy file's engagement_scope, else an
    engagement_scope key in the raw profile JSON, else the standalone
    engagement_scope.json. Absent everywhere means None (UNSCOPED); a
    default is never guessed."""
    from tools.relevance.taxonomy import load_taxonomy
    taxonomy = load_taxonomy(client_name)
    if taxonomy is not None and taxonomy.engagement_scope is not None:
        return taxonomy.engagement_scope
    from tools.capability import _slug
    path = os.path.join(_ROOT, "clients", _slug(client_name), "profile.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and isinstance(raw.get("engagement_scope"), dict):
            return EngagementScope(**raw["engagement_scope"])
    standalone = os.path.join(
        _ROOT, "clients", _slug(client_name), "engagement_scope.json")
    if os.path.exists(standalone):
        with open(standalone, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(
                f"engagement scope config must be an object: {standalone}")
        return EngagementScope(**raw)
    return None
