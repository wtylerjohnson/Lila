"""Successor and award-expiry context for teaming and best-fit cards.

A teaming thesis on an incumbent is incomplete intelligence if the
incumbent's contract is being recompeted: the recompete is simultaneously
the risk (the seat may be lost and the thesis dies) and the entry (the
incumbent needs differentiators to win, the client's cleanest pitch).
This module joins each play to its successor event.

Matcher precedence (documented contract; exact lanes precede fuzzy; fuzzy
ties use strongest evidence and a stable source-identity tie-break):
  1. AWARD/IDV EXACT · the play's award id equals a calendar row's award
     id, OR the play's referenced IDV piid equals a row award id, OR a
     forecast row's text cites the play's exact award id. Record-backed.
  2. SOLICITATION LINEAGE · a calendar/forecast row explicitly cites the
     play's predecessor identity (piid stem: issuing-office prefix and
     serial family) as the thing being recompeted/bridged/followed on.
     Record-backed only when the citation is exact; otherwise ANALYST.
  3. PROGRAM-NAME NORMALIZATION · normalized program stems carry enough
     identity evidence: a distinctive acronym, one exact strong token, or at
     least two distinctive token overlaps after generic procurement language
     is removed. ALWAYS ANALYST tier; a fuzzy match is never presented as
     record-backed, and one generic word can never join unrelated programs.

States (the render contract):
  EVENT KNOWN         · successor event or award expiry found; renders with
                        its exact evidence class, date, and source. An expiry
                        alone never claims that a recompete exists.
  NO RECOMPETE FOUND  · the calendar was queried, the program family is
                        inside its screened population, and no successor
                        exists in the screening window; stated explicitly
                        in client HTML (absence of evidence is a finding).
  CALENDAR GAP        · the program family is not covered (no calendar for
                        the client, or the family sits outside the
                        screened population). INTERNAL sidecar only; never
                        client HTML.

Freshness: every event carries source_system (forecast adapter, SAM, or
USASPENDING),
source_record_id, and retrieved_at (forecast rows carry their own; calendar
rows inherit the calendar's generated date). Events join the model's figure
provenance rows so the data-current line and freshness gate account for
them. No special cases.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

#: months ahead a successor event may sit and still join the horizon band
HORIZON_WINDOW_MONTHS = 36

_ROMAN = re.compile(r"\b(?:i{1,3}|iv|v|vi{0,3}|ix|x)\b")
_GEN_SUFFIX = re.compile(r"\b(?:2\.0|3\.0|ii|iii|iv|v|next|bridge|extension|"
                         r"follow[- ]?on|recompete)\b")
_PIID_RE = re.compile(r"\b[0-9A-Z]{2}[0-9A-Z]{6,15}\b")
_APFS_PUBLIC_FORECASTS = "https://apfs-cloud.dhs.gov/forecast/"


def normalize_program_name(text: str) -> str:
    """Lowercased stem with punctuation, roman numerals, and generation
    markers removed: 'ODOS III' -> 'odos'; 'Outcome-based Delivery and
    DevSecOps Services IV (ODOS IV)' -> stem containing 'odos'."""
    low = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    low = _GEN_SUFFIX.sub(" ", low)
    low = _ROMAN.sub(" ", low)
    return " ".join(low.split())


#: agency identities and generic procurement words: shared vocabulary that
#: can never make two PROGRAMS the same family (USCIS appearing in both a
#: play name and a forecast title proves nothing)
_STEM_STOPWORDS = frozenset({
    "uscis", "uspto", "cisa", "fema", "usss", "fletc", "darpa", "usaf",
    "army", "navy", "usda", "nasa", "commerce", "homeland", "veterans",
    "affairs", "defense", "department", "agency", "federal", "services",
    "service", "support", "delivery", "development", "systems", "system",
    "data", "cloud", "software", "security", "operations", "operational",
    "program", "solutions", "contract", "enterprise", "management",
    "devsecops", "devops", "transition", "modernization", "technology",
    "infrastructure", "analytics", "platform", "engineering", "digital",
    "record", "records", "purpose", "requirement", "requirements", "award",
    "awards", "action", "actions", "task", "order", "orders", "procurement",
    "acquisition", "work", "current", "existing", "future", "renewal",
    "maintenance", "license", "licenses", "hardware", "product", "products",
    "brand", "information", "application", "applications", "mission",
    "business", "office", "the", "this", "that", "these", "those", "and",
    "for", "from", "with", "into", "their", "other", "shall", "will",
    "include", "includes", "including", "provide", "provides", "providing",
    "obtain", "full", "open", "small", "large", "base", "option", "year",
    "name", "collaborate", "electronic", "digitization",
    "years", "period", "periods", "annual", "fee", "fees", "subscription",
    "subscriptions", "vehicle", "sewp", "pop", "network", "monitoring",
    "incident", "emergency", "command", "control", "case", "identity",
    "credential", "authentication", "document", "documents", "science",
    "analysis", "intelligence",
    "computer", "project", "projects", "standard", "standards", "solution",
    "archive", "workflow", "workflows", "reporting", "compliance",
    "integration", "integrated", "administration", "technical",
    "professional", "medical", "health", "financial", "workforce",
    "training", "research", "communications", "communication", "processing",
    "database", "storage", "hosting", "migration", "implementation",
    "capability", "capabilities", "environment", "environments",
})

_AGENCY_ACRONYMS = frozenset({
    "DHS", "DOD", "DOC", "USCIS", "USPTO", "CBP", "TSA", "ICE", "CISA",
    "FEMA", "USSS", "USCG", "USAF", "ARMY", "NAVY", "GSA", "NASA", "VA",
    "DOJ", "DOE", "DOI", "DOL", "DOS", "DOT", "EPA", "HHS", "IRS",
    # Generic acquisition/document acronyms are no more identifying than an
    # agency name. They cannot turn a one-word overlap into program lineage.
    "RFI", "RFP", "RFQ", "IFB", "SOW", "PWS", "IDIQ", "GWAC", "MAS",
    "FAR", "NAICS", "PSC", "SEWP", "POP", "API", "ATO", "FIPS", "SOC",
})

_RAW_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9]{3,}\b")
_STRONG_TOKEN_MIN_LENGTH = 8


def _acronyms(text: str) -> set[str]:
    """Distinctive program acronyms: 3-6 caps; agency identities and
    roman-numeral generation markers (III, VII) are never identity."""
    return {m.group(0).lower()
            for m in re.finditer(r"\b[A-Z]{3,6}\b", text or "")
            if m.group(0) not in _AGENCY_ACRONYMS
            and m.group(0).lower() not in _STEM_STOPWORDS
            and not re.fullmatch(r"[IVX]+", m.group(0))}


def _distinctive_tokens(text: str) -> set[str]:
    """Normalized non-generic program words eligible for identity evidence."""
    return {
        token
        for token in normalize_program_name(text).split()
        if len(token) >= 4 and token not in _STEM_STOPWORDS
    }


def _strong_tokens(text: str) -> set[str]:
    """Structurally distinctive exact tokens.

    Long non-generic words, alphanumeric program names, and stylized
    mixed-case names such as WebEOC/OpenFox can stand alone when the exact
    token occurs on both sides. Ordinary title capitalization does not count.
    """
    distinctive = _distinctive_tokens(text)
    strong: set[str] = set()
    for match in _RAW_TOKEN.finditer(text or ""):
        raw = match.group(0)
        token = raw.lower()
        if token not in distinctive:
            continue
        stylized = (
            any(character.isdigit() for character in raw)
            or raw.isupper()
            or (
                any(character.islower() for character in raw)
                and any(character.isupper() for character in raw[1:])
            )
        )
        if stylized or len(token) >= _STRONG_TOKEN_MIN_LENGTH:
            strong.add(token)
    return strong


@dataclass(frozen=True)
class _StemEvidence:
    rank: int
    overlaps: tuple[str, ...]
    strong: tuple[str, ...]
    acronyms: tuple[str, ...]


def _stem_evidence(a: str, b: str, *,
                   identity_b: Optional[str] = None) -> Optional[_StemEvidence]:
    overlaps = tuple(sorted(_distinctive_tokens(a) & _distinctive_tokens(b)))
    # A regular long word is strong only on the row's identity surface (the
    # forecast title), never merely because boilerplate deep in a description
    # repeats it. An all-caps predecessor token may establish that the shared
    # title token is a name even when the successor title uses normal case.
    identity = b if identity_b is None else identity_b
    title_overlaps = _distinctive_tokens(a) & _distinctive_tokens(identity)
    strong_markers = (
        _strong_tokens(a) | _strong_tokens(identity)
        | _acronyms(a) | _acronyms(identity)
    )
    strong = tuple(sorted(title_overlaps & strong_markers))
    acronyms = tuple(sorted(_acronyms(a) & _acronyms(b)))
    if acronyms:
        rank = 3
    elif strong:
        rank = 2
    elif len(overlaps) >= 2:
        rank = 1
    else:
        return None
    return _StemEvidence(
        rank=rank,
        overlaps=overlaps,
        strong=strong,
        acronyms=acronyms,
    )


@dataclass(frozen=True)
class RecompeteEvent:
    kind: str                    # recompete | bridge | follow_on | forecast | expiry
    label: str
    date: Optional[str]          # ISO date of the event when known
    source_system: str           # forecast adapter | 'sam' | 'usaspending'
    source_record_id: Optional[str]
    source_generated_id: Optional[str]  # USAspending generated identity
    source_url: Optional[str]
    retrieved_at: Optional[str]
    tier: str                    # 'record' | 'analyst'


@dataclass(frozen=True)
class RecompeteContext:
    state: str                   # 'known' | 'not_found' | 'gap'
    event: Optional[RecompeteEvent] = None
    reason: str = ""
    matched_by: str = ""         # 'award_idv' | 'lineage' | 'name_stem' | ''


@dataclass(frozen=True)
class PlayKey:
    """The contract identity a play or best-fit card is keyed to."""
    award_id: str = ""
    idv_piid: str = ""
    program_name: str = ""
    agency: str = ""


def _event_kind(text: str) -> str:
    low = (text or "").lower()
    if "bridge" in low or "extension" in low:
        return "bridge"
    if "follow" in low:
        return "follow_on"
    if re.search(r"\brecomp(?:ete(?:d|s)?|eting|etition)\b", low):
        return "recompete"
    # A forecast proves a sourced future signal, not the acquisition method.
    # Recompete is reserved for source text that says so explicitly.
    return "forecast"


def _forecast_event(row: dict, *, tier: str) -> RecompeteEvent:
    text = f"{row.get('title') or ''} {row.get('description') or ''}"
    source_url = row.get("url")
    source_identity = str(row.get("source") or "agency_forecast")
    # APFS retired its per-record /forecast/<database-id> routes (they now
    # return 404) while the official public forecast list remains live. Keep
    # the record's APFS number in source_record_id and link the human-readable
    # public list; never ship a known-dead detail URL.
    if (row.get("source") == "dhs_apfs"
            or str(source_url or "").startswith(
                "https://apfs-cloud.dhs.gov/forecast/")):
        source_url = _APFS_PUBLIC_FORECASTS
    return RecompeteEvent(
        kind=_event_kind(text),
        label=(row.get("title") or "forecast successor")[:80],
        date=(row.get("anticipated_solicitation")
              or row.get("anticipated_award") or None),
        source_system=(
            "apfs" if source_identity == "dhs_apfs" else source_identity),
        source_record_id=(row.get("source_id") or "").lstrip("*") or None,
        source_generated_id=None,
        source_url=source_url,
        retrieved_at=row.get("retrieved_at"),
        tier=tier,
    )


def _calendar_event(row: dict, *, generated: Optional[str],
                    tier: str) -> RecompeteEvent:
    return RecompeteEvent(
        kind="expiry",
        label=(f"{(row.get('description') or 'expiring award')[:60]} · "
               f"{row.get('award_id')}"),
        date=row.get("pop_end"),
        source_system="usaspending",
        source_record_id=row.get("award_id"),
        source_generated_id=row.get("internal_id"),
        # The stored URL is deliberately not consumed by the renderer. Award
        # links are constructed only from source_generated_id after the
        # fact-pack record has reconciled.
        source_url=None,
        retrieved_at=generated,
        tier=tier,
    )


def _row_identity(row: dict) -> tuple[str, ...]:
    """Stable P3 tie-break independent of collector/input ordering."""
    primary = tuple(str(row.get(field) or "").casefold() for field in (
        "source_id", "award_id", "internal_id", "title", "description",
        "anticipated_solicitation", "anticipated_award", "pop_end",
    ))
    canonical = json.dumps(
        row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str,
    )
    return primary + (canonical,)


def _best_stem_row(
    program_name: str,
    rows: list[dict],
    *,
    text_of: Callable[[dict], str],
    identity_text_of: Optional[Callable[[dict], str]] = None,
) -> Optional[dict]:
    """Choose the strongest eligible fuzzy join with a stable final tie-break."""
    eligible = []
    for row in rows:
        text = str(text_of(row) or "")
        identity = str(identity_text_of(row) or "") \
            if identity_text_of else text
        evidence = _stem_evidence(
            program_name, text, identity_b=identity)
        if evidence is None:
            continue
        # Lowest tuple wins: evidence strength/counts descend, stable source
        # identity ascends. No set or collector ordering reaches the outcome.
        rank = (
            -evidence.rank,
            -len(evidence.acronyms),
            -len(evidence.strong),
            -len(evidence.overlaps),
            _row_identity(row),
        )
        eligible.append((rank, row))
    return min(eligible, key=lambda item: item[0])[1] if eligible else None


def within_horizon(event_date: Optional[str], render_date: date,
                   months: int = HORIZON_WINDOW_MONTHS) -> bool:
    """Is the event inside the forward horizon window? An unparseable or
    absent date (APFS quarter text) is treated as inside: forecast
    statements are near-term by construction, and the render carries the
    source for the reader's own judgment."""
    if not event_date:
        return True
    try:
        d = date.fromisoformat(str(event_date)[:10])
    except ValueError:
        return True
    return (d - render_date).days <= months * 31


def _family_covered(key: PlayKey, calendar: Optional[dict]) -> bool:
    """The calendar screened this play's family: its population includes the
    play's awarding agency (or the exact award). Absence then means a
    finding, not a blind spot."""
    if not calendar:
        return False
    rows = list(calendar.get("attack") or []) + list(calendar.get("defend") or [])
    agency_low = (key.agency or "").lower()
    for row in rows:
        if key.award_id and row.get("award_id") == key.award_id:
            return True
        row_agency = f"{row.get('awarding_agency') or ''} {row.get('awarding_office') or ''}".lower()
        if agency_low and agency_low in row_agency:
            return True
    return False


def successor_events(key: PlayKey, calendar: Optional[dict],
                     forecasts: Optional[list] = None) -> RecompeteContext:
    """The matcher. Precedence: award/IDV exact, then solicitation lineage
    (exact predecessor citation), then program-name stem (always ANALYST)."""
    forecasts = [r for r in (forecasts or []) if isinstance(r, dict)]
    cal_rows = (list((calendar or {}).get("attack") or [])
                + list((calendar or {}).get("defend") or []))
    generated = (calendar or {}).get("generated")

    # P1 · award/IDV exact. An explicit forecast that cites the predecessor
    # proves a successor event; a matching calendar row proves only expiry.
    # Prefer the stronger successor evidence when both are present.
    for row in forecasts:
        text = f"{row.get('title') or ''} {row.get('description') or ''}"
        if key.award_id and key.award_id in text:
            return RecompeteContext(
                state="known", matched_by="award_idv",
                event=_forecast_event(row, tier="record"))
    for row in cal_rows:
        rid = str(row.get("award_id") or "")
        if rid and rid in (key.award_id, key.idv_piid):
            return RecompeteContext(
                state="known", matched_by="award_idv",
                event=_calendar_event(row, generated=generated, tier="record"))

    # P2 · solicitation lineage: the row cites the predecessor identity
    for row in forecasts:
        text = f"{row.get('title') or ''} {row.get('description') or ''}"
        cited = set(_PIID_RE.findall(text.upper()))
        if key.idv_piid and key.idv_piid.upper() in cited:
            return RecompeteContext(
                state="known", matched_by="lineage",
                event=_forecast_event(row, tier="record"))

    # P3 · program-name stem: ANALYST tier, never record-backed
    if key.program_name:
        forecast = _best_stem_row(
            key.program_name,
            forecasts,
            text_of=lambda row: (
                f"{row.get('title') or ''} {row.get('description') or ''}"
            ),
            identity_text_of=lambda row: row.get("title") or "",
        )
        if forecast is not None:
            return RecompeteContext(
                state="known", matched_by="name_stem",
                event=_forecast_event(forecast, tier="analyst"))
        calendar_row = _best_stem_row(
            key.program_name,
            cal_rows,
            text_of=lambda row: row.get("description") or "",
        )
        if calendar_row is not None:
            return RecompeteContext(
                state="known", matched_by="name_stem",
                event=_calendar_event(calendar_row, generated=generated,
                                      tier="analyst"))

    if _family_covered(key, calendar):
        return RecompeteContext(
            state="not_found",
            reason="calendar queried; no successor event in the screening "
                   "window for this program family")
    return RecompeteContext(
        state="gap",
        reason=("no recompete calendar for this client"
                if not calendar else
                "program family outside the calendar's screened population"))
