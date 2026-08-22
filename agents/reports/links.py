"""Deterministic post-render normalization for client-facing links.

This module deliberately operates on the rendered HTML string.  It does not
parse and reserialize the document, so bytes outside an eligible link remain
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import html as html_lib
from html.parser import HTMLParser
import re
from typing import Iterable
from urllib.parse import urlsplit


_SAM_WORKSPACE_HREF = re.compile(
    r"(?P<prefix>(?<!\S)(?i:href)\s*=\s*(?P<quote>[\"']?))"
    r"(?P<url>https?://sam\.gov/workspace/contract/opp/"
    r"(?P<guid>[0-9a-f]{32})/view)(?=$|[?#\s\"'<>])"
)
_SAM_WORKSPACE_URL = re.compile(
    r"(?:https?://)?sam\.gov/workspace[^\s\"'<>)]*"
)
_SECTION_TAG = re.compile(r"<(?P<close>/)?section\b(?P<attrs>[^>]*)>", re.I)
_SECTION_ATTR = re.compile(
    r"\b(?P<name>id|aria-label|class)\s*=\s*"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.I | re.S,
)
_HEADING = re.compile(
    r"<h[1-6]\b[^>]*>(?P<body>.*?)</h[1-6]\s*>", re.I | re.S
)
_TAG = re.compile(r"<[^>]+>")
_USASPENDING_GENERATED_ID = re.compile(
    r"(?:CONT_AWD|ASST_NON)_[A-Za-z0-9_-]+"
)
_SAM_NOTICE_GUID = re.compile(r"[0-9a-f]{32}")

USASPENDING_AWARD_BUILDER = "usaspending_award"
SAM_NOTICE_BUILDER = "sam_notice"


@dataclass(frozen=True)
class CanonicalFederalLink:
    """A client link emitted from a structured primary-record identity.

    ``reconciled`` is supplied by the caller after the record identity passes
    the existing freshness/dispute/absence gates.  The renderer carries this
    provenance into data attributes so client-HTML lint can reject an
    identical-looking URL that was typed by hand.
    """

    url: str
    builder: str
    record_id: str
    reconciled: bool = False


@dataclass(frozen=True)
class ParsedAnchor:
    """One browser-effective anchor start tag from rendered client HTML."""

    href: str
    section: str
    attrs: dict[str, str]
    duplicate_attrs: tuple[str, ...]
    raw: str


class _AnchorParser(HTMLParser):
    """Parse anchors without regex ambiguity around quoting or duplicates."""

    _LABEL_REGION_TAGS = {
        "article", "aside", "div", "footer", "header", "main", "nav",
        "section",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[ParsedAnchor] = []
        self._section = "document"
        self._section_stack: list[str] = []
        self._label_regions: list[tuple[str, str | None]] = []
        self._heading: str | None = None
        self._heading_text: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        normalized = [(str(name).lower(), "" if value is None else str(value))
                      for name, value in attrs]
        values: dict[str, str] = {}
        counts: dict[str, int] = {}
        for name, value in normalized:
            counts[name] = counts.get(name, 0) + 1
            values.setdefault(name, value)  # browsers honor the first value
        if tag == "section":
            self._section_stack.append(self._section)
            self._section = next((
                " ".join(values.get(name, "").split())
                for name in ("aria-label", "id", "class")
                if values.get(name, "").strip()
            ), "section")
        aria_label = " ".join(values.get("aria-label", "").split())
        if tag in self._LABEL_REGION_TAGS:
            # Retain unlabeled containers as nesting sentinels so closing an
            # inner <div> cannot accidentally end an outer labeled <div>.
            self._label_regions.append((tag, aria_label or None))
        if tag == "section":
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = tag
            self._heading_text = []
            return
        if tag != "a":
            return
        duplicates = tuple(sorted(name for name, count in counts.items()
                                  if count > 1))
        self.anchors.append(ParsedAnchor(
            href=values.get("href", "").strip(),
            section=next(
                (label for _tag, label in reversed(self._label_regions)
                 if label),
                self._section,
            ),
            attrs=values,
            duplicate_attrs=duplicates,
            raw=self.get_starttag_text() or "<a>",
        ))

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in self._LABEL_REGION_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data) -> None:
        if self._heading is not None:
            self._heading_text.append(data)

    def handle_endtag(self, tag) -> None:
        lowered = tag.lower()
        if self._heading == lowered:
            label = " ".join("".join(self._heading_text).split())
            if label:
                self._section = label
            self._heading = None
            self._heading_text = []
        for index in range(len(self._label_regions) - 1, -1, -1):
            if self._label_regions[index][0] == lowered:
                del self._label_regions[index:]
                break
        if lowered == "section" and self._section_stack:
            self._section = self._section_stack.pop()


def parse_client_anchors(html_text: str) -> tuple[ParsedAnchor, ...]:
    """Return anchors as a browser parses them, including unquoted hrefs."""
    parser = _AnchorParser()
    parser.feed(html_text or "")
    parser.close()
    return tuple(parser.anchors)


def federal_link_domain(url: str) -> str | None:
    """Identify SAM/USAspending hosts even in noncanonical URL spellings."""
    try:
        parsed = urlsplit(str(url))
        # Browsers and URL libraries disagree about percent escapes and
        # backslashes in an authority.  Never treat an ambiguous authority as
        # a trustworthy hostname; the link-integrity gate rejects it before
        # any request is made.
        if "%" in parsed.netloc or "\\" in parsed.netloc:
            return None
        host = (parsed.hostname or "").encode("idna").decode("ascii")
        host = host.lower().rstrip(".")
    except (UnicodeError, ValueError):
        return None
    if host == "sam.gov" or host.endswith(".sam.gov"):
        return "sam.gov"
    if host == "usaspending.gov" or host.endswith(".usaspending.gov"):
        return "usaspending.gov"
    return None


def build_usaspending_award_link(
    generated_internal_id: str,
    *,
    reconciled: bool = False,
) -> CanonicalFederalLink:
    """Build the one allowed USAspending award URL form from its GID."""
    record_id = str(generated_internal_id or "")
    if not record_id or not _USASPENDING_GENERATED_ID.fullmatch(record_id):
        raise ValueError(
            "USAspending award links require a generated internal ID, not "
            "a PIID, URL, path, query, or whitespace")
    return CanonicalFederalLink(
        url=f"https://www.usaspending.gov/award/{record_id}",
        builder=USASPENDING_AWARD_BUILDER,
        record_id=record_id,
        reconciled=reconciled,
    )


def build_sam_notice_link(
    notice_guid: str,
    *,
    reconciled: bool = False,
) -> CanonicalFederalLink:
    """Build the one allowed public SAM notice URL form from its GUID."""
    record_id = str(notice_guid or "")
    if not _SAM_NOTICE_GUID.fullmatch(record_id):
        raise ValueError(
            "SAM notice links require exactly 32 lowercase hexadecimal "
            "characters, not a URL, path, query, or workspace link")
    return CanonicalFederalLink(
        url=f"https://sam.gov/opp/{record_id}/view",
        builder=SAM_NOTICE_BUILDER,
        record_id=record_id,
        reconciled=reconciled,
    )


@dataclass(frozen=True)
class SamLinkRewrite:
    """One eligible workspace href rewritten to its public equivalent."""

    source_url: str
    public_url: str
    section: str


@dataclass(frozen=True)
class SamWorkspaceLink:
    """One workspace URL still present after the deterministic rewrite."""

    url: str
    section: str


@dataclass(frozen=True)
class SamLinkNormalization:
    """The normalized HTML plus an occurrence-level audit trail."""

    html: str
    rewrites: tuple[SamLinkRewrite, ...]
    remaining: tuple[SamWorkspaceLink, ...]

    @property
    def rewritten_urls(self) -> tuple[str, ...]:
        """Unique public URLs, in first-appearance order."""
        return tuple(dict.fromkeys(item.public_url for item in self.rewrites))


@dataclass
class _Section:
    start: int
    body_start: int
    end: int
    attrs: dict[str, str]


def _section_spans(html_text: str) -> list[_Section]:
    """Return section spans without reserializing or assuming valid HTML."""
    spans: list[_Section] = []
    stack: list[_Section] = []
    for match in _SECTION_TAG.finditer(html_text):
        if match.group("close"):
            if stack:
                section = stack.pop()
                section.end = match.end()
                spans.append(section)
            continue
        attrs = {
            item.group("name").lower(): html_lib.unescape(item.group("value"))
            for item in _SECTION_ATTR.finditer(match.group("attrs"))
        }
        stack.append(_Section(match.start(), match.end(), len(html_text), attrs))
    spans.extend(stack)
    return spans


def _section_label(html_text: str, offset: int, spans: list[_Section]) -> str:
    enclosing = [section for section in spans
                 if section.start <= offset < section.end]
    if not enclosing:
        return "document"
    section = max(enclosing, key=lambda item: item.start)
    body = html_text[section.body_start:section.end]
    heading = _HEADING.search(body)
    if heading:
        label = html_lib.unescape(_TAG.sub(" ", heading.group("body")))
        label = " ".join(label.split())
        if label:
            return label
    for attr in ("aria-label", "id", "class"):
        label = " ".join(section.attrs.get(attr, "").split())
        if label:
            return label
    return "section"


def find_sam_workspace_links(html_text: str) -> tuple[SamWorkspaceLink, ...]:
    """Find every residual SAM workspace URL and its enclosing section."""
    spans = _section_spans(html_text)
    return tuple(
        SamWorkspaceLink(
            url=match.group(0),
            section=_section_label(html_text, match.start(), spans),
        )
        for match in _SAM_WORKSPACE_URL.finditer(html_text)
    )


def normalize_sam_workspace_links(html_text: str) -> SamLinkNormalization:
    """Rewrite eligible SAM workspace hrefs without changing other bytes.

    Only the matched URL prefix is replaced.  A query string or fragment after
    ``/view`` is retained, as are quote style, attribute spacing, and every
    non-SAM link.
    """
    spans = _section_spans(html_text)
    rewrites: list[SamLinkRewrite] = []

    def replace(match: re.Match[str]) -> str:
        public_url = f"https://sam.gov/opp/{match.group('guid')}/view"
        rewrites.append(SamLinkRewrite(
            source_url=match.group("url"),
            public_url=public_url,
            section=_section_label(html_text, match.start(), spans),
        ))
        return match.group("prefix") + public_url

    normalized = _SAM_WORKSPACE_HREF.sub(replace, html_text)
    return SamLinkNormalization(
        html=normalized,
        rewrites=tuple(rewrites),
        remaining=find_sam_workspace_links(normalized),
    )


def format_workspace_link_issue(issue: SamWorkspaceLink) -> str:
    """Human-readable release-gate report entry."""
    return (f"SAM.gov workspace URL requires login in section "
            f"'{issue.section}': {issue.url}")


def verify_rewritten_sam_links(
    public_urls: Iterable[str],
    *,
    api_key: str | None = None,
    as_of: date | None = None,
) -> tuple[str, ...]:
    """Deprecated compatibility shim: SAM links are never HTTP-verified.

    The Link Integrity Gate proves SAM notice links exclusively through the
    reconciled primary-record identity.  Keeping this no-op avoids breaking
    older callers while structurally removing the former network path.
    """
    del public_urls, api_key, as_of
    return ()
