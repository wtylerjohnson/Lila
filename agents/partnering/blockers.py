"""Blocker detection; per pursuit, deterministic, from existing data plus
the human-attested profile. Never a guess: a check whose inputs are missing
degrades to N/A with an explicit gap.

The contract downstream code relies on:
    detect_blockers(...) -> list[Blocker]   (fired and N/A entries only;
                                             a check that affirms "no issue"
                                             emits nothing)
    has_partnering_play(blockers)           -> any fired blocker
No fired blocker means NO partnering analysis for that pursuit; this
feature never suggests teaming where the client can simply prime.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.partnering.config import (
    JV_STRUCTURE_FLAG, LIMITATIONS_ON_SUBCONTRACTING_FLAG, SCALE_MULTIPLE,
)
from agents.partnering.profile import PartneringProfile

SUB_TO_PRIME = "SUB_TO_PRIME"
PRIME_WITH_SUBS = "PRIME_WITH_SUBS"
JV_OR_TEAMING_ARRANGEMENT = "JV_OR_TEAMING_ARRANGEMENT"

BlockerKind = Literal["VEHICLE", "ELIGIBILITY", "SCALE", "INCUMBENCY", "ADJACENCY"]


class Blocker(BaseModel):
    kind: BlockerKind
    fired: bool
    na: bool = False
    inverse: bool = Field(
        default=False,
        description="ELIGIBILITY only: the client QUALIFIES for the set-aside "
                    "; an opportunity to prime with subs, not an obstacle")
    directions: list[str] = Field(default_factory=list)
    basis: str
    review_flags: list[str] = Field(default_factory=list)
    gap: Optional[str] = None


def has_partnering_play(blockers: list[Blocker]) -> bool:
    return any(b.fired for b in blockers)


# ── set-aside recognition ────────────────────────────────────────────────────
# (label substring or SAM typeOfSetAside code) -> (requires_small, required_cert)
# Order matters: EDWOSB must match before WOSB. Unrecognized set-asides go
# N/A with a review flag; never guessed.
_SET_ASIDE_RULES: list[tuple[tuple[str, ...], tuple[str, ...], Optional[str]]] = [
    # (label substrings, codes, required certification)
    (("8(A)",), ("8A", "8AN"), "8(a)"),
    (("HUBZONE",), ("HZC", "HZS"), "HUBZone"),
    (("SERVICE-DISABLED VETERAN",), ("SDVOSBC", "SDVOSBS"), "SDVOSB"),
    (("EDWOSB", "ECONOMICALLY DISADVANTAGED WOSB"), ("EDWOSB", "EDWOSBSS"), "EDWOSB"),
    (("WOMEN-OWNED SMALL BUSINESS", "WOSB"), ("WOSB", "WOSBSS"), "WOSB"),
    (("TOTAL SMALL BUSINESS", "PARTIAL SMALL BUSINESS", "SMALL BUSINESS SET-ASIDE",
      "SMALL BUSINESS SET ASIDE - TOTAL"),
     ("SBA", "SBP"), None),
]
_UNRESTRICTED = ("NO SET ASIDE", "NONE")


def parse_set_aside(label: Optional[str], code: Optional[str]) -> Optional[dict]:
    """None -> unrestricted / no set-aside. Otherwise {requires_small,
    required_cert, recognized}; recognized=False means a set-aside exists
    but is not in our rule table (N/A path, never a guess)."""
    lab = (label or "").strip().upper()
    cod = (code or "").strip().upper()
    if not lab and not cod:
        return None
    label_unrestricted = any(u in lab for u in _UNRESTRICTED)
    code_unrestricted = cod in _UNRESTRICTED
    if ((label_unrestricted and (not cod or code_unrestricted))
            or (not lab and code_unrestricted)):
        return None
    for substrings, codes, cert in _SET_ASIDE_RULES:
        if (any(s in lab for s in substrings) or lab in codes
                or (cod and cod in codes)):
            return {"requires_small": True, "required_cert": cert, "recognized": True}
    return {"requires_small": None, "required_cert": None, "recognized": False}


# ── the four checks ──────────────────────────────────────────────────────────

def _vehicle_blocker(pursuit, profile: PartneringProfile,
                     unset: list[str]) -> Optional[Blocker]:
    dossier = pursuit.dossier or {}
    if not pursuit.dossier:
        return Blocker(kind="VEHICLE", fired=False, na=True,
                       basis="vehicle requirement unknown: no dossier built for "
                             "this pursuit",
                       gap=f"pursuit #{pursuit.rank}: vehicle blocker N/A; run the "
                           "dossier step to learn the vehicle requirement")
    stated = (dossier.get("vehicle") or "").strip()
    if not stated:
        return None  # affirmed full-and-open: no vehicle required, no blocker
    if "vehicles_held" in unset:
        return Blocker(kind="VEHICLE", fired=False, na=True,
                       basis=f"notice implies vehicle '{stated}' but the client's "
                             "held vehicles are unattested",
                       gap=f"pursuit #{pursuit.rank}: vehicle blocker N/A; attest "
                           "vehicles_held in the partnering profile")
    low = stated.lower()
    held = [v for v in profile.vehicles_held if v.strip().lower() in low
            or low in v.strip().lower()]
    if held:
        return None  # client holds the paper; no blocker
    return Blocker(
        kind="VEHICLE", fired=True, directions=[SUB_TO_PRIME],
        basis=f"notice implies vehicle '{stated}' and the client attests "
              f"holding {profile.vehicles_held or 'no vehicles'}; access "
              "runs through a holder")


def _eligibility_blocker(pursuit, profile: PartneringProfile,
                         unset: list[str]) -> Optional[Blocker]:
    rule = parse_set_aside(getattr(pursuit, "set_aside", None),
                           getattr(pursuit, "set_aside_code", None))
    if rule is None:
        return None  # unrestricted: eligibility poses no blocker
    label = getattr(pursuit, "set_aside", None) or getattr(pursuit, "set_aside_code", "")
    if not rule["recognized"]:
        return Blocker(kind="ELIGIBILITY", fired=False, na=True,
                       basis=f"set-aside '{label}' is not in the recognition "
                             "table; never guessed",
                       review_flags=[f"unrecognized set-aside '{label}'; human review"],
                       gap=f"pursuit #{pursuit.rank}: eligibility blocker N/A; "
                           f"unrecognized set-aside '{label}'")
    cert = rule["required_cert"]
    # a required certification the client attests NOT holding excludes them
    # outright, whatever their size
    if cert and "certifications" not in unset and cert not in profile.certifications:
        return Blocker(
            kind="ELIGIBILITY", fired=True, directions=[SUB_TO_PRIME],
            basis=f"set-aside '{label}' requires {cert}; the client attests "
                  "not holding it; prime path closed, subcontract to a "
                  f"{cert} prime",
            review_flags=[LIMITATIONS_ON_SUBCONTRACTING_FLAG])
    size = profile.size_status_by_naics.get(pursuit.naics or "")
    if "size_status_by_naics" in unset or size is None:
        flags = []
        if cert and "certifications" not in unset and cert in profile.certifications:
            flags.append(f"{cert} attested; attest size status for NAICS "
                         f"{pursuit.naics} to unlock the prime path")
        return Blocker(kind="ELIGIBILITY", fired=False, na=True,
                       basis=f"set-aside '{label}' requires small in NAICS "
                             f"{pursuit.naics}; client size status unattested",
                       review_flags=flags,
                       gap=f"pursuit #{pursuit.rank}: eligibility blocker N/A; attest "
                           f"size_status_by_naics['{pursuit.naics}'] in the "
                           "partnering profile")
    if size == "other_than_small":
        return Blocker(
            kind="ELIGIBILITY", fired=True, directions=[SUB_TO_PRIME],
            basis=f"set-aside '{label}' requires small in NAICS {pursuit.naics}; "
                  "the client attests other_than_small; prime path closed",
            review_flags=[LIMITATIONS_ON_SUBCONTRACTING_FLAG])
    if cert and ("certifications" in unset):
        return Blocker(kind="ELIGIBILITY", fired=False, na=True,
                       basis=f"set-aside '{label}' requires {cert}; certifications "
                             "unattested",
                       gap=f"pursuit #{pursuit.rank}: eligibility blocker N/A; attest "
                           "certifications in the partnering profile")
    # the INVERSE: the client qualifies; large primes are excluded, so the
    # client can prime and recruit subs
    return Blocker(
        kind="ELIGIBILITY", fired=True, inverse=True, directions=[PRIME_WITH_SUBS],
        basis=f"client qualifies for set-aside '{label}' (small in NAICS "
              f"{pursuit.naics}{', ' + cert + ' attested' if cert else ''}); "
              "primes excluded by the set-aside cannot bid; prime with subs",
        review_flags=[LIMITATIONS_ON_SUBCONTRACTING_FLAG])


def _scale_blocker(pursuit, profile: PartneringProfile, unset: list[str],
                   naics_median_award: Optional[float]) -> Optional[Blocker]:
    if "award_band" in unset or profile.award_band is None \
            or profile.award_band.ceiling_usd is None:
        return Blocker(kind="SCALE", fired=False, na=True,
                       basis="scale check needs the attested award-band ceiling",
                       gap=f"pursuit #{pursuit.rank}: scale blocker N/A; attest "
                           "award_band.ceiling_usd in the partnering profile")
    if not isinstance(naics_median_award, (int, float)) or naics_median_award <= 0:
        return Blocker(kind="SCALE", fired=False, na=True,
                       basis="scale check needs the NAICS median award "
                             "(MARKET PROXY) and none is on record",
                       gap=f"pursuit #{pursuit.rank}: scale blocker N/A; no "
                           "USAspending median for this NAICS")
    ceiling = profile.award_band.ceiling_usd
    threshold = ceiling * SCALE_MULTIPLE
    if naics_median_award <= threshold:
        return None
    return Blocker(
        kind="SCALE", fired=True,
        directions=[SUB_TO_PRIME, JV_OR_TEAMING_ARRANGEMENT],
        basis=f"NAICS median award ${naics_median_award:,.0f} exceeds the "
              f"attested ceiling ${ceiling:,.0f} × SCALE_MULTIPLE "
              f"{SCALE_MULTIPLE:g} (= ${threshold:,.0f}); MARKET PROXY, "
              "notice carries no value",
        review_flags=[JV_STRUCTURE_FLAG])


def _incumbency_blocker(pursuit) -> Optional[Blocker]:
    dim = next((d for d in pursuit.grade.dimensions
                if d.dimension == "incumbency"), None)
    if dim is None or dim.score is None or dim.score > 1.0:
        return None  # high tier only (score 1.0 = named recompete); the
        #             dimension's own N/A is already gapped by grading
    return Blocker(
        kind="INCUMBENCY", fired=True, directions=[SUB_TO_PRIME],
        basis=f"incumbency risk at high tier: {dim.basis}; the incumbent-sub "
              "play applies")


def _adjacency_blocker(pursuit) -> Optional[Blocker]:
    """A monitor-grade notice is, by definition, one the client is TRACKING
    rather than priming (adjacent mission, wrong buyer, framework vehicle,
    sole-sourced to an incumbent). So the teaming question is always live:
    who wins this kind of work that the client could sub to. This fires only
    for monitor-verdict notices; pursue pursuits are untouched (they already
    fire real blockers or none)."""
    if getattr(pursuit, "verdict", None) != "monitor":
        return None
    return Blocker(
        kind="ADJACENCY", fired=True, directions=[SUB_TO_PRIME],
        basis="monitor-grade adjacency: the client is tracking this, not "
              "priming it; the teaming path is to sub to a prime that wins "
              "this kind of work")


def detect_blockers(pursuit, profile: PartneringProfile, unset: list[str], *,
                    naics_median_award: Optional[float] = None) -> list[Blocker]:
    """Fired and N/A entries only; checks that affirm 'no issue' are silent."""
    out = [
        _adjacency_blocker(pursuit),
        _vehicle_blocker(pursuit, profile, unset),
        _eligibility_blocker(pursuit, profile, unset),
        _scale_blocker(pursuit, profile, unset, naics_median_award),
        _incumbency_blocker(pursuit),
    ]
    return [b for b in out if b is not None]
