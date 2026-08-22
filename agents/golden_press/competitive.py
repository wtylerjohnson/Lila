"""Proactive Competitive Intelligence Build (operator order 2026-08-05).

This stage CREATES competitive completeness before composition. It does not
wait for a validator to reject an incomplete report: it discovers likely
competitors, expands their identities, searches every applicable lane,
feeds newly encountered vendors back into discovery, adjudicates every
candidate deterministically, maps account-level overlap, and only then lets
the composer write. The certification check downstream remains a backstop,
never the product.

DESIGN CONTRACT
- Each discovery round rebuilds the evidence pack through the ONE existing
  builder (retrieval.build_evidence_pack) with the grown roster, so the
  final pack is byte-indistinguishable from a press that had known every
  competitor from the start. No parallel report generator exists.
- Everything stored is produced by deterministic evidence rules. The
  composer may still attest WRONG DOMAIN: in prose (render/press own that),
  and witnessed-negative-context guards keep running inside the lanes; this
  module never bypasses either.
- Identity rules may ADMIT a name for searching, but an ambiguous alias
  (short, dictionary-word, or initialism) can never prove a match by
  itself: ambiguous aliases carry required anchors and ride the existing
  reject_entity_hit machinery, which demands the vendor or a product in the
  same text.
- Sidecars are the record: market definition, discovery, frame, search
  ledger, review, completeness. All are stable-ordered for replay.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from agents.golden_press.records import EvidencePack, GoldenRecord

COMPETITIVE_SCHEMA_VERSION = 1
SATURATION_CLEAN_ROUNDS = 2
MAX_ROUNDS = 4          # loud bound; the ledger says which round stopped it
MIN_ALIAS_CHARS = 3

RELATIONSHIP_REGISTRY = "data/reference/competitor_relationships.json"

# Channel participants are never product competitors. Federal IT resale is a
# short, stable list; a recipient matching it classifies as channel evidence
# and its OTHER brands become discovery candidates instead of rivals.
KNOWN_CHANNEL_PARTNERS = frozenset({
    "carahsoft", "carahsoft technology", "immixgroup", "shi international",
    "shi government solutions", "cdw government", "cdw-g", "cdw g",
    "insight public sector", "iron bow", "iron bow technologies",
    "world wide technology", "wwt", "swish data", "v3gate", "red river",
    "sterling computers", "govplace", "thundercat technology", "dlt solutions",
    "four points technology", "affigent", "govconnection", "connection",
    "mvation", "blue tech", "govsmart", "counter trade products",
})

_ACRONYM_RE = re.compile(r"^[A-Z][A-Z&\-]{0,2}$|^[A-Z]&[A-Z]$")
_GENERIC_SUFFIXES = frozenset({
    "technology", "technologies", "inc", "inc.", "corp", "corp.",
    "corporation", "company", "co", "co.", "llc", "ltd", "ltd.", "gmbh",
    "systems", "solutions", "group", "holdings", "international",
})
_BRAND_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{3,}(?:[ -][A-Z][A-Z0-9]{2,})?\b")
_AGENCYISH = frozenset({
    "DEPT", "DEPARTMENT", "AGENCY", "OFFICE", "BUREAU", "FEDERAL", "NATIONAL",
    "UNITED", "STATES", "DEFENSE", "ARMY", "NAVY", "FORCE", "COMMAND",
    "CENTER", "SERVICE", "ADMINISTRATION", "GOVERNMENT", "CONTRACT",
    "PURCHASE", "ORDER", "OPTION", "RENEWAL", "MAINTENANCE", "SUPPORT",
    "LICENSE", "LICENSES", "SOFTWARE", "HARDWARE", "EQUIPMENT", "SYSTEM",
    "SYSTEMS", "SERVICES", "BASE", "YEAR", "YEARS", "BRAND", "NAME",
    # procurement boilerplate and notice vocabulary (witnessed 2026-08-05:
    # "SOLE SOURCE", "AWARD SYNOPSIS", "SOURCES SOUGHT" reached the
    # confirmed roster as "competitors" on the first Thinklogical proof)
    "SOLE", "SOURCE", "SOURCES", "SOUGHT", "SYNOPSIS", "AWARD", "NOTICE",
    "INTENT", "SOLICITATION", "PRESOLICITATION", "COMBINED", "JUSTIFICATION",
    "RFI", "RFQ", "RFP", "IDIQ", "BPA", "NSN", "FMS", "EA", "QTY",
})
# Federal organization acronyms are buyers, never vendors: a token matching
# one can only ever be an agency reference (USCG, VISN witnessed 2026-08-05).
_FEDERAL_ORG_ACRONYMS = frozenset({
    "USCG", "USACE", "USAF", "USMC", "USSF", "USSOCOM", "SOCOM", "DISA",
    "DLA", "DHA", "NAVSEA", "NAVAIR", "NAVSUP", "NAVWAR", "SPAWAR", "AFMC",
    "AFLCMC", "VISN", "NIH", "CDC", "FDA", "CISA", "FEMA", "NOAA", "NASA",
    "GSA", "SEWP", "CIO", "OSD", "JPEO", "PEO",
})


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split())


def _key(value: Any) -> str:
    return _norm(value).casefold()


def _stable(rows: Iterable[dict], *keys: str) -> list[dict]:
    return sorted(rows, key=lambda r: tuple(str(r.get(k) or "") for k in keys))


# --------------------------------------------------------------------------- #
# 1 · market definition
# --------------------------------------------------------------------------- #
def build_market_definition(strategy: Any, profile: Optional[dict],
                            sweep: Optional[dict]) -> dict:
    """Structured market ontology from the packet, profile, and sweep.

    Codes qualify candidates for investigation; they never establish
    competitive relevance on their own (stated in the artifact itself).
    """
    profile = profile or {}
    keywords = getattr(strategy, "keywords", []) or []
    by_cat: dict[str, list[str]] = {}
    for row in keywords:
        cat = str(getattr(getattr(row, "category", None), "value", None)
                  or getattr(row, "category", "") or "")
        by_cat.setdefault(cat, []).append(_norm(getattr(row, "term", "")))
    terms = profile.get("capability_terms") or {}
    entities: dict[str, list[str]] = {}
    for entity in getattr(strategy, "research_entities", []) or []:
        kind = getattr(entity, "kind", None) or (
            entity.get("kind") if isinstance(entity, dict) else "")
        name = getattr(entity, "name", None) or (
            entity.get("name") if isinstance(entity, dict) else "")
        if kind and name:
            entities.setdefault(str(kind), []).append(_norm(name))
    census = {}
    if sweep:
        results = sweep.get("results") or {}
        sam = results.get("sam.gov") or []
        census = {"sam_rows": len(sam) if isinstance(sam, list) else 0}
    return {
        "schema_version": COMPETITIVE_SCHEMA_VERSION,
        "client_name": _norm(getattr(strategy, "client_name", "")),
        "core_product_category": sorted(set(
            (terms.get("core") or []) + by_cat.get("capability", []))),
        "adjacent_products": sorted(set(terms.get("adjacent") or [])),
        "substitute_technologies": sorted(set(by_cat.get("technology", []))),
        "integrators_and_resellers": sorted(set(
            entities.get("reseller", []))),
        "channel_partners": sorted(KNOWN_CHANNEL_PARTNERS
                                   & {_key(n) for kind in entities.values()
                                      for n in kind}) or [],
        "product_families": sorted(set(entities.get("product", []))),
        "known_competitor_seeds": sorted(set(entities.get("competitor", []))),
        "false_positive_terms": sorted(set(terms.get("excluded") or [])),
        "naics_qualifiers": sorted(set(
            str(c) for c in (profile.get("naics_boundary") or [])
            + list(getattr(strategy, "inferred_naics", []) or []))),
        "psc_qualifiers": sorted(set(
            str(c) for c in ((profile.get("code_universe") or {}).get("psc")
                             or []))),
        "code_rule": ("codes qualify candidates for investigation; they "
                      "never independently establish competitive relevance"),
        "sweep_census": census,
    }


# --------------------------------------------------------------------------- #
# 2 + 3 · discovery seeds and the identity graph
# --------------------------------------------------------------------------- #
def _load_relationship_registry(root: Path) -> list[dict]:
    """Curated corporate relations (acquisitions, former names, brands).

    Every entry carries a provenance note; a missing file weakens expansion
    and never fails the build.
    """
    try:
        payload = json.loads(
            (root / RELATIONSHIP_REGISTRY).read_text(encoding="utf-8"))
        return list(payload.get("relations") or [])
    except (OSError, ValueError):
        return []


def _initialism(name: str) -> Optional[str]:
    """'Guntermann & Drunck' -> 'G&D'. Only for names containing ' & '."""
    if " & " not in name:
        return None
    parts = [p for p in re.split(r"\s+&\s+|\s+", name) if p and p != "&"]
    if len(parts) < 2:
        return None
    return "&".join(p[0].upper() for p in parts[:2])


def _is_ambiguous_alias(alias: str, dictionary: frozenset) -> bool:
    """Short forms, initialisms, and dictionary words never prove a match."""
    flat = re.sub(r"[^A-Za-z0-9&]", "", alias)
    if len(flat) <= MIN_ALIAS_CHARS:
        return True
    if _ACRONYM_RE.match(alias.strip()):
        return True
    return alias.strip().casefold() in dictionary


def build_identity_graph(candidates: list[dict], *, root: Path,
                         client_name: str) -> dict:
    """Canonical roster with aliases, brands, anchors, and guard wiring.

    Deterministic rules, in order:
      CONTAINMENT   'Black Box Emerald' extends 'Black Box' -> product brand
                    of the shorter canonical; 'Vertiv Avocent' containing two
                    known names records Avocent as a brand of Vertiv.
      INITIALISM    a ' & ' name generates its initialism alias (G&D), which
                    is ALWAYS ambiguous and anchored.
      REGISTRY      curated acquisitions/former names expand aliases with a
                    provenance note (competitor_relationships.json).
    Ambiguous aliases carry required_anchors: the canonical name plus every
    distinctive product brand. reject_entity_hit enforces anchoring at match
    time; witnessed negative phrases ride the same guard.
    """
    from agents.golden_press.sam_lanes import load_dictionary
    from agents.golden_press.term_tiers import corpus_negative_phrases

    dictionary, _ = load_dictionary()
    registry = _load_relationship_registry(root)
    negatives = list(corpus_negative_phrases())
    client_key = _key(client_name)

    # seed nodes, deduped by casefold, stable order by (origin, name)
    seeds: dict[str, dict] = {}
    for cand in _stable(candidates, "origin", "name"):
        name = _norm(cand.get("name"))
        key = _key(name)
        if not key or key == client_key:
            continue
        node = seeds.setdefault(key, {
            "name": name, "origins": [], "evidence": [], "confidence": "low",
        })
        origin = str(cand.get("origin") or "")
        if origin and origin not in node["origins"]:
            node["origins"].append(origin)
        for ev in cand.get("evidence") or []:
            if ev not in node["evidence"]:
                node["evidence"].append(ev)
        rank = {"low": 0, "medium": 1, "high": 2}
        if rank.get(str(cand.get("confidence")), 0) >= rank[node["confidence"]]:
            node["confidence"] = str(cand.get("confidence") or "low")

    # containment components: longer names that extend a shorter seed
    keys = sorted(seeds)
    canonical_of: dict[str, str] = {k: k for k in keys}
    brands: dict[str, list[dict]] = {k: [] for k in keys}
    for longer in keys:
        for shorter in keys:
            if longer == shorter:
                continue
            if canonical_of[longer] != longer:
                continue
            if longer.startswith(shorter + " "):
                # the extension is a brand/product of the shorter canonical,
                # unless the remainder is a generic corporate suffix
                # ("Adder Technology" is an alias of Adder, not a brand)
                canonical_of[longer] = shorter
                remainder = _norm(seeds[longer]["name"][len(seeds[shorter]["name"]):])
                if remainder and _key(remainder) not in _GENERIC_SUFFIXES:
                    entry = {"brand": remainder,
                             "witness": f"containment:{seeds[longer]['name']}"}
                    if not any(_key(b["brand"]) == _key(remainder)
                               for b in brands[shorter]):
                        brands[shorter].append(entry)
                # a composite of TWO known names records the second as a
                # brand with its own alias standing (Vertiv Avocent)
                if _key(remainder) in seeds and canonical_of.get(_key(remainder)) == _key(remainder):
                    canonical_of[_key(remainder)] = shorter
                    if not any(_key(b["brand"]) == _key(remainder)
                               for b in brands[shorter]):
                        brands[shorter].append(
                            {"brand": _norm(seeds[_key(remainder)]["name"]),
                             "witness": f"composite:{seeds[longer]['name']}"})

    # initialism links: a generated initialism that equals another seed merges
    for key in keys:
        if canonical_of[key] != key:
            continue
        gen = _initialism(seeds[key]["name"])
        if gen and _key(gen) in seeds and canonical_of[_key(gen)] == _key(gen) \
                and _key(gen) != key:
            canonical_of[_key(gen)] = key

    # registry expansion
    registry_notes: list[dict] = []
    for rel in registry:
        target = _key(rel.get("canonical") or "")
        alias = _norm(rel.get("alias") or "")
        if not target or not alias:
            continue
        if target in seeds and canonical_of[target] == target:
            registry_notes.append({
                "canonical": seeds[target]["name"], "alias": alias,
                "relation": str(rel.get("relation") or "alias"),
                "provenance": str(rel.get("provenance") or "registry"),
            })

    competitors = []
    for key in keys:
        if canonical_of[key] != key:
            continue
        node = seeds[key]
        aliases = {node["name"]}
        for member, canon in canonical_of.items():
            if canon == key and member != key:
                aliases.add(seeds[member]["name"])
        gen = _initialism(node["name"])
        if gen:
            aliases.add(gen)
        for note in registry_notes:
            if _key(note["canonical"]) == key:
                aliases.add(note["alias"])
        brand_rows = _stable(brands[key], "brand")
        distinct, ambiguous = [], []
        for alias in sorted(aliases):
            (ambiguous if _is_ambiguous_alias(alias, dictionary)
             else distinct).append(alias)
        anchors = sorted({node["name"], *distinct,
                          *[b["brand"] for b in brand_rows]}
                         - set(ambiguous))
        origins = sorted({o for member, canon in canonical_of.items()
                          if canon == key for o in seeds[member]["origins"]})
        evidence = [ev for member, canon in sorted(canonical_of.items())
                    if canon == key for ev in seeds[member]["evidence"]]
        confidence = max((seeds[m]["confidence"]
                          for m, c in canonical_of.items() if c == key),
                         key=lambda v: {"low": 0, "medium": 1, "high": 2}[v])
        competitors.append({
            "canonical": node["name"],
            "aliases_distinctive": distinct,
            "aliases_ambiguous": ambiguous,
            "product_brands": brand_rows,
            "required_anchors_for_ambiguous": anchors,
            "witnessed_negative_phrases": [
                p for p in negatives
                if any(p in ev.get("text", "").casefold()
                       for ev in evidence if isinstance(ev, dict))] or [],
            "origins": origins,
            "evidence": evidence,
            "confidence": confidence,
        })
    return {
        "schema_version": COMPETITIVE_SCHEMA_VERSION,
        "client_name": _norm(client_name),
        "competitors": _stable(competitors, "canonical"),
        "registry_expansions": _stable(registry_notes, "canonical", "alias"),
        "ambiguity_rule": ("an ambiguous alias never proves a match; it "
                           "requires a required_anchor in the same text via "
                           "reject_entity_hit vendor/product anchoring"),
    }


def discover_seed_candidates(strategy: Any, profile: Optional[dict],
                             sweep: Optional[dict],
                             market: Optional[dict] = None) -> list[dict]:
    """Round-zero discovery from every path that needs no network call.

    operator_supplied  profile.named_competitors_and_incumbents
    client_named       packet research entities (kind=competitor)
    evidence_discovered top_incumbents in the sweep's award summaries and
                       brand/sole-source notice language in the sweep
    account_incumbent  incumbents at agencies where the client itself
                       appears in the sweep's buyer map
    """
    profile = profile or {}
    client_key = _key(getattr(strategy, "client_name", ""))
    out: list[dict] = []

    for name in profile.get("named_competitors_and_incumbents") or []:
        if _key(name) and _key(name) != client_key:
            out.append({"name": _norm(name), "origin": "operator_supplied",
                        "confidence": "high",
                        "evidence": [{"kind": "operator_roster",
                                      "text": "profile.named_competitors"}]})
    for entity in getattr(strategy, "research_entities", []) or []:
        kind = getattr(entity, "kind", None) or (
            entity.get("kind") if isinstance(entity, dict) else "")
        name = getattr(entity, "name", None) or (
            entity.get("name") if isinstance(entity, dict) else "")
        if str(kind) == "competitor" and _key(name) != client_key:
            out.append({"name": _norm(name), "origin": "client_named",
                        "confidence": "high",
                        "evidence": [{"kind": "intake_entity",
                                      "text": "packet research entity"}]})

    results = (sweep or {}).get("results") or {}
    client_present_agencies: set[str] = set()
    for bundle in results.get("usaspending.gov") or []:
        if not isinstance(bundle, dict):
            continue
        summary = bundle.get("summary") or {}
        awards = bundle.get("awards") or []
        client_here = any(client_key in _key(a.get("recipient"))
                          for a in awards if isinstance(a, dict))
        for a in awards:
            if isinstance(a, dict) and client_here:
                client_present_agencies.add(_key(a.get("awarding_agency")))
        for name in summary.get("top_incumbents") or []:
            key = _key(name)
            if not key or key == client_key or key in KNOWN_CHANNEL_PARTNERS:
                continue
            origin = ("account_incumbent" if client_here
                      else "evidence_discovered")
            out.append({
                "name": _norm(name), "origin": origin, "confidence": "medium",
                "evidence": [{"kind": "top_incumbent",
                              "text": f"NAICS {bundle.get('naics_code')}",
                              "naics": str(bundle.get("naics_code") or "")}]})

    # brand-name / sole-source language in the sweep's notices
    from agents.golden_press.sam_lanes import classify_lanes
    for row in results.get("sam.gov") or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("raw_payload") or {}
        text = _norm(f"{row.get('title')} {raw.get('description_snippet') or ''}")
        lanes = classify_lanes(str(raw.get("type") or ""), text)
        if not ({"sole_source_intent", "brand_name_or_equal"} & set(lanes)):
            continue
        # A brand token from a notice earns medium confidence only when the
        # notice itself speaks the market's language; otherwise it is a
        # low-confidence adjacency that never auto-searches.
        in_category = _market_term_in(text, market)
        for token in _brand_tokens(text, client_key):
            out.append({
                "name": token, "origin": "evidence_discovered",
                "confidence": "medium" if in_category else "low",
                "evidence": [{"kind": "brand_name_notice",
                              "text": text[:160],
                              "notice_id": str(row.get("source_id") or "")}]})
    return out


def _market_term_in(text: str, market: Optional[dict]) -> bool:
    """Does the witness text carry any market-definition term?"""
    terms = [t for t in (
        list((market or {}).get("core_product_category") or [])
        + list((market or {}).get("adjacent_products") or [])
        + list((market or {}).get("product_families") or [])) if _key(t)]
    if not terms:
        return True          # no ontology to gate on: stay permissive
    haystack = _key(text)
    return any(_key(t) in haystack for t in terms)


def _brand_tokens(text: str, client_key: str) -> list[str]:
    """Conservative vendor/brand token extraction from federal award text."""
    from agents.golden_press.sam_lanes import load_dictionary

    dictionary, _ = load_dictionary()
    seen: list[str] = []
    for match in _BRAND_TOKEN_RE.findall(text or ""):
        token = _norm(match)
        flat = re.sub(r"[^A-Za-z]", "", token)
        if len(flat) < 4:
            continue
        parts = token.upper().split()
        if any(part in _AGENCYISH for part in parts):
            continue
        if any(part in _FEDERAL_ORG_ACRONYMS for part in parts):
            continue                     # buyers, never vendors
        if re.fullmatch(r"[A-Z]+\d+[A-Z0-9]*", flat):
            continue                     # solicitation prefixes (SPRRA2)
        if token.casefold() in dictionary:
            continue
        if _key(token) == client_key or _key(token) in KNOWN_CHANNEL_PARTNERS:
            continue
        if token not in seen:
            seen.append(token)
    return seen[:8]


# --------------------------------------------------------------------------- #
# 5 · iterative discovery from retrieval results
# --------------------------------------------------------------------------- #
def extract_new_identities(pack: EvidencePack, frame: dict,
                           client_name: str,
                           market: Optional[dict] = None) -> list[dict]:
    """Vendor and product identities encountered in this round's evidence.

    Recipients of competitor/product-matched awards become candidates;
    channel registry names classify as channel and are never rivals; brand
    tokens in matched descriptions become unresolved candidates until
    corroborated.

    CATEGORY GATE (witnessed 2026-08-05, Thinklogical proof): the witness
    award's own text must carry a market-definition term for its recipient
    to reach medium confidence. Without it, "Black Box" style generic
    matches admit conglomerate recipients (General Electric, L3Harris)
    whose unrelated DLA hardware floods every downstream band. Codes and
    name matches alone qualify a candidate for investigation only (low),
    exactly as the market ontology states.
    """
    known = {_key(c["canonical"]) for c in frame.get("competitors", [])}
    for comp in frame.get("competitors", []):
        known.update(_key(a) for a in comp.get("aliases_distinctive", []))
        known.update(_key(a) for a in comp.get("aliases_ambiguous", []))
        known.update(_key(b["brand"]) for b in comp.get("product_brands", []))
    from agents.golden_press.validate import _record_affinity

    client_key = _key(client_name)
    out: list[dict] = []
    for record in pack.records:
        if record.lane != "L2_entity_award":
            continue
        affinity = _record_affinity(record, pack)
        recipient = _norm(record.recipient)
        rkey = _key(recipient)
        if rkey and rkey not in known and rkey != client_key:
            if rkey in KNOWN_CHANNEL_PARTNERS:
                out.append({"name": recipient, "origin": "evidence_discovered",
                            "confidence": "high", "classification": "channel",
                            "evidence": [{"kind": "channel_recipient",
                                          "text": record.title[:120],
                                          "record_id": record.record_id}]})
            elif affinity == "competitor":
                # the paper-holder behind a competitor-matched award is a
                # competitor identity worth its own lane pass, but only a
                # witness whose text carries the market's own language
                # earns a search; otherwise it is an unsearched adjacency
                witness = _norm(f"{record.title} {record.description or ''}")
                in_category = _market_term_in(witness, market)
                out.append({"name": recipient, "origin": "evidence_discovered",
                            "confidence": "medium" if in_category else "low",
                            "evidence": [{"kind": "award_recipient",
                                          "text": record.title[:120],
                                          "record_id": record.record_id,
                                          "url": record.url or "",
                                          "category_witness": in_category}]})
            else:
                # a recipient on the client's own core/channel paper is the
                # client's ecosystem: adjacency at most, never auto-searched
                out.append({"name": recipient,
                            "origin": "adjacency_candidate",
                            "confidence": "low",
                            "evidence": [{"kind": "ecosystem_recipient",
                                          "text": record.title[:120],
                                          "record_id": record.record_id}]})
        for token in _brand_tokens(record.description or record.title or "",
                                   client_key):
            if _key(token) not in known and _key(token) != rkey:
                out.append({"name": token, "origin": "adjacency_candidate",
                            "confidence": "low",
                            "evidence": [{"kind": "description_brand",
                                          "text": (record.description
                                                   or record.title or "")[:120],
                                          "record_id": record.record_id}]})
    return out


# --------------------------------------------------------------------------- #
# 6 · adjudication
# --------------------------------------------------------------------------- #
def adjudicate(pack: EvidencePack, frame: dict, candidates: list[dict],
               *, today: date, client_name: str) -> dict:
    """Deterministic classification of every candidate and evidence row.

    The model may have proposed classifications upstream (WRONG DOMAIN:
    attestations); the stored state here derives only from evidence rules:
    affinity, channel registry, current-versus-expired dates, and guards.
    """
    from agents.golden_press.validate import _record_affinity

    today_iso = today.isoformat()
    rows: list[dict] = []
    for record in pack.records:
        if record.lane != "L2_entity_award":
            continue
        affinity = _record_affinity(record, pack)
        if affinity not in ("competitor", "channel"):
            continue
        end = str(record.period_end or record.potential_end_date or "")[:10]
        current = bool(end and end >= today_iso)
        rkey = _key(record.recipient)
        classification = (
            "reseller_or_channel_evidence" if (affinity == "channel"
                                               or rkey in KNOWN_CHANNEL_PARTNERS)
            else "confirmed_direct_competitor_evidence")
        rows.append({
            "candidate": _norm(record.recipient) or record.record_id,
            "canonical_competitor": _canonical_for(record, frame),
            "evidence_excerpt": _norm(record.description
                                      or record.title)[:200],
            "source_link": record.url or "",
            "amount": record.obligated_dollars,
            "dates": {"start": record.period_start,
                      "end": record.period_end or record.potential_end_date},
            "buyer": _norm(record.sub_agency or record.agency),
            "products": record.entity_hits,
            "classification": classification,
            "current_vs_historical": "current" if current else "historical",
            "confidence": "high",
            "review_state": "auto_accepted",
            "record_id": record.record_id,
        })
    for cand in candidates:
        if str(cand.get("classification")) == "channel":
            classification, state = "reseller_or_channel_evidence", "auto_accepted"
        elif cand.get("confidence") == "low":
            classification, state = "unresolved_candidate", "review_required"
        else:
            classification, state = "ambiguous_review_required", "review_required"
        rows.append({
            "candidate": _norm(cand.get("name")),
            "canonical_competitor": "",
            "evidence_excerpt": "; ".join(
                _norm(e.get("text")) for e in cand.get("evidence") or []
                if isinstance(e, dict))[:200],
            "source_link": next((e.get("url") for e in cand.get("evidence") or []
                                 if isinstance(e, dict) and e.get("url")), ""),
            "amount": None, "dates": {}, "buyer": "", "products": [],
            "classification": classification,
            "current_vs_historical": "",
            "confidence": str(cand.get("confidence") or "low"),
            "review_state": state,
            "record_id": "",
        })
    return {
        "schema_version": COMPETITIVE_SCHEMA_VERSION,
        "client_name": _norm(client_name),
        "as_of": today_iso,
        "rows": _stable(rows, "classification", "candidate", "record_id"),
    }


def _canonical_for(record: GoldenRecord, frame: dict) -> str:
    hits = {_key(h) for h in record.entity_hits}
    for comp in frame.get("competitors", []):
        names = {_key(comp["canonical"])}
        names.update(_key(a) for a in comp.get("aliases_distinctive", []))
        names.update(_key(a) for a in comp.get("aliases_ambiguous", []))
        names.update(_key(b["brand"]) for b in comp.get("product_brands", []))
        if hits & names:
            return comp["canonical"]
    return ""


# --------------------------------------------------------------------------- #
# 7 · account-level competitive picture
# --------------------------------------------------------------------------- #
def account_picture(pack: EvidencePack, frame: dict, *, today: date,
                    client_name: str) -> dict:
    """Per-buyer competitive state with a deterministic recommended motion.

    Evidence classes stay separate: current head-to-head, current
    displacement, historical footprint, forward adjacency, and general
    presence. Expired paper may establish history; it never presents as a
    current account decision.
    """
    from agents.golden_press.validate import _record_affinity

    today_iso = today.isoformat()
    accounts: dict[str, dict] = {}

    def slot(record: GoldenRecord) -> Optional[dict]:
        buyer = _norm(record.sub_agency or record.agency)
        if not buyer:
            return None
        return accounts.setdefault(_key(buyer), {
            "buyer": buyer, "agency": _norm(record.agency),
            "client_current": [], "client_historical": [],
            "competitor_current": [], "competitor_historical": [],
            "channel_current": [], "forward": [], "clocks": [],
        })

    for record in pack.records:
        node = slot(record)
        if node is None:
            continue
        end = str(record.period_end or record.potential_end_date or "")[:10]
        current = bool(end and end >= today_iso)
        row = {
            "record_id": record.record_id, "title": record.title[:120],
            "recipient": _norm(record.recipient),
            "amount": record.obligated_dollars, "end": end or None,
            "vehicle": record.vehicle_class or record.vehicle,
            "url": record.url or "",
        }
        if record.lane == "L2_entity_award":
            affinity = _record_affinity(record, pack)
            if affinity == "core":
                (node["client_current"] if current
                 else node["client_historical"]).append(row)
            elif affinity == "channel":
                if current:
                    node["channel_current"].append(row)
            elif affinity == "competitor":
                (node["competitor_current"] if current
                 else node["competitor_historical"]).append(row)
            if current and end:
                node["clocks"].append({"end": end,
                                       "record_id": record.record_id})
        elif record.lane in ("L1_notice", "L4_forecast"):
            node["forward"].append({**row,
                                    "notice_type": record.notice_type,
                                    "deadline": record.response_deadline})

    rows = []
    for node in (accounts[k] for k in sorted(accounts)):
        motion, why = _account_motion(node)
        node["recommended_motion"] = motion
        node["why_this_matters"] = why
        for lane in ("client_current", "client_historical",
                     "competitor_current", "competitor_historical",
                     "channel_current", "forward", "clocks"):
            node[lane] = _stable(node[lane], "end", "record_id") \
                if node[lane] and "end" in (node[lane][0] or {}) \
                else _stable(node[lane], "record_id")
        rows.append(node)
    return {
        "schema_version": COMPETITIVE_SCHEMA_VERSION,
        "client_name": _norm(client_name), "as_of": today_iso,
        "accounts": rows,
    }


def _account_motion(node: dict) -> tuple[str, str]:
    """The deterministic motion table (order encodes priority)."""
    client = bool(node["client_current"])
    rival = bool(node["competitor_current"])
    channel = bool(node["channel_current"])
    history = bool(node["competitor_historical"])
    forward = bool(node["forward"])
    if client and rival:
        return ("defend the installed position",
                "client and a named competitor both hold current paper here; "
                "the sooner clock decides who is defending")
    if rival and not client:
        return ("displace the incumbent",
                "a named competitor holds the only current paper at this "
                "buyer; their renewal window is the entry")
    if channel and not client and not rival:
        return ("approach the paper holder",
                "a channel holder carries the current paper; the vendor "
                "conversation runs through them")
    if forward and history:
        return ("shape the forecast requirement",
                "forward demand exists where a competitor's paper has "
                "expired; the requirement is shapeable before solicitation")
    if forward:
        return ("pursue the forward notice",
                "forward demand exists with no current competitor paper "
                "cited at this buyer")
    if history:
        return ("monitor the adjacent competitor",
                "only expired competitor paper is cited here; history "
                "establishes presence, not a current decision")
    return ("maintain visibility",
            "client paper only; keep the renewal calendar warm")


# --------------------------------------------------------------------------- #
# 4 + 5 · the build loop: matrix, recursion, saturation
# --------------------------------------------------------------------------- #
def _ledger_cells(pack: EvidencePack, frame: dict, round_no: int) -> list[dict]:
    """Competitor-by-lane coverage cells derived from executed queries."""
    cells: list[dict] = []
    comp_keys = {}
    for comp in frame.get("competitors", []):
        for alias in ([comp["canonical"]] + comp.get("aliases_distinctive", [])
                      + comp.get("aliases_ambiguous", [])
                      + [b["brand"] for b in comp.get("product_brands", [])]):
            comp_keys[_key(alias)] = comp["canonical"]
    for query in pack.queries:
        body = query.body or {}
        keywords = ((body.get("filters") or {}).get("keywords")
                    or ([body.get("term")] if body.get("term") else []))
        searched = _norm(keywords[0]) if keywords else ""
        canonical = comp_keys.get(_key(searched))
        if not canonical:
            continue
        window = ((body.get("filters") or {}).get("time_period") or [{}])[0]
        error_note = query.note if "fail" in (query.note or "").casefold() \
            or "error" in (query.note or "").casefold() else ""
        cells.append({
            "round": round_no,
            "competitor": canonical,
            "searched_as": searched,
            "lane": str(getattr(query.lane, "value", query.lane)),
            "source": query.endpoint,
            "method": query.method,
            "requested_window": {k: window.get(k)
                                 for k in ("start_date", "end_date")
                                 if window.get(k)},
            "page": body.get("page"),
            "records_scanned": query.result_count,
            "candidates_retrieved": query.kept_after_screen,
            "errors": error_note,
            "status": "failed" if error_note else "covered",
        })
    for status in pack.lanes:
        cells.append({
            "round": round_no, "competitor": "*", "searched_as": "*",
            "lane": str(getattr(status.lane, "value", status.lane)),
            "source": "",
            "status": status.status,
            "note": status.detail or "",
        })
    return _stable(cells, "competitor", "lane", "searched_as")


def competitive_build(
    strategy: Any,
    *,
    sweep: Optional[dict],
    profile: Optional[dict],
    scope: Any,
    root: Path,
    today: Optional[date] = None,
    build_pack: Optional[Callable[..., EvidencePack]] = None,
    log: Callable[[str], None] = lambda m: None,
    **pack_kwargs: Any,
) -> dict:
    """The proactive build. Returns the final pack, the grown strategy, and
    every sidecar payload. Never raises for evidence scarcity: a lane error
    is a ledger row and an unresolved coverage note, and the build continues.
    """
    from agents.decisions.schemas import ResearchEntity
    from agents.golden_press.retrieval import build_evidence_pack

    build_pack = build_pack or build_evidence_pack
    today = today or date.today()
    client_name = _norm(getattr(strategy, "client_name", ""))

    market = build_market_definition(strategy, profile, sweep)
    seeds = discover_seed_candidates(strategy, profile, sweep, market=market)
    log(f"competitive: {len(seeds)} seed candidates from "
        f"{len({s['origin'] for s in seeds})} discovery paths")

    discovery_rounds: list[dict] = []
    ledger_cells: list[dict] = []
    all_candidates: list[dict] = list(seeds)
    frame = build_identity_graph(seeds, root=root, client_name=client_name)
    pack: Optional[EvidencePack] = None
    clean_rounds = 0
    searched_roster: set[str] = set()

    for round_no in range(1, MAX_ROUNDS + 1):
        confirmed = [c for c in frame["competitors"]
                     if c["confidence"] in ("high", "medium")]
        roster_now = {_key(c["canonical"]) for c in confirmed}
        new_names = sorted(roster_now - searched_roster)
        strategy = _strategy_with_roster(strategy, confirmed, ResearchEntity)
        log(f"competitive round {round_no}: searching "
            f"{len(confirmed)} confirmed competitors"
            + (f" (+{len(new_names)} new: {', '.join(new_names[:5])})"
               if new_names and round_no > 1 else ""))
        try:
            pack = build_pack(strategy, sweep=sweep, scope=scope, today=today,
                              **pack_kwargs)
        except Exception as exc:  # noqa: BLE001 - a lane error never ends research
            if pack is None:
                raise
            log(f"competitive round {round_no}: rebuild failed "
                f"({type(exc).__name__}: {exc}); keeping round "
                f"{round_no - 1} evidence and recording the gap")
            ledger_cells.append({
                "round": round_no, "competitor": "*", "searched_as": "*",
                "lane": "*", "source": "build_evidence_pack",
                "errors": f"{type(exc).__name__}: {exc}"[:200],
                "status": "failed",
            })
            break
        searched_roster |= roster_now
        ledger_cells.extend(_ledger_cells(pack, frame, round_no))

        found = extract_new_identities(pack, frame, client_name,
                                       market=market)
        material = [c for c in found
                    if c.get("confidence") in ("high", "medium")
                    and str(c.get("classification")) != "channel"]
        all_candidates.extend(found)
        discovery_rounds.append({
            "round": round_no,
            "confirmed_searched": sorted(c["canonical"] for c in confirmed),
            "new_candidates": len(found),
            "new_material": sorted(_norm(c["name"]) for c in material),
        })
        if not material:
            clean_rounds += 1
            if clean_rounds >= SATURATION_CLEAN_ROUNDS:
                break
        else:
            clean_rounds = 0
            frame = build_identity_graph(all_candidates, root=root,
                                         client_name=client_name)

    saturated = clean_rounds >= SATURATION_CLEAN_ROUNDS
    if not saturated:
        log(f"competitive: round cap {MAX_ROUNDS} reached before two clean "
            "discovery rounds; the ledger names the unresolved candidates")

    # Adjudication and the account picture ride the PACK's date, never the
    # wall clock: a replayed engine run over the same pack reproduces the
    # same current-versus-historical seating and the same "as of" stamp,
    # and the rendered date carries pack provenance.
    try:
        effective_today = date.fromisoformat(
            str(pack.generated_at or "")[:10])
    except ValueError:
        effective_today = today
    review = adjudicate(pack, frame, [c for c in all_candidates
                                      if _key(c.get("name")) not in
                                      {_key(x["canonical"])
                                       for x in frame["competitors"]}],
                        today=effective_today, client_name=client_name)
    accounts = account_picture(pack, frame, today=effective_today,
                               client_name=client_name)
    discovery = {
        "schema_version": COMPETITIVE_SCHEMA_VERSION,
        "client_name": client_name,
        "seed_paths": sorted({s["origin"] for s in seeds}),
        "rounds": discovery_rounds,
        "saturated": saturated,
        "saturation_rule": (f"{SATURATION_CLEAN_ROUNDS} consecutive rounds "
                            "with no new high/medium non-channel candidate, "
                            "all confirmed competitors searched"),
        "candidates": _stable(
            [{k: v for k, v in c.items() if k != "evidence"}
             | {"evidence_kinds": sorted({e.get('kind', '')
                                          for e in c.get('evidence') or []
                                          if isinstance(e, dict)})}
             for c in all_candidates], "origin", "name"),
    }
    ledger = {
        "schema_version": COMPETITIVE_SCHEMA_VERSION,
        "client_name": client_name,
        "rounds_run": len(discovery_rounds),
        "cells": ledger_cells,
    }
    completeness = score_completeness(
        market=market, discovery=discovery, frame=frame, ledger=ledger,
        review=review, accounts=accounts, pack=pack)
    log(f"competitive completeness: {completeness['score']}/10 "
        f"({completeness['status']}); "
        f"{len(frame['competitors'])} competitors in frame, "
        f"{len([r for r in review['rows'] if 'competitor' in r['classification']])} "
        "adjudicated competitor evidence rows")
    return {
        "pack": pack, "strategy": strategy, "frame": frame,
        "market_definition": market, "discovery": discovery,
        "search_ledger": ledger, "review": review, "accounts": accounts,
        "completeness": completeness,
    }


def _strategy_with_roster(strategy: Any, confirmed: list[dict],
                          entity_cls: Any) -> Any:
    """Grown strategy: every confirmed competitor and its DISTINCTIVE
    aliases/brands become research entities. Ambiguous aliases stay out of
    the search roster by design; they are match-time anchored, not queried.
    """
    existing = list(getattr(strategy, "research_entities", []) or [])
    have = set()
    for entity in existing:
        kind = getattr(entity, "kind", None) or (
            entity.get("kind") if isinstance(entity, dict) else "")
        name = getattr(entity, "name", None) or (
            entity.get("name") if isinstance(entity, dict) else "")
        have.add((str(kind), _key(name)))
    added = []
    for comp in confirmed:
        for name in ([comp["canonical"]] + comp.get("aliases_distinctive", [])):
            if ("competitor", _key(name)) not in have:
                added.append(entity_cls.model_validate(
                    {"kind": "competitor", "name": name}))
                have.add(("competitor", _key(name)))
        for brand in comp.get("product_brands", []):
            if ("competitor", _key(brand["brand"])) not in have \
                    and not _is_ambiguous_alias(
                        brand["brand"],
                        frozenset()):
                added.append(entity_cls.model_validate(
                    {"kind": "competitor", "name": brand["brand"]}))
                have.add(("competitor", _key(brand["brand"])))
    if not added:
        return strategy
    return strategy.model_copy(
        update={"research_entities": existing + added})


# --------------------------------------------------------------------------- #
# 9 · completeness
# --------------------------------------------------------------------------- #
def score_completeness(*, market: dict, discovery: dict, frame: dict,
                       ledger: dict, review: dict, accounts: dict,
                       pack: EvidencePack) -> dict:
    """Ten binary points for the PROACTIVE build. 10/10 means the research
    process completed; a fully researched zero is complete_zero, never a
    failure."""
    confirmed = [c for c in frame.get("competitors", [])
                 if c["confidence"] in ("high", "medium")]
    covered = {(c.get("competitor"), c.get("lane"))
               for c in ledger.get("cells", [])
               if c.get("status") == "covered"}
    award_lanes = {lane for comp, lane in covered if comp != "*"}
    review_rows = review.get("rows", [])
    competitor_rows = [r for r in review_rows
                      if r["classification"].startswith("confirmed")]
    current_rows = [r for r in competitor_rows
                    if r.get("current_vs_historical") == "current"]
    acct_rows = accounts.get("accounts", [])
    points = {
        "market_defined": bool(market.get("core_product_category")),
        "universe_multi_path": len(discovery.get("seed_paths", [])) >= 2,
        "identity_graph_with_guards": bool(confirmed) and all(
            c.get("required_anchors_for_ambiguous")
            for c in confirmed if c.get("aliases_ambiguous")),
        "award_lanes_searched": bool(confirmed)
            and all(any(comp == c["canonical"] and "L2" in str(lane)
                        for comp, lane in covered) for c in confirmed),
        "notice_forecast_lanes_searched": any(
            "L1" in str(lane) or "L4" in str(lane)
            for _c, lane in covered) or any(
            "L1" in str(getattr(s, "lane", "")) or
            "L4" in str(getattr(s, "lane", "")) for s in pack.lanes),
        "iterated_to_saturation": bool(discovery.get("saturated")),
        "all_material_adjudicated": all(
            r.get("review_state") in ("auto_accepted", "review_required")
            for r in review_rows),
        "evidence_classes_separated": all(
            set(a) >= {"client_current", "competitor_current",
                       "competitor_historical", "channel_current", "forward"}
            for a in acct_rows) if acct_rows else True,
        "findings_actionable": all(
            a.get("recommended_motion") and a.get("why_this_matters")
            for a in acct_rows) if acct_rows else True,
        "binding_and_replay": bool(pack and pack.records is not None
                                   and ledger.get("cells") is not None),
    }
    score = sum(1 for v in points.values() if v)
    status = ("complete_found" if score == 10 and current_rows
              else "complete_zero" if score == 10
              else "incomplete")
    return {
        "schema_version": COMPETITIVE_SCHEMA_VERSION,
        "client_name": _norm(getattr(pack, "client_name", "")),
        "score": score,
        "points": points,
        "status": status,
        "confirmed_competitors": sorted(c["canonical"] for c in confirmed),
        "current_overlap_rows": len(current_rows),
        "historical_rows": len([r for r in competitor_rows
                                if r.get("current_vs_historical")
                                == "historical"]),
        "unresolved": sorted({r["candidate"] for r in review_rows
                              if r["classification"]
                              == "unresolved_candidate"}),
    }
