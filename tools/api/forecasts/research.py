"""Source-bound forecast discovery, separate from direct-prime qualification.

Reviewed buyer vocabulary and exploratory competitor retrieval can retain a
research subject despite procurement eligibility. No record here is a live
notice, product-conformity finding, partner agreement or seller-ready lead.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from agents.schemas import CapabilityProfile, ForecastRecord
from tools.relevance.engine import score_record, score_text
from tools.relevance.taxonomy import CapabilityTaxonomy, TaxonomyTerm, KillRule
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
            scoring_record["agency"] = scope_identity(record) or record.agency
        from tools.relevance.scope import in_scope
        inside, scope_basis = in_scope(scoring_record, engagement_scope)
        if not inside or engagement_scope is not None and "unresolved" in scope_basis:
            continue
        # Honor operator record exclusions before consulting supplemental or
        # exploratory vocabulary. Span exclusions remain local in the scorer.
        if taxonomy is not None and any(
                rule.scope == "record" and matching_phrases(
                    f"{record.title} {record.description or ''}", [rule.term])
                for rule in taxonomy.exclude):
            continue
        native_verdict = score_record(scoring_record, taxonomy, engagement_scope=engagement_scope) if taxonomy is not None else None
        excluded_terms = {m.term.casefold() for m in taxonomy.retrieval_mappings if m.disposition == "excluded"} if taxonomy is not None else set()
        hits, spans, exploratory, receipts = set(), [], set(), []
        for vocabulary in definitions:
            exclusions = list(taxonomy.exclude) if taxonomy is not None else list(vocabulary.exclude)
            exclusions += [KillRule(term=term, scope="span", reason="Operator excluded retrieval mapping") for term in excluded_terms]
            scoring_vocabulary = vocabulary.model_copy(update={
                "exclude": exclusions,
                "core": [term for term in vocabulary.core if term.term.casefold() not in excluded_terms],
            })
            verdict = score_record(scoring_record, scoring_vocabulary, engagement_scope=engagement_scope)
            if verdict.relevant:
                hits.update(verdict.core_terms)
                spans.extend(s.model_dump(mode="json") for s in verdict.spans if s.tier == "core")
                receipts.append({"client": vocabulary.client_name, "version": vocabulary.version,
                                 "definition_sha256": hashlib.sha256(vocabulary.model_dump_json().encode()).hexdigest(),
                                 "disposition": "capability_research", "terms": verdict.core_terms})
            # Exploratory mappings never contribute capability or score.
            terms = [m.term for m in vocabulary.retrieval_mappings
                     if m.disposition == "exploratory"]
            exploratory_taxonomy = CapabilityTaxonomy(
                client_name=profile.client_name, version=1, updated=vocabulary.updated,
                core=[TaxonomyTerm(term=term, mode="exact_phrase") for term in terms if term.casefold() not in excluded_terms],
                exclude=exclusions)
            for field in ("title", "description"):
                surviving, _ = score_text(getattr(record, field) or "", exploratory_taxonomy)
                exploratory.update(span.term for span in surviving)
                for span in surviving:
                    mapping = next(m for m in vocabulary.retrieval_mappings if m.term == span.term)
                    receipt = {"client": vocabulary.client_name, "version": vocabulary.version,
                               "definition_sha256": hashlib.sha256(vocabulary.model_dump_json().encode()).hexdigest(),
                               "disposition": mapping.disposition, "term": mapping.term, "reason": mapping.reason}
                    if receipt not in receipts:
                        receipts.append(receipt)
        if not hits and not exploratory:
            continue
        eligible = _set_aside_ok(record, profile)
        out.append({
            "schema_version": 1, "record": record,
            "subject_kind": "forecast", "status": "research_needed",
            "capability_terms": sorted(hits), "match_spans": spans,
            "exploratory_terms": sorted(exploratory), "vocabulary": receipts,
            "direct_prime_set_aside_eligible": eligible,
            "direct_code_boundary": "unknown" if native_verdict is None else "excluded" if native_verdict.excluded_by_code else "not_excluded",
            "native_boundary_evidence": native_verdict.model_dump(mode="json") if native_verdict is not None else None,
            "discovery_policy": "research_only_including_explicit_off_code_functions",
            "qualification_effect": "none; direct qualification is unchanged",
            "route_hypothesis": "OEM or partner research; eligible supplier and route remain to be established" if not eligible else "research; acquisition route remains to be established",
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
