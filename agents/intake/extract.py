"""Discrete product / NAICS / exclusion extract for the intake dossier.

Probe findings and scrape pages arrive as essays, markdown, or load-error
pages. Offerings, keywords, and retrieval units must be short product or
capability names with real excerpts. This module is deterministic: no
model call. An optional structure() pass may enrich the same surface.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from pydantic import BaseModel, Field


MAX_OFFERING_CHARS = 80
MAX_OFFERING_WORDS = 8

_WS = re.compile(r"\s+")
_GARBAGE = re.compile(
    r"\b(error loading|load error|loading error|browser error|"
    r"enable javascript|enable js|access denied|just a moment|"
    r"attention required|cloudflare|captcha|page not found|"
    r"error code|failed to (?:load|fetch|open)|timeout|"
    r"502 bad gateway|503 service|404 not found)\b",
    re.I,
)
_GENERIC = frozenset({
    "products", "product", "platforms", "platform", "services", "service",
    "offerings", "offering", "overview", "about", "solutions", "solution",
    "capabilities", "capability", "portfolio", "federal", "footprint",
    "channels", "competitors", "boundaries", "company", "government",
    "partners", "customers", "features", "benefits",
})
_NAV = re.compile(
    r"\b(learn more|read more|get started|contact us|careers|"
    r"privacy policy|terms of (?:use|service)|cookie|sign in|"
    r"log in|subscribe|view all|see all|home|menu|search)\b",
    re.I,
)
_HEADING = re.compile(r"^#{1,4}\s+(.+)$", re.M)
_BULLET = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.+)$", re.M)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_NAICS_NEAR = re.compile(
    r"NAICS(?:\s+(?:code|codes))?\s*[:#]?\s*(\d{6})",
    re.I,
)
_EXCLUSION_LEAD = re.compile(
    r"(?:not (?:to be )?confused with|distinct from|"
    r"unrelated(?: (?:firm|company|companies|firms))?|"
    r"false friends?|name collisions?|excluding|excluded|"
    r"other (?:companies|firms) (?:named|sharing|that share))\s+"
    r"[:\-]?\s*(.+)",
    re.I,
)
_COLLISION_LIST = re.compile(
    r"(?:collisions?|unrelated|false friends?|not (?:this|our) company)"
    r"(?: include| are| with|:)\s+(.+?)(?:\.|$)",
    re.I,
)
_FEDERAL_PATH = re.compile(
    r"\b([A-Z][A-Za-z0-9&']+(?:\s+[A-Z][A-Za-z0-9&']+){0,4}\s+"
    r"(?:Government Sales|Federal Sales|Public Sector)\s+"
    r"(?:LLC|Inc\.?|Incorporated))\b",
)
_TRAIL_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_TRAIL_DASH = re.compile(r"\s+[-:]\s+.+$")
_TRAIL_COLON = re.compile(r":\s+.+$")


class RelatedEntity(BaseModel):
    name: str
    relation: str = "affiliate"
    official_domain: Optional[str] = None
    rationale: str = ""


class ExtractedSurface(BaseModel):
    """Discrete names lifted from probes / scrape / an optional model pass."""

    offerings: list[str] = Field(default_factory=list)
    naics: list[tuple[str, str]] = Field(default_factory=list)  # code, rationale
    exclusions: list[str] = Field(default_factory=list)
    related_entities: list[RelatedEntity] = Field(default_factory=list)


def _clean(text: str) -> str:
    return _WS.sub(" ", str(text or "")).strip().strip(".,;:\"'`")


def is_garbage_text(text: str) -> bool:
    raw = str(text or "")
    if not raw.strip():
        return True
    if raw.strip().casefold() in {"citation", "cite", "source", "url"}:
        return True
    if _GARBAGE.search(raw):
        return True
    if raw.count("\n") >= 3 and len(raw) > 160:
        return True
    return False


def is_discrete_name(text: str, *, allow_one_word: bool = True) -> bool:
    """Short product / capability name. Rejects essays and load-error blobs."""
    name = _clean(text)
    if not name or is_garbage_text(name):
        return False
    if "\n" in str(text or "").strip():
        return False
    if _NAV.search(name):
        return False
    if name.casefold() in _GENERIC:
        return False
    if ":" in name or "(" in name or ")" in name:
        return False
    words = name.split()
    if len(name) > MAX_OFFERING_CHARS or len(words) > MAX_OFFERING_WORDS:
        return False
    if len(words) == 1 and not allow_one_word:
        return False
    if len(words) == 1 and not _one_word_product(name):
        return False
    if name[:1].islower() and len(words) > 3:
        return False
    lowered = name.casefold()
    if lowered.startswith(("the company", "this company", "official site")):
        return False
    return True


def _one_word_product(name: str) -> bool:
    if re.fullmatch(r"[A-Z]{2,12}", name):
        return True
    if re.fullmatch(r"[A-Z][A-Za-z0-9]+[A-Z][A-Za-z0-9]*", name):
        return True
    if re.search(r"\d", name) and re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return True
    if re.fullmatch(r"[A-Z][a-z]{2,16}", name) and name not in {
        "Network", "Networks", "Systems", "Services", "Solutions",
        "Company", "Federal", "Government",
    }:
        return True
    return False


_MD_WRAP = re.compile(r"[*_`]+")
_LEAD_INCLUDE = re.compile(
    r"^(?:include|includes|including|are|with|such as)\s+", re.I)
_TRAIL_FOR = re.compile(r"\s+for\s+.+$", re.I)


def _strip_label(raw: str) -> str:
    name = _MD_WRAP.sub("", str(raw or ""))
    name = _clean(name)
    name = _LEAD_INCLUDE.sub("", name)
    name = _TRAIL_FOR.sub("", name)
    name = _TRAIL_COLON.sub("", name)
    name = _TRAIL_DASH.sub("", name)
    name = _TRAIL_PAREN.sub("", name)
    name = _clean(name)
    for prefix in ("product:", "platform:", "offering:", "capability:"):
        if name.casefold().startswith(prefix):
            name = _clean(name[len(prefix):])
    return name


def _split_slash_names(name: str) -> list[str]:
    if "/" not in name:
        return [name] if name else []
    parts = [_strip_label(p) for p in name.split("/")]
    usable = [p for p in parts if p and is_discrete_name(p) and len(p.split()) <= 4]
    if len(usable) >= 2:
        return usable
    return [name] if is_discrete_name(name) else usable


def candidate_names(text: str) -> list[str]:
    """Headings, bullets, bold spans, and slash-lists from markdown/prose."""
    raw = str(text or "")
    found: list[str] = []
    seen: set[str] = set()

    def _add(piece: str) -> None:
        for part in _split_slash_names(_strip_label(piece)):
            key = part.casefold()
            if not part or key in seen or not is_discrete_name(part):
                continue
            seen.add(key)
            found.append(part)

    for matcher in (_HEADING, _BULLET):
        for match in matcher.finditer(raw):
            _add(match.group(1))
    for match in _BOLD.finditer(raw):
        _add(match.group(1))
    return found


def excerpt_from(text: str, *, needle: str = "", limit: int = 280) -> str:
    """A real slice of page/probe text. Never the placeholder 'citation'."""
    raw = _WS.sub(" ", str(text or "")).strip()
    if not raw or is_garbage_text(raw) and len(raw) < 40:
        return ""
    if needle:
        idx = raw.casefold().find(needle.casefold())
        if idx >= 0:
            start = max(0, idx - 40)
            return raw[start:start + limit].strip()
    return raw[:limit].strip()


def extract_naics(text: str) -> list[tuple[str, str]]:
    """Six-digit codes only when the text actually says NAICS."""
    raw = str(text or "")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _NAICS_NEAR.finditer(raw):
        code = match.group(1)
        if code in seen:
            continue
        start = max(0, match.start() - 80)
        end = min(len(raw), match.end() + 80)
        snippet = _WS.sub(" ", raw[start:end]).strip()
        rationale = snippet if len(snippet.split()) >= 5 else (
            f"federal or product research cited NAICS {code} for this company"
        )
        seen.add(code)
        out.append((code, rationale[:240]))
    return out


def extract_exclusions(text: str) -> list[str]:
    raw = str(text or "")
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        piece = _strip_label(name)
        key = piece.casefold()
        if not piece or key in seen or not is_discrete_name(piece):
            return
        seen.add(key)
        found.append(piece)

    for match in _EXCLUSION_LEAD.finditer(raw):
        for part in re.split(r",|/|;|\band\b", match.group(1)):
            _add(part)
    for match in _COLLISION_LIST.finditer(raw):
        for part in re.split(r",|/|;|\band\b", match.group(1)):
            _add(part)
    return found


def extract_related_entities(
    text: str, *, bound_name: str = "", official_domain: Optional[str] = None,
) -> list[RelatedEntity]:
    raw = str(text or "")
    out: list[RelatedEntity] = []
    seen: set[str] = set()
    bound = (bound_name or "").casefold()
    for match in _FEDERAL_PATH.finditer(raw):
        name = _clean(match.group(1))
        key = name.casefold()
        if not name or key in seen:
            continue
        if bound and bound.split(",")[0] not in key and key.split()[0] not in bound:
            # still record a Government Sales LLC that shares the first token
            first = (bound.split() or [""])[0]
            if first and first not in key:
                continue
        seen.add(key)
        out.append(RelatedEntity(
            name=name,
            relation="federal_path",
            official_domain=official_domain,
            rationale=(
                "related legal person on the federal path; does not replace "
                "the bound public-company identity"
            ),
        ))
    return out


def extract_surface(
    *,
    probes: Optional[Iterable] = None,
    scrape=None,
    product_ingest: Optional[dict] = None,
    structured: Optional["StructuredProductSurface"] = None,
    bound_name: str = "",
    official_domain: Optional[str] = None,
    client_name: str = "",
) -> ExtractedSurface:
    """Union of ingest names, deterministic probe/scrape extract, optional model."""
    offerings: list[str] = []
    naics: list[tuple[str, str]] = []
    exclusions: list[str] = []
    related: list[RelatedEntity] = []
    seen_off: set[str] = set()
    seen_ex: set[str] = set()
    skip = {t for t in (client_name or bound_name or "").casefold().split()
            if t in {"inc", "llc", "ltd", "corp", "co", "the", "and", "networks"}}
    skip.add((client_name or "").casefold())
    skip.add((bound_name or "").casefold())

    def _offer(name: str) -> None:
        piece = _strip_label(name)
        key = piece.casefold()
        if not piece or key in seen_off or key in skip:
            return
        if not is_discrete_name(piece):
            return
        seen_off.add(key)
        offerings.append(piece)

    def _exclude(name: str) -> None:
        piece = _strip_label(name)
        key = piece.casefold()
        if not piece or key in seen_ex or key in seen_off:
            return
        if not is_discrete_name(piece):
            return
        seen_ex.add(key)
        exclusions.append(piece)

    for row in (product_ingest or {}).get("capabilities") or []:
        name = (row.get("name") if isinstance(row, dict) else str(row) or "").strip()
        _offer(name)

    scrape_text = ""
    if scrape is not None:
        if hasattr(scrape, "combined_text"):
            scrape_text = scrape.combined_text(max_chars=4000) or ""
        elif getattr(scrape, "pages", None):
            scrape_text = "\n".join(getattr(p, "text", "") or "" for p in scrape.pages)
        if scrape_text and not is_garbage_text(scrape_text):
            for name in candidate_names(scrape_text):
                _offer(name)
            naics.extend(extract_naics(scrape_text))

    for probe in probes or []:
        title = str(getattr(probe, "name", None) or (
            probe.get("name") if isinstance(probe, dict) else "") or "")
        findings = str(getattr(probe, "findings", None) or (
            probe.get("findings") if isinstance(probe, dict) else "") or "")
        if not findings.strip() or is_garbage_text(findings) and len(findings) < 80:
            continue
        kind = title.casefold()
        names = candidate_names(findings)
        if "boundar" in kind:
            for name in names:
                _exclude(name)
            for name in extract_exclusions(findings):
                _exclude(name)
        elif "channel" in kind or "reseller" in kind or "compet" in kind:
            pass
        else:
            for name in names:
                _offer(name)
        for name in extract_exclusions(findings):
            _exclude(name)
        naics.extend(extract_naics(findings))
        related.extend(extract_related_entities(
            findings, bound_name=bound_name or client_name,
            official_domain=official_domain,
        ))

    if structured is not None and not hasattr(structured, "offerings"):
        structured = None
    if structured is not None:
        for name in structured.offerings or []:
            _offer(name)
        for code in structured.naics or []:
            raw = str(code).strip()
            if raw.isdigit() and len(raw) == 6:
                naics.append((
                    raw,
                    f"structured extract cited NAICS {raw} from company research",
                ))
        for name in structured.exclusions or []:
            _exclude(name)
        for name in structured.related_entities or []:
            related.extend(extract_related_entities(
                str(name), bound_name=bound_name or client_name,
                official_domain=official_domain,
            ) or [RelatedEntity(
                name=_clean(str(name)), relation="affiliate",
                official_domain=official_domain,
                rationale="related legal person named in structured extract",
            )])

    # de-dupe naics keeping first rationale
    seen_n: set[str] = set()
    naics_u: list[tuple[str, str]] = []
    for code, why in naics:
        if code in seen_n:
            continue
        seen_n.add(code)
        naics_u.append((code, why))

    seen_r: set[str] = set()
    related_u: list[RelatedEntity] = []
    for row in related:
        key = row.name.casefold()
        if key in seen_r:
            continue
        seen_r.add(key)
        related_u.append(row)

    return ExtractedSurface(
        offerings=offerings, naics=naics_u,
        exclusions=exclusions, related_entities=related_u,
    )


class StructuredProductSurface(BaseModel):
    """Optional model-shaped extract. Empty lists mean the model named none."""

    offerings: list[str] = Field(default_factory=list)
    naics: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)


_STRUCTURE_PRODUCTS = """\
Extract SHORT discrete product and platform names the bound company sells.
Each offering is a name (EOS, CloudVision), not a paragraph and not a
page-load error. Extract six-digit NAICS only when the findings cite them.
Extract name-collision exclusions (other firms that share the name).
Extract related legal persons (for example a Government Sales LLC) without
replacing the bound public company. Do not invent names.
"""


def structure_product_surface(engine, probes, *, identity=None):
    """Best-effort model extract. Returns None on skip or failure."""
    if engine is None or not probes:
        return None
    blob = []
    for probe in probes:
        title = getattr(probe, "name", "") or ""
        findings = getattr(probe, "findings", "") or ""
        if findings.strip():
            blob.append(f"[{title}]\n{findings}")
    if not blob:
        return None
    try:
        out = engine.structure(
            instructions=_STRUCTURE_PRODUCTS,
            findings="\n\n".join(blob)[:8000],
            schema=StructuredProductSurface,
        )
    except Exception:  # noqa: BLE001 - extract fails open to the deterministic path
        return None
    if not isinstance(out, StructuredProductSurface) and not hasattr(out, "offerings"):
        return None
    return out
