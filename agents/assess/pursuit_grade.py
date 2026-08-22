"""Pursuit grades; reproducible, rubric-computed, never vibes.

grade(inputs, as_of) is a PURE function: the same artifact inputs always produce
the same grade, and the worksheet shows exactly how. Five dimensions, each
scored 0-4 from deterministic inputs; a dimension whose input is missing scores
N/A and its weight redistributes proportionally across the dimensions that DO
have evidence; the worksheet says so out loud rather than hiding the gap.

Dimension inputs (today's honest sources):
  fit        ; most-specific available judgment, mapped to one canonical scale:
                dossier fit_verdict > qualify FitRationale.verdict > triage verdict
  timing     ; days-to-due computed from response_deadline vs as_of
  vehicle    ; notice's stated vehicle joined against the verified catalog
                (data/reference/vehicles.json); no vehicle stated = full-and-open
  incumbency ; recompete/incumbent evidence (contract_awards) when present;
                N/A otherwise (SAM data-role quota pending; decision 2026-07-06)
  dollar     ; market proxy: NAICS median award from USAspending, LABELED a
                proxy; notice-level values do not exist in SAM extract data

Letter bands: A >= 3.5, B >= 2.75, C >= 2.0, D >= 1.25, F below.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

WEIGHTS = {"fit": 0.35, "timing": 0.25, "vehicle": 0.20,
           "incumbency": 0.10, "dollar": 0.10}

_LETTER_BANDS = [(3.5, "A"), (2.75, "B"), (2.0, "C"), (1.25, "D")]

# one canonical fit scale (0-4), mapped from the three LLM vocabularies
_FIT_SCALES = {
    "dossier": {"strong_fit": 4, "conditional_fit": 3, "stretch": 2},
    "qualify": {"strong_fit": 4, "partial_fit": 3, "weak_fit": 1, "no_fit": 0},
    "triage": {"pursue": 4, "monitor": 2, "discard": 0},
}


class DimensionScore(BaseModel):
    dimension: str
    score: Optional[float] = Field(default=None, description="0-4; None = N/A")
    weight: float = Field(description="nominal rubric weight")
    effective_weight: float = Field(description="after N/A redistribution")
    basis: str = Field(description="the input values and which source supplied them")
    na_reason: Optional[str] = None


class PursuitGrade(BaseModel):
    letter: str
    score: float = Field(description="weighted 0-4 over available dimensions")
    dimensions: list[DimensionScore]
    scored_dimensions: int
    as_of: date


def _fit(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    for source, field in (("dossier", "dossier_fit"), ("qualify", "qualify_fit"),
                          ("triage", "triage_verdict")):
        raw = (inputs.get(field) or "").strip().lower().replace(" ", "_")
        if raw and raw in _FIT_SCALES[source]:
            return (float(_FIT_SCALES[source][raw]),
                    f"{source} says '{raw}' -> {_FIT_SCALES[source][raw]}/4 "
                    f"(precedence: dossier > qualify > triage)", None)
    return None, "no fit judgment on record", "no triage/qualify/dossier verdict available"


def _timing(inputs: dict, as_of: date) -> tuple[Optional[float], str, Optional[str]]:
    deadline = inputs.get("response_deadline")
    if isinstance(deadline, str):
        try:
            deadline = date.fromisoformat(deadline[:10])
        except ValueError:
            deadline = None
    if not isinstance(deadline, date):
        return None, "no response deadline on the notice", "undated notice"
    days = (deadline - as_of).days
    if days < 0:
        return 0.0, f"deadline {deadline.isoformat()} passed {-days}d ago -> 0/4", None
    if days <= 7:
        return 2.0, f"{days}d to due; actionable only with an immediate bid decision -> 2/4", None
    if days <= 45:
        return 4.0, f"{days}d to due; inside the capture sweet spot (8-45d) -> 4/4", None
    if days <= 120:
        return 3.0, f"{days}d to due; runway to position (46-120d) -> 3/4", None
    return 2.0, f"{days}d to due; distant; priorities may shift (>120d) -> 2/4", None


def _vehicle(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    stated = (inputs.get("vehicle") or "").strip()
    catalog = inputs.get("vehicle_catalog") or []  # [{name, verified, software_vendor_path}]
    if not stated:
        return 3.0, "no vehicle stated; full-and-open path, no contract paper required -> 3/4", None
    low = stated.lower()
    for v in catalog:
        if (v.get("name") or "").lower() in low and v.get("name"):
            if v.get("verified") and v.get("software_vendor_path"):
                return 4.0, (f"stated vehicle matches verified catalog entry '{v['name']}' "
                             f"with a vendor path -> 4/4"), None
            return 2.0, (f"stated vehicle matches catalog entry '{v['name']}' but the "
                         f"path is unverified -> 2/4"), None
    return 1.0, f"vehicle '{stated[:40]}' stated but not in the verified catalog -> 1/4", None


def _incumbency(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    recompete = inputs.get("recompete")  # {awardee, completion} when matched
    if isinstance(recompete, dict) and recompete.get("awardee"):
        return 1.0, (f"recompete of {recompete['awardee']} (completion "
                     f"{recompete.get('completion', '?')}); incumbent advantage -> 1/4"), None
    incumbents = inputs.get("naics_incumbents") or []
    if incumbents:
        n = len(incumbents)
        score = 2.0 if n >= 4 else 3.0
        return score, (f"{n} repeat incumbents in this NAICS market "
                       f"({', '.join(incumbents[:3])}…) -> {score:g}/4 "
                       f"(market-level signal, not pursuit-level)"), None
    return None, "no incumbency evidence on record", \
        "contract_awards data unavailable (SAM quota); dimension stays N/A per 2026-07-06 decision"


def _dollar(inputs: dict) -> tuple[Optional[float], str, Optional[str]]:
    median = inputs.get("naics_median_award")
    if not isinstance(median, (int, float)) or median <= 0:
        return None, "no market dollar evidence for this NAICS", "no USAspending median available"
    if median < 250_000:
        score = 2.0
        band = "small-dollar market"
    elif median <= 5_000_000:
        score = 4.0
        band = "mid-market sweet spot"
    else:
        score = 3.0
        band = "large-prime territory; teaming likely"
    return score, (f"NAICS median award ${median:,.0f} ({band}) -> {score:g}/4 "
                   f"; MARKET PROXY, notice carries no value"), None


_SCORERS = {"fit": _fit, "vehicle": _vehicle, "incumbency": _incumbency, "dollar": _dollar}


def grade(inputs: dict[str, Any], as_of: Optional[date] = None) -> PursuitGrade:
    """Compute one pursuit's grade + worksheet. `inputs` is the joined record
    the document composer assembles; every value must trace to an artifact."""
    as_of = as_of or date.today()
    raw: dict[str, tuple[Optional[float], str, Optional[str]]] = {
        name: fn(inputs) for name, fn in _SCORERS.items()
    }
    raw["timing"] = _timing(inputs, as_of)

    available = {k for k, (s, _b, _r) in raw.items() if s is not None}
    live_weight = sum(WEIGHTS[k] for k in available) or 1.0

    dims, total = [], 0.0
    for name in ("fit", "timing", "vehicle", "incumbency", "dollar"):
        score, basis, na = raw[name]
        eff = (WEIGHTS[name] / live_weight) if name in available else 0.0
        if score is not None:
            total += score * eff
        dims.append(DimensionScore(dimension=name, score=score, weight=WEIGHTS[name],
                                   effective_weight=round(eff, 4), basis=basis,
                                   na_reason=na))

    letter = next((mark for floor, mark in _LETTER_BANDS if total >= floor), "F")
    return PursuitGrade(letter=letter, score=round(total, 3), dimensions=dims,
                        scored_dimensions=len(available), as_of=as_of)
