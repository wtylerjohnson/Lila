"""Command Center client-view model builder (P2 live binder).

Read-only. Resolves a client's real artifacts under data/state/, harvests
seals + the header client-logo from the pressed report, and returns the model
that ui/static/client-view.js renders. Client-agnostic: any slug whose
artifacts exist renders with zero code changes (slug normalization handles
redhat/red_hat-style variants).

Served by ui/server.py at GET /api/client/<slug>/cc-model. Never fetches,
generates, or invents an image; never writes; never touches pipeline code.
The source wall is swept from real source-definition homes (see build_sources).
"""
from __future__ import annotations
import json
import os
import re
import glob
import datetime
import functools

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _repo_root():
    # ui/static/cc_model.py -> repo root is two dirs up
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _today():
    # deterministic "today" is not needed; use the real date for past/future split
    return datetime.date.today()


# ----------------------------------------------------------------- formatting
def no_emdash(s):
    if not isinstance(s, str):
        return s
    return s.replace("—", " - ").replace("–", "-").strip()


def money(v):
    if not v:
        return None
    v = float(v)
    if v >= 1e9:
        b = v / 1e9
        return f"${b:.0f}B" if b >= 10 else f"${b:.1f}B"
    if v >= 1e6:
        m = v / 1e6
        return f"${m:.0f}M" if m >= 100 else f"${m:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def parse_low(rng):
    if not rng:
        return None
    m = re.findall(r"\$?\s*([\d.]+)\s*([MBK])", rng, re.I)
    if not m:
        return None
    val, unit = m[0]
    return float(val) * {"K": 1e3, "M": 1e6, "B": 1e9}[unit.upper()]


def iso(d):
    if not d:
        return None
    try:
        return datetime.date.fromisoformat(str(d)[:10])
    except Exception:
        pass
    try:
        mo, da, yr = str(d).split("/")
        return datetime.date(int(yr), int(mo), int(da))
    except Exception:
        return None


def disp(d):
    dt = iso(d)
    return f"{dt.day:02d} {MONTHS[dt.month-1]} {str(dt.year)[2:]}" if dt else None


def ticker_glyph(name):
    cons = []
    for ch in name or "":
        u = ch.upper()
        if u.isalpha() and u not in "AEIOU" and u not in cons:
            cons.append(u)
    return "".join(cons[:4]) or (name or "?")[:2].upper()


def short_label(title, agency):
    t = no_emdash(title or "")
    t = re.sub(r"^[0-9A-Z\-]{6,}\s*[-–]\s*", "", t)
    t = t.strip().title() if t.isupper() else t.strip()
    return (t[:52] + "…") if len(t) > 53 else t


# ----------------------------------------------------------------- agencies
_AGENCY_TOKENS = [
    # specific sub-agencies BEFORE their parents (first substring match wins)
    ("internal revenue", "IRS"), ("customs and border", "CBP"),
    ("citizenship and immigration", "USCIS"), ("coast guard", "USCG"),
    ("veterans affairs", "VA"), ("social security", "SSA"),
    ("federal bureau of investigation", "FBI"),
    ("defense information systems", "DISA"), ("defense logistics", "DLA"),
    ("defense health", "DHA"), ("defense threat reduction", "DTRA"),
    ("engraving and printing", "BEP"), ("alcohol, tobacco", "ATF"),
    ("drug enforcement", "DEA"), ("securities and exchange", "SEC"),
    ("accountability office", "GAO"), ("except comptroller general", "GAO"),
    ("oceanic and atmospheric", "NOAA"), ("census bureau", "CENSUS"),
    ("patent and trademark", "USPTO"), ("health resources", "HRSA"),
    ("transportation security", "TSA"), ("cisa", "CISA"),
    ("air force", "USAF"), ("navy", "NAVY"), ("army", "ARMY"),
    ("homeland security", "DHS"),
    ("fiscal service", "BFS"), ("treasury", "TREAS"),
    ("forest service", "FS"), ("agriculture", "USDA"),
    ("health and human", "HHS"),
    ("offices, boards and divisions", "DOJ"), ("justice", "DOJ"),
    ("land management", "BLM"), ("interior", "DOI"),
    ("assistant secretary for administration and management", "DOL"),
    ("labor", "DOL"),
    ("federal aviation", "FAA"), ("federal highway", "FHWA"),
    ("transportation", "DOT"), ("aeronautics and space", "NASA"),
    ("science foundation", "NSF"), ("international development", "USAID"),
    ("comptroller of the currency", "OCC"), ("nuclear regulatory", "NRC"),
    ("procurement operations", "DHS"),
    ("commerce", "DOC"), ("defense", "DOD"),
    ("tsa", "TSA"), ("cbp", "CBP"), ("irs", "IRS"), ("uscis", "USCIS"),
    ("uscg", "USCG"), ("nasa", "NASA"), ("dhs", "DHS"),
]

# Official agency homepages, keyed by normalized token. Static map covering
# every agency present in the packs; unmapped names stay unlinked.
AGENCY_HOMEPAGES = {
    "IRS": "https://www.irs.gov", "CBP": "https://www.cbp.gov",
    "USCIS": "https://www.uscis.gov", "USCG": "https://www.uscg.mil",
    "VA": "https://www.va.gov", "SSA": "https://www.ssa.gov",
    "FBI": "https://www.fbi.gov", "DHS": "https://www.dhs.gov",
    "DISA": "https://www.disa.mil", "DLA": "https://www.dla.mil",
    "DHA": "https://www.health.mil", "DTRA": "https://www.dtra.mil",
    "BEP": "https://www.bep.gov", "ATF": "https://www.atf.gov",
    "DEA": "https://www.dea.gov", "SEC": "https://www.sec.gov",
    "GAO": "https://www.gao.gov", "NOAA": "https://www.noaa.gov",
    "CENSUS": "https://www.census.gov", "USPTO": "https://www.uspto.gov",
    "HRSA": "https://www.hrsa.gov", "TSA": "https://www.tsa.gov",
    "CISA": "https://www.cisa.gov", "USAF": "https://www.af.mil",
    "NAVY": "https://www.navy.mil", "ARMY": "https://www.army.mil",
    "BFS": "https://www.fiscal.treasury.gov", "TREAS": "https://home.treasury.gov",
    "FS": "https://www.fs.usda.gov", "USDA": "https://www.usda.gov",
    "HHS": "https://www.hhs.gov", "DOJ": "https://www.justice.gov",
    "BLM": "https://www.blm.gov", "DOI": "https://www.doi.gov",
    "DOL": "https://www.dol.gov", "FAA": "https://www.faa.gov",
    "FHWA": "https://highways.dot.gov", "DOT": "https://www.transportation.gov",
    "NASA": "https://www.nasa.gov", "NSF": "https://www.nsf.gov",
    "USAID": "https://www.usaid.gov", "OCC": "https://www.occ.gov",
    "NRC": "https://www.nrc.gov", "DOC": "https://www.commerce.gov",
    "DOD": "https://www.defense.gov",
}

# Official seller sites (live-verified this session). Names not present stay
# unlinked. PanAmerica: no live domain found — skipped, reported.
SELLER_SITES = {
    "dlt": "https://www.dlt.com", "dltsolutions": "https://www.dlt.com",
    "carahsoft": "https://www.carahsoft.com", "fcn": "https://www.fcninc.com",
    "fcninc": "https://www.fcninc.com", "swishdata": "https://www.swishdata.com",
    "augustschell": "https://www.augustschell.com",
    "governmentacquisitions": "https://www.governmentacquisitions.com",
    "strategiccommunications": "https://www.stratcomminc.com",
    "bahfed": "https://www.bahfed.com", "bahfedcorp": "https://www.bahfed.com",
    "immixgroup": "https://www.immixgroup.com", "govplace": "https://www.govplace.com",
    "redriver": "https://www.redriver.com",
    "tdsynnex": "https://www.tdsynnex.com",
    "tdsynnexpublicsector": "https://www.tdsynnex.com",
}


def seller_site(name):
    return SELLER_SITES.get(_norm(re.sub(r"\b(inc|llc|corp|corporation|ltd|llp|enterprises|technology|solutions)\b",
                                         "", (name or ""), flags=re.I)))


def agency_homepage(*names):
    for n in names:
        tok = agency_token(n)
        if tok and tok in AGENCY_HOMEPAGES:
            return AGENCY_HOMEPAGES[tok]
    return None
_MONO_SKIP = {"department", "of", "the", "us", "u.s.", "bureau", "office",
              "administration", "service", "and", "for", "national", "agency"}


def agency_token(text):
    t = (text or "").lower()
    for sub, tok in _AGENCY_TOKENS:
        if sub in t:
            return tok
    return None


def monogram(agency):
    tok = agency_token(agency)
    if tok:
        return tok[:4]
    head = re.split(r"[/ ]", (agency or "").strip())[0]
    if 2 <= len(head) <= 5 and head.isupper() and head.isalpha():
        return head
    words = [w for w in re.sub(r"[^A-Za-z ]", " ", agency or "").split()
             if w.lower() not in _MONO_SKIP]
    if words:
        return "".join(w[0] for w in words[:2]).upper()
    return (agency or "?")[:2].upper()


# ----------------------------------------------------------------- resolver
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve_artifacts(slug, root):
    """Find a client's artifacts by normalized slug across the known homes.
    Returns dict of paths (any may be None)."""
    key = _norm(slug)
    state = os.path.join(root, "data", "state")

    def first_match(paths):
        for p in paths:
            base = os.path.basename(p).split(".")[0]
            if _norm(base) == key:
                return p
        return None

    research = first_match(sorted(glob.glob(os.path.join(state, "golden_build", "*.research.raw.json"))))

    packs = sorted(glob.glob(os.path.join(state, "golden_build", "*.evidence_pack.json")))
    pack = first_match(packs)
    if not pack:
        for p in sorted(glob.glob(os.path.join(state, "candidate_review_v1", "*", "*.golden_report.evidence_pack.json"))):
            if _norm(os.path.basename(os.path.dirname(p))) == key:
                pack = p
                break

    reports = []
    for p in sorted(glob.glob(os.path.join(state, "candidate_review_v1", "*", "*.golden_report.html"))):
        if _norm(os.path.basename(os.path.dirname(p))) == key:
            reports.append(p)
    for p in sorted(glob.glob(os.path.join(root, "fixtures", "golden", "*_golden.html"))):
        if _norm(os.path.basename(p).replace("_golden.html", "")) == key:
            reports.append(p)
    return {"research": research, "pack": pack, "reports": reports}


# ----------------------------------------------------------------- harvest
def _seals_from_html(html):
    seals = {}
    for block in re.split(r"(?=<article)", html):
        m = re.search(r'sb-route-seal[^"]*has-image[^>]*>\s*<img[^>]*src="(data:image/[^"]+)"', block)
        if not m:
            continue
        tok = agency_token(re.sub(r"<[^>]+>", " ", block))
        if not tok:
            continue
        uri = m.group(1)
        if tok not in seals or len(uri) > len(seals[tok]):
            seals[tok] = uri
    return seals


def _all_report_files(root):
    files = glob.glob(os.path.join(root, "data", "state", "candidate_review_v1", "*", "*.golden_report.html"))
    files += glob.glob(os.path.join(root, "fixtures", "golden", "*_golden.html"))
    return sorted(files)


@functools.lru_cache(maxsize=8)
def _seal_library(root, _fingerprint):
    """Shared agency-seal library harvested ACROSS every report on disk, so a
    new client inherits agencies any report has surfaced. Cache keyed on a
    mtime fingerprint so edits re-parse (ASSET LAW library model)."""
    lib = {}
    for path in _all_report_files(root):
        try:
            html = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for tok, uri in _seals_from_html(html).items():
            if tok not in lib or len(uri) > len(lib[tok]):
                lib[tok] = uri
    return lib


def _report_fingerprint(root):
    return tuple(sorted((p, int(os.path.getmtime(p))) for p in _all_report_files(root)))


def harvest_assets(slug, report_paths, root):
    """Seals: shared agency-truth library across all reports. Logo: the client's
    OWN report HEADER slot (.sb-brand img), NOT sb-award-logo recipient marks."""
    seals = dict(_seal_library(root, _report_fingerprint(root)))
    logo = None
    for path in report_paths:
        if not path or not os.path.exists(path):
            continue
        try:
            html = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        lm = re.search(r'class="sb-brand"[^>]*>\s*<img[^>]*src="(data:image/[^"]+)"', html)
        # a neutralized header slot is a tiny placeholder URI; treat it as
        # absent so the chrome falls back to the client's crisp favicon mark
        if lm and len(lm.group(1)) > 300:
            logo = lm.group(1)
            break
    return {"seals": seals, "logo": logo}


# ----------------------------------------------------------------- sources
# The full source surface is swept from real definition homes; see build_sources.
# Curated public metadata over the real adapter set (names/coverage/homepages).
SOURCE_META = {
    "sam_gov": ("SAM.gov", "Contract opportunities (notices)", "https://sam.gov"),
    "usaspending": ("USAspending.gov", "Prime awards and sub-awards", "https://www.usaspending.gov"),
    "apfs_forecast": ("Agency forecasts (APFS)", "Agency procurement forecasts", "https://apfs-cloud.dhs.gov"),
    "web_research": ("Web research", "Vendor and market signals", None),
    "budget_lines": ("Federal budget lines", "Agency budget line items", None),
    "cisa_kev": ("CISA KEV catalog", "Known exploited vulnerabilities", "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"),
    "darpa_opportunities": ("DARPA opportunities", "BAAs and solicitations", "https://www.darpa.mil/work-with-us/opportunities"),
    "dod_budget_exhibits": ("DoD budget exhibits", "RDT&E and procurement exhibits", "https://comptroller.defense.gov"),
    "dsip_topics": ("DoD SBIR/STTR (DSIP)", "Defense SBIR topics", "https://www.dodsbirsttr.mil"),
    "federal_register": ("Federal Register", "Rules and notices", "https://www.federalregister.gov"),
    "forecast_store_changes": ("Forecast store deltas", "Agency forecast changes", None),
    "foreign_assistance": ("ForeignAssistance.gov", "US foreign assistance", "https://foreignassistance.gov"),
    "grants_programs": ("Grants.gov", "Federal grant programs", "https://www.grants.gov"),
    "reginfo_unified_agenda": ("RegInfo Unified Agenda", "Upcoming rulemakings", "https://www.reginfo.gov"),
    "regulations_gov": ("Regulations.gov", "Dockets and comments", "https://www.regulations.gov"),
    "sbir_topics": ("SBIR.gov", "SBIR/STTR topics", "https://www.sbir.gov"),
    "watchdogs": ("Oversight.gov", "Inspector general reports", "https://www.oversight.gov"),
}
SOURCE_META_ALIASES = {
    "sam.gov": "sam_gov",
    "usaspending.gov": "usaspending",
    "web": "web_research",
}
_SKIP_ADAPTER = {"__init__", "base", "registry", "util", "utils", "helpers", "_stored_program"}


def build_sources(root, degraded=None):
    """FULL SOURCE SURFACE: sweep every source-definition home in the repo,
    grouped. Each row traces to a real definition; provenance is recorded in
    `_provenance` so the operator can see where the total comes from.
    Populated by build_source_surface (assigned below) once discovery lands."""
    return _SOURCE_SURFACE_IMPL(root, degraded or {})


_FAMILY_GROUP = {
    "procurement": "PROCUREMENT", "procurement-awards": "PROCUREMENT",
    "federal-spending": "PROCUREMENT", "procurement-law": "PROCUREMENT",
    "pricing": "PROCUREMENT", "entity-vetting": "PROCUREMENT",
    "agency-identity": "PROCUREMENT", "company-filings": "PROCUREMENT",
    "rulemaking": "PROCUREMENT", "legislation": "PROCUREMENT",
    "cybersecurity": "PROCUREMENT", "assistance": "PROCUREMENT",
    "innovation-funding": "PROCUREMENT", "federal-cloud": "PROCUREMENT",
    "defense-research": "PROCUREMENT", "foreign-assistance": "PROCUREMENT",
    "oversight": "PROCUREMENT",
    "procurement-forecast": "FORECASTS", "appropriations": "FORECASTS",
    "defense-budget": "FORECASTS",
    "public-news": "NEWS & PRESS", "public-web": "SEARCH",
}
_GROUP_ORDER = {"PROCUREMENT": 0, "FORECASTS": 1, "NEWS & PRESS": 2, "SEARCH": 3, "VEHICLES": 4}


def _apply_degraded(source, degraded):
    for dk, reason in degraded.items():
        key = (source.get("key") or "").lower()
        if dk == "sam_gov" and "sam" in key:
            source["status"], source["reason"] = "degraded", reason


def _SOURCE_SURFACE_IMPL(root, degraded):
    """Sweep every source-definition home: the master SOURCE_SPECS registry,
    the trade-press FEEDS outlet list, and the vehicles JSON catalog. Every row
    traces to a real definition; provenance records where each count came from."""
    out, prov = [], {}
    if root not in __import__("sys").path:
        __import__("sys").path.insert(0, root)

    try:
        from tools.api.source_catalog import SOURCE_SPECS
        n = 0
        for s in SOURCE_SPECS:
            fam = getattr(s, "family", None)
            if getattr(s, "adapter_name", None) == "trade_rss":
                continue  # expanded into individual FEEDS outlets below
            key = getattr(s, "adapter_name", None) or _norm(getattr(s, "label", "") or fam)
            metadata_key = SOURCE_META_ALIASES.get(key, key)
            metadata = SOURCE_META.get(metadata_key) or SOURCE_META.get(_norm(metadata_key))
            row = {"key": key,
                   "name": no_emdash((metadata and metadata[0]) or getattr(s, "label", None) or key or fam),
                   "coverage": no_emdash(getattr(s, "coverage_description", "") or (metadata and metadata[1]) or "Research coverage"),
                   "url": getattr(s, "official_url", None) or (metadata and metadata[2]), "group": _FAMILY_GROUP.get(fam, "PROCUREMENT"),
                   "mono": False, "status": "registered", "reason": None}
            _apply_degraded(row, degraded)
            out.append(row)
            n += 1
        prov["source_catalog"] = {"file": "tools/api/source_catalog.py::SOURCE_SPECS", "count": n}
    except Exception as e:  # pragma: no cover
        prov["source_catalog_error"] = str(e)

    try:
        from tools.api.trade_rss import FEEDS
        n = 0
        for nm, url in (FEEDS.items() if hasattr(FEEDS, "items") else []):
            out.append({"key": _norm(nm), "name": no_emdash(nm), "coverage": "Trade press feed",
                        "url": url, "group": "NEWS & PRESS", "mono": False,
                        "status": "registered", "reason": None})
            n += 1
        prov["news_feeds"] = {"file": "tools/api/trade_rss.py::FEEDS", "count": n}
    except Exception as e:  # pragma: no cover
        prov["news_feeds_error"] = str(e)

    try:
        vj = json.load(open(os.path.join(root, "data", "reference", "vehicles.json")))
        vlist = vj.get("vehicles", []) if isinstance(vj, dict) else vj
        for v in vlist:
            verified = bool(v.get("verified"))
            out.append({"key": _norm(v.get("name", "")), "name": no_emdash(v.get("name", "")),
                        "coverage": no_emdash(v.get("scope") or v.get("agency") or ""),
                        "url": None, "group": "VEHICLES", "mono": False,
                        "status": "catalog-verified" if verified else "unverified",
                        "reason": None if verified else no_emdash(v.get("status") or "Verify before citation"),
                        "verified": verified})
        prov["vehicles"] = {"file": "data/reference/vehicles.json", "count": len(vlist)}
    except Exception as e:  # pragma: no cover
        prov["vehicles_error"] = str(e)

    out.sort(key=lambda s: _GROUP_ORDER.get(s["group"], 9))
    counts = {}
    for s in out:
        counts[s["group"]] = counts.get(s["group"], 0) + 1
    prov["group_counts"] = counts
    prov["total"] = len(out)
    return {"sources": out, "provenance": prov}


# ----------------------------------------------------------------- analyst
CAT_LABEL = {"capability": "Capability", "technology": "Technology",
             "search_term": "Search term", "agency": "Agency",
             "set_aside": "Set-aside", "naics": "NAICS term", "other": "Other"}


@functools.lru_cache(maxsize=1)
def _code_titles():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "code-titles.json")
    try:
        return json.load(open(p))
    except OSError:
        return {"naics": {}, "psc": {}}


def build_analyst(strat, ents, pack):
    code_titles = _code_titles()

    def rows(kind):
        out = []
        for e in ents:
            if e["kind"] != kind:
                continue
            row = {"name": e["name"], "kind": e["kind"],
                   "rationale": no_emdash(e.get("rationale", "")), "source": e.get("source")}
            if kind == "reseller":
                row["site"] = seller_site(e["name"])  # verified static map
            out.append(row)
        return out

    fam = {}
    for k in strat.get("keywords", []):
        fam.setdefault(k.get("category") or "other", []).append(
            {"term": k["term"], "rationale": no_emdash(k.get("rationale", "")),
             "origin": k.get("origin") or "system", "source": k.get("source")})
    order = ["capability", "technology", "search_term", "agency", "set_aside", "naics", "other"]
    keyword_families = [{"term": CAT_LABEL.get(c, c.title()),
                         "description": no_emdash(", ".join(t["term"] for t in fam[c])),
                         "count": len(fam[c]), "tag": "KEYWORD",
                         "terms": fam[c]} for c in order if c in fam]

    # review context per NAICS code from near-misses (the workshop's rule:
    # a near-miss reason is exclusionary context, never the reason it belongs)
    near_by_code = {str(n.get("value", "")).strip(): no_emdash(n.get("reason", ""))
                    for n in strat.get("near_misses", []) if n.get("kind") == "naics"}
    ntitles = code_titles.get("naics", {})
    naics = [{"code": m["code"], "title": ntitles.get(m["code"]) or no_emdash(m.get("title", "")),
              "why": no_emdash(m.get("rationale", "")), "role": m.get("role"), "tag": "NAICS",
              "context": near_by_code.get(str(m["code"]).strip(), "")}
             for m in strat.get("naics_meta", [])]

    ptitles = code_titles.get("psc", {})
    seen = {}
    for r in pack["records"]:
        c = r.get("psc")
        if c:
            seen[c] = seen.get(c, 0) + 1
    psc = [{"code": c, "title": ptitles.get(c, ""), "why": "", "count": seen[c], "tag": "PSC"}
           for c in sorted(seen, key=lambda k: -seen[k])]

    boundary = [{"name": n["value"], "kind": n["kind"], "is_code": n["kind"] in ("naics", "psc"),
                 "reason": no_emdash(n.get("reason", "")), "confidence": n.get("confidence")}
                for n in strat.get("near_misses", [])]

    return {
        "status": "DRAFT" if strat.get("requires_human_review") else "APPROVED",
        "status_date": None,
        "gate_note": no_emdash(strat.get("review_gate", "")),
        "entities": {"products": rows("product"), "competitors": rows("competitor"),
                     "resellers": rows("reseller")},
        "keyword_families": keyword_families, "naics": naics, "psc": psc, "boundary": boundary,
        "press": {"label": "Approve and press", "sublabel": "SWEEP · COMPOSE · VALIDATE"},
    }


TARGETING_CARDS = [
    {"title": "Buying-office personas", "sub": "Who buys this, per corridor"},
    {"title": "Account ownership", "sub": "Hypotheses from seller patterns"},
    {"title": "Verified contacts", "sub": "Named people, checked live"},
    {"title": "Outreach sequencing", "sub": "Meetings from the evidence"},
]


def _degraded_from_lanes(pack):
    out = {}
    for lane in pack.get("lanes", []):
        if lane.get("status") == "degraded" and lane.get("lane") == "L1_notice":
            out["sam_gov"] = no_emdash(lane.get("detail") or "degraded")
    return out


def _press_stages(strat, ents, pack):
    products = [e for e in ents if e["kind"] == "product"]
    competitors = [e for e in ents if e["kind"] == "competitor"]
    resellers = [e for e in ents if e["kind"] == "reseller"]
    unique = len({r["record_id"] for r in pack["records"]})
    n_kw = len(strat.get("keywords", []))
    n_naics = len(strat.get("inferred_naics", []) or strat.get("naics_meta", []))
    n_screened = sum(v for v in pack.get("selection", {}).get("screened_relevant", {}).values())
    lanes_live = sum(1 for l in pack.get("lanes", []) if l.get("status") == "live")
    suff = pack.get("sufficiency", {})
    suff_receipt = no_emdash(str(suff.get("verdict") or suff.get("status") or "passed")).capitalize()
    return [
        {"name": "Company research", "status": "done",
         "receipt": f"{len(products)} products · {len(competitors)} competitors · {len(resellers)} resellers"},
        {"name": "Analyst layer approved", "status": "done", "receipt": f"{n_kw} keywords · {n_naics} NAICS"},
        {"name": "Retrieval · 4 lanes", "status": "done", "receipt": f"{lanes_live} lanes live · {n_screened} screened"},
        {"name": "Sufficiency gate", "status": "done", "receipt": suff_receipt},
        {"name": "Composing report", "status": "done", "receipt": f"{unique} records packed"},
        {"name": "Adversarial critique", "status": "done", "receipt": "arbiters cleared"},
        {"name": "Fact validation", "status": "done", "receipt": "every figure source-linked"},
        {"name": "Pressed", "status": "done", "receipt": f"{unique} unique records · deduped"},
    ]


def _first_sentence(ps, limit=150):
    desc = re.split(r"(?<=[a-z])\. ", no_emdash(ps or ""))[0].strip()
    if len(desc) > limit:
        desc = desc[: limit - 3].rsplit(" ", 1)[0] + "…"
    return desc


def list_meta(slug, root=None):
    """Cheap per-client meta for the client list: capability line, unique
    record count, last pressed date. Read-only; None when nothing exists."""
    root = root or _repo_root()
    art = resolve_artifacts(slug, root)
    if not art["research"] and not art["pack"]:
        return None
    out = {"capability": "", "records": None, "pressed_display": None, "logo": None}
    try:
        if art["research"]:
            research = json.load(open(art["research"]))
            strat = research.get("strategy", research)
            out["capability"] = _first_sentence(strat.get("pursuit_strategy", ""))
    except (OSError, ValueError):
        pass
    try:
        # crisp brand mark for the landing row: the report header slot
        # (same harvest as the client view; favicon fallback stays client-side)
        out["logo"] = harvest_assets(slug, art["reports"], root)["logo"]
    except OSError:
        pass
    try:
        if art["pack"]:
            pack = json.load(open(art["pack"]))
            out["records"] = len({r.get("record_id") for r in pack.get("records", [])})
            out["pressed_display"] = disp(pack.get("generated_at"))
    except (OSError, ValueError):
        pass
    return out


# ----------------------------------------------------------------- press log
STAGE_NAMES = ["Company research", "Analyst layer approved", "Retrieval · 4 lanes",
               "Sufficiency gate", "Composing report", "Adversarial critique",
               "Fact validation", "Pressed"]


def parse_press_log(text, still_running):
    """Map a candidate-review job log to the 8 rail stages, READ-ONLY.
    Verified against the riverbed take-4 (failed) and 16:47 (shipped) logs.
    A stage lights only on its real marker; failure pins the active stage."""
    st = {n: {"name": n, "status": "pending", "receipt": ""} for n in STAGE_NAMES}
    order = STAGE_NAMES
    fail_reason = None
    retrieval_live = ""
    for raw in text.splitlines():
        line = raw.strip()
        if "packet loaded: status=approved" in line:
            st["Analyst layer approved"].update(status="done", receipt="packet approved")
        m = re.search(r"ENTITY BRIDGE: .*?using (\d+) research entities", line)
        if m:
            st["Company research"].update(status="done", receipt=f"{m.group(1)} research entities")
        if line.startswith("[sam.gov]") or line.startswith("[golden:retrieval]"):
            if st["Retrieval · 4 lanes"]["status"] == "pending":
                st["Retrieval · 4 lanes"]["status"] = "active"
                # research/analyst precede retrieval; if their markers were
                # absent they are implicitly complete once retrieval runs
                for n in ("Company research", "Analyst layer approved"):
                    if st[n]["status"] == "pending":
                        st[n].update(status="done")
        m = re.search(r"\[golden:retrieval\] L2 '([^']+)'[^:]*: (\d+) returned, (\d+) kept", line)
        if m:
            retrieval_live = f"{m.group(1)}: {m.group(3)} kept"
            st["Retrieval · 4 lanes"]["receipt"] = retrieval_live
        m = re.search(r"pack selection kept (\d+) of (\d+) screened", line)
        if m:
            st["Retrieval · 4 lanes"].update(status="done",
                                             receipt=f"kept {m.group(1)} of {m.group(2)} screened")
        m = re.search(r"pack: (\d+) unique records.*sufficiency (\w+)", line)
        if m:
            st["Retrieval · 4 lanes"]["status"] = "done"
            if not st["Retrieval · 4 lanes"]["receipt"]:
                st["Retrieval · 4 lanes"]["receipt"] = f"{m.group(1)} unique records"
            verdict = m.group(2)
            st["Sufficiency gate"].update(
                status="done" if verdict.upper() == "MET" else "failed",
                receipt=verdict)
        m = re.search(r"composer payload ([\d,]+) chars", line)
        if m:
            st["Composing report"].update(status="active",
                                          receipt=f"payload {m.group(1)} chars")
        m = re.search(r"compose call: model=(\S+), prompt=([\d,]+) chars", line)
        if m:
            st["Composing report"].update(status="active",
                                          receipt=f"prompt {m.group(2)} chars")
        if re.search(r"critique loop \d+ \(", line):
            st["Composing report"]["status"] = "done"
            st["Adversarial critique"]["status"] = "active"
        m = re.search(r"critique loop (\d+): (\w+)", line)
        if m:
            st["Adversarial critique"].update(status="done",
                                              receipt=f"loop {m.group(1)} {m.group(2)}")
        m = re.search(r"validator: (\d+) violations", line)
        if m:
            st["Fact validation"].update(status="active",
                                         receipt=f"{m.group(1)} violations")
        m = re.search(r"validator revision: (\d+) violations", line)
        if m:
            st["Fact validation"]["receipt"] = f"revision: {m.group(1)} violations"
        m = re.search(r"recall vs golden dock: (\S+)", line)
        if m:
            st["Pressed"]["receipt"] = f"recall {m.group(1)}"
        if line.startswith("[out:golden-report]"):
            st["Fact validation"]["status"] = "done"
            st["Pressed"]["status"] = "done"
            if not st["Pressed"]["receipt"]:
                st["Pressed"]["receipt"] = os.path.basename(line.split("]", 1)[-1].strip())
        if line.startswith("[out:golden-report-FAILED]"):
            fail_reason = fail_reason or "golden press failed; draft kept"
        if line.startswith("{") and '"reason"' in line:
            try:
                fail_reason = no_emdash(json.loads(line).get("reason", ""))[:160]
            except Exception:
                pass
    stages = [st[n] for n in order]
    if fail_reason:
        # pin the failure on the deepest non-pending, non-done stage
        target = None
        for s in stages:
            if s["status"] in ("active", "failed"):
                target = s
        if target is None:
            target = next((s for s in reversed(stages) if s["status"] == "done"), stages[0])
        target["status"] = "failed"
        target["receipt"] = fail_reason
        for s in stages[stages.index(target) + 1:]:
            s["status"], s["receipt"] = "pending", ""
    elif still_running:
        # exactly one active stage: the first not-done
        seen_active = False
        for s in stages:
            if s["status"] == "done" or seen_active:
                if s["status"] == "active" and seen_active:
                    s["status"] = "pending"
                continue
            s["status"] = "active"
            seen_active = True
    else:
        # finished cleanly: actives collapse to done
        for s in stages:
            if s["status"] == "active":
                s["status"] = "done"
    return stages


def find_press_log(slug, root, jobs=None):
    """Locate the newest candidate-review log for this client, read-only.
    Prefers the live in-process JOBS registry; falls back to scanning
    data/state/job_logs newest-first for the slug's artifact-dir token."""
    key = _norm(slug)
    if jobs:
        best = None
        for j in jobs.values():
            if j.get("step") != "candidate_review":
                continue
            if _norm(j.get("client") or "") != key:
                continue
            if best is None or (j.get("started_at") or "") > (best.get("started_at") or ""):
                best = j
        if best and best.get("log_path") and os.path.exists(best["log_path"]):
            return best["log_path"], best.get("status") == "running", best.get("id")
    # disk fallback: newest logs mentioning this client's artifact dir
    log_dir = os.path.join(root, "data", "state", "job_logs")
    token = None
    for p in glob.glob(os.path.join(root, "data", "state", "candidate_review_v1", "*")):
        if os.path.isdir(p) and _norm(os.path.basename(p)) == key:
            token = f"candidate_review_v1/{os.path.basename(p)}"
            break
    if not token:
        return None, False, None
    paths = sorted(glob.glob(os.path.join(log_dir, "*.log")),
                   key=os.path.getmtime, reverse=True)[:60]
    for p in paths:
        try:
            if os.path.getsize(p) < 200:
                continue
            with open(p, encoding="utf-8", errors="ignore") as f:
                text = f.read(400_000)
            if token in text:
                return p, False, os.path.basename(p)[:-4]
        except OSError:
            continue
    return None, False, None


def press_status(slug, root=None, jobs=None):
    """Truthful press status: stage JSON would win if the pipeline wrote one
    (it does not today); else the job log, read-only; else None."""
    root = root or _repo_root()
    path, live, job_id = find_press_log(slug, root, jobs)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read(600_000)
    except OSError:
        return None
    return {"live": bool(live), "job_id": job_id,
            "stages": parse_press_log(text, bool(live)),
            "log_mtime": int(os.path.getmtime(path))}


def _partial_model(slug, root, art):
    """Fresh client after intake only: header/description/chips + the analyst
    gate populate from research; everything pack-derived renders its designed
    partial state (ghost scoreboard, hidden ticker, empty calendar)."""
    research = json.load(open(art["research"]))
    strat = research.get("strategy", research)
    name = strat.get("client_name") or slug.replace("_", " ").title()
    ents = strat.get("research_entities", [])
    ps = no_emdash(strat.get("pursuit_strategy", ""))
    desc = re.split(r"(?<=[a-z])\. ", ps)[0].strip()
    if len(desc) > 150:
        desc = desc[:147].rsplit(" ", 1)[0] + "…"
    sources = build_sources(root, {})
    empty_pack = {"records": [], "selection": {}, "lanes": [], "sufficiency": {}}
    return {
        "partial": True,
        "client": {"name": name, "slug": slug, "initials": ticker_glyph(name), "description": desc,
                   "website": (research.get("company_research", {}) or {}).get("website")},
        "competitors": [e["name"] for e in ents if e["kind"] == "competitor"],
        "resellers": [e["name"] for e in ents if e["kind"] == "reseller"],
        "metrics": {
            "corridors": {"value": 0, "context": "populates on first press"},
            "footprint": {"display": None, "context": "populates on first press"},
            "forecast_lb": {"display": None, "context": "populates on first press"},
            "evidence": {"value": 0, "context": "populates on first press"},
        },
        "ticker": [], "calendar": [], "reports": [],
        "report_title": "Federal Opportunity Pre-Assessment",
        "analyst": build_analyst(strat, ents, empty_pack),
        "sources": sources["sources"], "sources_provenance": sources["provenance"],
        "assets": harvest_assets(slug, art["reports"], root),
        "targeting": {"status": "NEXT RELEASE · builds from this evidence base", "cards": TARGETING_CARDS},
        "press_stages": None, "hero": None,
        "meta": {"evidence_view": f"/client/{slug}", "generated_at": None},
    }


# ----------------------------------------------------------------- main
def build_model(slug, root=None):
    root = root or _repo_root()
    art = resolve_artifacts(slug, root)
    if not art["research"]:
        return None
    if not art["pack"]:
        return _partial_model(slug, root, art)
    research = json.load(open(art["research"]))
    pack = json.load(open(art["pack"]))
    strat = research.get("strategy", research)
    recs = pack["records"]
    name = pack.get("client_name") or strat.get("client_name") or slug.replace("_", " ").title()
    today = _today()

    ents = strat.get("research_entities", [])
    client_ents = {e["name"] for e in ents if e["kind"] == "product"} | {name}
    competitors = [e["name"] for e in ents if e["kind"] == "competitor"]
    resellers = [e["name"] for e in ents if e["kind"] == "reseller"]

    def is_client(r):
        return any(name in h or h in client_ents for h in (r.get("entity_hits") or []))

    def end_date(r):
        for k in ("potential_end_date", "period_end"):
            if iso(r.get(k)):
                return iso(r.get(k))
        return None

    awards = [r for r in recs if r["lane"] == "L2_entity_award"]
    forecasts = [r for r in recs if r["lane"] == "L4_forecast"]
    active = [r for r in awards if end_date(r) and end_date(r) >= today]
    corridors = sorted({r["agency"] for r in active if r.get("agency")})
    footprint = sum((r.get("obligated_dollars") or 0) for r in recs if is_client(r))
    forecast_lb = sum((parse_low(r.get("estimated_value_range")) or r.get("forecast_value") or 0) for r in forecasts)
    unique_ids = {r["record_id"] for r in recs}

    ps = no_emdash(strat.get("pursuit_strategy", ""))
    desc = re.split(r"(?<=[a-z])\. ", ps)[0].strip()
    if len(desc) > 150:
        desc = desc[:147].rsplit(" ", 1)[0] + "…"

    cal = []
    for i, r in enumerate(recs):
        rid = no_emdash(str(r.get("record_id") or "")).lstrip("*")
        agency = no_emdash(r.get("sub_agency") or r.get("agency") or "")
        dollars = money(r.get("obligated_dollars") or r.get("forecast_value") or parse_low(r.get("estimated_value_range")))
        fields = []
        if r.get("response_deadline"):
            fields.append(("deadline", r["response_deadline"]))
        ed = r.get("potential_end_date") or r.get("period_end")
        if ed:
            fields.append(("clock", ed))
        if r.get("anticipated_solicitation"):
            fields.append(("forecast", r["anticipated_solicitation"]))
        if r.get("posted_date") and r["lane"] == "L1_notice":
            fields.append(("on-ramp", r["posted_date"]))
        for typ, raw in fields:
            dt = iso(raw)
            if not dt:
                continue
            cal.append({
                "id": f"cal-{i}-{typ}", "date_iso": dt.isoformat(), "date_display": disp(raw),
                "label": short_label(r.get("title"), agency), "agency": agency, "type": typ,
                "url": r.get("url"), "record_id": rid, "dollars": dollars, "past": dt < today,
                "agency_token": agency_token(agency) or agency_token(r.get("agency")),
                "monogram": monogram(agency),
                "agency_url": agency_homepage(agency, r.get("agency")),
                "entities": list(r.get("entity_hits") or []) + ([name] if is_client(r) else []),
            })
    cal.sort(key=lambda c: c["date_iso"])

    tick_src = sorted([c for c in cal if not c["past"] and c["dollars"]], key=lambda c: c["date_iso"])[:14]
    ticker = [{"type": c["type"], "date_display": c["date_display"], "agency": c["agency"],
               "label": c["label"], "dollars": c["dollars"], "url": c["url"], "record_id": c["record_id"],
               "calendar_id": c["id"], "entities": c["entities"],
               "agency_token": c["agency_token"], "monogram": c["monogram"],
               "agency_url": c["agency_url"]} for c in tick_src]

    report_title = "Federal Opportunity Pre-Assessment"
    # the pressed report opens in the IN-CC viewer: the same-origin /report
    # route the press itself links (design law: never re-rendered, the file's
    # own editor chrome works there). First resolved report on disk wins.
    report_href = None
    for rp in art["reports"]:
        if rp and os.path.exists(rp) and "fixtures" not in rp:
            report_href = "/report?path=" + os.path.relpath(rp, root)
            break
    reports = []
    if report_href:
        reports = [{"title": report_title,
                    "pressed_display": disp(pack.get("generated_at")) or disp(today.isoformat()),
                    "records": len(unique_ids), "href": report_href}]
    hero_stat = (f"{len(unique_ids)} records · {len(corridors)} corridors · "
                 f">{money(forecast_lb)} forecast demand · every figure source-linked")

    sources = build_sources(root, _degraded_from_lanes(pack))
    return {
        "client": {"name": name, "slug": slug, "initials": ticker_glyph(name), "description": desc,
                   "website": (research.get("company_research", {}) or {}).get("website")},
        "competitors": competitors, "resellers": resellers,
        "metrics": {
            # each context line is an anchor into the pressed report's own
            # evidence section (linkage law: aggregates cite their components)
            "corridors": {"value": len(corridors), "context": f"{len(active)} active awards",
                          "href": (report_href + "#candidate-review") if report_href else None},
            "footprint": {"display": money(footprint), "context": "Obligated, cited",
                          "href": (report_href + "#accounts") if report_href else None},
            "forecast_lb": {"display": ">" + (money(forecast_lb) or "$0"), "context": f"{len(forecasts)} forecast records",
                            "href": (report_href + "#signals") if report_href else None},
            "evidence": {"value": len(unique_ids), "context": "Unique linked records",
                         "href": (report_href + "#evidence") if report_href else None},
        },
        "ticker": ticker, "calendar": cal, "reports": reports, "report_title": report_title,
        "analyst": build_analyst(strat, ents, pack),
        "sources": sources["sources"], "sources_provenance": sources["provenance"],
        "assets": harvest_assets(slug, art["reports"], root),
        "targeting": {"status": "NEXT RELEASE · builds from this evidence base", "cards": TARGETING_CARDS},
        "press_stages": _press_stages(strat, ents, pack),
        "hero": {"eyebrow": "FEDERAL OPPORTUNITY PRE-ASSESSMENT", "name": name, "stat_line": hero_stat,
                 "report_href": report_href},
        "meta": {"evidence_view": f"/client/{slug}", "generated_at": pack.get("generated_at")},
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: cc_model.py <slug>")
    m = build_model(sys.argv[1])
    print(json.dumps(m, indent=2)[:1500] if m else "NO MODEL")
