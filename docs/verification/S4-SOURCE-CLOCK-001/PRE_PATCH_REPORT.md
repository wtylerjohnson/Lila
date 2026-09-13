# S4-SOURCE-CLOCK-001: strict assessment acquisition clocks

Status: read-only investigation complete; root-owned P1 implementation reserved.
Plan: LILA-WEEKEND-2026-09-11-v1. This is a bounded follow-up to the canonical plan, not a replacement roadmap.
Investigated code: 625f0e0e6f644dec9e9dab28575a4dc66fad832e.
Owner: root LILA integration task, with existing source_receipt helper. Coordinator retains Step4 and operator ownership.

## Verified defect and live evidence

The preserved NetScout assessment has 25 notice EvidenceRefs across 23 families. Every retrieved_at equals assessment approval/as_of, 2026-09-13T00:04:30Z. All 25 saved SAM rows lack acquisition/retrieval/fetch timestamp keys, including nested payloads. The search artifact's generated_at is 2026-09-12T23:55:16+00:00; that is not automatically source acquisition. The run used operating main f0f7aecb034d617152907f445ca0d697e135081c.

Root independently checked all four live artifact hashes recorded in ../STEP4-049/NETSCOUT_SOURCE_CLOCK_FOLLOWUP.json. Original assessment, QA, searches and run receipt were not changed. Coordinator synthetic reproductions on 625f0e0 establish the fallback; equal live timestamps alone would not establish its cause.

agents/assess/ledger.py:821 substitutes assessment as_of when trusted-depth acquisition is absent or malformed. Its general parser at lines 87-102 also supplies UTC for naive/date-only inputs. Family verified_at at 866 is an assessment clock and may legitimately change. Approval/rematerialization at 2084-2106 must not manufacture new source acquisition. Evidence IDs at 827 bind original posting/depth content and should remain stable for unchanged inputs.

## Actual authority and scope

Trusted native depth is copied beside model analysis by run_dossiers.py:102-129 from sam_notice_detail.py. Cache reuse retains the collector's recorded acquisition. Original fetch and refresh timestamps have different recording points; preserve those semantics without claiming exact response-completion time.

RawOpportunity has no acquisition field. Native extract/API mappings do not expose an established row acquisition receipt. A parseable row/model/imported retrieved_at is not sufficient authority. Local extract reads, notice-store last_seen, generation, approval, posting and attachment clocks cannot be substituted for requirement-description acquisition. A newer posting cannot freshen older requirement text.

The bounded correction preserves valid, aware, correctly bound depth acquisition and otherwise represents unknown acquisition. A new authenticated import/row acquisition pipeline is outside this patch. Do not add source collection, change qualification thresholds, expand customer scope, redesign reports or touch frozen benchmark cases.

## Required contract and consumer changes

1. EvidenceRef in agents/assess/contracts.py currently requires retrieved_at. Add an explicit nullable acquisition representation with typed status and bound provenance sufficient to distinguish missing, invalid, date-only, unknown-timezone, conflict and untrusted inputs. Retain original value/field and evidence component. Reuse verified Step4 parsing; selection of trusted authority is a separate decision.
2. Review, attachment, BID_NOW and run validators compare retrieved_at directly (contracts.py:102,242-268,307-315,514-526). Preserve every evidence record and the full census. Unknown chronology must produce a named gap or refusal where chronology is required; never skip a comparison in a way that grants approval. Existing strict depth, review, attachment and horizon gates remain effective.
3. agents/leadgen/from_assess.py:824-836 carries EvidenceRefs into ActionableExternalPathway. Update actual generated schemas and export/revalidation consumers together. Inspect precise repository paths when implementing; no ad hoc report-only correction.
4. agents/reports/facts.py:265-269 fills missing source time from sweep generated_at, including strict evidence copied at 441. Explicit strict unknown must survive stamping. Preserve verification.py:113-152 freshness refusals and valid known-source positives.
5. product_leadgen.py:54-58,94-101 drops unknown times before aggregation; agents/golden_press/external_product_projection.py:386,417-419 can substitute pack generation time. Carry source-clock completeness so a mixed known/unknown family cannot advertise complete current-source coverage. Preserve all members.
6. Lead evidence HTML in press_html.py:226-229 should distinguish recorded acquisition or unknown collection time from the existing assessment-as-of label. Keep layout scope bounded to this truthful evidence field.
7. Explicitly handle shared USAspending partner evidence at ledger.py:1749, which also substitutes as_of, or retain a named residual with an exact boundary. Do not label the shared contract fully fixed while silently retaining the same false-clock path.

## Immutable compatibility

ledger.py:1961-1963 serializes complete models for run identity; 2417-2443 recomputes identity on immutable reads. Added defaults change serialization. Implement and verify deliberate legacy read compatibility and versioned new writes. Preserve old bytes, IDs and rollback pointers. Do not rewrite a prior run under its existing ID. Legacy retrieval values remain origin-unresolved unless rebound to original evidence; historical approval times cannot silently become authenticated acquisition.

## Acceptance

- Reassess/approve identical copied inputs twice: source IDs, known acquisition and unknown states remain stable while assessment verification can advance. Preserve the NetScout 25-posting/23-family census where the same inputs apply.
- Aware trusted-depth time and offset survive cache reuse, rematerialization, export, LeadRow JSON, source projections and HTML.
- Missing, malformed, date-only, naive, conflicting and untrusted timestamps remain explicit unknowns through all consumers; no generation/approval fallback.
- Fresh row plus old or missing depth cannot freshen requirement evidence or create BID_NOW. Wrong-notice depth, pre-evidence review, attachment drift and forged model_copy still refuse; valid controls continue to pass.
- Mixed known/unknown families retain every member and disclose incomplete acquisition coverage. Existing report freshness checks continue to reject unknown/stale evidence where required.
- Legacy immutable reads, new schema round trips, original artifact hashes and rollback pointers are tested separately from new native output.
- Required targeted and full combined regression, report/export checks, and exact-candidate independent review precede production acceptance. Synthetic proof is not a recovered lead or authentic-source success.

## Sequencing

Continue the existing coordinator's exact-candidate Step4 regression, closure and review. Reserve this strict-Assess extension for one isolated root-owned implementation on the verified Step4 base. Do not modify the tree during its current review, spawn a competing NetScout implementation, or launch a separate review now. Source002 and SourceClock001 remain distinct issues. SourceClock001 is a production-integration dependency; bounded Step4 development acceptance does not close it.

No code, schemas, gates, live artifacts or production refs were modified during this investigation.
