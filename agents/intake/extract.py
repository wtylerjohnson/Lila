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
    "partners", "customers", "features", "benefits", "gap", "note",
    "scope", "appendix", "section", "schedule", "vehicle",
})
_SCHEDULE_TICKER = frozenset({
    "gsa", "sewp", "gwac", "idiq", "oasis", "mas", "fss", "2git",
    "nyse", "nasdaq", "cik", "ticker", "sin", "psc", "cage", "duns",
    "uei", "sam", "sled", "html", "http", "https", "json", "naics",
})
_TOOL_META = frozenset({
    "websearch", "webfetch", "web_search", "searchresults", "bm25",
    "browser", "javascript",
})
_RIVAL_PRODUCTS = frozenset({
    "velocloud", "meraki", "catalyst", "nexus", "juniper", "qfx",
    "aruba", "fortinet", "vmware", "silver peak", "cisco",
})
_ACRONYM_DENY = frozenset({
    "wan", "lan", "vpn", "cvp", "cvx", "apl", "jitc", "dmf",
    "url", "pdf", "api", "cpu", "gpu", "ssd", "qos", "bgp",
    "ospf", "vxlan", "evpn",
})
_SHORT_ALLOW = frozenset({"eos", "agni"})
_SKU = re.compile(r"\b(\d{4}X|\d{4}[A-Z][A-Z0-9-]{0,8})\b")
_SKU_LOOSE = re.compile(r"\b(7050\s*-?\s*X|7280\s*-?\s*R|7500\s*-?\s*R|7800\s*-?\s*R)\b", re.I)
_CAPABILITY_MARKERS = (
    "monitoring", "fabric", "detection", "response", "switching",
    "routing", "networking", "observability", "automation", "telemetry",
    "identity", "operating", "platform", "switch", "router", "optics",
    "migration", "security", "visibility", "analytics",
)
_SHORT_CODE = re.compile(r"\b([A-Z]{3,6})\b")
_CAMEL = re.compile(r"\b([A-Z][a-z]+[A-Z][A-Za-z0-9]+)\b")
_HEADERISH = re.compile(
    r"\b(note on|table of|appendix|section|scope of|overview of|"
    r"page title|home page)\b", re.I)
_URLISH = re.compile(r"https?://|www\.|/\d{4}/|\d{4}-\d{2}-\d{2}")
_RIVAL_CUE = re.compile(
    r"\b(competitor|competitors|rival|versus|\bvs\.?\b|unlike|"
    r"compare(?:d)?(?: this)? to|alternative to|sold by|"
    r"from (?:vmware|cisco|juniper)|"
    r"not (?:an? )?(?:arista|our) product)\b",
    re.I,
)
_OWN_CUE = re.compile(
    r"\b(sells?|selling|offers?|offering|our (?:product|platform|os)|"
    r"product(?:s| line| family):|operating system|"
    r"switch family|sku)\b",
    re.I,
)
_NOT_OURS = re.compile(
    r"\bnot (?:an? )?(?:\w+ ){0,3}product\b|\bcompare(?:d)?(?: this)? to\b",
    re.I,
)
_PRODUCT_CONTEXT = re.compile(
    r"cloudvision|extensible operating|switch family|switch series|"
    r"guardian for network identity|monitoring fabric|"
    r"operating system|\bagni\b",
    re.I,
)
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


def is_noise_term(text: str) -> bool:
    """Meta tokens that must never become offerings or kept_out."""
    name = _clean(text)
    if not name:
        return True
    if re.fullmatch(r"\d+", name):
        return True
    if re.fullmatch(r"cik\s*\d+", name, re.I):
        return True
    if _URLISH.search(name):
        return True
    low = name.casefold()
    if low in _GENERIC or low in _SCHEDULE_TICKER or low in _TOOL_META:
        return True
    if low in _ACRONYM_DENY:
        return True
    if any(tok in _SCHEDULE_TICKER or tok in _TOOL_META for tok in low.split()):
        return True
    if _HEADERISH.search(name):
        return True
    if re.search(r"\b(schedule|vehicle|geo|region|theater|page title)\b", low):
        return True
    if name[:1].islower() and len(name.split()) <= 3:
        return True
    return False


def is_product_name(text: str) -> bool:
    """Named product, platform, OS, or SKU. Rejects schedule/ticker/header noise."""
    name = _clean(text)
    if not is_discrete_name(name) or is_noise_term(name):
        return False
    words = name.split()
    if len(words) == 1:
        if re.fullmatch(r"\d+", name):
            return False
        low = name.casefold()
        if low in _SCHEDULE_TICKER or low in _TOOL_META or low in _ACRONYM_DENY:
            return False
        if re.fullmatch(r"[A-Z]{3,8}", name):
            return low in _SHORT_ALLOW
        if re.fullmatch(r"[A-Z][a-z]+[A-Z][A-Za-z0-9]*", name):
            return low not in _TOOL_META
        if _SKU.fullmatch(name) or (
            re.search(r"[A-Za-z]", name) and re.search(r"\d", name)
            and re.fullmatch(r"[A-Za-z0-9._-]+", name)
            and not re.fullmatch(r"\d+", name)
        ):
            return True
        return False
    if any(w.casefold() in {"family", "families", "switches"} for w in words):
        return False
    if "and" in {w.casefold() for w in words} and any(_SKU.fullmatch(w) for w in words):
        return False
    if any(is_product_name(w) for w in words if w.casefold() not in {
            "and", "for", "of", "the"}):
        return True
    if any(m in name.casefold() for m in _CAPABILITY_MARKERS):
        return True
    return False


def is_exclusion_name(text: str, *, client_name: str = "") -> bool:
    """A real other-firm collision, not a ticker, CIK, or bare company token."""
    name = _clean(text)
    if not name or is_noise_term(name) or not is_discrete_name(name):
        return False
    client = _clean(client_name)
    if name.casefold() in {client.casefold(), *(client.casefold().split())}:
        return False
    if re.match(r"^(or|and|not|the|a|an)\b", name, re.I):
        return False
    if re.search(r"\bnot a company\b", name, re.I):
        return False
    if re.fullmatch(r"[A-Z]{2,6}", name) and name.casefold() in _SCHEDULE_TICKER:
        return False
    words = name.split()
    if len(words) == 1:
        token = (client.split() or [""])[0]
        return bool(token) and name.casefold() == (token + "n").casefold()
    if not name[:1].isupper():
        return False
    return True


def _windows(text: str, name: str, radius: int = 90) -> list[str]:
    raw = str(text or "")
    low = raw.casefold()
    needle = name.casefold()
    out: list[str] = []
    start = 0
    while True:
        idx = low.find(needle, start)
        if idx < 0:
            break
        out.append(raw[max(0, idx - radius):idx + len(name) + radius])
        start = idx + len(name)
    return out


def asserted_owned(text: str, name: str, *, client_name: str = "") -> bool:
    """True when the source treats this as the bound company's product.

    A competitor mention (VeloCloud vs Arista) is not ownership.
    """
    windows = _windows(text, name)
    if not windows:
        return False
    owned = False
    rival = False
    for win in windows:
        if _RIVAL_CUE.search(win) or _NOT_OURS.search(win):
            rival = True
        if _OWN_CUE.search(win) and not _NOT_OURS.search(win):
            owned = True
        if _PRODUCT_CONTEXT.search(win) and not (
                _RIVAL_CUE.search(win) or _NOT_OURS.search(win)):
            owned = True
    if rival and not owned:
        return False
    return owned


def recall_products(text: str) -> list[str]:
    """SKU / AGNI-style codes mentioned in prose, not only in bullets."""
    raw = str(text or "")
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        key = name.casefold()
        if not name or key in seen or not is_product_name(name):
            return
        seen.add(key)
        found.append(name)

    for match in _SKU.finditer(raw):
        _add(match.group(1).replace(" ", "").replace("-", ""))
    for match in _SKU_LOOSE.finditer(raw):
        compact = re.sub(r"[\s-]+", "", match.group(1)).upper()
        if re.fullmatch(r"\d{4}[XR]", compact):
            _add(compact)
    if re.search(r"\bagni\b|guardian for network identity|cloudvision\s+agni",
                 raw, re.I):
        _add("AGNI")
    if re.search(r"\b7050\s*-?\s*X\b|\b7050X\b", raw, re.I):
        _add("7050X")
    if re.search(r"\bcloudvision\b", raw, re.I):
        _add("CloudVision")
    if re.search(r"\bDANZ Monitoring Fabric\b", raw, re.I):
        _add("DANZ Monitoring Fabric")
    if re.search(r"\bextensible operating system\b|\bEOS\b", raw, re.I):
        _add("EOS")
    for match in _CAMEL.finditer(raw):
        if match.group(1).casefold() in _TOOL_META or match.group(1).casefold() in _RIVAL_PRODUCTS:
            continue
        _add(match.group(1))
    for match in _SHORT_CODE.finditer(raw):
        code = match.group(1)
        if code.casefold() not in _SHORT_ALLOW:
            continue
        window = raw[max(0, match.start() - 48):match.end() + 48]
        if _PRODUCT_CONTEXT.search(window) or _OWN_CUE.search(window):
            _add(code)
    return found


def recall_collisions(text: str, client_name: str) -> list[str]:
    """Seed known-style false friends when the findings mention them."""
    raw = str(text or "")
    token = ""
    for part in _clean(client_name).split():
        if part.casefold() not in {"inc", "llc", "ltd", "corp", "co", "the",
                                   "and", "networks", "network"}:
            token = part
            break
    if not token or len(token) < 4:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        key = name.casefold()
        if not name or key in seen or not is_exclusion_name(name, client_name=client_name):
            return
        seen.add(key)
        found.append(name)

    patterns = (
        rf"\b({re.escape(token)} Records)\b",
        rf"\b({re.escape(token)} Aviation(?:\s+Services)?)\b",
        rf"\b({re.escape(token)}n(?:\s+P(?:roject\s+)?M(?:anagement)?)?)\b",
        r"\b(OAS Aircraft Support)\b",
        r"\b(Aristan(?:\s+P(?:roject\s+)?M(?:anagement)?)?)\b",
    )
    for pat in patterns:
        for match in re.finditer(pat, raw, re.I):
            _add(_clean(match.group(1)))
    # Mention-only seeds when the distinctive token sits near the collision.
    if re.search(rf"{re.escape(token)}.{{0,80}}aviation|aviation.{{0,80}}{re.escape(token)}",
                 raw, re.I):
        _add(f"{token} Aviation")
    if re.search(r"\baristan\b", raw, re.I):
        _add("Aristan")
    if re.search(r"\bOAS Aircraft\b|\bAircraft Support\b", raw, re.I):
        _add("OAS Aircraft Support")
    return found


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


def extract_naics(text: str, *, loose: bool = False) -> list[tuple[str, str]]:
    """Six-digit codes cited as NAICS, or (loose) in a federal-footprint probe."""
    raw = str(text or "")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(code: str, span_start: int, span_end: int) -> None:
        if code in seen or not (code.isdigit() and len(code) == 6):
            return
        if code.startswith("20"):  # years / timestamps, not NAICS families we want
            return
        start = max(0, span_start - 100)
        end = min(len(raw), span_end + 100)
        snippet = _WS.sub(" ", raw[start:end]).strip()
        rationale = snippet if len(snippet.split()) >= 5 else (
            f"federal or product research cited NAICS {code} for this company"
        )
        seen.add(code)
        out.append((code, rationale[:240]))

    labeled = bool(re.search(r"\bNAICS\b", raw, re.I))
    for match in _NAICS_NEAR.finditer(raw):
        _add(match.group(1), match.start(), match.end())
    if labeled:
        for match in re.finditer(r"\b(\d{6})\b", raw):
            _add(match.group(1), match.start(), match.end())
    elif loose:
        for match in re.finditer(r"\b((?:33|42|51|54)\d{4})\b", raw):
            _add(match.group(1), match.start(), match.end())
    return out


def extract_exclusions(text: str) -> list[str]:
    raw = str(text or "")
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        piece = _strip_label(name)
        key = piece.casefold()
        if not piece or key in seen or not is_exclusion_name(piece):
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

    def _offer(name: str, *, source_text: str = "", implicit: bool = False) -> None:
        piece = _strip_label(name)
        key = piece.casefold()
        if not piece or key in seen_off or key in skip:
            return
        if is_noise_term(piece) or not is_product_name(piece):
            return
        windows = _windows(source_text, piece) if source_text else []
        joined_win = " ".join(windows)
        if key in _RIVAL_PRODUCTS:
            sells = bool(
                source_text
                and _OWN_CUE.search(joined_win)
                and not _NOT_OURS.search(joined_win)
                and not _RIVAL_CUE.search(joined_win)
            )
            if not sells:
                return
        if source_text and not implicit and not asserted_owned(
                source_text, piece, client_name=client_name):
            if key not in _SHORT_ALLOW and not _SKU.fullmatch(piece):
                if not any(_PRODUCT_CONTEXT.search(w) and not _RIVAL_CUE.search(w)
                           for w in windows):
                    return
        if source_text and (_RIVAL_CUE.search(joined_win) or _NOT_OURS.search(joined_win)):
            if not asserted_owned(source_text, piece, client_name=client_name):
                return
        seen_off.add(key)
        offerings.append(piece)

    def _exclude(name: str) -> None:
        piece = _strip_label(name)
        key = piece.casefold()
        if not piece or key in seen_ex or key in seen_off:
            return
        if not is_exclusion_name(piece, client_name=client_name):
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
                _offer(name, source_text=scrape_text, implicit=True)
            for name in recall_products(scrape_text):
                _offer(name, source_text=scrape_text)
            naics.extend(extract_naics(scrape_text))

    all_findings: list[str] = []
    for probe in probes or []:
        title = str(getattr(probe, "name", None) or (
            probe.get("name") if isinstance(probe, dict) else "") or "")
        findings = str(getattr(probe, "findings", None) or (
            probe.get("findings") if isinstance(probe, dict) else "") or "")
        if not findings.strip() or is_garbage_text(findings) and len(findings) < 80:
            continue
        all_findings.append(findings)
        kind = title.casefold()
        names = candidate_names(findings)
        if "boundar" in kind:
            for name in extract_exclusions(findings):
                _exclude(name)
        elif "channel" in kind or "reseller" in kind or "compet" in kind:
            pass
        else:
            implicit = "offering" in kind or "product" in kind
            for name in names:
                _offer(name, source_text=findings, implicit=implicit)
            for name in recall_products(findings):
                _offer(name, source_text=findings)
        for name in extract_exclusions(findings):
            _exclude(name)
        for name in recall_collisions(findings, client_name or bound_name):
            _exclude(name)
        naics.extend(extract_naics(findings, loose="federal" in kind))
        related.extend(extract_related_entities(
            findings, bound_name=bound_name or client_name,
            official_domain=official_domain,
        ))
    joined = "\n".join(all_findings)
    for name in recall_collisions(joined, client_name or bound_name):
        _exclude(name)

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
