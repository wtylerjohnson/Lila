"""Aesthetic template library (client-approved Riverbed rework, 2026-07-31).

Extracted from the CREDIBLE_SOURCE_MIX revision builder and generalized: the
rework's Riverbed fixture data stayed behind; the SHAPES and the MARK
RESOLVER are the library. render.py draws markup from here, and styles come
only from report.css (sibling file) plus data/reference/design_tokens.json.
This package supersedes the Playfair/IBM Plex house style.

LAWS (design_tokens.json .laws, same standing as the linkage law):
  - mark-slot-never-empty: every named agency or organisation renders a
    mark: the official catalogued mark when one is on file, else a
    MONOGRAM (first letters of words, max 3, uppercase). A monogram is a
    DISCLOSED GAP: callers pass a collector and the render receipts every
    gap; a silent blank ships nothing and a guessed logo ships a lie.
  - claims bind to primary records: claim() refuses an unbound sentence.
  - no em dashes in any output-reachable string; band numbers stay
    position-derived (validate.py owns both).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

_ROOT = Path(__file__).resolve().parents[2]
SEALS_PATH_ENV = "LILA_SEALS_PATH"
TOKENS_PATH_ENV = "LILA_DESIGN_TOKENS_PATH"
_SEALS_DEFAULT = _ROOT / "data" / "reference" / "seals.json"
_TOKENS_DEFAULT = _ROOT / "data" / "reference" / "design_tokens.json"
_CSS_PATH = Path(__file__).resolve().parent / "report.css"

_cache: dict[str, Any] = {}


def _load_json(path: Path) -> dict:
    key = str(path)
    if key not in _cache:
        try:
            _cache[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache[key] = {}
    return _cache[key]


def seal_entries() -> dict:
    """The packaged mark catalog ({key: {src, alt}}), env-overridable for
    hermetic tests. A missing catalog degrades every mark to a disclosed
    monogram gap; it never crashes a press."""
    import os
    path = Path(os.environ.get(SEALS_PATH_ENV, "") or _SEALS_DEFAULT)
    entries = (_load_json(path) or {}).get("entries") or {}
    # a non-image src (an HTML error page fetched as an icon) can never
    # render as a mark; it reads as absent and monograms honestly
    return {k: v for k, v in entries.items()
            if str(v.get("src", "")).startswith("data:image/")}


def design_tokens() -> dict:
    import os
    path = Path(os.environ.get(TOKENS_PATH_ENV, "") or _TOKENS_DEFAULT)
    return _load_json(path) or {}


def report_css() -> str:
    key = f"css:{_CSS_PATH}"
    if key not in _cache:
        try:
            _cache[key] = _CSS_PATH.read_text(encoding="utf-8")
        except OSError:
            _cache[key] = ""
    return _cache[key]


def tokens_css() -> str:
    """The design tokens as CSS custom properties: the one style source the
    stylesheet and any inline fallback both read."""
    tokens = design_tokens()
    lines = [f"--t-{name}:{value};" for name, value in
             (tokens.get("color") or {}).items()]
    stack = (tokens.get("type") or {}).get("stack")
    mono = (tokens.get("type") or {}).get("mono")
    if stack:
        lines.append(f"--t-stack:{stack};")
    if mono:
        lines.append(f"--t-mono:{mono};")
    return ":root{" + "".join(lines) + "}"


def aesthetic_style_block() -> str:
    """One <style> travelling with the content region (the skeleton stays
    byte-verbatim by doctrine): tokens first, then the library stylesheet."""
    return ("<style id=\"lila-aesthetic\">"
            + tokens_css() + "\n" + report_css() + "</style>")


# --------------------------------------------------------------------------- #
# the mark resolver
# --------------------------------------------------------------------------- #
def monogram_initials(label: str) -> str:
    """design_tokens .components.monogram rule: first letters of words,
    max 3, uppercase."""
    words = [w for w in re.split(r"[\s.,]+", str(label or "")) if w]
    return "".join(w[0] for w in words)[:3].upper() or "FED"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


# G2 (2026-08-03): USAspending display names resolve to catalogued marks
# through normalization (casefold, punctuation/suffix strip) plus explicit
# subtier-to-parent aliases, so "U.S. Customs and Border Protection" finds
# agency:cbp without a hand map per spelling.
_AGENCY_ALIASES = {
    "internal revenue service": "irs",
    "department of veterans affairs": "va",
    "veterans affairs": "va",
    "general services administration": "gsa",
    "federal acquisition service": "gsa",
    "national aeronautics and space administration": "nasa",
    "transportation security administration": "tsa",
    "us coast guard": "uscg",
    "coast guard": "uscg",
    "us customs and border protection": "cbp",
    "customs and border protection": "cbp",
    "office of the comptroller of the currency": "occ",
    "comptroller of the currency": "occ",
    "social security administration": "ssa",
    "us citizenship and immigration services": "uscis",
    "citizenship and immigration services": "uscis",
    "agency for international development": "usaid",
    "office of procurement operations": "dhs-opo",
    "department of energy": "doe",
    "department of labor": "dol",
    "department of agriculture": "usda",
    "department of commerce": "commerce",
    "national science foundation": "nsf",
    "defense information systems agency": "disa",
    "defense logistics agency": "dla",
    "defense health agency": "dha",
    "centers for disease control and prevention": "cdc",
    "centers for medicare and medicaid services": "cms",
    "federal bureau of investigation": "fbi",
    "federal aviation administration": "faa",
    "bureau of land management": "blm",
    "national institutes of health": "nih",
    "us patent and trademark office": "uspto",
    "federal emergency management agency": "fema",
    "us immigration and customs enforcement": "ice",
    "us secret service": "usss",
    "government accountability office": "gao",
    "gao except comptroller general": "gao",
    "office of personnel management": "opm",
    "small business administration": "sba",
    "environmental protection agency": "epa",
    "federal communications commission": "fcc",
    "national oceanic and atmospheric administration": "noaa",
    "bureau of fiscal service": "fiscal-service",
    "executive office of president": "eop",
    "nuclear regulatory commission": "nrc",
    "securities and exchange commission": "sec",
    "us securities and exchange commission": "sec",
    "national transportation safety board": "ntsb",
    "pension benefit guaranty corporation": "pbgc",
    "department of defense": "dod",
    "department of state": "state",
    "department of justice": "doj",
    "department of homeland security": "dhs",
    "department of treasury": "treasury",
    "department of transportation": "dot",
    "department of interior": "doi",
    "department of education": "ed",
    "department of health and human services": "hhs",
    "department of housing and urban development": "hud",
    "department of navy": "navy",
    "department of army": "army",
    "department of air force": "air-force",
    "us marine corps": "marines",
    "us space force": "space-force",
    "us agency for global media": "usagm",
    "united states agency for global media": "usagm",
    "equal employment opportunity commission": "eeoc",
}


def _normalize_agency(label: str) -> str:
    value = re.sub(r"[.,()]+", " ", str(label or "").casefold())
    value = re.sub(r"\bu\s+s\b", "us", value)
    value = re.sub(r"\bu\.?s\.?\b", "us", value)
    words = [w for w in value.split() if w not in ("the",)]
    return " ".join(words)


def _agency_key_candidates(label: str) -> list[str]:
    """agency:<abbr> candidates for a buying-agency display name, derived
    through the curated agency catalog (the one abbreviation owner), plus a
    full-name slug fallback."""
    out: list[str] = []
    value = " ".join(str(label or "").split())
    if not value:
        return out
    normalized = _normalize_agency(value)
    aliases_normalized = {_normalize_agency(k): v
                          for k, v in _AGENCY_ALIASES.items()}
    if normalized in aliases_normalized:
        out.append(f"agency:{aliases_normalized[normalized]}")
    try:
        from tools.agency_scope import _department_abbrs
        for abbr in sorted(_department_abbrs(value) or ()):
            out.append(f"agency:{_slug(abbr)}")
    except Exception:  # noqa: BLE001 - catalog trouble degrades to monogram
        pass
    try:
        from tools.agencies import AGENCIES
        folded = value.casefold()
        for row in AGENCIES:
            if str(row.get("name", "")).casefold() == folded \
                    or str(row.get("abbr", "")).casefold() == folded:
                out.append(f"agency:{_slug(row['abbr'])}")
    except Exception:  # noqa: BLE001
        pass
    out.append(f"agency:{_slug(value)}")
    # SUBTIER -> PARENT FALLBACK (2026-08-04): a bureau with no seal of its
    # own resolves to its parent department's official seal (State's
    # bureaus render the State seal; Defense components without their own
    # mark render DoD). The parent seal IS an official mark.
    try:
        from agents.golden_press.decision_rules import _department_key
        parent = _department_key(value)
        _PARENT_KEYS = {
            "USDA": "usda", "DOC": "commerce", "DOD": "dod", "ED": "ed",
            "DOE": "doe", "HHS": "hhs", "DHS": "dhs", "HUD": "hud",
            "DOI": "doi", "DOJ": "doj", "DOL": "dol", "DOS": "state",
            "DOT": "dot", "TREAS": "treasury", "VA": "va", "GSA": "gsa",
            "NASA": "nasa", "SSA": "ssa", "EPA": "epa", "NSF": "nsf",
            "OPM": "opm", "SBA": "sba", "STATE": "state",
            "TREASURY": "treasury", "DEFENSE": "dod",
        }
        if parent:
            mapped = _PARENT_KEYS.get(str(parent).upper())
            out.append(f"agency:{mapped or _slug(str(parent))}")
    except Exception:  # noqa: BLE001
        pass
    normalized_words = set(normalized.split())
    if {"defense", "dod"} & normalized_words:
        out.append("agency:dod")
    if "navy" in normalized_words:
        out.insert(0, "agency:navy")
    if "army" in normalized_words:
        out.insert(0, "agency:army")
    if {"air"} <= normalized_words and "force" in normalized_words:
        out.insert(0, "agency:air-force")
    return list(dict.fromkeys(out))


# PRODUCT -> VENDOR marks (2026-08-04): a product line renders its
# vendor's mark (PolicyNote is FiscalNote's; SteelHead is Riverbed's).
# Deterministic prefix/name table; extending it is data, not code.
_PRODUCT_VENDOR = {
    "policynote": "fiscalnote", "votervoice": "fiscalnote",
    "cq": "cq-roll-call", "roll call": "cq-roll-call",
    "euit": "fiscalnote", "frontierview": "fiscalnote",
    "curate": "fiscalnote", "fireside": "fiscalnote",
    "pacbuilder": "fiscalnote",
    "bloomberg government": "bloomberg-government",
    "bloomberg industry": "bloomberg-industry",
    "politico": "politico", "politico pro": "politico",
    "red hat": "red-hat", "steelhead": "riverbed",
    "aternity": "riverbed", "riverbed": "riverbed",
    "netscout": "netscout", "apexportal": "apexanalytix",
    "qubiton": "apexanalytix", "bankpro": "apexanalytix",
    "smartvm": "apexanalytix",
    "global bank account confidence score": "apexanalytix",
    "suse": "suse", "canonical": "canonical", "oracle": "oracle",
    "vmware": "vmware", "puppet": "puppet", "chef": "chef",
    "dynatrace": "dynatrace", "solarwinds": "solarwinds",
    "thousandeyes": "thousandeyes", "quorum": "quorum",
    "lexisnexis": "lexisnexis", "td synnex": "td-synnex",
    "dlt solutions": "dlt", "red river": "red-river",
    "general dynamics": "gdit", "northrop": "northrop",
    "international business machines": "ibm", "ibm": "ibm",
    "accenture": "accenture-federal", "caci": "caci",
    "science applications international": "saic", "saic": "saic",
    "carahsoft": "carahsoft", "immixgroup": "immixgroup",
    "immixtechnology": "immixgroup", "ec america": "immixgroup",
    "cdw": "cdw-g", "shi": "shi", "insight": "insight",
    "anduril": "anduril", "palantir": "palantir", "optiv": "optiv",
    "leidos": "leidos", "mantech": "mantech", "peraton": "peraton",
    "deloitte": "deloitte", "guidehouse": "guidehouse", "relx": "relx",
}


def _org_key_candidates(label: str) -> list[str]:
    """org:<first-token> then org:<full-slug>: 'FCN, INC.' resolves org:fcn,
    'THUNDERCAT TECHNOLOGY, LLC' resolves org:thundercat."""
    value = " ".join(str(label or "").split())
    if not value:
        return []
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", value) if t]
    out = []
    folded = " ".join(value.casefold().replace(".", " ").split())
    # longest product/vendor prefix wins
    for name in sorted(_PRODUCT_VENDOR, key=len, reverse=True):
        if folded == name or folded.startswith(name + " "):
            out.append(f"org:{_PRODUCT_VENDOR[name]}")
            break
    # corporate suffix strip: drop inc/llc/corp/ltd/llp tails for the slug
    suffixless = re.sub(
        r"\b(incorporated|inc|llc|l l c|corp|corporation|company|co|ltd|"
        r"llp|plc)\b\.?", " ", folded).strip()
    if tokens:
        out.append(f"org:{_slug(tokens[0])}")
    out.append(f"org:{_slug(suffixless)}")
    out.append(f"org:{_slug(value)}")
    return list(dict.fromkeys(out))


def find_mark(label: str, *, kind: str = "agency",
              key: Optional[str] = None) -> Optional[dict]:
    """The catalogued official mark for a label, or None (a gap)."""
    entries = seal_entries()
    if key and key in entries:
        return entries[key]
    candidates = (_agency_key_candidates(label) if kind == "agency"
                  else _org_key_candidates(label))
    for candidate in candidates:
        if candidate in entries:
            return entries[candidate]
    return None


def mark(label: str, *, kind: str = "agency", key: Optional[str] = None,
         cls: str = "mk", gaps: Optional[list] = None) -> str:
    """Every named agency or organisation renders a mark. No silent blanks.

    Official catalogued mark when on file; else the monogram, and the gap is
    recorded on `gaps` so the render can receipt it (design law
    mark-slot-never-empty; gaps feed the receipts band)."""
    label = " ".join(str(label or "").split()) or "Federal"
    entry = find_mark(label, kind=kind, key=key)
    if entry and entry.get("src"):
        alt = entry.get("alt") or f"{label} mark"
        return (f'<span class="{html.escape(cls)} has-img">'
                f'<img src="{html.escape(entry["src"])}" '
                f'alt="{html.escape(alt)}" loading="lazy" '
                f'decoding="async"></span>')
    if gaps is not None:
        gaps.append(label)
    return (f'<span class="{html.escape(cls)} no-img" '
            f'title="{html.escape(label)} · no official mark on file" '
            f'aria-label="{html.escape(label)}">'
            f"{html.escape(monogram_initials(label))}</span>")


# --------------------------------------------------------------------------- #
# bound-claim shapes
# --------------------------------------------------------------------------- #
def claim(text: str, *, aid: Optional[str] = None, href: Optional[str] = None,
          label: Optional[str] = None,
          url_builder: Optional[Callable[[str], str]] = None) -> str:
    """A sentence bound to a primary record. No binding, no render."""
    if aid is None and href is None:
        raise ValueError(f"unbound claim refused: {str(text)[:60]}")
    if aid is not None and url_builder is None and href is None:
        raise ValueError("claim with an award id needs a url_builder")
    target = href if href is not None else url_builder(aid)
    display = label or (aid if aid else "official record")
    return (f'<p class="claim">{text} '
            f'<a class="ev" href="{html.escape(target, quote=True)}" '
            f'target="_blank" rel="noopener">'
            f'{html.escape(display)}<span class="arw">&#8599;</span></a></p>')


def ev(label: str, *, aid: Optional[str] = None, href: Optional[str] = None,
       url_builder: Optional[Callable[[str], str]] = None) -> str:
    """An evidence pill: label anchored to its primary record."""
    if aid is None and href is None:
        raise ValueError(f"unbound evidence pill refused: {label[:60]}")
    if aid is not None and url_builder is None and href is None:
        raise ValueError("evidence pill with an award id needs a url_builder")
    target = href if href is not None else url_builder(aid)
    return (f'<a class="pill" href="{html.escape(target, quote=True)}" '
            f'target="_blank" rel="noopener">'
            f'{html.escape(label)}<span class="arw">&#8599;</span></a>')


# --------------------------------------------------------------------------- #
# parameterized row/card shapes (the rework's markup, its data left behind)
# --------------------------------------------------------------------------- #
def clock_row(date_label: str, account: str, what: str, amount: str,
              aid: str, url: str, *, flag: str = "",
              what_html: Optional[str] = None) -> str:
    """One clock-rail row; a nonempty flag renders the expired-flag chip
    (design_tokens .components.expired-flag). ``what_html``, when given, is
    renderer-built SAFE html for the scope cell and takes precedence over
    the escaped ``what`` (the own-id anchoring case, 2026-08-05: a scope
    text carrying the record's own id must ride its canonical anchor,
    which escaping here would flatten to bare copy)."""
    flag_html = f'<span class="flag">{html.escape(flag)}</span>' if flag else ""
    cls = ' class="dead"' if flag else ""
    scope = what_html if what_html is not None else html.escape(what)
    return (f'<tr{cls}><td class="cd">{html.escape(date_label)}{flag_html}</td>'
            f'<td class="ca">{html.escape(account)}</td>'
            f'<td class="cw">{scope}</td>'
            f'<td class="cv">{html.escape(amount)}</td>'
            f'<td class="cr"><a href="{html.escape(url, quote=True)}" '
            f'target="_blank" rel="noopener">{html.escape(aid)}'
            f'<span class="arw">&#8599;</span></a></td></tr>')


def recipient_row(rank: int, name: str, dollars: str, count: int,
                  where: str, *, gaps: Optional[list] = None) -> str:
    plural = "s" if count != 1 else ""
    return (f'<tr><td class="rk">{rank:02d}</td>'
            f'<td class="rn">{mark(name, kind="org", cls="pmk", gaps=gaps)}'
            f'{html.escape(name)}</td>'
            f'<td class="rv">{html.escape(dollars)}</td>'
            f'<td class="rc">{count} record{plural}</td>'
            f'<td class="rw">{html.escape(where)}</td></tr>')


def event_card(when: str, where: str, title: str, agenda: str, ask: str,
               links: Iterable[tuple[str, str]], *,
               org_label: Optional[str] = None,
               gaps: Optional[list] = None) -> str:
    pills = " ".join(ev(label, href=href) for label, href in links)
    return ('<article class="event">'
            f'<div class="e-when">{html.escape(when)}</div>'
            f'<h3>{mark(org_label or where, kind="org", cls="emk", gaps=gaps)}'
            f'{html.escape(title)}</h3>'
            f'<p class="e-agenda">{html.escape(agenda)}</p>'
            f'<p class="e-ask"><span class="e-lab">Arrive with</span> '
            f'{html.escape(ask)}</p>'
            f'<div class="pills">{pills}</div></article>')
