"""Golden skeleton split (GOLDEN_BUILD Phase 3a).

The golden reference splits ONCE into a skeleton (doctype, head, CSS, JS,
hero and edit chrome, kept byte-verbatim; this is how seals, logos, and
formatting survive untouched) and the content region (the numbered report
bands, section 01 "forecast" through section 08 "evidence"). The skeleton is
reused as-is; only the content region is ever composed.

The split is anchor-exact and reversible: splice(skeleton, content) must
reproduce the golden file byte-for-byte (test-pinned).
"""

from __future__ import annotations

import re

CONTENT_PLACEHOLDER = "<!-- LILA:GOLDEN-CONTENT -->"
# 2026-07-24 (Red Hat full press, operator-directed): the hero and ticker are
# CLIENT-IDENTITY surfaces, so they compose with the bands instead of riding
# the skeleton as Riverbed golden-day text. Content region = the hero section
# through the Next-steps aside (everything inside <main> after the utility
# header); the skeleton keeps head/CSS/JS, the GTM utility chrome, and the
# edit script.
CONTENT_START_ANCHOR = '<section class="sb-hero"'
CONTENT_END_ANCHOR = "</main>"


class GoldenSplitError(RuntimeError):
    """The golden reference no longer matches the split anchors. Loud."""


def split_golden(html: str) -> tuple[str, str]:
    """(skeleton_with_placeholder, content_region) from the golden bytes."""
    start = html.find(CONTENT_START_ANCHOR)
    if start < 0:
        raise GoldenSplitError(
            "content start anchor not found (section class=sb-hero)")
    if html.find(CONTENT_START_ANCHOR, start + 1) >= 0:
        raise GoldenSplitError("content start anchor is not unique")
    end = html.rfind(CONTENT_END_ANCHOR)
    if end < 0 or end <= start:
        raise GoldenSplitError("content end anchor (</main>) not after start")
    if CONTENT_PLACEHOLDER in html:
        raise GoldenSplitError("placeholder token collides with golden bytes")
    skeleton = html[:start] + CONTENT_PLACEHOLDER + html[end:]
    return skeleton, html[start:end]


def splice(skeleton: str, content: str) -> str:
    """Replace exactly one placeholder with the composed content region."""
    if skeleton.count(CONTENT_PLACEHOLDER) != 1:
        raise GoldenSplitError(
            f"skeleton must contain exactly one placeholder; "
            f"found {skeleton.count(CONTENT_PLACEHOLDER)}")
    return skeleton.replace(CONTENT_PLACEHOLDER, content, 1)


# --------------------------------------------------------------------------- #
# Embedded assets inside the content region (portraits, seals): the composer
# cannot reproduce base64 payloads, so the prompt copy carries deterministic
# placeholders and the press restores the original bytes afterwards. Design
# inheritance stays byte-verbatim; the model only ever repositions the tags.
# --------------------------------------------------------------------------- #
_ASSET_SRC_RE = re.compile(r'src="(data:image/[^"]+)"')
ASSET_TOKEN_FMT = "data:,LILA-ASSET-%d"


def elide_assets(content: str) -> tuple[str, dict[str, str]]:
    """(elided_content, {placeholder_src: original_src})."""
    mapping: dict[str, str] = {}

    def _swap(match: re.Match) -> str:
        token = ASSET_TOKEN_FMT % len(mapping)
        mapping[token] = match.group(1)
        return f'src="{token}"'

    return _ASSET_SRC_RE.sub(_swap, content), mapping


def restore_assets(content: str, mapping: dict[str, str]) -> str:
    """Swap every surviving placeholder back to its original data URI."""
    for token, original in mapping.items():
        content = content.replace(f'src="{token}"', f'src="{original}"')
    return content


# --------------------------------------------------------------------------- #
# Skeleton identity branding (Red Hat full press, 2026-07-24): the skeleton's
# head/utility chrome carries the golden client's identity strings (title,
# meta, report id, download name, brand word, brand logo slot). For a
# non-golden client these swap deterministically, exact-count enforced, so a
# drifted golden fails loudly instead of shipping wrong-client chrome.
# --------------------------------------------------------------------------- #
_NEUTRAL_LOGO_SRC = ("data:image/gif;base64,"
                     "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def _swap_pattern_once(text: str, pattern: str, new: str, label: str) -> str:
    """Replace exactly one regex match, or fail loudly.

    ANCHOR ON THE ATTRIBUTE, NOT ON ITS VALUE (2026-08-07). The literal
    swaps below were pinned to the values a 2026-07-22 golden happened to
    carry, so every later skeleton revision broke branding with "found 0"
    even though the chrome was structurally identical. The exact-count
    guarantee is what stops wrong-client chrome from shipping and is kept
    exactly as strict; only the anchor moves from the value to the field.
    """
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise GoldenSplitError(
            f"skeleton branding expects exactly one {label}; found "
            f"{len(matches)}")
    return re.sub(pattern, lambda _m: new, text, count=1)


def _swap_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise GoldenSplitError(
            f"skeleton branding expects exactly one {label}; found {count}")
    return text.replace(old, new, 1)


_NAV_EVIDENCE_RE = re.compile(
    r'(<a[^>]*href="#evidence"[^>]*>.*?</a>)', re.S)


# Short nav labels per contract band id; the sequence itself comes from the
# contract, so a renamed or reordered band renames its nav entry with it.
_NAV_LABELS = {
    "decisions": "Decisions", "forward": "Forward", "clocks": "Clocks",
    "paper": "Paper", "agencies": "Agencies", "competitive": "Competitive",
    "events": "Events", "method": "Method",
}


def add_band_nav(skeleton: str, *, with_events: bool = True) -> str:
    """REBUILD the section nav from the contract band sequence.

    CONTRACT (2026-08-03): the inherited golden nav anchors the retired
    ten-band grammar and even carries a banned L7 term in a label, so it is
    replaced wholesale with entries derived from REPORT_CONTRACT.md §1. The
    with_events parameter is retained for callers but inert: every contract
    band is always present.
    """
    del with_events
    from agents.golden_press.contract import contract_section_ids
    entries = "\n        ".join(
        f'<a href="#{band_id}">{_NAV_LABELS.get(band_id, band_id.title())}</a>'
        for band_id in contract_section_ids())
    rebuilt, n = re.subn(
        r'(<nav class="sb-nav"[^>]*>).*?(</nav>)',
        lambda m: m.group(1) + "\n        " + entries + "\n      "
        + m.group(2),
        skeleton, count=1, flags=re.S)
    if not n:
        raise GoldenSplitError("skeleton carries no sb-nav to rebuild")
    return rebuilt


def brand_skeleton(skeleton: str, *, client_name: str, slug: str,
                   stamp: str) -> str:
    """The pressing client's identity in the skeleton chrome, mechanically."""
    display = " ".join(str(client_name).split())
    underscored = re.sub(r"[^A-Za-z0-9]+", "_", display).strip("_")
    out = skeleton
    out = _swap_pattern_once(
        out, r'data-report-id="[^"]*"',
        f'data-report-id="{slug}-candidate-review-golden-{stamp}"',
        "report id")
    out = _swap_pattern_once(
        out, r'data-download-name="[^"]*"',
        f'data-download-name="{underscored}_Federal_Opportunity_Assessment_'
        f'CANDIDATE_REVIEW_EDITABLE_{stamp}.html"',
        "download name")
    out = _swap_pattern_once(
        out, r"<title>[^<]*</title>",
        f"<title>{display} · Federal Opportunity Pre-Assessment</title>",
        "title tag")
    out = _swap_pattern_once(
        out, r'<meta name="description" content="[^"]*"',
        f'<meta name="description" content="{display} federal opportunity '
        f'pre-assessment, pressed {stamp}."',
        "meta description")
    out = _swap_pattern_once(
        out, r'<span class="sb-brand-word">[^<]*</span>',
        f'<span class="sb-brand-word">{display.upper()}</span>',
        "brand word")
    # The header carries TWO logo slots: the client's and GTM's own mark.
    # Only the client's is rebranded; the pressing firm's mark is never
    # swapped for a client's name.
    out = _swap_pattern_once(
        out, r'alt="[^"]*logo" data-logo-id="header-(?!gtm-logo)[^"]*-logo"',
        f'alt="{display} logo slot" data-logo-id="header-{slug}-logo"',
        "brand logo slot")
    # The golden client's actual mark never ships on another client: the
    # header logo slot goes neutral; the operator drops the real mark via
    # the existing editable slot chrome.
    brand_img = re.search(
        r'<img[^>]*data-logo-id="header-' + re.escape(slug)
        + r'-logo"[^>]*>', out)
    if not brand_img:
        raise GoldenSplitError("skeleton branding lost the header logo slot")
    tag = brand_img.group(0)
    neutral = re.sub(r'src="[^"]*"', f'src="{_NEUTRAL_LOGO_SRC}"', tag, 1)
    out = out.replace(tag, neutral, 1)
    return out
