"""Adapter over the working LILA intelligence systems.

The adapter supplies one graph-shaped interface while preserving ownership:
retrieval owns embeddings, contact_graph owns observations, brand_marks owns
marks, targets_store owns approved person observations, and evidence_route
owns classification.  Press consumers use this module as the single access
boundary while the durable stores remain in place.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

from agents.golden_press import evidence_route as er
from tools.intelligence_graph.cache import CacheResult, PersistentCache, stable_hash

ADAPTER_VERSION = "existing-systems-graph-adapter-v2"
CLASSIFICATION_CACHE_VERSION = "classification-decision-overlay-v1"
ENTITY_NORMALIZER_VERSION = "canonical-entity-registry-v1"
APOLLO_CACHE_VERSION = "apollo-enrichment-cache-v1"
SOURCE_EXTRACT_VERSION = "source-extract-cache-v1"
RESOLVED_URL_VERSION = "resolved-url-cache-v1"
MARK_CACHE_VERSION = "resolved-mark-cache-v1"

# This is the classifier's public cache contract.  Amounts, contacts, URLs and
# presentation fields deliberately stay outside it: those values must remain
# fresh without forcing the record's evidence/route decision to be researched
# again.  When evidence_route starts reading another record field, add it here
# and bump CLASSIFICATION_CACHE_VERSION.
CLASSIFICATION_INPUT_FIELDS = (
    "lane", "title", "description", "relevance_matched", "requirement",
    "additional_info", "naics", "naics_code", "psc", "psc_code",
    "response_deadline", "release_date", "estimated_release_date",
    "fiscal_year", "fy", "recipient", "set_aside",
)
CLASSIFICATION_DECISION_FIELDS = (
    "service_fit", "fit_basis", "window_state", "window_basis",
    "evidence_class", "evidence_basis", "route_relationship",
    "commercial_route", "eligible_route", "route_basis",
    "canonical_entity_id", "canonical_entity", "relationship_provenance",
)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


class ExistingSystemsGraphAdapter:
    """One interface for reusable graph facts and existing system delegates."""

    def __init__(
        self,
        *,
        root: str | os.PathLike,
        slug: str,
        client_name: str,
        classification_context: dict,
        cache_dir: str | os.PathLike | None = None,
        as_of: Any = None,
        schema_version: str = "evidence-pack-v2",
        rule_version: str | None = None,
        model_version: str = "deterministic",
        configuration: Optional[dict] = None,
        deterministic_seed: int = 0,
    ):
        self.root = Path(root)
        self.slug = str(slug)
        self.client_name = str(client_name)
        self.context = classification_context
        selected_cache = cache_dir or os.environ.get("LILA_INTELLIGENCE_CACHE_DIR")
        self.cache = PersistentCache(
            selected_cache or self.root / "data" / "state" / "intelligence_cache")
        self._stats: dict[str, Counter] = defaultdict(Counter)
        self._reasons: dict[str, Counter] = defaultdict(Counter)
        self._pruned: dict[str, list[str]] = defaultdict(list)
        self._context_hash = stable_hash(classification_context)
        self.semantic_dependencies = {
            "as_of": str(as_of or classification_context.get("as_of") or ""),
            "schema_version": str(schema_version),
            "rule_version": str(
                rule_version or
                getattr(er, "CLASSIFIER_VERSION", "evidence-route-v1")
            ),
            "model_version": str(model_version),
            "configuration_hash": stable_hash(configuration or {}),
            "deterministic_seed": int(deterministic_seed),
        }
        self.normalized_entities = self._cache_normalized_entities()

    def _track(self, namespace: str, result: CacheResult) -> Any:
        self._stats[namespace][result.state] += 1
        self._reasons[namespace][result.reason] += 1
        return result.value

    def _cache_normalized_entities(self) -> dict:
        value = {
            "client_aliases": sorted(self.context.get("client_aliases") or []),
            "canonical_entities": self.context.get("canonical_entities") or {},
            "validated_competitors": self.context.get("validated_competitors") or {},
            "named_partners": self.context.get("named_partners") or {},
        }
        result = self.cache.get_or_compute(
            "normalized_entity_identities",
            self.slug,
            inputs=value,
            dependencies=self.semantic_dependencies,
            producer=ENTITY_NORMALIZER_VERSION,
            compute=lambda: value,
            provenance={"owner": "agents.golden_press.evidence_route.build_context"},
        )
        return self._track("normalized_entity_identities", result)

    def classify_record(self, record: dict) -> dict:
        record_id = str(record.get("record_id") or record.get("notice_id") or "")
        if not record_id:
            record_id = stable_hash({
                "lane": record.get("lane"), "title": record.get("title"),
                "agency": record.get("agency"), "url": record.get("url"),
            })[:20]
        identity = f"{self.slug}:{record.get('lane') or 'record'}:{record_id}"

        classification_inputs = {
            field: record.get(field) for field in CLASSIFICATION_INPUT_FIELDS
        }

        def compute_decision() -> dict:
            classified = er.classify_record(record, self.context)
            return {
                field: classified.get(field)
                for field in CLASSIFICATION_DECISION_FIELDS
            }

        result = self.cache.get_or_compute(
            "classification_decisions",
            identity,
            inputs=classification_inputs,
            dependencies={
                "classification_context": self._context_hash,
                **self.semantic_dependencies,
            },
            producer=(
                f"{getattr(er, 'CLASSIFIER_VERSION', 'evidence-route-v1')}+"
                f"{CLASSIFICATION_CACHE_VERSION}"
            ),
            compute=compute_decision,
            provenance={
                "owner": "agents.golden_press.evidence_route.classify_record",
                "record_id": record_id,
                "client_slug": self.slug,
                "cached_value": "classification decision overlay",
                "input_fields": list(CLASSIFICATION_INPUT_FIELDS),
            },
        )
        decision = self._track("classification_decisions", result)
        out = dict(record)
        out.update(decision)
        return out

    def cache_source_extract(
        self, source_identity: str, *, source_revision: Any,
        extractor: Callable[[], Any], producer: str = SOURCE_EXTRACT_VERSION,
    ) -> Any:
        result = self.cache.get_or_compute(
            "source_page_extracts", f"{self.slug}:{source_identity}",
            inputs=source_revision, dependencies=self.semantic_dependencies,
            producer=producer, compute=extractor,
            provenance={"owner": "source adapter", "source": source_identity},
        )
        return self._track("source_page_extracts", result)

    def resolve_url(
        self, record_identity: str, candidate_url: str,
        resolver: Callable[[], Any], *, producer: str = RESOLVED_URL_VERSION,
    ) -> Any:
        result = self.cache.get_or_compute(
            "resolved_urls", f"{self.slug}:{record_identity}",
            inputs={"candidate_url": candidate_url},
            dependencies=self.semantic_dependencies, producer=producer,
            compute=resolver,
            provenance={"owner": "URL resolver", "record": record_identity},
        )
        return self._track("resolved_urls", result)

    def resolve_mark(
        self, mark_identity: str, mark_inputs: Any,
        resolver: Callable[[], Any], *, producer: str = MARK_CACHE_VERSION,
    ) -> Any:
        result = self.cache.get_or_compute(
            "resolved_marks", f"{self.slug}:{mark_identity}", inputs=mark_inputs,
            dependencies=self.semantic_dependencies, producer=producer,
            compute=resolver,
            provenance={
                "owner": "tools.brand_marks / report_assets",
                "client_slug": self.slug,
            },
        )
        return self._track("resolved_marks", result)

    @staticmethod
    def _apollo_identity(row: dict) -> str:
        person = (row.get("apollo_id") or row.get("person_id") or
                  row.get("linkedin") or row.get("email"))
        if not person:
            person = "|".join((
                _norm(row.get("name")), _norm(row.get("title")),
                _norm(row.get("organization")),
            ))
        family = str(row.get("requirement_family") or "unbound")
        return f"{family}:{person}"

    def merge_apollo_enrichments(
        self,
        rows: Optional[list[dict]],
        *,
        active_requirement_families: Optional[set[str]] = None,
    ) -> list[dict]:
        """Persist approved Apollo rows and reuse them on later presses.

        The requirement-family binding remains in every row.  Downstream target
        assembly still drops rows whose opportunity family no longer exists.
        """
        if rows:
            for raw in rows:
                row = dict(raw)
                identity = f"{self.slug}:{self._apollo_identity(row)}"
                result = self.cache.get_or_compute(
                    "apollo_contact_enrichment", identity, inputs=row,
                    dependencies=self.semantic_dependencies,
                    producer=APOLLO_CACHE_VERSION, compute=lambda row=row: row,
                    provenance={
                        "owner": "tools.contact_graph.outreach / Apollo",
                        "client_slug": self.slug,
                        "requirement_family": row.get("requirement_family"),
                    },
                )
                self._track("apollo_contact_enrichment", result)
        if active_requirement_families is not None:
            active = {str(value) for value in active_requirement_families}
            stale = self.cache.prune(
                "apollo_contact_enrichment",
                remove_when=lambda entry: (
                    str(entry.get("identity") or "").startswith(f"{self.slug}:")
                    and isinstance(entry.get("value"), dict)
                    and str(entry["value"].get("requirement_family") or
                            "unbound") not in active
                ),
            )
            self._pruned["apollo_contact_enrichment"].extend(stale)
        cached = self.cache.values(
            "apollo_contact_enrichment", identity_prefix=f"{self.slug}:")
        keyed: dict[str, dict] = {}
        for row in cached:
            if isinstance(row, dict):
                keyed[self._apollo_identity(row)] = dict(row)
        return [keyed[key] for key in sorted(keyed)]

    def _provider_map(self) -> dict:
        dense_db = self.root / "data" / "state" / "retrieval" / "dense_vectors.db"
        dense_receipt = self.root / "data" / "state" / "retrieval" / "dense_receipt.json"
        contact_store = self.root / "data" / "state" / "contact_graph"
        mark_store = self.root / "data" / "state" / "brand_marks"
        return {
            "embeddings": {
                "owner": "tools.retrieval.dense",
                "kind": "existing_sqlite_sidecar",
                "path": str(dense_db),
                "exists": dense_db.exists(),
                "receipt": str(dense_receipt),
            },
            "contact_graph": {
                "owner": "tools.contact_graph",
                "kind": "existing_append_only_observation_store",
                "path": str(contact_store),
                "exists": contact_store.exists(),
            },
            "target_observations": {
                "owner": "agents.golden_press.targets_store",
                "kind": "migrated_consumer",
                "access": "tools.intelligence_graph.adapter",
            },
            "agency_and_company_marks": {
                "owner": "tools.brand_marks and agents.reports.report_assets",
                "kind": "existing_local_mark_cache",
                "path": str(mark_store),
                "exists": mark_store.exists(),
            },
            "classification": {
                "owner": "agents.golden_press.evidence_route",
                "kind": "migrated_consumer",
                "producer": getattr(er, "CLASSIFIER_VERSION", "evidence-route-v1"),
            },
            "renderer": {
                "owner": "agents.golden_press.market_map_render",
                "kind": "graph_backed_consumer",
            },
        }

    def receipt(self) -> dict:
        namespaces = sorted(set(self._stats) | set(self._reasons))
        return {
            "schema_version": "incremental-cache-receipt-v2",
            "adapter": ADAPTER_VERSION,
            "cache_root": str(self.cache.root),
            "client_slug": self.slug,
            "context_hash": self._context_hash,
            "semantic_dependencies": dict(self.semantic_dependencies),
            "namespaces": {
                namespace: {
                    "hits": self._stats[namespace].get("hit", 0),
                    "misses": self._stats[namespace].get("miss", 0),
                    "reasons": dict(sorted(self._reasons[namespace].items())),
                }
                for namespace in namespaces
            },
            "actions": {
                "reused": sum(
                    stats.get("hit", 0) for stats in self._stats.values()),
                "recomputed": sum(
                    stats.get("miss", 0) for stats in self._stats.values()),
                "invalidations": sum(
                    count for reasons in self._reasons.values()
                    for reason, count in reasons.items()
                    if reason not in {"reused", "absent"}
                ),
                "pruned": sum(len(values) for values in self._pruned.values()),
                "pruned_identities": {
                    namespace: sorted(set(values))
                    for namespace, values in sorted(self._pruned.items())
                },
            },
            "providers": self._provider_map(),
        }


def load_target_observations(
    slug: str, root: str | os.PathLike | None = None,
) -> Optional[dict]:
    """Read approved target observations through the graph access boundary."""
    from agents.golden_press import targets_store
    return targets_store.load(slug, root)


def admissible_target_observations(
    payload: Optional[dict], *, spec_ids: Optional[list[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """Apply the durable store's admissibility law through one interface."""
    from agents.golden_press import targets_store
    return targets_store.admissible(payload, spec_ids=spec_ids)


def target_field_provenance(row: dict, field: str) -> dict:
    from agents.golden_press import targets_store
    return targets_store.field_provenance(row, field)


def write_target_sequence_csv(path: str | os.PathLike, targeting: dict) -> int:
    from agents.golden_press import targets_store
    return targets_store.write_sequence_csv(path, targeting)
