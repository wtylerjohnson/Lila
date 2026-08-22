"""Deterministic language lint — the hard floor under the language-editor agent.

The editor layer (Claude) rewrites for concision; this module PROVES the result
is clean. If a draft fails lint, the editor gets one retry with the violations
attached; if it still fails, the run stops at the review gate with the list —
a human sees the problem, the report never ships with it.

Also validates fact citations: every [F#] in the text must exist in the FactPack,
and quantitative sentences must carry at least one citation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, Field

# LLM verbosity / consultant filler. Lowercase; matched on word boundaries.
BANNED_PHRASES = [
    "delve", "delving",
    "leverage", "leveraging",
    "streamlined", "streamlining",
    "seamless", "seamlessly",
    "robust",
    "holistic",
    "synergy", "synergies",
    "cutting-edge", "state-of-the-art", "best-in-class", "world-class",
    "game-changer", "game-changing",
    "unlock", "unlocking",
    "empower", "empowering",
    "elevate your",
    "navigate the landscape", "evolving landscape", "dynamic landscape",
    "in today's",
    "it is important to note", "it's important to note", "it is worth noting",
    "furthermore", "moreover", "additionally,",
    "comprehensive suite",
    "tailored solutions",
    "unparalleled",
    "meticulously",
    "fostering", "foster a",
    "underscores", "underscoring",
    "testament to",
    "poised to",
    "myriad",
    "plethora",
    "utilize", "utilizing",
]

_CITE_RE = re.compile(r"\[F(\d+)\]")
_NUM_RE = re.compile(r"\$[\d,]+|\b\d{2,}\b")  # dollars or any 2+ digit figure


class LintViolation(BaseModel):
    rule: str
    detail: str
    excerpt: str = ""


class LintResult(BaseModel):
    ok: bool
    violations: list[LintViolation] = Field(default_factory=list)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


_FORECAST_MARK = 'data-forecast="1"'
_SIGNALS_OPEN = re.compile(r'<section class="early-signals"[^>]*>', re.I)


def lint_forecast_context(html_text: str) -> LintResult:
    """HARD RULE: a ForecastRecord (marked data-forecast="1") may render ONLY
    inside a section.early-signals wrapper. Forecasts are agency-stated intent;
    rendering one among live opportunities misrepresents the evidence, so the
    build fails."""
    v: list[LintViolation] = []
    spans: list[tuple[int, int]] = []
    for m in _SIGNALS_OPEN.finditer(html_text):
        end = html_text.find("</section>", m.end())
        spans.append((m.start(), end if end != -1 else len(html_text)))
    idx = html_text.find(_FORECAST_MARK)
    while idx != -1:
        if not any(a <= idx < b for a, b in spans):
            start = max(0, idx - 60)
            v.append(LintViolation(
                rule="forecast_outside_signals",
                detail="forecast record rendered outside a signals-labeled section",
                excerpt=html_text[start:idx + 40].strip()))
        idx = html_text.find(_FORECAST_MARK, idx + 1)
    return LintResult(ok=not v, violations=v)


# --- developing horizon --------------------------------------------------------
# Horizon items are PROJECTIONS (analyst judgment over cited signals). Two hard
# rules keep them honest: they render only inside the developing-horizon
# section (never among live opportunities), and every item must show its work —
# at least one source citation and the literal projection label.

_HORIZON_MARK = 'data-horizon="1"'
_HORIZON_OPEN = re.compile(r'<section class="developing-horizon"[^>]*>', re.I)
_HORIZON_LABEL = "GTM Group projection"


def lint_horizon(html_text: str) -> LintResult:
    """HARD RULE: a horizon item (marked data-horizon="1") renders only inside
    a section.developing-horizon wrapper, carries >=1 source citation, and its
    projection is explicitly labeled. An unlabeled or uncited projection is
    indistinguishable from a claimed fact — the build fails."""
    v: list[LintViolation] = []
    spans: list[tuple[int, int]] = []
    for m in _HORIZON_OPEN.finditer(html_text):
        end = html_text.find("</section>", m.end())
        spans.append((m.start(), end if end != -1 else len(html_text)))
    marks = [m.start() for m in re.finditer(re.escape(_HORIZON_MARK), html_text)]
    for i, idx in enumerate(marks):
        if not any(a <= idx < b for a, b in spans):
            v.append(LintViolation(
                rule="horizon_outside_section",
                detail="horizon item rendered outside the developing-horizon section",
                excerpt=html_text[max(0, idx - 60):idx + 40].strip()))
            continue
        # the item's chunk: this marker to the next one, or the section end
        sec_end = next(b for a, b in spans if a <= idx < b)
        chunk_end = marks[i + 1] if i + 1 < len(marks) and marks[i + 1] < sec_end else sec_end
        chunk = html_text[idx:chunk_end]
        if _HORIZON_LABEL not in chunk:
            v.append(LintViolation(
                rule="horizon_projection_unlabeled",
                detail=f"horizon item missing the '{_HORIZON_LABEL}' label — "
                       "a projection must never read as fact",
                excerpt=chunk[:100].strip()))
        if "href=" not in chunk:
            v.append(LintViolation(
                rule="horizon_item_uncited",
                detail="horizon item carries no source citation — shown work "
                       "is the whole point",
                excerpt=chunk[:100].strip()))
    return LintResult(ok=not v, violations=v)


# --- client-facing terminology -------------------------------------------------
# Internal name: capture brief. Client-facing name: Federal Opportunity
# Assessment. The internal term must never reach a client deliverable — not in
# template strings, not in LLM-generated copy.
_INTERNAL_TERM = re.compile(r"capture[\s\-_]?brief", re.I)

#: THE approved client-facing deliverable titles, by tier: the naming map.
#: Renderers take titles from here; the terminology lint rejects the internal
#: name everywhere client-facing and holds each tier to its approved titles.
APPROVED_CLIENT_TITLES: dict[str, tuple[str, ...]] = {
    # "Federal Opportunity Pre-Assessment" is the canonical public Signal
    # Board title in every client tier. "Federal Opportunity Assessment"
    # remains the approved title for the separate assessment artifact family.
    "assessment": ("Federal Opportunity Assessment",
                   "Federal Opportunity Pre-Assessment"),
    "teaser": ("Federal Opportunity Assessment",
               "Federal Opportunity Pre-Assessment"),
}


def lint_client_terminology(html_text: str,
                            tier: str = "assessment") -> LintResult:
    """HARD RULE: the internal deliverable name never appears in rendered
    client HTML, whatever the tier. Catches template regressions and
    generated-copy leaks alike. Approved public titles are recorded in
    ``APPROVED_CLIENT_TITLES``; artifact-specific gates enforce which one a
    particular deliverable must render."""
    v: list[LintViolation] = []
    for m in _INTERNAL_TERM.finditer(html_text):
        start = max(0, m.start() - 40)
        v.append(LintViolation(
            rule="internal_term_in_client_copy",
            detail="'capture brief' must render as 'Federal Opportunity Assessment'",
            excerpt=html_text[start:m.end() + 40].strip()))
    return LintResult(ok=not v, violations=v)


# --- public SAM links ---------------------------------------------------------
SAM_WORKSPACE_LINK_RULE_TEXT = (
    "SAM.gov workspace links require login; client HTML must use public "
    "https://sam.gov/opp/{guid}/view URLs"
)

FEDERAL_LINK_BUILDER_RULE_TEXT = (
    "USAspending award and SAM notice URLs in client HTML must be emitted "
    "by the canonical record-ID builders; free-string federal links are "
    "forbidden"
)

_HTTP_URL = re.compile(r"(?:https?:)?//[^\s\"'<>]+", re.I)
_BARE_FEDERAL_URL = re.compile(
    r"(?<![\w./-])(?:www\.)?(?:sam\.gov|usaspending\.gov)"
    r"[/?#][^\s\"'<>)]*",
    re.I,
)


def lint_federal_link_construction(
    html_text: str,
    *,
    expected_links: Optional[Iterable[object]] = None,
) -> LintResult:
    """HARD RULE: SPA-domain links carry canonical-builder provenance.

    Canonical bytes alone are insufficient proof: a hand-typed URL can look
    identical.  The renderer emits builder, record, and reconciliation
    attributes from a structured identity.  This lint validates all three and
    also catches federal URLs in comments, metadata, generated copy, or any
    other non-href string.
    """
    from collections import Counter
    from urllib.parse import urlsplit

    from agents.reports.links import (
        SAM_NOTICE_BUILDER,
        USASPENDING_AWARD_BUILDER,
        build_sam_notice_link,
        build_usaspending_award_link,
        federal_link_domain,
        parse_client_anchors,
    )

    violations: list[LintViolation] = []
    anchors = parse_client_anchors(html_text)
    federal_anchors = [anchor for anchor in anchors
                       if federal_link_domain(anchor.href)]
    for anchor in federal_anchors:
        attrs = anchor.attrs
        href = anchor.href
        domain = federal_link_domain(href)
        if domain is None:
            continue
        builder = attrs.get("data-link-builder", "")
        record_id = attrs.get("data-link-record", "")
        reconciled = attrs.get("data-link-reconciled", "")
        expected_builder = (USASPENDING_AWARD_BUILDER
                            if domain == "usaspending.gov"
                            else SAM_NOTICE_BUILDER)
        try:
            expected = (build_usaspending_award_link(record_id).url
                        if expected_builder == USASPENDING_AWARD_BUILDER
                        else build_sam_notice_link(record_id).url)
        except ValueError:
            expected = ""
        duplicate_proof = sorted(set(anchor.duplicate_attrs) & {
            "href", "data-link-builder", "data-link-record",
            "data-link-reconciled",
        })
        if (duplicate_proof or builder != expected_builder or href != expected
                or reconciled != "1"):
            violations.append(LintViolation(
                rule="federal_link_not_builder_derived",
                detail=(f"{FEDERAL_LINK_BUILDER_RULE_TEXT}; href={href!r}, "
                        f"builder={builder or 'absent'!r}, "
                        f"record={record_id or 'absent'!r}, "
                        f"reconciled={reconciled or 'absent'!r}, "
                        f"duplicate_attrs={duplicate_proof or 'none'}"),
                excerpt=anchor.raw[:240],
            ))

    href_counts = Counter(anchor.href for anchor in federal_anchors)
    candidates = [match.group(0).rstrip("),.;:]}")
                  for match in _HTTP_URL.finditer(html_text)]
    candidates.extend(match.group(0)
                      for match in _BARE_FEDERAL_URL.finditer(html_text))
    for url in candidates:
        parseable = (url if url.lower().startswith(
            ("http://", "https://", "//")) else f"https://{url}")
        domain = federal_link_domain(parseable)
        try:
            path = urlsplit(parseable).path.lower()
        except ValueError:
            path = ""
        if domain != "sam.gov" and not (
                domain == "usaspending.gov"
                and (path == "/award" or path.startswith("/award/"))):
            continue
        if href_counts[url]:
            href_counts[url] -= 1
            continue
        violations.append(LintViolation(
            rule="federal_link_free_string",
            detail=f"{FEDERAL_LINK_BUILDER_RULE_TEXT}; found {url}",
            excerpt=url,
        ))

    if expected_links is not None:
        actual = Counter((
            anchor.href,
            anchor.attrs.get("data-link-builder", ""),
            anchor.attrs.get("data-link-record", ""),
            anchor.attrs.get("data-link-reconciled", ""),
        ) for anchor in federal_anchors)
        expected = Counter((
            str(getattr(link, "url", "")),
            str(getattr(link, "builder", "")),
            str(getattr(link, "record_id", "")),
            "1" if bool(getattr(link, "reconciled", False)) else "0",
        ) for link in expected_links)
        if actual != expected:
            missing = list((expected - actual).elements())[:3]
            extra = list((actual - expected).elements())[:3]
            violations.append(LintViolation(
                rule="federal_link_manifest_mismatch",
                detail=("rendered federal anchors do not match the structured "
                        f"builder manifest; missing={missing or 'none'}; "
                        f"extra={extra or 'none'}"),
                excerpt="federal link manifest",
            ))
    return LintResult(ok=not violations, violations=violations)


def lint_sam_workspace_links(html_text: str) -> LintResult:
    """HARD RULE: ``sam.gov/workspace`` appears nowhere in client HTML.

    This scans the raw rendered string, including attributes, generated copy,
    comments, and metadata, so a hand edit cannot bypass the export transform.
    """
    from agents.reports.links import find_sam_workspace_links
    violations = [LintViolation(
        rule="sam_workspace_link",
        detail=(f"{SAM_WORKSPACE_LINK_RULE_TEXT}; found in section "
                f"'{issue.section}': {issue.url}"),
        excerpt=issue.url,
    ) for issue in find_sam_workspace_links(html_text)]
    return LintResult(ok=not violations, violations=violations)


# --- count reconciliation ------------------------------------------------------
# The 2026-07-06 Thinklogical assessment said "four pursue-grade notices" while
# rendering three OPP cards: prose and structure were generated in separate
# passes. Layer 2 of the fix: reconcile every numeric claim near a count noun
# against the structural counts actually rendered (data-entity markers), and
# fail the build naming the claim, its location, the claimed value, and the
# rendered value. Unclassifiable claims FLAG for human review — never silent.
# Scans visible text and comments only, never base64 payloads (lint_whitelabel
# false-positived on 'gpt' inside the GTM logo's base64).

_B64_ATTR = re.compile(r'(src\s*=\s*["\'])data:[^"\']{40,}(["\'])', re.I)
_TAG = re.compile(r"<[^>]+>")

_WORD_NUMS = {w: i for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
     "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
     "sixteen", "seventeen", "eighteen", "nineteen", "twenty"])}

# count noun -> the data-entity marker it must reconcile against
_COUNT_NOUNS = {
    "pursuit": "pursuit", "pursuits": "pursuit",
    "opportunity": "pursuit", "opportunities": "pursuit",
    "notice": "pursuit", "notices": "pursuit",
    "posting": "pursuit", "postings": "pursuit",
    "solicitation": "pursuit", "solicitations": "pursuit",
    "competitor": "competitor", "competitors": "competitor",
    "dossier": "dossier", "dossiers": "dossier",
    "agency": "agency", "agencies": "agency",
    "watchlist": "watchlist_entry",
}

_CLAIM = re.compile(
    r"\b(?P<num>\d{1,3}|" + "|".join(_WORD_NUMS) + r")\b"
    r"(?P<gap>(?:\s+[\w\-]+){0,3}?)\s+"
    r"(?P<noun>" + "|".join(sorted(_COUNT_NOUNS, key=len, reverse=True)) + r")\b",
    re.I)

# exclusions: windows/deadlines, "N of M" numbering, dollars, NAICS, dates
_EXCLUDE_AFTER = re.compile(r"^\s*(?:of\s+\d|%)", re.I)
_EXCLUDE_GAP = re.compile(r"\b(?:day|days|week|weeks|month|months|year|years|hour|hours)\b", re.I)
_EXCLUDE_BEFORE = re.compile(r"(?:\$\s*|NAICS\s+|Section\s+|#|\bof\s+)$", re.I)
_DATEISH = re.compile(r"^\s*[\-/–]\s*\d|^\s*,\s*20\d\d")


def strip_base64(html_text: str) -> str:
    """Blank base64 data-URI payloads before scanning (identity logos etc.)."""
    return _B64_ATTR.sub(r"\1data:stripped\2", html_text)


_BLOCK_TAG = re.compile(
    r"</?(?:td|th|tr|li|div|p|h[1-6]|section|table|ul|ol|header|footer)\b[^>]*>", re.I)
_VERIFIED_SPAN = re.compile(
    r'<span[^>]*\bdata-counts-verified\s*=\s*["\']1["\'][^>]*>.*?</span>', re.S | re.I)


def _visible_text(html_text: str) -> str:
    """Rendered text plus comment text; markup removed, base64 stripped.

    Two structural rules: content assembled by the Layer-1 count machinery
    (marked data-counts-verified) is ground truth by construction and leaves
    the scan; block boundaries become hard separators so adjacent table cells
    ('4' | 'Extra Pursuit') can never fuse into a phantom count claim."""
    cleaned = strip_base64(html_text)
    cleaned = _VERIFIED_SPAN.sub(" ", cleaned)
    cleaned = re.sub(r"<!--(.*?)-->", r" \1 ", cleaned, flags=re.S)
    cleaned = re.sub(r"<(script|style)\b.*?</\1>", " ", cleaned, flags=re.S | re.I)
    cleaned = _BLOCK_TAG.sub(" ¦ ", cleaned)
    return _TAG.sub(" ", cleaned)


def structural_counts(html_text: str) -> dict[str, int]:
    """What the document actually renders, from data-entity markers."""
    counts: dict[str, int] = {}
    for m in re.finditer(r'data-entity\s*=\s*["\'](\w+)["\']', strip_base64(html_text)):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


_COUNT_TOKEN = re.compile(r"\{\{COUNT(?:_WORD)?:[a-z_]+\}\}")


def lint_count_tokens(html_text: str) -> LintResult:
    """HARD RULE: no unresolved {{COUNT:*}} token survives into rendered HTML.

    A leftover token means the renderer never substituted it — either the render
    path skipped resolve_count_tokens, or the compose used a count name the
    resolver doesn't know. A raw '{{COUNT:pursue_notices}}' reaching a client is
    the failure this guards against. This is the token half of lint_counts,
    split out so the legacy capture-brief path — which emits no data-entity
    markers to reconcile against — can gate on tokens without false-positiving
    on every resolved count claim. Scans visible text only, never base64."""
    text = _visible_text(html_text)
    v = [LintViolation(rule="count_token_unresolved",
                       detail=f"unresolved count token {m.group(0)}",
                       excerpt=text[max(0, m.start() - 40):m.end() + 20].strip())
         for m in _COUNT_TOKEN.finditer(text)]
    return LintResult(ok=not v, violations=v)


def lint_counts(html_text: str) -> LintResult:
    """HARD RULE: every count claim in copy reconciles with rendered structure."""
    # unresolved count tokens are a build failure, not a silent pass
    v: list[LintViolation] = list(lint_count_tokens(html_text).violations)
    rendered = structural_counts(html_text)
    text = _visible_text(html_text)

    for m in _CLAIM.finditer(text):
        before = text[max(0, m.start() - 12):m.start()]
        after = text[m.end():m.end() + 12]
        gap = m.group("gap") or ""
        if (_EXCLUDE_BEFORE.search(before) or _EXCLUDE_AFTER.match(after)
                or _EXCLUDE_GAP.search(gap) or _DATEISH.match(after)):
            continue
        num_raw = m.group("num").lower()
        claimed = _WORD_NUMS.get(num_raw, None)
        if claimed is None:
            try:
                claimed = int(num_raw)
            except ValueError:
                continue
        noun = m.group("noun").lower()
        entity = _COUNT_NOUNS.get(noun)
        excerpt = text[max(0, m.start() - 50):m.end() + 30].strip()
        if entity is None:
            v.append(LintViolation(
                rule="count_claim_unclassified",
                detail=f"count claim '{m.group(0).strip()}' could not be classified — human review",
                excerpt=excerpt))
            continue
        actual = rendered.get(entity)
        if actual is None:
            # A claim of ZERO is verified BY the absence of elements: no pursuit
            # cards rendered IS "zero pursue-grade notices". Only a NONZERO claim
            # against an empty section is suspect (the section may have failed to
            # render). First surfaced by Osprey's live zero-opportunity compose.
            if claimed == 0:
                continue
            v.append(LintViolation(
                rule="count_claim_unverified",
                detail=(f"claim '{m.group(0).strip()}' names {claimed} {noun} but the "
                        f"document renders no '{entity}' elements to reconcile against — human review"),
                excerpt=excerpt))
        elif actual != claimed:
            v.append(LintViolation(
                rule="count_mismatch",
                detail=(f"copy claims {claimed} {noun} ('{m.group(0).strip()}') but the "
                        f"document renders {actual} {entity} element(s)"),
                excerpt=excerpt))
    return LintResult(ok=not v, violations=v)


# --- white-label ---------------------------------------------------------------
# The output is a GTM Group product, full stop: no tooling/vendor references in
# any rendered deliverable — visible text, comments, or metadata. Exemptions,
# both narrow by decision (2026-07-06): base64 payloads are never scanned (the
# GTM logo contains 'gpt' by accident of encoding), and CITED third-party news
# titles (elements marked data-thirdparty="1") may name vendors — "Booz Allen
# and OpenAI partner" is market news, not tooling; composed prose stays gated.

_BLOCKLIST = re.compile(
    r"(?i)\b(?:anthropic|claude|openai|chatgpt|gpt-?[0-9o]*|llms?|"
    r"sonnet|opus|haiku|max[\s\-_]?plan|ai[\s\-]generated|"
    r"claude-(?:opus|sonnet|haiku|fable)[\w\-.]*|anthropic_api_key|"
    r"arbiter-(?:anthropic|openai))\b")
_THIRDPARTY = re.compile(
    r'<(\w+)[^>]*\bdata-thirdparty\s*=\s*["\']1["\'][^>]*>.*?</\1>', re.S | re.I)


def lint_whitelabel(html_text: str) -> LintResult:
    """HARD RULE: no vendor/tooling terms anywhere a client could read."""
    cleaned = strip_base64(html_text)
    cleaned = _THIRDPARTY.sub(" ", cleaned)
    scannable = re.sub(r"<(script|style)\b.*?</\1>", " ", cleaned, flags=re.S | re.I)
    scannable = _TAG.sub(" ", re.sub(r"<!--(.*?)-->", r" \1 ", scannable, flags=re.S))
    v: list[LintViolation] = []
    for m in _BLOCKLIST.finditer(scannable):
        start = max(0, m.start() - 40)
        v.append(LintViolation(
            rule="vendor_term_in_deliverable",
            detail=f"'{m.group(0)}' must never appear in a GTM Group deliverable",
            excerpt=scannable[start:m.end() + 40].strip()))
    return LintResult(ok=not v, violations=v)


# --- client bleed --------------------------------------------------------------
# Proven failure mode (2026-07-09): the Osprey brief shipped Recorded Future
# channel copy from a shared reference block — a mail-merge tell that kills a
# fractional consultancy's positioning on the page it appears. Same mechanism
# as the vendor blocklist, new list: every OTHER known client's name and
# curated product terms are grepped against every outbound brief. Cited
# third-party evidence (data-thirdparty="1") is exempt — a prior client in
# cited news or a structured award/partner slot is coverage, not bleed;
# base64 is never scanned.

_TERMS_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "client_terms.json"
_REVIEW_DIR = Path(__file__).resolve().parents[2] / "data" / "review"


def known_client_terms() -> dict[str, list[str]]:
    """client name -> blocklist terms. Names auto-derive from every review
    packet on disk; data/reference/client_terms.json adds curated product
    terms (distinctive strings only — short abbreviations false-positive)."""
    terms: dict[str, list[str]] = {}
    for p in sorted(_REVIEW_DIR.glob("*.review.json")):
        try:
            packet = json.loads(p.read_text())
            name = (packet.get("client_name")
                    or packet.get("client") or "").strip()
        except (OSError, json.JSONDecodeError):
            continue
        if name:
            terms.setdefault(name, [name])
    try:
        extra = json.loads(_TERMS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        extra = {}
    for name, extras in extra.items():
        if name.startswith("_") or not isinstance(extras, list):
            continue
        terms.setdefault(name, [name]).extend(
            t for t in extras if t and t not in terms[name])
    return terms


def _scannable(html_text: str) -> str:
    cleaned = strip_base64(html_text)
    cleaned = _THIRDPARTY.sub(" ", cleaned)
    cleaned = re.sub(r"<(script|style)\b.*?</\1>", " ", cleaned, flags=re.S | re.I)
    return _TAG.sub(" ", re.sub(r"<!--(.*?)-->", r" \1 ", cleaned, flags=re.S))


def lint_client_bleed(html_text: str, client_name: Optional[str] = None) -> LintResult:
    """HARD RULE: no other client's name or product terms in an outbound
    brief. client_name defaults from the <title> (every brief carries
    '<client> · Federal Opportunity Assessment')."""
    if client_name is None:
        m = re.search(r"<title>(.*?)(?:·|</title>)", html_text, re.S)
        client_name = (m.group(1).strip() if m else "")
    scannable = _scannable(html_text)
    v: list[LintViolation] = []
    cur = client_name.strip().lower()
    for name, terms in known_client_terms().items():
        if name.strip().lower() == cur:
            continue
        for t in terms:
            for m in re.finditer(r"(?i)(?<![\w])" + re.escape(t) + r"(?![\w])",
                                 scannable):
                start = max(0, m.start() - 50)
                v.append(LintViolation(
                    rule="client_bleed",
                    detail=(f"'{m.group(0)}' belongs to client '{name}' — "
                            f"another client's name/product in this brief is "
                            f"a mail-merge tell; the deliverable cannot ship"),
                    excerpt=scannable[start:m.end() + 50].strip()))
    return LintResult(ok=not v, violations=v)


# --- em dashes -------------------------------------------------------------------
# House style bans em dashes in deliverable copy (the AI-tell). Cited
# third-party headlines stay verbatim (exempt); everything else fails.

def lint_emdash(html_text: str) -> LintResult:
    """HARD RULE: no em dash in any rendered copy GTM authored."""
    scannable = _scannable(html_text)
    v = [LintViolation(
            rule="emdash_in_copy",
            detail="em dash in deliverable copy — banned by house style",
            excerpt=scannable[max(0, m.start() - 60):m.end() + 60].strip())
         for m in re.finditer("—", scannable)]
    return LintResult(ok=not v, violations=v)


_SCREEN_CENSUS_RE = re.compile(
    r'<span[^>]*data-screen-census="1"[^>]*>(.*?)</span>', re.S | re.I)
_CENSUS_ATTR_RE = re.compile(
    r'data-(screened|census|complete|held-closed)="(\d+)"')


def lint_screen_census(html_text: str) -> LintResult:
    """L17: a screening count that never varies is a ceiling, not a census.
    Every screened-N line carries the census mark; when the underlying pull
    was INCOMPLETE (complete=0), the visible line MUST reconcile as
    'screened X of Y' — a partial pull rendering as a complete screen fails
    the build."""
    v: list[LintViolation] = []
    for m in _SCREEN_CENSUS_RE.finditer(html_text):
        attrs = dict((k, int(val)) for k, val
                     in _CENSUS_ATTR_RE.findall(m.group(0)))
        body = re.sub(r"<[^>]+>", " ", m.group(1))
        body_low = body.lower()
        census = attrs.get("census", 0)
        screened = attrs.get("screened", 0)
        held_closed = attrs.get("held-closed", 0) == 1
        if held_closed:
            if "held closed" not in body_low:
                v.append(LintViolation(
                    rule="strict_live_hold_not_disclosed",
                    detail=("strict live census is held closed but copy does not "
                            "disclose it"),
                    excerpt=body.strip()[:160]))
            held_needle = f"{screened:,} of {census:,}"
            if census > 0 and held_needle not in body:
                v.append(LintViolation(
                    rule="strict_live_hold_unreconciled",
                    detail=("held-closed copy does not reconcile accepted rows "
                            f"against the census as '{held_needle}'"),
                    excerpt=body.strip()[:160]))
        if screened == 0 and census > 0:
            zero_needle = f"0 of {census:,}"
            if zero_needle not in body:
                v.append(LintViolation(
                    rule="zero_screen_census_unreconciled",
                    detail=(f"zero report-truth rows do not reconcile against the "
                            f"nonzero census ({census:,}) as '{zero_needle}'"),
                    excerpt=body.strip()[:160]))
        if census > 0 and "pull returned none" in body_low:
            v.append(LintViolation(
                rule="nonzero_census_called_empty",
                detail=(f"copy says the pull returned none despite a "
                        f"{census:,}-record census"),
                excerpt=body.strip()[:160]))
        if attrs.get("complete", 1) == 1:
            continue
        needle = f"of {census:,}"
        if needle not in body:
            v.append(LintViolation(
                rule="partial_screen_as_complete",
                detail=(f"screen census incomplete (census {census:,}) but the "
                        f"line does not reconcile as 'screened X {needle}'"),
                excerpt=body.strip()[:160]))
    return LintResult(ok=not v, violations=v)


_RECOMPETE_CLAIM_RE = re.compile(
    r"recompete[^.!?]{0,40}?\b(posted|is live|has dropped|released|is out|"
    r"went live|hit the street)\b", re.I)
_NOTICE_ID_TOKEN_RE = re.compile(r"\b[A-Z0-9][A-Z0-9]{2,}[-_]?[A-Z0-9]{4,}\b")


def lint_notice_tier_claims(html_text: str) -> LintResult:
    """L15 tier discipline: expiry math is PROGRAM-tier evidence; the claim
    that a recompete notice EXISTS is NOTICE-tier and needs the verified
    notice behind it. Any sentence asserting a recompete posted/released
    without a notice-id token in the same sentence fails."""
    text = _visible_text(html_text)
    v = []
    for s in _sentences(text):
        m = _RECOMPETE_CLAIM_RE.search(s)
        if not m:
            continue
        if _NOTICE_ID_TOKEN_RE.search(s) or "notice id" in s.lower():
            continue
        v.append(LintViolation(
            rule="unverified_notice_claim",
            detail=("sentence asserts a recompete notice exists without a "
                    "verified notice ID; expiry math never implies a posting"),
            excerpt=s[:160]))
    return LintResult(ok=not v, violations=v)


_ENTITY_ROW_RE = re.compile(
    r'<tr data-entity="(?:competitor|competitor_lane)"[^>]*>\s*<td[^>]*>'
    r'(?:<[^>]+>)*([^<]+)<', re.S)


def lint_entity_lineage(html_text: str) -> LintResult:
    """L14: two rows in one table must never resolve to the same corporate
    door without having been merged. Catches the Perspecta/Peraton class of
    defect at render time, permanently."""
    from tools.entity_lineage import door_key
    v = []
    seen: dict = {}
    for m in _ENTITY_ROW_RE.finditer(html_text):
        name = m.group(1).strip()
        if not name or name.startswith("Locked"):
            continue
        key = door_key(name)
        if key in seen and seen[key] != name:
            v.append(LintViolation(
                rule="entity_lineage",
                detail=(f"'{seen[key]}' and '{name}' resolve to one corporate "
                        f"door but render as two rows; merge them"),
                excerpt=name))
        else:
            seen.setdefault(key, name)
    return LintResult(ok=not v, violations=v)


_POP_BLOCK_RE = re.compile(
    r'<div class="vb-comp" data-pop="(?P<pop>\w+)" '
    r'data-pop-count="(?P<count>\d+)">(?P<body>.*?)</div>', re.S)


def lint_scoreboard_populations(html_text: str) -> LintResult:
    """L13 (2026-07-10): every scoreboard figure draws its count AND its named
    examples from ONE population. The displayed count must equal the
    population count, named examples plus 'and X more' must reconcile to it,
    and the remainder can never be negative. Cross-population arithmetic
    (a count from one population beside names from another) fails here."""
    v = []
    for m in _POP_BLOCK_RE.finditer(html_text):
        pop, count, body = m.group("pop"), int(m.group("count")), m.group("body")
        lead = re.search(r"<b>(?:<[^>]+>)?(\d+)", body)
        names = body.count('data-pop-name="1"')
        more_m = re.search(r"and (-?\d+) more", body)
        more = int(more_m.group(1)) if more_m else 0
        if not lead or int(lead.group(1)) != count:
            v.append(LintViolation(
                rule="scoreboard_population",
                detail=f"{pop}: displayed count does not match its population",
                excerpt=body[:120]))
        elif more < 0:
            v.append(LintViolation(
                rule="scoreboard_population",
                detail=f"{pop}: negative remainder; arithmetic crossed populations",
                excerpt=body[:120]))
        elif names + more != count:
            v.append(LintViolation(
                rule="scoreboard_population",
                detail=(f"{pop}: {names} named + {more} more != population "
                        f"of {count}"),
                excerpt=body[:120]))
    return LintResult(ok=not v, violations=v)


# --- identity assets ---------------------------------------------------------
# The capture brief's identity assets are fixed constants of the locked,
# client-approved template: the GTM logo (img#gtmLogoImg, base64 payload), the
# cover constellation art (svg.cover-art), and a client logo element inside
# .cover-logo. A brief missing any of them is a broken render, not a variant.
_GTM_IMG = re.compile(
    r'<img[^>]*\bid\s*=\s*["\']gtmLogoImg["\'][^>]*\bsrc\s*=\s*["\']data:[^"\']+;base64,[A-Za-z0-9+/=]{100,}'
    r'|<img[^>]*\bsrc\s*=\s*["\']data:[^"\']+;base64,[A-Za-z0-9+/=]{100,}[^>]*\bid\s*=\s*["\']gtmLogoImg["\']',
    re.I,
)
_COVER_ART = re.compile(r'<svg[^>]*\bclass\s*=\s*["\'][^"\']*\bcover-art\b', re.I)
_COVER_LOGO_OPEN = re.compile(r'<div[^>]*\bclass\s*=\s*["\'][^"\']*\bcover-logo\b[^>]*>', re.I)


def lint_gtm_logo(html_text: str) -> LintResult:
    """HARD RULE for every client HTML document: the GTM logo (img#gtmLogoImg
    with a base64 payload) must be present. The assessment's full identity set
    is checked by lint_brief_identity; other documents carry at least the logo."""
    if _GTM_IMG.search(html_text):
        return LintResult(ok=True)
    return LintResult(ok=False, violations=[LintViolation(
        rule="missing_gtm_logo",
        detail="no <img id=\"gtmLogoImg\"> with a base64 data payload")])


def lint_brief_identity(html_text: str) -> LintResult:
    """HARD RULE: every capture brief carries the locked template's identity
    assets — GTM logo img (id=gtmLogoImg, base64 payload), svg.cover-art, and a
    client logo element inside .cover-logo. Any absence fails the build."""
    v: list[LintViolation] = []

    if not _GTM_IMG.search(html_text):
        v.append(LintViolation(
            rule="missing_gtm_logo",
            detail="no <img id=\"gtmLogoImg\"> with a base64 data payload"))

    if not _COVER_ART.search(html_text):
        v.append(LintViolation(
            rule="missing_cover_art",
            detail="no <svg class=\"cover-art\"> element"))

    logo_ok = False
    m = _COVER_LOGO_OPEN.search(html_text)
    if m:
        close = html_text.find("</div>", m.end())
        inner = html_text[m.end(): close if close != -1 else m.end() + 400]
        logo_ok = "<svg" in inner.lower()
    if not logo_ok:
        v.append(LintViolation(
            rule="missing_client_logo",
            detail="no client logo element (svg) inside .cover-logo"))

    return LintResult(ok=not v, violations=v)


# --- contact rendering -------------------------------------------------------
# A rendered contact must always carry its grade and its source. Renderers wrap
# each contact in an element marked data-contact, and that element must carry
# data-grade="A|B|C" and a non-empty data-source. The backstop below also
# catches a federal (.gov/.mil) email or a tel: link rendered without such a
# block at all — i.e. a POC someone forgot to grade/source.
_CONTACT_OPEN = re.compile(r"<\s*([a-zA-Z][\w-]*)[^>]*\bdata-contact\b[^>]*>", re.I)
_GRADE_ATTR = re.compile(r'data-grade\s*=\s*["\']?\s*([ABC])\b', re.I)
_SOURCE_ATTR = re.compile(r'data-source\s*=\s*["\']([^"\']+)["\']', re.I)
_FED_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]*\.(?:gov|mil)\b", re.I)
_TEL_RE = re.compile(r"tel:\+?[\d\-().\s]{7,}", re.I)


def lint_contact_rendering(html_text: str) -> LintResult:
    """HARD RULE: any client-facing artifact rendering a contact must include the
    channel's grade and source. A contact without a grade is a claim of currency
    we never verified; a contact without a source can't be traced to an official
    publication. Either one fails the build."""
    v: list[LintViolation] = []

    valid_spans: list[tuple[int, int]] = []
    for m in _CONTACT_OPEN.finditer(html_text):
        tag_end = html_text.find(">", m.start())
        opening = html_text[m.start(): (tag_end + 1) if tag_end != -1 else len(html_text)]
        name = (re.match(r"<\s*([a-zA-Z][\w-]*)", opening) or [None, "span"])[1]
        close = html_text.find(f"</{name}", tag_end if tag_end != -1 else m.end())
        end = close if close != -1 else len(html_text)

        has_grade = bool(_GRADE_ATTR.search(opening))
        has_source = bool(_SOURCE_ATTR.search(opening))
        if has_grade and has_source:
            valid_spans.append((m.start(), end))
        else:
            missing = []
            if not has_grade:
                missing.append("grade (data-grade=A|B|C)")
            if not has_source:
                missing.append("source (data-source=…)")
            v.append(LintViolation(
                rule="contact_missing_grade_or_source",
                detail="contact block missing " + " and ".join(missing),
                excerpt=opening[:120]))

    for rx, kind in ((_FED_EMAIL_RE, "federal email"), (_TEL_RE, "phone")):
        for m in rx.finditer(html_text):
            if not any(a <= m.start() < b for a, b in valid_spans):
                start = max(0, m.start() - 40)
                v.append(LintViolation(
                    rule="ungraded_contact",
                    detail=f"{kind} rendered outside a graded, sourced contact block",
                    excerpt=html_text[start:m.end() + 20].strip()))

    return LintResult(ok=not v, violations=v)


def lint_text(text: str, valid_fact_ids: set[str] | None = None) -> LintResult:
    v: list[LintViolation] = []
    low = text.lower()

    for phrase in BANNED_PHRASES:
        for m in re.finditer(r"\b" + re.escape(phrase), low):
            start = max(0, m.start() - 30)
            v.append(LintViolation(
                rule="banned_phrase",
                detail=f"'{phrase}'",
                excerpt=text[start:m.end() + 30].strip(),
            ))

    if "—" in text:  # em dash — the house style forbids it
        i = text.index("—")
        v.append(LintViolation(rule="em_dash", detail="use a comma or a period",
                               excerpt=text[max(0, i - 30):i + 30].strip()))

    if valid_fact_ids is not None:
        cited = {f"F{m}" for m in _CITE_RE.findall(text)}
        for bad in sorted(cited - valid_fact_ids):
            v.append(LintViolation(rule="unknown_citation",
                                   detail=f"[{bad}] cites a fact that does not exist"))
        for s in _sentences(text):
            if _NUM_RE.search(s) and not _CITE_RE.search(s):
                v.append(LintViolation(
                    rule="uncited_number",
                    detail="sentence contains a figure but cites no fact",
                    excerpt=s[:90],
                ))

    return LintResult(ok=not v, violations=v)


def lint_svg_geometry(html_text: str) -> LintResult:
    """R13 SVG render safety (2026-07-09): every inline SVG's elements must
    clear an 8-unit margin inside the viewBox. Root incident: money-in-
    motion axis labels clipped at x=0 (glyph side-bearing extends slightly
    negative at the edge). The renderer auto-fixes deterministically; this
    gate catches whatever survives."""
    from agents.reports.svg_safety import _SVG_RE, svg_margin_violations
    violations = []
    for m in _SVG_RE.finditer(html_text):
        for v in svg_margin_violations(m.group(0)):
            violations.append(LintViolation(rule="svg_geometry", detail=v))
    return LintResult(ok=not violations, violations=violations)
