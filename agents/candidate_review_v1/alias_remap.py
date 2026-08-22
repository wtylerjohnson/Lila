"""Evidence alias remap for Candidate Review v1.

``build_candidate_inventory`` canonicalizes evidence: rows sharing one exact
source identity collapse onto the lexicographically smallest evidence id and
every collapsed row becomes an ``EvidenceAlias``.  Objects minted BEFORE that
canonicalization (event seeds, vehicle signals, vehicle watch records,
authored claims, search concepts, the execution framework, and the accepted
vehicle evidence mapping) may still reference alias ids.  The composer
resolves references against the canonical universe only and silently drops
whatever does not resolve, which produces a structurally valid but hollow
report.  This module rewrites those references onto the canonical ids so
populated content survives composition.

Pure by design: no driver, filesystem, network, or model imports.  The
rewrite is dump, rewrite, re-validate, so every contract validator re-runs
on the remapped object.

Rewrite rules (docs/reviews/candidate-review-v1-alias-remap-handoff.md):

- any mapping key ENDING in ``evidence_ids`` whose value is a sequence of
  strings is mapped id by id, then deduplicated order-preserving, because two
  aliases may collapse onto one canonical id and the ``_unique`` validators
  must keep holding;
- keys EXACTLY ``evidence_id`` or ``source_evidence_id`` carrying a string
  are mapped; the exact match deliberately protects ``alias_evidence_id``,
  ``canonical_evidence_id``, and ``anchor_evidence_id``;
- ``date_id`` and ``money_id`` are NEVER rewritten: the deterministic author
  namespaces them as ``f"{evidence_id}:{kind}"``, and after a collapse two
  dates may correctly keep distinct ids while citing one canonical record.
  Rewriting the prefix would create collisions in the composer's global
  date and money namespaces.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar

from pydantic import BaseModel

from agents.candidate_review_v1.contracts import EvidenceRecord

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)

_LIST_KEY_SUFFIX = "evidence_ids"
_SCALAR_KEYS = frozenset({"evidence_id", "source_evidence_id"})


def _is_string_sequence(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and all(isinstance(item, str) for item in value)
    )


def _mapped_ids(values: Sequence[str], alias: Mapping[str, str]) -> list[str]:
    return list(dict.fromkeys(alias.get(item, item) for item in values))


def _rewrite(value: object, alias: Mapping[str, str]) -> object:
    if isinstance(value, dict):
        rewritten: dict[object, object] = {}
        for key, item in value.items():
            if (
                isinstance(key, str)
                and key.endswith(_LIST_KEY_SUFFIX)
                and _is_string_sequence(item)
            ):
                rewritten[key] = _mapped_ids(item, alias)
            elif (
                isinstance(key, str)
                and key in _SCALAR_KEYS
                and isinstance(item, str)
            ):
                rewritten[key] = alias.get(item, item)
            else:
                rewritten[key] = _rewrite(item, alias)
        return rewritten
    if isinstance(value, (list, tuple)):
        return [_rewrite(item, alias) for item in value]
    return value


def remap_evidence_ids(
    model: BaseModelT,
    alias: Mapping[str, str],
) -> BaseModelT:
    """Return ``model`` with every evidence reference mapped onto canonical ids.

    An empty ``alias`` returns the identical object without a rebuild.  The
    remapped object is re-validated by its own contract, so mapped-and-deduped
    tuples must still satisfy every ``_unique`` and ``min_length`` rule.
    """

    if not alias:
        return model
    data = model.model_dump(mode="python")
    return type(model).model_validate(_rewrite(data, alias))


def remap_all(
    models: Sequence[BaseModelT],
    alias: Mapping[str, str],
) -> tuple[BaseModelT, ...]:
    """Remap a sequence of contract objects; identity when ``alias`` is empty."""

    if not alias:
        return tuple(models)
    return tuple(remap_evidence_ids(model, alias) for model in models)


def remap_accepted_evidence(
    accepted: Mapping[str, Sequence[EvidenceRecord]],
    alias: Mapping[str, str],
    canonical_by_id: Mapping[str, EvidenceRecord],
) -> dict[str, tuple[EvidenceRecord, ...]]:
    """Swap accepted vehicle evidence RECORDS onto their canonical objects.

    This mapping holds ``EvidenceRecord`` objects, not ids, so aliased records
    are replaced by the canonical record object from the candidate build (the
    canonical row also carries the merged supports and retrieval times), then
    deduplicated per query.  A record that cannot resolve to the canonical
    universe raises: silently dropping it here would recreate the exact
    hollow-coverage failure this module exists to prevent.
    """

    remapped: dict[str, tuple[EvidenceRecord, ...]] = {}
    for query_id, records in accepted.items():
        kept: list[EvidenceRecord] = []
        seen: set[str] = set()
        for record in records:
            canonical_id = alias.get(record.evidence_id, record.evidence_id)
            canonical = canonical_by_id.get(canonical_id)
            if canonical is None:
                raise LookupError(
                    "accepted vehicle evidence "
                    f"{record.evidence_id} does not resolve to the canonical "
                    f"universe (mapped id {canonical_id})"
                )
            if canonical_id not in seen:
                seen.add(canonical_id)
                kept.append(canonical)
        remapped[query_id] = tuple(kept)
    return remapped
