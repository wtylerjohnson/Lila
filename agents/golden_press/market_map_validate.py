"""The Market Map validator: the thing that stops the format drifting back.

A renderer produces the format once. THIS keeps it. Every check below exists
because the assessment family already drifted in exactly that way:

  banned vocabulary   a single invented word ("corridor") shipped a press
                      uncertified, and nothing caught it until validation
  em dash             R9's ban, enforced full-artifact and mechanically
  REPEATED SENTENCE   NEW. Nothing in the band validator watches density, so
                      a document could restate the same claim in three
                      sections and read as thorough
  REPEATED ROW KEY    NEW. One contact appeared TWENTY TIMES in a target list
                      because twenty specs named the same agency. Same person,
                      same agency, near-identical sentence: padding produced
                      by the data model rather than by intent
  BLANK CELL          NEW. The whole point of this family is saying what we do
                      not have. An empty cell says nothing and reads as an
                      oversight; a named gap is a work order
  MISSING RECEIPT     a money or opportunity section with no linked record id
                      is an assertion, not evidence

Contract: Appendix A of docs/MARKET_MAP_CONTRACT.md. These checks preserve the
legacy internal studio invariants; they do not define the external slots.
"""

from __future__ import annotations

import re
from typing import Any

MARKET_MAP_VALIDATE_VERSION = "market_map_validate.v2.2026-08-07"

# `research` is first-class (ontology brief, 2026-08-07): research demand
# is an output of the document, not an apology at the end of it.
REQUIRED_SECTIONS = ("market-lifecycle", "company", "market", "competition",
                     "opportunities", "teaming", "contacts", "events",
                     "research", "evidence-method", "pursuit-thesis",
                     "pursuit-board", "research-gates", "vocabulary")

# L7 invented terms. Extend HERE, never in the contract prose.
BANNED_VOCABULARY = (
    "corridor", "research clock", "360 assessment", "360° assessment",
    "candidate opportunity for review", "preliminary match",
)

# LLM filler. A document that says these is talking rather than reporting.
BANNED_FILLER = (
    "delve", "leverage", "robust", "seamless", "cutting-edge", "best-in-class",
    "in today's", "it is important to note", "landscape of", "tapestry",
    "navigate the complexities", "unlock", "supercharge", "game-changer",
)

# Sections that must show at least one linked record id (a receipt).
RECEIPT_SECTIONS = ("market", "competition", "opportunities", "teaming")

_SENTENCE = re.compile(r"[^.!?]+[.!?]")
_TAG = re.compile(r"<[^>]+>")
# ID-BASED, not class-based. The first regex here required class="mm", a
# vocabulary the skeleton rebuild retired; every section then read as
# missing while sitting in plain sight. The id IS the section contract.
_SECTION = re.compile(
    r'<section[^>]*\bid="([^"]+)"[^>]*>(.*?)</section>', re.S)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_RECORD_ID = re.compile(r"\b[A-Z0-9]{6,}\b")


def _text(html: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S)
    return re.sub(r"\s+", " ", _TAG.sub(" ", body)).strip()


def _norm_sentence(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ",
                  re.sub(r"\s+", " ", str(value or "")).casefold()).strip()


def validate_market_map(html: str, *, gap_phrases: Any = None) -> dict:
    """(ok, violations, receipts) for one rendered Market Map. Pure."""
    from agents.golden_press.market_map import GAP_PHRASES

    gaps = tuple(gap_phrases or GAP_PHRASES)
    violations: list = []
    content = re.sub(
        r'<div[^>]*class="[^"]*ticker[^"]*"[^>]*>.*?</div>', " ",
        html or "", flags=re.S)
    # Card labels are chrome, not prose: flattening "Published value" and
    # "Type and access" into the text welds adjacent cells into one
    # pseudo-sentence that repeats wherever two rows share honest states
    # (two undated forecasts, mark43). Real prose duplication still reads
    # as prose after the labels are gone.
    content = re.sub(r'<span[^>]*class="opp-label"[^>]*>.*?</span>', " ",
                     content, flags=re.S)
    # Derived record data (type · access · responses) is a fact cell, not
    # prose: two undated forecasts legitimately share it, exactly as two
    # table rows share a column value. The row-identity rule still judges
    # whole rows; the sentence rule judges writing.
    content = re.sub(r'<p[^>]*class="record-facts"[^>]*>.*?</p>', " ",
                     content, flags=re.S)
    # The receipts appendix is the drawer data made scriptless: it MIRRORS
    # figures and populations the sections already state, by design. It was
    # excluded as <script> JSON before; it stays excluded as <details>.
    content = re.sub(r'<aside[^>]*receipts-appendix.*?</aside>', " ",
                     content, flags=re.S)
    # Verbatim source quotes (mined phrases) are government text, not our
    # voice; the filler and vocabulary lints judge what WE wrote.
    content = re.sub(r'<span[^>]*source-quote[^>]*>.*?</span>', " ",
                     content, flags=re.S)
    text = _text(content)
    sections = {sid: body for sid, body in _SECTION.findall(html or "")}

    # ---- §1 section sequence -------------------------------------------- #
    order = [sid for sid, _ in _SECTION.findall(html or "")]
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        violations.append({
            "rule": "sections_missing",
            "detail": f"required section(s) absent: {', '.join(missing)}"})
    if order and order != [s for s in REQUIRED_SECTIONS if s in sections]:
        violations.append({
            "rule": "section_order",
            "detail": f"sections render as {order}, contract order is "
                      f"{list(REQUIRED_SECTIONS)}"})

    # ---- §3 vocabulary --------------------------------------------------- #
    low = text.casefold()
    for word in BANNED_VOCABULARY:
        n = low.count(word.casefold())
        if n:
            violations.append({
                "rule": "banned_vocabulary",
                "detail": f"{word!r} appears {n}x (contract L7)"})
    for word in BANNED_FILLER:
        n = low.count(word.casefold())
        if n:
            violations.append({
                "rule": "banned_filler",
                "detail": f"{word!r} appears {n}x; this family reports, it "
                          f"does not talk (L-M3)"})
    if "—" in text:
        violations.append({
            "rule": "em_dash",
            "detail": f"{text.count(chr(0x2014))} em dash(es) in rendered "
                      f"text (R9); use a comma"})

    # ---- L-M2 no repeated sentence --------------------------------------- #
    seen_sentences: dict = {}
    for raw in _SENTENCE.findall(text):
        norm = _norm_sentence(raw)
        if len(norm.split()) < 6:
            continue
        seen_sentences[norm] = seen_sentences.get(norm, 0) + 1
    for norm, count in seen_sentences.items():
        if count > 1:
            violations.append({
                "rule": "repeated_sentence",
                "detail": f"a sentence repeats {count}x: {norm[:70]!r} "
                          f"(L-M2: a fact appears once)"})

    # ---- L-M2 no repeated row key inside one table ----------------------- #
    # THE WHOLE ROW, not its first cell. A shared category label ("Status
    # check", "TBD") is how a table groups; an IDENTICAL ROW is padding. An
    # earlier build keyed on cell[0] and flagged every table that had a
    # motion column, which is the false-positive class that trains an
    # operator to ignore a validator.
    for sid, body in sections.items():
        rows: dict = {}
        for row in _ROW.findall(body):
            cells = [_text(c) for c in _CELL.findall(row)]
            if len(cells) < 2:
                continue
            key = _norm_sentence(" | ".join(cells))
            if not key or len(key.split()) < 3:
                continue
            rows[key] = rows.get(key, 0) + 1
        for key, count in rows.items():
            if count > 1:
                violations.append({
                    "rule": "repeated_row",
                    "detail": f"section {sid!r} renders an identical row "
                              f"{count}x: {key[:56]!r} (L-M2: state it once "
                              f"with its motions named)"})

    # ---- L-M1 no blank cell ---------------------------------------------- #
    blanks = 0
    for sid, body in sections.items():
        for row in _ROW.findall(body):
            for cell in _CELL.findall(row):
                if not _text(cell):
                    blanks += 1
    if blanks:
        violations.append({
            "rule": "blank_cell",
            "detail": f"{blanks} empty table cell(s); L-M1 requires a value "
                      f"or a named gap such as {gaps[0]!r}"})

    # ---- receipts --------------------------------------------------------- #
    for sid in RECEIPT_SECTIONS:
        body = sections.get(sid)
        if body is None:
            continue
        # The rule exists for sections that CLAIM numbers. A stated
        # research order, or a section carrying no dollar figure at all
        # (eight named rivals with a measured zero of federal paper),
        # asserts nothing a record id could receipt.
        if "$" not in _text(body):
            continue
        if "href=" not in body and not _RECORD_ID.search(_text(body)):
            violations.append({
                "rule": "missing_receipt",
                "detail": f"section {sid!r} states figures with no linked "
                          f"record id"})

    # ---- id-link binding --------------------------------------------------#
    # A USAspending award link EMBEDS the award id in its href, so the
    # displayed id must appear there (round 1: five cards showed one award
    # and linked another). SAM links are exempt by construction: a
    # solicitation number correctly opens its notice's internal-id URL, and
    # binding those is the store lane's job, not string containment's.
    for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>([^<]{4,40})</a>',
                         html or ""):
        href, label = m.group(1), _text(m.group(2)).replace(" ↗", "").strip()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{5,}", label):
            continue
        if "usaspending.gov/award" in href and label not in href:
            violations.append({
                "rule": "id_link_binding",
                "detail": f"link text {label!r} opens a different record: "
                          f"{href[:80]}"})

    # ---- coverage stated --------------------------------------------------#
    for sid, body in sections.items():
        plain = _text(body).casefold()
        stated = any(k in plain for k in (
            "coverage", "linked record", "shown", "stated here", "next research",
            "ready now", "not in current research", "screened",
            "is not stated", "this edition", "confirm before"))
        if not stated:
            violations.append({
                "rule": "coverage_unstated",
                "detail": f"section {sid!r} claims neither completeness nor a "
                          f"named gap; silence is a violation"})

    # ---- client-artifact hygiene ----------------------------------------- #
    for needle, rule in (("<script", "script_in_client_artifact"),
                         ("contenteditable", "editable_in_client_artifact"),
                         ("data-studio-", "studio_marker_in_client_artifact")):
        if needle in (html or "").casefold():
            violations.append({"rule": rule,
                               "detail": f"{needle!r} present in the client "
                                         f"artifact"})

    gap_uses = sum(_text(html).count(g) for g in gaps)
    return {
        "version": MARKET_MAP_VALIDATE_VERSION,
        "ok": not violations,
        "violations": violations,
        "receipts": {
            "sections_rendered": order,
            "distinct_sentences": len(seen_sentences),
            "stated_gaps": gap_uses,
            "characters": len(text),
        },
    }
