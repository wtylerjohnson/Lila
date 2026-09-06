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
    "casestudies", "casestudy", "case studies", "learnmore",
    "customersuccess", "customer success", "customer success story",
    "operating system", "data sheet", "datasheet", "solution brief",
    "proof points", "proof point",
})
_NAV_GLUED = frozenset({
    "casestudies", "casestudy", "learnmore", "customersuccess",
    "goingbig", "cognitivcampus", "cognitivecampus",
    "solutionbrief", "solutionbriefs", "datasheet", "datasheets",
})
_IDP_NOISE = frozenset({
    "onelogin", "okta", "pingidentity", "ping identity", "duo",
    "azure ad", "azuread", "auth0",
})
_PROSE_FRAGMENT = re.compile(
    r"\b(also|has a|is a|there is|this is|these are|we have|"
    r"coming soon|learn more)\b",
    re.I,
)
_CUSTOMER_JUNK = frozenset({
    "customer success story", "success story", "customer story",
    "going big", "group vp", "cognitive campus", "case study",
    "case studies", "customer success", "proof points", "proof point",
    "pdf", "ease of deployment", "troubleshoot workloads",
    "unmatched visibility", "operating system",
})
_INDUSTRY_SEGMENTS = frozenset({
    "hedge funds", "hedge fund", "financial services", "financial service",
    "cloud providers", "service providers", "enterprises", "enterprise",
    "media", "healthcare", "retail", "education", "manufacturing",
    "telecommunications", "telecom", "public sector", "verticals",
    "industries", "sectors", "markets", "service provider",
    "cloud provider", "fortune 500", "fortune 100",
})
_JOB_TITLE = re.compile(
    r"\b(vp|vice president|director|manager|officer|president|"
    r"head of|group vp|engineer)\b",
    re.I,
)
_STORY_TITLE = re.compile(
    r"\b(success story|customer story|case stud|going big|"
    r"cognitive campus|\bstories\b)\b",
    re.I,
)
_PEOPLE_NAV = re.compile(
    r"\b(team|staff|leadership|board|careers|executives?|"
    r"senior management|management team|our people|officers)\b",
    re.I,
)
_TITLE_TAIL = re.compile(
    r"\b(overview|page|home|index|guide|datasheets?|data\s*sheets?|"
    r"whitepapers?|white\s*papers?|solution\s*briefs?|briefs?)\s*$",
    re.I,
)
_SLOGAN = re.compile(
    r"^(from|to|unmatched|leading|ultimate|discover|unlock|"
    r"reimagine|welcome)\b|\bfrom\b.+\bto\b|&amp;|&",
    re.I,
)
_CUSTOMER_CONTEXT = re.compile(
    r"\b(customers?|case stud|trusted by|proof points?|logo|"
    r"deployed (?:at|by)|used by|clients?)\b",
    re.I,
)
_ORG_PROOF = re.compile(
    r"\b(customer|case stud|trusted|proof point|deployed|uses|chose|"
    r"selected|logo|production|ran|running)\b",
    re.I,
)
_ORG_RUN = re.compile(
    r"\b([A-Z][A-Za-z0-9&'!-]{1,40}"
    r"(?:\s+[A-Z][A-Za-z0-9&'!-]{1,24}){0,3})\b"
)
_NOT_ORG = frozenset({
    "unlike", "versus", "compared", "alternatives", "alternative",
    "customers", "customer", "proof", "points", "case", "study",
    "trusted", "official", "about", "welcome", "ease", "pdf",
    "operating", "system", "solution", "brief", "data", "sheet",
})
_NOT_RIVAL = frozenset({
    "analyst", "analysts", "gartner", "forrester", "wikipedia",
    "crunchbase", "industry", "vendors", "vendor", "notes",
})
_RIVAL_TITLE_TAIL = re.compile(
    r"\s+(comparisons?|overview|alternatives?|versus|\bvs\.?)$",
    re.I,
)
_CATEGORY_HEADERS = frozenset({
    "visibility", "telemetry", "network visibility", "network telemetry",
    "visibility fabric", "telemetry fabric", "visibility and telemetry",
    "visibility or telemetry", "visibility or telemetry fabrics",
    "operating system", "unmatched visibility",
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
_RIVAL_VENDORS = frozenset({
    "cisco", "juniper", "vmware", "nvidia", "hpe", "hewlett packard",
    "aruba", "fortinet", "extreme", "palo alto", "meraki",
    "velocloud", "silver peak", "cumulus", "darktrace",
})
_GENERIC_CUSTOMERS = frozenset({
    "enterprises", "enterprise", "governments", "government",
    "organizations", "organisation", "customers", "clients",
    "partners", "companies", "agencies", "fortune", "industry",
    "the company", "this company",
})
_COMPETITOR_LEAD = re.compile(
    r"(?:unlike|versus|\bvs\.?\b|compared to|compare(?:d)?(?: this)? to|"
    r"alternative(?:s)? to|competitors?(?: include| are|:)|"
    r"rivals?(?: include| are|:)|instead of)\s+"
    r"([A-Z][A-Za-z0-9&.\'-]{1,40}(?:\s+[A-Z][A-Za-z0-9&.\'-]{1,24}){0,3})",
    re.I,
)
_CUSTOMER_LEAD = re.compile(
    r"(?:customers?(?: include| are|:)|case stud(?:y|ies)[:\s]+|"
    r"trusted by|used by|deployed (?:at|by)|clients? include|"
    r"proof points?[:\s]+)\s*(.+?)(?:\.|$)",
    re.I,
)
_ACRONYM_DENY = frozenset({
    "wan", "lan", "vpn", "cvp", "cvx", "apl", "jitc", "dmf",
    "url", "pdf", "api", "cpu", "gpu", "ssd", "qos", "bgp",
    "ospf", "vxlan", "evpn",
})
_SHORT_ALLOW = frozenset({"eos", "agni"})
# Switch/router families: 7050X, 7060X6, 7280R, 7800R4. Not SEWP 0119Y.
_SKU = re.compile(r"\b(\d{4}[XR][A-Z0-9]{0,4})\b")
_GSA_MAS = re.compile(r"^47[A-Z0-9]{8,}$", re.I)
_LEGACY_GSA = re.compile(r"^GS[-A-Z0-9]+$", re.I)
_DOD_PIID = re.compile(r"^N[0-9]{5,}[-A-Z0-9]*$", re.I)
_SEWP_SHORT = re.compile(r"^\d{4}[A-WY-Z]$", re.I)
_LONG_AWARD = re.compile(r"^[A-Z0-9]{8,}$", re.I)
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
    r"compare(?:d)?(?: this)? to|compared with|(?<!\bno\s)head-to-head|"
    r"alternative(?:s)?(?: to)?|sold by|"
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
_CLASSIFICATION_NEAR = re.compile(
    r"(?:industry classification|classification codes?|sic)\s*[:#]?\s*(\d{6})",
    re.I,
)
_CLASSIFICATION_LABEL = re.compile(
    r"\b(?:naics|industry classification|classification codes?|sic)\b",
    re.I,
)
_NAICS_SECTORS = frozenset({
    "11", "21", "22", "23", "31", "32", "33",
    "42", "44", "45", "48", "49",
    "51", "52", "53", "54", "55", "56",
    "61", "62", "71", "72", "81", "92",
})
_AVIATION_NAICS = frozenset({
    "336411", "336412", "336413", "336414", "336415", "336419",
    "481111", "481112", "481211", "481212", "481219",
    "488111", "488119", "488190",
})
_RECORDS_NAICS = frozenset({
    "512110", "512120", "512191", "512199",
    "512210", "512220", "512230", "512240", "512250", "512290",
})
_AVIATION_CUE = re.compile(
    r"\b(aviation|aircraft(?:-support)?|air transportation)\b", re.I)
_RECORDS_CUE = re.compile(
    r"\b(records(?: label)?|music label|sound recording)\b", re.I)
_COLLISION_CUE = re.compile(
    r"\b(namesake|collision|false friend|not this company|do not confuse|"
    r"unrelated|other (?:firm|company)|keep(?:s)? out)\b",
    re.I,
)
_NOT_NAICS_CONTEXT = re.compile(
    r"\b(forecast|solicitation|notice(?:\s+id)?|pipeline|"
    r"opportunity id|req(?:uest)?\s*id)\b",
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
    naics: list[tuple[str, str, str]] = Field(default_factory=list)  # code, rationale, role
    exclusions: list[str] = Field(default_factory=list)
    related_entities: list[RelatedEntity] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)


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
    if name.casefold() in _GENERIC or name.casefold() in _NAV_GLUED:
        return False
    if name.casefold() in _IDP_NOISE:
        return False
    if _PROSE_FRAGMENT.search(name):
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


def looks_like_contract_or_schedule_id(text: str) -> bool:
    """GSA/SEWP/award vehicle IDs are not product names (47QSWA18D008F, 0119Y)."""
    name = _clean(text)
    if not name:
        return False
    compact = re.sub(r"[\s_-]+", "", name)
    if _SKU.fullmatch(name) or _SKU.fullmatch(compact):
        return False
    if (
        _GSA_MAS.fullmatch(compact)
        or _LEGACY_GSA.fullmatch(name)
        or _DOD_PIID.fullmatch(name)
        or _SEWP_SHORT.fullmatch(compact)
    ):
        return True
    if (
        _LONG_AWARD.fullmatch(compact)
        and re.search(r"[A-Za-z]", compact)
        and re.search(r"\d", compact)
    ):
        return True
    return False


def is_noise_term(text: str) -> bool:
    """Meta tokens that must never become offerings or kept_out."""
    name = _clean(text)
    if not name:
        return True
    if looks_like_contract_or_schedule_id(name):
        return True
    if re.fullmatch(r"\d+", name):
        return True
    if re.fullmatch(r"cik\s*\d+", name, re.I):
        return True
    if _URLISH.search(name):
        return True
    low = name.casefold()
    if low in _GENERIC or low in _NAV_GLUED or low in _IDP_NOISE:
        return True
    if low in _SCHEDULE_TICKER or low in _TOOL_META:
        return True
    if low in _CATEGORY_HEADERS:
        return True
    if _PROSE_FRAGMENT.search(name):
        return True
    if low in _ACRONYM_DENY:
        return True
    if any(tok in _SCHEDULE_TICKER or tok in _TOOL_META for tok in low.split()):
        return True
    if _HEADERISH.search(name):
        return True
    if _PEOPLE_NAV.search(name) or _TITLE_TAIL.search(name):
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
    if looks_like_contract_or_schedule_id(name):
        return False
    if name.casefold() in _CATEGORY_HEADERS:
        return False
    if _PEOPLE_NAV.search(name) or _TITLE_TAIL.search(name):
        return False
    if _SLOGAN.search(name) or "&" in name:
        return False
    if name.casefold() in {"operating system", "unmatched visibility"}:
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
            return low not in _TOOL_META and low not in _NAV_GLUED and low not in _IDP_NOISE
        if _SKU.fullmatch(name):
            return True
        # Letter-led product codes (CCS-720XP). Digit-led mixed IDs must be SKUs.
        if (
            re.search(r"[A-Za-z]", name) and re.search(r"\d", name)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,20}", name)
        ):
            # CCS-720XP style. Reject bare account codes (JCT600).
            return bool(re.search(r"[-_]", name))
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
        rf"\b({re.escape(token)}n(?:\s+P(?:roject\s+)?M(?:anagement)?)?)\b(?!\s*-?\s*style)",
        r"\b(OAS Aircraft Support)\b",
        r"\b(Aristan(?:\s+P(?:roject\s+)?M(?:anagement)?)?)\b(?!\s*-?\s*style)",
    )
    for pat in patterns:
        for match in re.finditer(pat, raw, re.I):
            _add(_clean(match.group(1)))
    # Mention-only seeds when the distinctive token sits near the collision.
    if re.search(rf"{re.escape(token)}.{{0,80}}aviation|aviation.{{0,80}}{re.escape(token)}",
                 raw, re.I):
        _add(f"{token} Aviation")
    if (
        re.search(r"\bAristan\b", raw, re.I)
        and not re.search(r"\baristan(?:-|\s+)style\b", raw, re.I)
    ):
        _add("Aristan")
    if re.search(r"\bOAS Aircraft Support\b", raw, re.I):
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


def excerpt_supports_name(name: str, snippet: str) -> bool:
    """True when the excerpt names the firm or a tight token phrase."""
    hay = (snippet or "").casefold()
    needle = (name or "").casefold().strip()
    if not hay or not needle:
        return False
    if needle in hay:
        return True
    tokens = [
        t for t in name.split()
        if t.casefold() not in {"the", "a", "an", "and", "of", "for"}
    ]
    if len(tokens) == 2 and all(t.casefold() in hay for t in tokens):
        return True
    if len(tokens) >= 3:
        phrase = " ".join(tokens[-2:]).casefold()
        return phrase in hay and tokens[0].casefold() in hay
    return False


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


def is_plausible_naics_code(code: str) -> bool:
    """Six digits in a real NAICS sector. Rejects forecast-id fragments (685031)."""
    raw = str(code or "").strip()
    if not (raw.isdigit() and len(raw) == 6):
        return False
    if raw.startswith("20"):
        return False
    return raw[:2] in _NAICS_SECTORS


def is_aviation_industry_naics(code: str) -> bool:
    raw = str(code or "").strip()
    return raw in _AVIATION_NAICS or raw.startswith(("3364", "4811", "4881"))


def aviation_codes_in_text(text: str) -> list[str]:
    """Six-digit aviation NAICS that actually appear in the source text."""
    raw = str(text or "")
    found: list[str] = []
    for code in sorted(_AVIATION_NAICS):
        if re.search(rf"\b{code}\b", raw):
            found.append(code)
    return found


def is_records_industry_naics(code: str) -> bool:
    raw = str(code or "").strip()
    return raw in _RECORDS_NAICS or raw.startswith("5122")


def is_namesake_industry_naics(code: str) -> bool:
    return is_aviation_industry_naics(code) or is_records_industry_naics(code)


def exclude_blob_hits_industry(code: str, exclude_blob: str) -> bool:
    blob = (exclude_blob or "").casefold()
    if not blob:
        return False
    if is_aviation_industry_naics(code) and any(
            w in blob for w in ("aviation", "aircraft", "oas")):
        return True
    if is_records_industry_naics(code) and any(
            w in blob for w in ("records", "music")):
        return True
    return False


def naics_search_role(
    code: str,
    snippet: str = "",
    *,
    exclude_blob: str = "",
) -> str:
    """Core only when the code is this company's search lane, not a namesake."""
    hay = snippet or ""
    if exclude_blob_hits_industry(code, exclude_blob):
        return "boundary"
    if is_aviation_industry_naics(code) and (
            _AVIATION_CUE.search(hay) or _COLLISION_CUE.search(hay)):
        return "boundary"
    if is_records_industry_naics(code) and (
            _RECORDS_CUE.search(hay) or _COLLISION_CUE.search(hay)):
        return "boundary"
    return "core"


def extract_naics(text: str, *, loose: bool = False) -> list[tuple[str, str, str]]:
    """Cited six-digit NAICS with a search role (core | boundary)."""
    raw = str(text or "")
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def _add(code: str, span_start: int, span_end: int) -> None:
        if code in seen or not is_plausible_naics_code(code):
            return
        start = max(0, span_start - 80)
        end = min(len(raw), span_end + 80)
        snippet = _WS.sub(" ", raw[start:end]).strip()
        near = raw[max(0, span_start - 24):span_end + 16]
        if _NOT_NAICS_CONTEXT.search(snippet) and not _CLASSIFICATION_LABEL.search(near):
            return
        rationale = snippet if len(snippet.split()) >= 5 else (
            f"federal or product research cited NAICS {code} for this company"
        )
        seen.add(code)
        out.append((code, rationale[:240], naics_search_role(code, snippet)))

    labeled = bool(_CLASSIFICATION_LABEL.search(raw))
    for match in _NAICS_NEAR.finditer(raw):
        _add(match.group(1), match.start(), match.end())
    for match in _CLASSIFICATION_NEAR.finditer(raw):
        _add(match.group(1), match.start(), match.end())
    if labeled:
        for match in re.finditer(r"\b(\d{6})\b", raw):
            window = raw[max(0, match.start() - 40):match.end() + 16]
            if not _CLASSIFICATION_LABEL.search(window):
                continue
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


def citation_is_official(url: str, official_domain: Optional[str]) -> bool:
    if not url or not official_domain:
        return False
    host = url.casefold()
    dom = official_domain.casefold().lstrip(".")
    return dom in host


def is_error_page(text: str) -> bool:
    """Load-error / interstitial only. Multi-page crawls are not essays."""
    try:
        from tools.scrape.site import is_unrendered_text
        return is_unrendered_text(text)
    except Exception:  # noqa: BLE001 - keep extract usable offline
        raw = str(text or "")
        if not raw.strip():
            return True
        if raw.strip().casefold() in {"citation", "cite", "source", "url"}:
            return True
        return bool(_GARBAGE.search(raw))


def is_customer_name(text: str, *, client_name: str = "") -> bool:
    """Named buying org. Rejects story titles, job titles, and verticals."""
    name = _party_name(text, client_name=client_name)
    if not name:
        return False
    if not name[:1].isupper():
        return False
    if re.fullmatch(r"\d+", name) or len(name) < 2 or "." in name:
        return False
    low = name.casefold()
    if low in _CUSTOMER_JUNK or low in _GENERIC or low in _NAV_GLUED:
        return False
    if low in _INDUSTRY_SEGMENTS or low in _GENERIC_CUSTOMERS:
        return False
    if low in _IDP_NOISE or low in _RIVAL_VENDORS or low in _RIVAL_PRODUCTS:
        return False
    if low in _NOT_ORG or low in _ACRONYM_DENY or low in _TOOL_META:
        return False
    if _JOB_TITLE.search(name) or _STORY_TITLE.search(name):
        return False
    if _PEOPLE_NAV.search(name) or _TITLE_TAIL.search(name):
        return False
    if _PROSE_FRAGMENT.search(name) or _SLOGAN.search(name):
        return False
    if is_product_name(name):
        return False
    words = name.split()
    if any(w.casefold() in {
            "story", "stories", "success", "going",
            "services", "funds", "sector", "vertical",
            "proof", "points", "pdf", "troubleshoot", "workloads",
            "ease", "deployment", "visibility", "unmatched",
            "sheet", "brief", "datasheet"}
           for w in words):
        return False
    if all(w.casefold() in _INDUSTRY_SEGMENTS or w.casefold() in {
            "hedge", "financial", "cloud", "service", "public"}
           for w in words):
        return False
    return True


def normalize_rival_name(raw: str, *, client_name: str = "") -> str:
    """Strip page-title tails so 'Darktrace Comparison' becomes Darktrace."""
    name = _strip_label(raw)
    name = _RIVAL_TITLE_TAIL.sub("", name)
    return _party_name(name, client_name=client_name)


def is_competitor_name(text: str, *, client_name: str = "") -> bool:
    """Rival company name. Rejects page titles and comparison headings."""
    name = normalize_rival_name(text, client_name=client_name)
    if not name:
        return False
    low = name.casefold()
    if low in _GENERIC or low in _GENERIC_CUSTOMERS or low in _NAV_GLUED:
        return False
    if low in _NOT_RIVAL or low in _NOT_ORG:
        return False
    if _TITLE_TAIL.search(name) or _PEOPLE_NAV.search(name):
        return False
    if re.search(r"\b(comparison|overview|alternative|versus)\b", low):
        return False
    if is_noise_term(name):
        return False
    return True


def excerpt_supports_rival(name: str, snippet: str) -> bool:
    """True when the excerpt names the rival in a compare / vs claim."""
    if not excerpt_supports_name(name, snippet):
        return False
    return bool(_RIVAL_CUE.search(snippet) or _COMPETITOR_LEAD.search(snippet))


def citation_mismatches_rival(
    url: str, name: str, official_domain: Optional[str] = None,
) -> bool:
    """True when the URL is another vendor's site (darktrace URL + HPE claim)."""
    if not url or not name:
        return False
    host = url.casefold()
    if official_domain and official_domain.casefold().lstrip(".") in host:
        return False
    rival = name.casefold().split()[0]
    label = re.sub(r"^https?://(www\.)?", "", host).split("/")[0].split(":")[0]
    first = label.split(".")[0]
    if rival and rival in first:
        return False
    for vendor in _RIVAL_VENDORS:
        token = vendor.split()[0]
        if token == rival:
            continue
        if token in first:
            return True
    return False


def usable_site_text(scrape, *, max_chars: int = 24000) -> str:
    """Official-site text from usable pages. JS shells do not wipe hubs."""
    if scrape is None:
        return ""
    chunks: list[str] = []
    pages = getattr(scrape, "pages", None)
    if pages is not None:
        for page in pages:
            text = getattr(page, "text", "") or ""
            if not text.strip() or is_error_page(text):
                continue
            url = getattr(page, "url", "") or ""
            chunks.append(f"# {url}\n{text}" if url else text)
        return "\n\n".join(chunks)[:max_chars]
    if hasattr(scrape, "combined_text"):
        blob = scrape.combined_text(max_chars=max_chars) or ""
        if blob.strip() and not is_error_page(blob):
            return blob[:max_chars]
    return ""


def site_has_usable_text(scrape) -> bool:
    return bool(usable_site_text(scrape, max_chars=400))


def _protect_abbrevs(text: str) -> str:
    return re.sub(r"\bU\.S\.", "US", str(text or ""))


def _party_name(raw: str, *, client_name: str = "") -> str:
    name = _strip_label(_protect_abbrevs(raw))
    name = re.split(r",|;|/|\.", name)[0].strip()
    name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.I)
    run = re.match(
        r"^((?:the\s+)?[A-Z][A-Za-z0-9&'!-]{1,40}"
        r"(?:\s+(?:the\s+)?[A-Z][A-Za-z0-9&'!-]{1,24}){0,3})",
        name,
    )
    if run:
        name = run.group(1)
    name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.I)
    name = _clean(name).rstrip("!")
    if not name or not is_discrete_name(name):
        return ""
    if name.casefold() in _GENERIC_CUSTOMERS or name.casefold() in _GENERIC:
        return ""
    client = _clean(client_name).casefold()
    if client and (name.casefold() == client or name.casefold() in client.split()):
        return ""
    return name


def recall_competitors(text: str, client_name: str = "") -> list[str]:
    """Named rivals from comparison / vs / alternative language on the site."""
    raw = _protect_abbrevs(text)
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        piece = normalize_rival_name(name, client_name=client_name)
        if not is_competitor_name(piece, client_name=client_name):
            return
        key = piece.casefold()
        if not piece or key in seen or key in _GENERIC_CUSTOMERS:
            return
        if key in _SCHEDULE_TICKER or is_noise_term(piece):
            return
        seen.add(key)
        found.append(piece)

    for match in _COMPETITOR_LEAD.finditer(raw):
        chunk = match.group(1)
        for part in re.split(r"\band\b|,", chunk):
            _add(part)
    for vendor in _RIVAL_VENDORS:
        if not re.search(rf"\b{re.escape(vendor)}\b", raw, re.I):
            continue
        windows = _windows(raw, vendor)
        if any(_RIVAL_CUE.search(w) or _COMPETITOR_LEAD.search(w) for w in windows):
            _add(vendor.title() if vendor.islower() else vendor)
    return found


def names_from_customer_context(text: str, client_name: str = "") -> list[str]:
    """Org names on a proof / case-study / customer page, not lead lists only."""
    raw = _protect_abbrevs(text)
    if not raw.strip():
        return []
    if not (_CUSTOMER_CONTEXT.search(raw) or _ORG_PROOF.search(raw)):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in _ORG_RUN.finditer(raw):
        piece = _party_name(match.group(1), client_name=client_name)
        if not is_customer_name(piece, client_name=client_name):
            continue
        key = piece.casefold()
        if not piece or key in seen or key in _NOT_ORG:
            continue
        seen.add(key)
        found.append(piece)
    return found


def recall_customers(text: str, client_name: str = "") -> list[str]:
    """Named customers / case-study hooks from site proof language."""
    raw = _protect_abbrevs(text)
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        piece = _party_name(name, client_name=client_name)
        if not is_customer_name(piece, client_name=client_name):
            return
        key = piece.casefold()
        if not piece or key in seen:
            return
        seen.add(key)
        found.append(piece)

    for match in _CUSTOMER_LEAD.finditer(raw):
        for part in re.split(r",|;|\band\b", match.group(1)):
            _add(part)
    for name in names_from_customer_context(raw, client_name=client_name):
        _add(name)
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
    naics: list[tuple[str, str, str]] = []
    exclusions: list[str] = []
    related: list[RelatedEntity] = []
    competitors: list[str] = []
    customers: list[str] = []
    seen_off: set[str] = set()
    seen_ex: set[str] = set()
    seen_comp: set[str] = set()
    seen_cust: set[str] = set()
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

    def _compete(name: str) -> None:
        piece = normalize_rival_name(name, client_name=client_name)
        if not is_competitor_name(piece, client_name=client_name):
            return
        key = piece.casefold()
        if not piece or key in seen_comp or key in seen_off:
            return
        seen_comp.add(key)
        competitors.append(piece)

    def _customer(name: str) -> None:
        piece = _party_name(name, client_name=client_name)
        if not is_customer_name(piece, client_name=client_name):
            return
        key = piece.casefold()
        if not piece or key in seen_cust or key in seen_off or key in seen_comp:
            return
        seen_cust.add(key)
        customers.append(piece)

    for row in (product_ingest or {}).get("capabilities") or []:
        name = (row.get("name") if isinstance(row, dict) else str(row) or "").strip()
        _offer(name)

    scrape_text = usable_site_text(scrape, max_chars=24000)
    site_ok = bool(scrape_text)
    if scrape_text:
        for name in candidate_names(scrape_text):
            _offer(name, source_text=scrape_text, implicit=True)
        for name in recall_products(scrape_text):
            _offer(name, source_text=scrape_text)
        for name in recall_competitors(scrape_text, client_name or bound_name):
            _compete(name)
        for name in recall_customers(scrape_text, client_name or bound_name):
            _customer(name)
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
        cites = list(getattr(probe, "citations", None) or (
            probe.get("citations") if isinstance(probe, dict) else []) or [])
        official = any(citation_is_official(c, official_domain) for c in cites)
        if "boundar" in kind:
            for name in extract_exclusions(findings):
                _exclude(name)
        elif "channel" in kind or "reseller" in kind:
            pass
        elif "compet" in kind:
            if official:
                for name in recall_competitors(findings, client_name or bound_name):
                    _compete(name)
        elif "customer" in kind or "proof" in kind:
            if official:
                for name in recall_customers(findings, client_name or bound_name):
                    _customer(name)
        elif official:
            implicit = "offering" in kind or "product" in kind
            for name in names:
                _offer(name, source_text=findings, implicit=implicit)
            for name in recall_products(findings):
                _offer(name, source_text=findings)
        elif site_ok:
            # Site already painted the product picture; skip generic SERP blurbs.
            pass
        else:
            # Thin site: still refuse generic industry essays as offerings.
            pass
        for name in extract_exclusions(findings):
            _exclude(name)
        for name in recall_collisions(findings, client_name or bound_name):
            _exclude(name)
        naics.extend(extract_naics(
            findings, loose="federal" in kind or "classif" in kind))
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
        if site_ok:
            for name in structured.offerings or []:
                _offer(name)
        for code in structured.naics or []:
            raw = str(code).strip()
            if is_plausible_naics_code(raw):
                why = f"structured extract cited NAICS {raw} from company research"
                naics.append((raw, why, naics_search_role(raw, why)))
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

    fabric = "DANZ Monitoring Fabric"
    blob = "\n".join(all_findings + [scrape_text])
    offerings = [o for o in offerings if o.casefold() not in _CATEGORY_HEADERS]
    if re.search(r"\bDANZ Monitoring Fabric\b", blob, re.I) or any(
            o.casefold() == fabric.casefold() for o in offerings):
        offerings = [o for o in offerings if o.casefold() != "danz"]
        if fabric.casefold() not in {o.casefold() for o in offerings}:
            offerings.append(fabric)

    # de-dupe naics keeping first rationale/role
    seen_n: set[str] = set()
    naics_u: list[tuple[str, str, str]] = []
    exclude_blob = " ".join(exclusions)
    for row in naics:
        code, why = row[0], row[1]
        prior_role = row[2] if len(row) == 3 else "core"
        if code in seen_n or not is_plausible_naics_code(code):
            continue
        role = "boundary" if prior_role == "boundary" else naics_search_role(
            code, why, exclude_blob=exclude_blob)
        seen_n.add(code)
        naics_u.append((code, why, role))

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
        competitors=competitors, customers=customers,
    )


class StructuredProductSurface(BaseModel):
    """Optional model-shaped extract. Empty lists mean the model named none."""

    offerings: list[str] = Field(default_factory=list)
    naics: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)


_STRUCTURE_PRODUCTS = """\
Extract SHORT discrete product and platform names the bound company sells.
Each offering is a name (EOS, CloudVision, AGNI, 7050X, DANZ Monitoring
Fabric), not a paragraph, not a page-load error, and not a GSA/SEWP/award
ID. Extract six-digit NAICS only when THIS company states them. Do not
copy aviation/records namesake industry codes or forecast IDs. Extract
name-collision exclusions only when the findings name that other firm.
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
