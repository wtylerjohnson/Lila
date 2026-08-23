"""Internal REPORT_CONTRACT loader: the one legacy band-list source (§1).

docs/REPORT_CONTRACT.md is the internal compatibility contract for this report
family. It cannot override the external product slots. This module parses its
§1 band table and §0 L7 banned-vocabulary law so validate.py, compose.py
and render.py all derive their band lists from the same bytes. The old
triplication (GOLDEN_BAND_ORDER / doctrine.SECTIONS / renderer literals) is
retired: a band exists because the contract says so, numbered by its position
in the table, and nothing else may add one.

Parsing is deliberately strict and loud: a malformed table is a build error,
never a silently shorter report.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH_ENV = "LILA_REPORT_CONTRACT_PATH"
_CONTRACT_DEFAULT = _ROOT / "docs" / "REPORT_CONTRACT.md"

# §0 L7: the contract names the base banned terms and directs extensions to
# live in the validator, not the contract. The validator unions both.
_L7_LINE_RE = re.compile(
    r"L7 VOCABULARY:.*?Banned invented terms:\s*(?P<terms>.*?)\(Extend",
    re.S)

_ROW_RE = re.compile(
    r"^\|\s*(?P<num>[0-9]{2}|--)\s*\|\s*(?P<bid>[a-z][a-z-]*)\s*\|"
    r"\s*(?P<name>[^|]+?)\s*\|\s*(?P<required>[^|]+?)\s*\|"
    r"\s*(?P<zero>[^|]+?)\s*\|\s*$")


class ContractError(RuntimeError):
    """The contract file is missing or its §1 table does not parse."""


@dataclass(frozen=True)
class ContractBand:
    number: Optional[int]     # None for the unnumbered footer row
    band_id: str
    name: str
    required: bool
    zero_state: str


_cache: dict[str, tuple] = {}


def _contract_path() -> Path:
    return Path(os.environ.get(CONTRACT_PATH_ENV, "") or _CONTRACT_DEFAULT)


def load_contract_bands() -> tuple[ContractBand, ...]:
    """§1's band table, in table order. Position IS the number authority:
    a row whose printed number disagrees with its position fails loudly
    (the contract's own no-stale-numerals law applied to itself)."""
    path = _contract_path()
    key = str(path)
    if key in _cache:
        return _cache[key]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"REPORT_CONTRACT.md unreadable at {path}: {exc}")
    section = re.search(r"## 1\. BAND SEQUENCE.*?(?=\n## )", text, re.S)
    if not section:
        raise ContractError("contract carries no '## 1. BAND SEQUENCE' section")
    bands: list[ContractBand] = []
    position = 0
    for line in section.group(0).splitlines():
        m = _ROW_RE.match(line.strip())
        if not m or m.group("bid") == "id":
            continue
        printed = m.group("num")
        number: Optional[int]
        if printed == "--":
            number = None
        else:
            number = int(printed)
            if number != position:
                raise ContractError(
                    f"band {m.group('bid')!r} printed number {printed} "
                    f"disagrees with its table position {position:02d}; "
                    "numbering is position-derived, fix the table")
            position += 1
        bands.append(ContractBand(
            number=number,
            band_id=m.group("bid"),
            name=m.group("name").strip(),
            required="always" in m.group("required").casefold(),
            zero_state=m.group("zero").strip(),
        ))
    if len(bands) < 3:
        raise ContractError(
            f"contract band table parsed only {len(bands)} rows; refusing")
    ids = [b.band_id for b in bands]
    if len(set(ids)) != len(ids):
        raise ContractError(f"contract band ids repeat: {ids}")
    result = tuple(bands)
    _cache[key] = result
    return result


def contract_band_ids(*, numbered_only: bool = False) -> tuple[str, ...]:
    return tuple(b.band_id for b in load_contract_bands()
                 if not (numbered_only and b.number is None))


def contract_section_ids() -> tuple[str, ...]:
    """The <section class="sb-band" id=...> sequence the artifact must carry:
    every numbered band except the hero (which renders as the sb-hero
    section, band 00 by contract, not an sb-band frame). The footer renders
    as a footer element, not a band."""
    return tuple(b.band_id for b in load_contract_bands()
                 if b.number is not None and b.band_id != "hero")


def banned_vocabulary() -> tuple[str, ...]:
    """§0 L7's base banned terms, parsed from the contract's own sentence."""
    path = _contract_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"REPORT_CONTRACT.md unreadable at {path}: {exc}")
    m = _L7_LINE_RE.search(text)
    if not m:
        raise ContractError("contract carries no parseable L7 vocabulary law")
    terms = [t.strip().rstrip(".") for t in m.group("terms")
             .replace("\n", " ").split(",")]
    out = tuple(t for t in (re.sub(r"\s+", " ", t) for t in terms) if t)
    if not out:
        raise ContractError("L7 banned-term list parsed empty")
    return out


def band(band_id: str) -> ContractBand:
    for row in load_contract_bands():
        if row.band_id == band_id:
            return row
    raise ContractError(f"unknown contract band: {band_id}")


# --------------------------------------------------------------------------- #
# §2 band specs: the laws a band's own contract block states
# --------------------------------------------------------------------------- #
# A posture token is a run of two or more ALL-CAPS words (the house label
# grammar: "NO CONTACTS RESOLVED", "TARGETING · NONE"). Parsing them out of
# the contract rather than restating them in the validator is what makes a
# renamed posture a contract edit instead of a silent drift.
_POSTURE_RE = re.compile(r"\b([A-Z][A-Z]+(?:[ ·]+[A-Z][A-Z]+)+)\b")
_BACKTICK_RE = re.compile(r"`([a-z_]+)`")


def band_spec_text(band_id: str) -> str:
    """The §2 block for one band, verbatim, or '' when §2 has no block."""
    path = _contract_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"REPORT_CONTRACT.md unreadable at {path}: {exc}")
    row = band(band_id)
    if row.number is None:
        return ""
    heading = re.search(
        rf"^### {row.number:02d} .*?$(?P<body>.*?)(?=^### |\n---)",
        text, re.S | re.M)
    return heading.group("body") if heading else ""


def band_law(band_id: str, label: str) -> str:
    """One named bullet of a band's §2 laws ('JOIN LAW', 'ZERO STATE', ...).

    Loud when the law is gone: a validator check that silently stops finding
    the rule it enforces is worse than no check.
    """
    body = band_spec_text(band_id)
    m = re.search(rf"^- {re.escape(label)}:(?P<law>.*?)(?=^- |\Z)",
                  body, re.S | re.M)
    if not m:
        raise ContractError(
            f"contract §2/{band_id} states no {label!r} law")
    return m.group("law")


def band_postures(band_id: str, label: str = "ZERO STATE") -> tuple:
    """The rendered posture tokens a band's named law itself names."""
    found = _POSTURE_RE.findall(band_law(band_id, label))
    out = tuple(dict.fromkeys(" ".join(t.split()) for t in found))
    if not out:
        raise ContractError(
            f"contract §2/{band_id} {label!r} names no posture token")
    return out


def band_provenance_classes(band_id: str) -> tuple:
    """The provenance classes a band's PROVENANCE law admits, in order."""
    out = tuple(dict.fromkeys(_BACKTICK_RE.findall(
        band_law(band_id, "PROVENANCE"))))
    if not out:
        raise ContractError(
            f"contract §2/{band_id} PROVENANCE law names no class")
    return out
