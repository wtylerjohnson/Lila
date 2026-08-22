"""Brand-name SAM.gov lanes: where a notice names a product by name.

WHY THESE LANES. Most federal notices describe a requirement. A small,
high-value minority name a PRODUCT: a justification for buying one vendor's
software, an intent to sole-source, a brand-name-or-equal solicitation. For a
product company those are the notices that matter most, because they are
either a defence (someone is justifying a competitor into your account) or an
opening (a brand-name-or-equal lets you bid the "equal").

THE DAILY EXTRACT IS THE SOURCE. sam.gov publishes every active Contract
Opportunity as a daily CSV with FULL description text: 76,848 notices and
184 MB of text on 2026-07-27, free, costing zero metered API quota. The
metered API allows roughly ten calls a day and cannot see this.

WHY A LEDGER, NOT A SNAPSHOT (measured 2026-07-27). Comparing the 07-24 and
07-27 extracts: 5,191 notices present on the 24th were GONE by the 27th, 6.4%
in three days, including 12% of all Justifications. The extract is a snapshot
of what is ACTIVE, so a notice posted and closed between two pulls is
invisible forever. Scanning today's file can never mean "nothing gets past
us"; only accumulating every day's scan into a durable ledger can. Hits are
therefore merged into an append-only store that keeps a notice after sam.gov
stops serving it, with first_seen and last_seen recorded.

HONEST COUNTS. The receipt reports every lane and every searched entity
INCLUDING THE ZEROS. On 2026-07-27 a Riverbed scan returned zero hits for all
six products and all six competitors across all four lanes: no active notice
named them. That is a real finding, not an empty section to hide, and it is
the difference between "we found nothing" and "we did not look".
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

LEDGER_DIR = Path(os.environ.get(
    "LILA_SAM_LANE_DIR",
    str(Path(__file__).resolve().parents[2] / "data" / "state" / "sam_lanes")))

# A notice type of Justification IS the lane; the others are text signals.
JA_TYPES = {"Justification", "Justification and Approval (J&A)"}

# FAR 6.302-1 is "only one responsible source". These phrasings are what a
# contracting officer actually writes when competition is being limited.
_SOLE = re.compile(
    r"intent to (?:sole.?source|award)|sole.?source|single source|"
    r"only one responsible source|6\.302-1|"
    r"other than full and open competition", re.I)
# "or equal" alone is far too loose (it appears in ordinary spec language), so
# the brand-name lane requires the phrase "brand name" or the FAR term of art.
_BNE = re.compile(r"brand.?name|salient characteristic", re.I)

LANES: tuple[tuple[str, str], ...] = (
    ("posted_ja", "Posted justification for limiting competition"),
    ("sole_source_intent", "Stated intent to award without competition"),
    ("brand_name_or_equal", "Brand name or equal, with salient characteristics"),
    ("sources_sought", "Sources sought and market research"),
)

# Product names that are also ordinary English words. A single-token name in
# this set (or in the system dictionary) must have the vendor named in the
# same notice, or "SteelHead" matches a notice about steelhead trout. That is
# not hypothetical: it is the false positive this guard was built from, on
# "NOTICE OF INTENT TO SOLE-SOURCE TO INNOVASEA", an aquaculture buy, and the
# notice is on record: the word list lives in the polysemy corpus
# (data/reference/polysemy_corpus.json, common_word_products) next to that
# witness. A missing corpus weakens the guard to dictionary-only; it never
# fails a scan.
_CORPUS_PATH = (Path(__file__).resolve().parents[2]
                / "data" / "reference" / "polysemy_corpus.json")
_SYSTEM_DICTIONARIES = ("/usr/share/dict/words", "/usr/dict/words")


def _common_word_products(path: Optional[Path] = None) -> frozenset:
    """The corpus-recorded fallback word list, lowercased."""
    try:
        data = json.loads((path or _CORPUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    words = (data.get("common_word_products") or {}).get("words") or []
    return frozenset(str(w).strip().lower() for w in words if str(w).strip())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_dictionary() -> tuple[frozenset, bool]:
    """(words, had_system_dictionary). The corpus collision list always
    applies; the system dictionary widens it where one exists. The caller
    discloses which mode ran, because the guard is weaker without one."""
    corpus_words = _common_word_products()
    for path in _SYSTEM_DICTIONARIES:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                words = {w.strip().lower() for w in fh if w.strip()}
            return frozenset(words | corpus_words), True
        except OSError:
            continue
    return frozenset(corpus_words), False


def aliases(name: str, vendor: Optional[str] = None) -> set[str]:
    """Written forms of one product name.

    "Riverbed AppResponse" also appears as "AppResponse" on its own, and
    "SteelCentral" appears as "Steel Central" and "Steel-Central". Matching
    only the canonical string misses most real mentions.
    """
    name = " ".join(str(name or "").split())
    if not name:
        return set()
    out = {name}
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    out.add(spaced)
    out.add(spaced.replace(" ", "-"))
    out.add(name.replace(" ", ""))
    if vendor:
        stripped = re.sub(rf"^{re.escape(vendor)}\s+", "", name, flags=re.I)
        if stripped and stripped != name:
            out |= aliases(stripped)
    return {a for a in out if len(a) >= 3}


def is_distinctive(name: str, words: frozenset) -> bool:
    """True when this name can match on its own; False when it needs the
    vendor named in the same notice to count."""
    tokens = [t for t in re.split(r"[^A-Za-z0-9+]+", str(name or "")) if t]
    if len(tokens) > 1:
        return True
    if not tokens:
        return False
    token = tokens[0]
    if len(token) <= 3:
        return False
    # Test the JOINED lowercase form, not the camel-cased one: "SteelHead"
    # reads as distinctive to the eye but "steelhead" is an English word.
    return token.lower() not in words


def _pattern(forms: Iterable[str]) -> re.Pattern:
    ordered = sorted({f for f in forms}, key=len, reverse=True)
    return re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(re.escape(f) for f in ordered)
        + r")(?![A-Za-z0-9])", re.I)


def build_matchers(entities: dict, vendor: Optional[str] = None) -> dict:
    """{name: {pattern, kind, distinctive, forms}} for every searched entity."""
    words, _ = load_dictionary()
    out: dict[str, dict] = {}
    for kind, names in (entities or {}).items():
        for name in names or []:
            forms = aliases(name, vendor)
            if not forms:
                continue
            out[name] = {"pattern": _pattern(forms), "kind": kind,
                         "distinctive": is_distinctive(name, words),
                         "forms": sorted(forms)}
    return out


def _corpus_negatives() -> tuple:
    """Witnessed negative phrases, late-imported to keep lanes cycle-free."""
    from agents.golden_press.term_tiers import corpus_negative_phrases
    return tuple(corpus_negative_phrases())


def reject_entity_hit(matcher: dict, *, vendor_present: bool,
                      product_hits: Any, text: str = "",
                      negative_phrases: Any = ()) -> Optional[str]:
    """THE shared entity guard. None to admit, else the reason for rejecting.

    Both the brand-name lane and the store-backed L1 lane call this. It is a
    function rather than a copied pair of conditions so the two lanes cannot
    drift: a guard that is right in one place and stale in the other is worse
    than no guard, because the difference is invisible from either side.

    Three rules, each earned from a real false positive on live data:

      AMBIGUOUS NAME WITHOUT THE VENDOR. "SteelHead" matched a Commerce
      Department sole-source to Innovasea, an aquaculture buy, because
      steelhead is a trout. A single-token product name that is an ordinary
      English word must have the vendor named in the same notice.

      RESELLER WITHOUT THE VENDOR OR A PRODUCT. Carahsoft resells for
      hundreds of vendors, so "Carahsoft" alone matched Smartsheet,
      ServiceNow and an audio-visual buy while telling us nothing about the
      client. A reseller hit needs the vendor or one of its products present.

      WITNESSED NEGATIVE CONTEXT (operator ruling 2026-08-04). A
      dictionary-distinctive name still collides: "Sentra" matched DLA
      gas-detection probe parts ("PROBE,GASKON SENTRA") and a State
      Department Nissan sedan purchase. Phrases witnessed in the polysemy
      corpus (provenance-carrying, byte-identical) reject the hit when
      they co-occur in the notice or award text.

    Capability terms are NOT passed through here. They are procurement
    language rather than entity names, they carry no vendor to anchor
    against, and they have their own negative-context guards. Running them
    through this would silently kill legitimate capability finds.
    """
    if not matcher.get("distinctive") and not vendor_present:
        return "ambiguous_name_without_vendor"
    if matcher.get("kind") == "reseller" and not (vendor_present or product_hits):
        return "reseller_without_vendor_or_product"
    if text and negative_phrases:
        haystack = " ".join(str(text).casefold().split())
        for phrase in negative_phrases:
            if phrase and str(phrase).casefold() in haystack:
                return f"witnessed_negative_context:{phrase}"
    return None


def classify_lanes(notice_type: str, text: str) -> list[str]:
    """Which brand-name lanes this notice belongs to. A notice can be in
    several: a Justification is usually also sole-source language."""
    lanes = []
    if notice_type in JA_TYPES:
        lanes.append("posted_ja")
    if _SOLE.search(text):
        lanes.append("sole_source_intent")
    if _BNE.search(text):
        lanes.append("brand_name_or_equal")
    if notice_type == "Sources Sought":
        lanes.append("sources_sought")
    return lanes


def _excerpt(text: str, match: re.Match, width: int = 260) -> str:
    """The words around the match, so a hit carries its own proof."""
    start = max(0, match.start() - width // 2)
    end = min(len(text), match.end() + width // 2)
    return " ".join(text[start:end].split())


# How close a product mention must sit to the restriction language before we
# call the product the SUBJECT of the restriction rather than an aside.
SUBJECT_WINDOW = 400


def grade(text: str, match: re.Match) -> tuple[str, int]:
    """("subject" | "mentioned", distance) for one entity match.

    Both are real hits and neither is dropped, but they are worth very
    different things. Measured on the 2026-07-27 extract:

      SUBJECT   "Brand Name Requirement for the following specific
                components: Cisco Systems Networking Hardware Catalyst 9300
                switches ... No substitutions will be accepted."  The product
                sits 23 characters from the restriction. This is the buy.

      MENTIONED "Provide access to training delivered via WebEx/video
                conference."  Webex is named in a VA software RFI thousands of
                characters from any brand-name language. Real, but it is not
                what is being bought.

    Sorting by this is what keeps a lane readable when one common product
    name appears in a dozen notices for incidental reasons.
    """
    nearest = None
    for pattern in (_BNE, _SOLE):
        for found in pattern.finditer(text):
            distance = (match.start() - found.end() if found.end() < match.start()
                        else found.start() - match.end())
            distance = abs(distance)
            if nearest is None or distance < nearest:
                nearest = distance
    if nearest is None:
        return ("mentioned", -1)
    return (("subject" if nearest <= SUBJECT_WINDOW else "mentioned"), nearest)


def canonical_notice_url(row: dict) -> str:
    """The public sam.gov notice URL, never the workspace form.

    The extract's own link column emits
    sam.gov/workspace/contract/opp/<guid>/view, which requires a login. The
    linkage law wants the public form built from the notice id, so the guid
    is what we trust and the column is only a fallback.
    """
    guid = str(row.get("notice_id") or "").strip().lower()
    try:
        from agents.reports.links import build_sam_notice_link
        return build_sam_notice_link(guid).url
    except (ImportError, ValueError):
        return str(row.get("url") or "")


def scan_extract(rows: Iterable[dict], entities: dict, *,
                 vendor: Optional[str] = None,
                 extract_name: str = "") -> tuple[list[dict], dict]:
    """Scan one daily extract for brand-name mentions.

    Returns (hits, receipt). The receipt names every lane and every entity
    searched with its count INCLUDING ZEROS, so a client can tell the
    difference between a lane that found nothing and a lane that never ran.
    """
    words, had_dict = load_dictionary()
    matchers = build_matchers(entities, vendor)
    vendor_pattern = _pattern(aliases(vendor)) if vendor else None
    product_names = {n for n, m in matchers.items()
                     if m["kind"] in ("product", "competitor")}

    hits: list[dict] = []
    counts = {lane: {name: 0 for name in matchers} for lane, _ in LANES}
    subject_counts = {lane: 0 for lane, _ in LANES}
    lane_totals = {lane: 0 for lane, _ in LANES}
    scanned = 0
    seen_at = _now()

    for row in rows:
        scanned += 1
        title = row.get("title") or ""
        text = f"{title}\n{row.get('description') or ''}"
        lanes = classify_lanes(row.get("type") or "", text)
        if not lanes:
            continue
        for lane in lanes:
            lane_totals[lane] += 1
        vendor_here = bool(vendor_pattern.search(text)) if vendor_pattern else False
        matched_products = {
            name for name in product_names
            if matchers[name]["pattern"].search(text)}
        for name, matcher in matchers.items():
            found = matcher["pattern"].search(text)
            if not found:
                continue
            if reject_entity_hit(matcher, vendor_present=vendor_here,
                                 product_hits=matched_products,
                                 text=text,
                                 negative_phrases=_corpus_negatives()):
                continue
            strength, distance = grade(text, found)
            for lane in lanes:
                counts[lane][name] += 1
                if strength == "subject":
                    subject_counts[lane] += 1
                hits.append({
                    "strength": strength,
                    "signal_distance": distance,
                    "notice_id": row.get("notice_id") or "",
                    "lane": lane,
                    "entity": name,
                    "entity_kind": matcher["kind"],
                    "matched_text": found.group(0),
                    "notice_type": row.get("type") or "",
                    "title": title,
                    "agency": row.get("agency") or "",
                    "sub_agency": row.get("subtier") or "",
                    "office": row.get("office") or "",
                    "posted": row.get("posted") or "",
                    "deadline": row.get("deadline") or "",
                    "naics": row.get("naics") or "",
                    "psc": row.get("psc") or "",
                    "set_aside": row.get("set_aside") or "",
                    "url": canonical_notice_url(row),
                    "workspace_url": row.get("url") or "",
                    "poc_name": row.get("poc_name") or "",
                    "poc_email": row.get("poc_email") or "",
                    "excerpt": _excerpt(text, found),
                    "vendor_in_notice": vendor_here,
                    "first_seen": seen_at,
                    "last_seen": seen_at,
                    "source_extract": extract_name,
                })

    receipt = {
        "generated_at": seen_at,
        "source": "sam.gov daily Contract Opportunities extract",
        "extract": extract_name,
        "metered_quota_spent": 0,
        "notices_scanned": scanned,
        "lane_totals": lane_totals,
        "lanes": [{"lane": lane, "description": desc,
                   "notices_in_lane": lane_totals[lane],
                   "hits": sum(counts[lane].values()),
                   "subject_hits": subject_counts[lane],
                   "entities": [{"name": n, "kind": matchers[n]["kind"],
                                 "count": counts[lane][n],
                                 "matched_alone": matchers[n]["distinctive"]}
                                for n in sorted(matchers)]}
                  for lane, desc in LANES],
        "entities_searched": len(matchers),
        "ambiguous_entities": sorted(
            n for n, m in matchers.items() if not m["distinctive"]),
        "dictionary": ("system dictionary plus embedded collision list"
                       if had_dict else
                       "embedded collision list only (no system dictionary): "
                       "the ambiguous-name guard is narrower here"),
        "total_hits": len(hits),
    }
    return hits, receipt


# --------------------------------------------------------------------------- #
# durable ledger
# --------------------------------------------------------------------------- #
def _key(hit: dict) -> str:
    return f"{hit['notice_id']}|{hit['lane']}|{hit['entity']}"


def ledger_path(slug: str, root: Optional[Path] = None) -> Path:
    return (root or LEDGER_DIR) / f"{slug}.brand_hits.jsonl"


def load_ledger(slug: str, root: Optional[Path] = None) -> dict[str, dict]:
    """Every hit ever recorded for this client, keyed. Missing file is an
    empty ledger, never an error: the first scan has nothing to load."""
    path = ledger_path(slug, root)
    out: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue          # a torn line loses one hit, not the file
                out[_key(row)] = row
    except OSError:
        return {}
    return out


def merge_into_ledger(slug: str, hits: list[dict],
                      root: Optional[Path] = None) -> dict:
    """Fold today's scan into the durable ledger and rewrite it.

    A notice that has dropped out of the extract KEEPS its ledger row: that
    is the whole point. last_seen stops advancing, which is what tells a
    reader the notice is no longer active on sam.gov.
    """
    existing = load_ledger(slug, root)
    added = refreshed = 0
    for hit in hits:
        key = _key(hit)
        prior = existing.get(key)
        if prior:
            prior["last_seen"] = hit["last_seen"]
            prior["source_extract"] = hit["source_extract"]
            refreshed += 1
        else:
            existing[key] = hit
            added += 1
    path = ledger_path(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in sorted(existing.values(),
                          key=lambda r: (r.get("posted", ""), r.get("notice_id", ""))):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return {"path": str(path), "total": len(existing), "added": added,
            "refreshed": refreshed,
            "carried_from_earlier_scans": len(existing) - added - refreshed}


def active_and_historical(ledger: dict[str, dict],
                          today: Optional[str] = None) -> tuple[list, list]:
    """Split the ledger by whether the notice was in the most recent scan.

    A notice absent from today's extract is not deleted and not hidden; it is
    reported as historical, with the date we last saw it live.
    """
    if not ledger:
        return [], []
    latest = max((r.get("last_seen") or "") for r in ledger.values())
    cutoff = today or latest
    live = [r for r in ledger.values() if (r.get("last_seen") or "") >= cutoff]
    past = [r for r in ledger.values() if (r.get("last_seen") or "") < cutoff]
    by_posted = lambda rows: sorted(rows, key=lambda r: r.get("posted") or "",
                                    reverse=True)
    return by_posted(live), by_posted(past)
