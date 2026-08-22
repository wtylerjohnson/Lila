"""The report's federal-sales reading order and plain-speak vocabulary.

The evidence model and the HTML editor are deliberately independent of this
file. A report family can change how it talks without changing a record,
source URL, dollar calculation, or Studio control.

CONTRACT DERIVATION (2026-08-03): the band ORDER and IDS come from
docs/REPORT_CONTRACT.md §1 through agents.golden_press.contract; this file
authors only the words each band speaks (heading, meta strip, reader
question). A contract band this file cannot speak for fails loudly at import
of SECTIONS, never silently renders unnamed. The old ten-band grammar
(forecast → candidate-review → signals → accounts → capabilities →
leadership → method → calendar → events → evidence) is RETIRED per §1.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.golden_press.contract import contract_section_ids


@dataclass(frozen=True)
class SectionDoctrine:
    band_id: str
    heading: str
    meta: str
    reader_question: str


# The words for each contract band id. Text lives here; membership and order
# live in the contract. Federal capture vocabulary only (contract L7).
_TEXT: dict[str, tuple[str, str, str]] = {
    "decisions": (
        "Account decisions · client and rival paper",
        "CLIENT AWARD × NAMED RIVAL · NEAREST CLOCK FIRST · SENTENCE RECEIPTS",
        "Where are we and a named rival both funded, and what closes first?",
    ),
    "forward": (
        "Forward lane · qualify before it posts",
        "PUBLISHED FORECASTS · SHAPEABLE NOTICES · ADJACENCY EVIDENCE",
        "What is forming where this client already holds paper?",
    ),
    "clocks": (
        "Decision clocks",
        "PERIOD ENDS · RESPONSE DEADLINES · EXPIRED FLAGGED, NEVER HIDDEN",
        "When does each decision in this report actually close?",
    ),
    "paper": (
        "Who holds the paper",
        "PRIME RECIPIENTS · CITED OBLIGATIONS · ROUTES, NOT PARTNERS",
        "Which primes already hold the funded routes on the cited records?",
    ),
    "agencies": (
        "Past obligations by buying agency",
        "HISTORICAL BUYING CONTEXT · PER-AGENCY RECORDS · SOURCE LINKS",
        "What has each buying agency spent on the linked records?",
    ),
    "competitive": (
        "Account-by-account competitive picture",
        "BUYER · WHO HOLDS THE PAPER · RECOMMENDED MOTION · SOURCE LINKS",
        "At each buying account, who holds current paper and what is the move?",
    ),
    "events": (
        "Where to show up",
        "OFFICIAL EVENT LINKS · WHY ATTEND · ARRIVE WITH",
        "Where can the account team meet the buyers this report names?",
    ),
    "method": (
        "Show the work",
        "LINK CLASSES · SELECTION RULES · MARKS · SCREENING RECEIPTS",
        "How was this assembled, and what does it deliberately not claim?",
    ),
    "targeting": (
        "Targeting · who to call about this",
        "BUYING COMPONENT · PERSONA TIERS · JOINED RECORD · ENRICHMENT",
        "Every band above says what is true. Who do we call about it?",
    ),
}

SECTIONS = tuple(
    SectionDoctrine(band_id, *_TEXT[band_id])
    for band_id in contract_section_ids()
)

BY_ID = {section.band_id: section for section in SECTIONS}
BAND_SEQUENCE = tuple(
    (section.band_id, section.heading, section.meta) for section in SECTIONS
)

PROSE_GRAMMAR = (
    "what the evidence says",
    "why it matters",
    "what to verify or do next",
    "external source",
)


def section(band_id: str) -> SectionDoctrine:
    """Return the one doctrine row for a load-bearing renderer band."""

    try:
        return BY_ID[band_id]
    except KeyError as exc:
        raise ValueError(f"unknown golden-press band: {band_id}") from exc
