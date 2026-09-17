"""Offline PROGRAM research and internal stage accounting, not qualification.

Read the ordinary sweep, never issue hidden collection calls. Counts describe
the supplied artifact, not the agency's universe. A missing stage is null.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from agents.reports.horizon_discovery._stored_program import stored_program_records
from tools.relevance.engine import TEXT_KEYS, score_record
from tools.relevance.taxonomy import CapabilityTaxonomy, KillRule, TaxonomyTerm

PROGRAM_KINDS = {"budget", "legislation", "regulation", "agency_announcement", "watchdog"}
FAMILIES = {
    "budget": ("dod_budget_exhibits", "agency_program_documents", "omb_public_budget_database", "omb_sf133", "budget_pressure", "funded_demand", "govinfo"),
    "acquisition_planning": ("forecast_signals", "sam.gov"),
    "strategy_program": ("darpa_opportunities", "dsip_topics", "reginfo_unified_agenda", "grants_gov", "sbir_gov"),
    "oversight": ("watchdogs", "gao_legal", "oversight_gov_reports"),
    "award_lifecycle": ("contract_awards", "incumbent_buyer_map"),
    "prime_partner": ("subawards", "dod_contracts"),
    "announcements": ("federal_register", "congress", "news", "gdelt", "web"),
}


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def narrative(row):
    return "\n".join(row[k].strip() for k in TEXT_KEYS
                     if isinstance(row.get(k), str) and row[k].strip())


def profile_taxonomy(profile):
    terms = profile.capability_terms
    return CapabilityTaxonomy(client_name=profile.client_name, version=1,
        updated="1970-01-01",
        core=[TaxonomyTerm(term=t) for t in terms.core],
        adjacent=[TaxonomyTerm(term=t) for t in terms.adjacent],
        exclude=[KillRule(term=t, scope="span") for t in terms.excluded])


def screen_programs(searches, profile, *, scope, as_of):
    """Keep source-bound capability matches as research, including off-code.

    No dollars, live solicitation or confirmed funding is required. A match is
    only an investigation candidate, never an actionable lead. Conflicting
    revisions are held with every version in the original sweep for resolution.
    """
    from agents.assess.ledger import _agency_is_in_scope
    from tools.api.source_mesh import program_payloads
    results = program_payloads(searches.get("results") or {})
    taxonomy = None
    candidates, decisions, versions = [], [], {}
    for source, payload in sorted(results.items()):
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            continue
        valid = list(stored_program_records(payload, source=source))
        valid_hashes = {hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest() for r in valid}
        for row in payload["records"]:
            if not isinstance(row, dict) or row.get("tier") != "program":
                continue
            sha = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()
            d = {"source": source, "source_record_id": row.get("record_id"),
                 "agency": row.get("agency"), "component": row.get("component"),
                 "source_url": row.get("canonical_url"), "row_sha256": sha,
                 "parsed": False, "screened": False, "relevant": None}
            decisions.append(d)
            if row.get('evidence_grain') == 'budget_account':
                d.update(parsed=bool(row.get('record_id') and row.get('source_payload_sha256')),
                         reason='account_context_without_buying_narrative', grain='budget_account',
                         screening_state='unsupported', investigation_state='unsupported')
                continue
            if sha not in valid_hashes or row.get("kind") not in PROGRAM_KINDS or not narrative(row):
                d["reason"] = "source_schema_or_primary_evidence_gap"
                continue
            d["parsed"] = True
            observed = stamp(row.get("retrieved_at"))
            if not observed or observed > as_of:
                d["reason"] = "not_available_at_cutoff"
                continue
            published = stamp(row.get("data_as_of"))
            # A source date without a time is still allowed to veto a future day.
            try:
                future_day = datetime.fromisoformat(str(row.get("data_as_of"))[:10]).date() > as_of.date()
            except ValueError:
                future_day = False
            if future_day or published and published > as_of:
                d["reason"] = "source_date_after_cutoff"
                continue
            if not _agency_is_in_scope(" ".join(str(row.get(k) or "") for k in ("agency", "component")), scope):
                d["reason"] = "outside_declared_scope"
                continue
            d["screened"] = True
            if taxonomy is None:
                frozen = results.get('upstream_vocabulary')
                taxonomy = CapabilityTaxonomy.model_validate(frozen) if frozen else profile_taxonomy(profile)
                if taxonomy.client_name.strip().casefold() != profile.client_name.strip().casefold():
                    raise ValueError('upstream vocabulary belongs to a different client')
                # Program investigation is not procurement-code qualification.
                taxonomy = taxonomy.model_copy(update={'code_universe': None})
            verdict = score_record(row, taxonomy)
            d.update(relevant=verdict.relevant, reason="capability_research" if verdict.relevant else "no_supported_capability_match")
            # Identity is agency + publisher + original record, not similar prose.
            identity = (source, row["record_id"])
            versions.setdefault(identity, {}).setdefault(sha, []).append(d)
            if verdict.relevant:
                candidates.append((identity, sha, row, {
                    "capability_terms": verdict.core_terms,
                    "match_spans": [s.model_dump(mode="json") for s in verdict.spans],
                    "qualification_effect": "none", "status": "research_needed",
                    "unknowns": ["Whether the need remains unresolved", "Specific product scope and deployment constraints",
                                 "Current decision and timing", "Funding status and permitted acquisition or partner route",
                                 "Named program or technical owner and permitted contact route"],
                }))
    output, seen = [], set()
    for identity, sha, row, context in candidates:
        if len(versions[identity]) > 1:
            for variant in versions[identity].values():
                for decision in variant:
                    decision.update(reason="conflicting_versions_need_review", relevant=None)
            continue
        if identity in seen:
            continue
        seen.add(identity)
        output.append((row, context))
    # Separate publications about one explicitly identified program support
    # one subject. No title-based grouping or generic agency overlap joins.
    grouped, retained = {}, []
    for row, context in output:
        if row.get('program_id'):
            key = (row['source'], row.get('agency'), row.get('component'), row['program_id'])
            grouped.setdefault(key, []).append((row, context))
        else:
            retained.append((row, context))
    for group in grouped.values():
        group.sort(key=lambda item: (item[0].get('kind') != 'budget',
                                    str(item[0].get('data_as_of') or ''), item[0]['record_id']))
        retained.append(group[0])
        for row, _ in group[1:]:
            for decision in decisions:
                if decision['source'] == row['source'] and decision['source_record_id'] == row['record_id']:
                    decision.update(reason='supporting_publication_same_program',
                                    joined_subject_record_id=group[0][0]['record_id'])
    return retained, decisions


def coverage_matrix(searches, *, agencies, decisions=(), research=None, parents=None):
    """Internal diagnostics. Unknown stages and universe sizes stay null.

    Receipt inventories are source-wide and intentionally NOT assigned to every
    component. Later-stage counts require actual source-bound native artifacts.
    """
    from tools.api.source_mesh import program_payloads
    results = program_payloads(searches.get("results") or {})
    from agents.reports.source_coverage import coverage_from_sweep
    attempts = {r['source']: r for r in coverage_from_sweep(searches)['lanes']}
    forecasts = results.get('forecast_signals') or {}
    inventory = forecasts.get('parsed_inventory') if isinstance(forecasts, dict) else None
    def belongs(agency, row):
        if agency in {row.get('agency'), row.get('component')}:
            return True
        # APFS publishes slash-delimited organization paths. Roll up that
        # explicit hierarchy, not guessed relationships from narrative text.
        if row.get('source') == 'dhs_apfs' and str(row.get('component') or '').startswith(agency + '/'):
            return True
        from tools.agencies import find
        expected = find(agency)
        labels = [row.get('agency'), row.get('component')]
        if row.get('source') == 'dhs_apfs':
            labels += str(row.get('component') or '').split('/')
        return bool(expected and any(actual and expected['name'] == actual['name']
                    for actual in (find(str(label or '')) for label in labels)))
    def subject_family(subject):
        if subject.source_kind == 'forecast': return 'acquisition_planning'
        if subject.source_kind == 'award': return 'award_lifecycle'
        return next((f for f, src in FAMILIES.items() if subject.source_system in src), None)
    rows = []
    for agency in agencies:
        for family, sources in FAMILIES.items():
            receipts = []
            for source in sources:
                payload = results.get(source)
                if payload is None:
                    receipts.append({"source": source, "state": "not_searched"})
                    continue
                if not isinstance(payload, dict):
                    receipts.append({"source": source, "state": "artifact_present_scope_unmeasured"})
                    continue
                receipts.append({"source": source,
                    "state": "failed_or_partial" if payload.get("error") or payload.get("errors") else "artifact_present_scope_unmeasured",
                    "error": payload.get("error") or payload.get("errors"),
                    "attempt_receipt": attempts.get(source),
                    "provenance": payload.get("_provenance"),
                    "evidence_interface": payload.get("evidence_interface"),
                    "coverage": payload.get("coverage_contract"),
                    "source_attempts": payload.get("source_attempts") or payload.get("sources"),
                    "retrieved_at": payload.get("retrieved_at"),
                    "data_as_of": payload.get("data_as_of")})
            scoped = [d for d in decisions if d["source"] in sources and belongs(agency, d)]
            subjects = None if research is None else [s for s in research.items
                if subject_family(s) == family and belongs(agency, s.model_dump(mode='json'))]
            native = None if parents is None else [p for p in parents if p.research_subject
                and subject_family(p.research_subject) == family and belongs(agency, p.research_subject.model_dump(mode='json'))]
            from agents.leadgen.action_sheet import bound_parent_research
            # Negative adjudications are still completed investigation work.
            reviewed = None if native is None else [p for p in native if bound_parent_research(p)]
            forecast_rows = ([r for r in inventory if isinstance(r, dict) and belongs(agency, r)]
                             if family == 'acquisition_planning' and isinstance(inventory, list) else None)
            if not forecast_rows:
                # A DHS-only pull says nothing about MDA. Without an explicit
                # per-agency empty-search receipt, absence is unmeasured.
                forecast_rows = None
            matched = [c for c in forecasts.get('research_candidates', []) if c.get('capability_terms')
                       and isinstance(c.get('record'), dict) and belongs(agency, c['record'])] if forecast_rows is not None else []
            from tools.api.forecasts.contacts import contact_record
            published_contacts = [contact_record(c['record']) for c in matched]
            observed = bool(scoped or subjects or native or forecast_rows)
            # No actionability score is inferred from keywords or verified email.
            rows.append({"agency_or_component": agency, "family": family,
                "available": None, "collected": len(scoped) if scoped else None,
                "parsed": sum(d["parsed"] for d in scoped) if scoped else None,
                "screened": sum(d["screened"] for d in scoped) if scoped else None,
                "relevant": sum(d["relevant"] is True for d in scoped) if scoped else None,
                "native_research": None if subjects is None or not observed else len(subjects),
                "investigated": None if reviewed is None or not observed else len(reviewed),
                "actionable_lead": None,
                "named_contact": None if reviewed is None or not observed else sum(any(t.name and (t.email or t.phone) for t in p.targets) for p in reviewed),
                "published_forecast_contact": (sum(bool(c and any(t.name and (t.email or t.phone) for t in c['contacts'])) for c in published_contacts)
                                               if forecast_rows is not None else None),
                "counted_sources": sorted({d['source'] for d in scoped}),
                "grain": "record observations; overlapping agency/component rows must not be summed",
                "coverage_claim": "partial artifact observations; not an agency census",
                "receipts": receipts, "decisions": scoped})
            if forecast_rows is not None:
                rows[-1].update(collected=len(forecast_rows), parsed=len(forecast_rows),
                                screened=len(forecast_rows), relevant=len(matched),
                                counted_sources=['forecast_signals'],
                                stage_boundary='Forecast inventory only. SAM and other acquisition signals remain separately unmeasured.')
            if scoped and all(d.get('grain') == 'budget_account' for d in scoped):
                rows[-1].update(screened=None, relevant=None, investigated=None,
                                stage_boundary='Account context parsed; narrative screening and investigation unsupported')
    return {"schema_version": "upstream.coverage.v1", "internal_only": True,
            "generated_from": searches.get("generated_at"), "rows": rows}


def sweep_coverage(searches, profile, *, research=None, parents=None):
    from agents.assess.ledger import scope_from_sweep
    scope = scope_from_sweep(searches)
    cutoff = stamp(searches.get('generated_at'))
    if cutoff is None:
        raise ValueError('coverage requires an aware frozen sweep cutoff')
    _, decisions = screen_programs(searches, profile, scope=scope, as_of=cutoff)
    agencies = sorted({str(d[k]) for d in decisions for k in ('agency', 'component') if d.get(k)}
                      | {a.name for a in scope.agencies}
                      | {str(r[k]) for r in (searches.get('results', {}).get('forecast_signals', {}).get('parsed_inventory') or [])
                         for k in ('agency', 'component') if isinstance(r, dict) and r.get(k)})
    from tools.agencies import find
    agencies = sorted({find(a)['name'] if find(a) else a for a in agencies})
    return coverage_matrix(searches, agencies=agencies or ['scope not enumerated'],
                           decisions=decisions, research=research, parents=parents)
