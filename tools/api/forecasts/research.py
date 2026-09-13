"""Source-bound forecast discovery, separate from direct-prime qualification.

Reviewed buyer vocabulary and exploratory competitor retrieval can retain a
research subject despite procurement eligibility. No record here is a live
notice, product-conformity finding, partner agreement or seller-ready lead.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from agents.schemas import CapabilityProfile, ForecastRecord
from tools.relevance.engine import score_record
from tools.relevance.taxonomy import CapabilityTaxonomy
from tools.text_match import matching_phrases
from .matching import _set_aside_ok
from .store import record_payload


def research_taxonomy(client_name: str) -> CapabilityTaxonomy | None:
    # Reviewed definitions are explicit, versioned source files. Do not mutate
    # the operator's client profile/taxonomy or infer a definition for a client.
    if client_name.strip().casefold() != "netscout":
        return None
    path = Path(__file__).resolve().parents[2] / "relevance" / "definitions" / "netscout_forecast_research.json"
    return CapabilityTaxonomy.model_validate(json.loads(path.read_text()))


def discover_forecasts(records: list[ForecastRecord], profile: CapabilityProfile,
                       *, taxonomy: CapabilityTaxonomy | None = None,
                       engagement_scope=None) -> list[dict]:
    if taxonomy is not None and taxonomy.client_name.strip().casefold() != profile.client_name.strip().casefold():
        raise ValueError("Research taxonomy belongs to a different client")
    definition = research_taxonomy(profile.client_name)
    definitions = [value for value in (taxonomy, definition) if value is not None]
    out = []
    for record in records:
        url = urlsplit(record.url or "")
        if url.scheme != "https" or not (url.hostname or "").endswith((".gov", ".mil")):
            continue
        scoring_record = record.model_dump(mode="python")
        if record.source == "dhs_apfs":
            from .dhs_apfs import scope_identity
            scoring_record["agency"] = " ".join(scope_identity(record)) or record.agency
        from tools.relevance.scope import in_scope
        if not in_scope(scoring_record, engagement_scope)[0]:
            continue
        # Honor operator record exclusions before consulting supplemental or
        # exploratory vocabulary. Span exclusions remain local in the scorer.
        if taxonomy is not None and any(
                rule.scope == "record" and matching_phrases(
                    f"{record.title} {record.description or ''}", [rule.term])
                for rule in taxonomy.exclude):
            continue
        hits, spans, exploratory, receipts = set(), [], set(), []
        for vocabulary in definitions:
            scoring_vocabulary = vocabulary
            if vocabulary is definition and taxonomy is not None:
                scoring_vocabulary = vocabulary.model_copy(update={"exclude": taxonomy.exclude})
            verdict = score_record(scoring_record, scoring_vocabulary, engagement_scope=engagement_scope)
            if verdict.relevant:
                hits.update(verdict.core_terms)
                spans.extend(s.model_dump(mode="json") for s in verdict.spans if s.tier == "core")
                receipts.append({"client": vocabulary.client_name, "version": vocabulary.version})
            # Exploratory mappings never contribute capability or score.
            terms = [m.term for m in vocabulary.retrieval_mappings
                     if m.disposition == "exploratory"]
            exploratory.update(matching_phrases(f"{record.title} {record.description or ''}", terms))
        if not hits and not exploratory:
            continue
        eligible = _set_aside_ok(record, profile)
        out.append({
            "schema_version": 1, "record": record,
            "subject_kind": "forecast", "status": "research_needed",
            "capability_terms": sorted(hits), "match_spans": spans,
            "exploratory_terms": sorted(exploratory), "vocabulary": receipts,
            "direct_prime_set_aside_eligible": eligible,
            "route_hypothesis": "OEM or partner research; route unverified" if not eligible else "research; acquisition route unverified",
            "unknowns": ["Current solicitation and status", "Exact product and configuration conformity", "Tool ownership and purchasing authority", "Eligible supplier and permitted OEM role", "Alternatives or complementary products allowed"],
            "communication_permission": "none",
        })
    return out


def serialize_forecast_match(match: dict) -> dict:
    """Keep the matcher evidence through the ordinary sweep JSON boundary."""
    return record_payload(match["record"]) | {
        key: match[key] for key in ("reasons", "score", "keyword_hits", "adjacent_hits", "naics_lane_match", "match_spans") if key in match
    }


def serialize_research(candidate: dict) -> dict:
    return {**candidate, "record": record_payload(candidate["record"])}
