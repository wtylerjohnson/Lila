"""Client capability profile; HUMAN-ATTESTED config, never inferred.

Distinct from agents/schemas.CapabilityProfile (intake-INFERRED, drives the
Assess engine). This file is what the client attests about themselves: size
status per NAICS, socioeconomic certifications, vehicles held, typical award
band. The partnering blockers read ONLY this; nothing here is ever guessed.

File: data/review/<slug>.partnering.json  (template:
data/reference/partnering.profile.example.json). Keys starting with "_" are
comments and ignored.

Unset vs attested-empty; the load-bearing distinction:
    key ABSENT from the file  -> UNSET (unconfirmed; blocker degrades to N/A
                                 with an explicit gap)
    key PRESENT and empty     -> ATTESTED (e.g. "certifications": [] means
                                 the client attests they hold none)
A missing file leaves every field unset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

_ROOT = Path(__file__).resolve().parents[2]

KNOWN_CERTIFICATIONS = ("8(a)", "SDVOSB", "WOSB", "EDWOSB", "HUBZone")
SIZE_STATUSES = ("small", "other_than_small")

# the four fields the blockers key on; each one unset -> its checks go N/A
ATTESTABLE_FIELDS = ("size_status_by_naics", "certifications",
                     "vehicles_held", "award_band")


class ProfileError(ValueError):
    """The profile file exists but cannot be trusted; reject loudly, never
    coerce: a typo in an eligibility-bearing field must not become a blocker
    decision."""


class AwardBand(BaseModel):
    floor_usd: Optional[float] = Field(default=None, ge=0)
    ceiling_usd: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self):
        if (self.floor_usd is not None and self.ceiling_usd is not None
                and self.ceiling_usd < self.floor_usd):
            raise ValueError("award_band.ceiling_usd is below floor_usd")
        return self


class PartneringProfile(BaseModel):
    client_name: Optional[str] = None
    size_status_by_naics: dict[str, str] = Field(
        default_factory=dict,
        description="NAICS -> 'small' | 'other_than_small', as the client attests")
    certifications: list[str] = Field(
        default_factory=list,
        description=f"subset of {KNOWN_CERTIFICATIONS}; empty = attests none held")
    vehicles_held: list[str] = Field(default_factory=list)
    award_band: Optional[AwardBand] = None
    capability_keywords: list[str] = Field(default_factory=list)
    attested_at: Optional[str] = None

    @model_validator(mode="after")
    def _known_values(self):
        bad = [c for c in self.certifications if c not in KNOWN_CERTIFICATIONS]
        if bad:
            raise ValueError(
                f"unknown certification(s) {bad}; known: {list(KNOWN_CERTIFICATIONS)}")
        bad = {k: v for k, v in self.size_status_by_naics.items()
               if v not in SIZE_STATUSES}
        if bad:
            raise ValueError(
                f"size_status_by_naics values must be one of {list(SIZE_STATUSES)}; got {bad}")
        return self


def profile_path(slug: str) -> Path:
    review = Path(os.environ.get("LILA_REVIEW_DIR", _ROOT / "data" / "review"))
    return review / f"{slug}.partnering.json"


def _known_vehicle_names() -> set[str]:
    try:
        doc = json.loads((_ROOT / "data" / "reference" / "vehicles.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {v.get("name", "").strip().lower()
            for v in doc.get("vehicles", []) if v.get("name")}


def load_partnering_profile(slug: str) -> tuple[PartneringProfile, list[str], list[str]]:
    """(profile, unset_fields, warnings).

    Missing file -> empty profile with EVERY attestable field unset (the
    blockers then run N/A with gaps). Invalid JSON or invalid values ->
    ProfileError, loudly. Unknown vehicle names are KEPT (the human
    attestation wins over our reference list) with a warning."""
    path = profile_path(slug)
    if not path.exists():
        return PartneringProfile(), list(ATTESTABLE_FIELDS), []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProfileError(f"{path.name}: unreadable or invalid JSON ({e})") from e
    if not isinstance(raw, dict):
        raise ProfileError(f"{path.name}: top level must be a JSON object")
    provided = {k for k in raw if not k.startswith("_")}
    data = {k: v for k, v in raw.items() if not k.startswith("_")}
    try:
        profile = PartneringProfile(**data)
    except (ValidationError, ValueError) as e:
        raise ProfileError(f"{path.name}: {e}") from e

    unset = [f for f in ATTESTABLE_FIELDS if f not in provided]
    warnings: list[str] = []
    known = _known_vehicle_names()
    for v in profile.vehicles_held:
        if known and v.strip().lower() not in known:
            warnings.append(
                f"vehicle '{v}' not in data/reference/vehicles.json; kept as attested")
    for naics in profile.size_status_by_naics:
        if not (naics.isdigit() and len(naics) == 6):
            warnings.append(f"size_status_by_naics key '{naics}' is not a 6-digit NAICS")
    return profile, unset, warnings
