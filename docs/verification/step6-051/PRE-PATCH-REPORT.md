# Step6 native surface baseline

Work: **REPAIR-STEP6-BASELINE-051**. Read-only baseline on accepted **497b7f3b1bdef6a58081918fd0e4d62b66fa7d76**, branch `codex/step5-evidence-states`. Product checkout remained clean. Coordinator owns implementation; this report does not authorize Step7 or a release.

**Five demonstrated consumer defects need repair.** The most consequential is a second qualification path: the Golden graph classifies boilerplate and explicitly negated requirements as qualified opportunities even though the accepted triage gate rejects the same inputs. Other defects concern family identity, incomplete compact diagnostics, unsupported funding/displacement copy, and `live_bid` labels on research/HOLD children. Existing C1, source-clock and seller-ready qualification controls passed the bounded regression selection.

## Evidence and proof boundaries

`run02/RESULTS.json` contains eight completed synthetic probe groups. `run03/RESULTS.json` adds the actual graph-classifier-to-external-projection probe. Each preserves input, native output and desired-contract checks; false checks are demonstrated defects, not failing harness execution. `run02/CONTROLS.log` records **24 passed** focused regression tests. No full suite, model deliberation, browser, collection, source refresh, cloud review, complete bundle build, certified release or authentic new lead was run.

The initial `run01` completed all eight synthetic groups, but pytest initialization was blocked when our write guard rejected `/dev/null`; its control exit was 3. That original script/log/result is retained. Allowing the null device fixed the harness; `run02` had no denied events and control exit 0. Both runs produced the same synthetic results. Run03 added a new consumer probe without repeating the controls. The final portable script adds only an explicit `--expected-sha` guard parameter for future exact-candidate review. Run02/run03 script reconstructions are labeled as such; their changes from the preserved run01 script are documented in `COMMANDS.json`.

`SOURCE_RECEIPTS.json` binds the inspected instructions, source files and opened runtime dependencies. All initial instruction hashes and guarded runtime hashes still matched at close; `BASE_AFTER.json` records clean accepted HEAD. Product functions were imported from the existing checkout using the existing Python 3.14.5 environment. The probe audit hook blocked networking, subprocesses after initial git inspection, and writes outside its owned run directory (except `/dev/null`). No fixture was loaded into product data.

## Actual native surfaces and order

| Stage | Native source and behavior | Measurement implication |
| --- | --- | --- |
| Retrieval and screen | `run_searches.py:2300` onward retains `results["sam.gov"]`, calls `triage.deterministic_prefilter`, then model triage and decision coverage. Full screening records are retained. | Raw retrieval census, screened candidates and recommendations are different populations. This baseline never called the runner/model. |
| Family suppression | `agents/decisions/triage.py:168` selects latest structured solicitation/agency/office/source threads, then `deterministic_prefilter:490` suppresses older candidates using the full census. Selected indices retain native input order. | The complete family census must survive even when the latest member has no term. |
| Research Picture | `research_picture.distill:252` takes the first 15 pursue and first 15 monitor records in source order; revalidation at `:199`, projection at `:229`; `research_evidence.current_notice:311` requires current bound notice, trusted requested-work and response-instruction evidence. | Top opportunity and research-signal arrays retain distinct meanings; a cited context statement does not inherit current-action or route authority. These functions were traced; model synthesis was not executed. |
| Command Center Review | `ui/server.py:915` returns all raw notices sorted by triage priority, then deadline; stable ties preserve input order. `:1360` builds strict rows from native Assess ledger order. `ui/index.html:4260` shows strict current truth and a labeled legacy diagnostic drawer. | Probe input RAW-FIRST/RAW-SECOND/DISCARD became RAW-SECOND/RAW-FIRST/DISCARD. This is documented native UI order, not a post-capture rerank. Historical case005 stays unchanged. |
| Signal Board compatibility surface | `composer.select_best_fit:367` sorts family rank, descending lexical score/core count, deadline, source ID, cap 3. `select_award_best_fit:617` sorts score/core count, PoP end, amount, ID/canonical bytes, stable-partitions material awards, cap 3. `compose_content:4793` creates best-fit cards. | This surface has award-corridor fallbacks and is not the sole certified external product. Its labels still need evidence-bound language. |
| External product | `product_bundle.build_complete_bundle:243` preserves assessment population, invokes `_build_graph:179` -> `build_corrected_pack:556` -> graph classifier -> family resolver -> `qualify_opportunities:400`, then Market Map and `external_product_projection.build_external_product_document:312`. | This is the actual eight-slot client product chain, not a benchmark dashboard. This baseline exercised its pure classifier/qualifier/projection functions, not the entire release path. |
| External ordering | Graph resolver sorts family hashes; qualifier follows that list. External projection follows qualified input order. Default priority takes up to 7 qualified rows, or up to 5 forecasts if none. Complete LeadGen switches to at most 3 T1/T2 children; explicitly prioritized reviewed research can separately occupy up to 3 labeled research priorities. | Do not equate all priority cards, forecasts or slot5 assessment rows with qualified recommendations or seller actions. In the mixed fixture Q-B precedes Q-A; no alphabetical rerank occurred. |
| Assess to LeadRow | `from_assess.draft_lead_rows:138` preserves parents before optional children; `_next_action_and_tier:917` retains evidence/route/clock holds. `qualify.py` independently requires seller-ready proof. | A parent, contact, motion kind, WATCH/HOLD child and qualified seller action require separate counts and states. |

## Demonstrated findings

### S6-051-01: external qualification bypasses the accepted requirement gate (P1)

`evidence_route.classify_service_fit:364` accepts a core phrase without requested-work semantics. `classify_evidence:472` promotes an L1 row with a future response date, and `classify_route:545` defaults unrecognized/absent access restrictions to direct eligibility. `evidence_pack_v2.qualify_opportunities:400` repeats those independent classifiers instead of consuming the accepted requirement/action decision.

In `run03`, the requested-work control qualified. **Both “Evaluation uses SPRS supplier risk management information. Deliver projectors only.” and “No supplier risk management software is required.” were excluded from triage candidates but became `current_opportunity`, direct-fit, direct-route, qualified rows and external priority references.** A cancelled/inactive record with a future deadline also qualified. An expired-date control remained excluded. An `8(a)` display-string fixture defaulted to direct eligibility; the classifier's explicit code table recognizes `8a`, not that display string. This is a normalization/unknown-access failure demonstrated for that exact input, not proof that every structured restriction fails.

The projection probe supplied no complete LeadGen pack: this demonstrates its supported graph-only branch and underlying qualified-row contract. Complete LeadGen with no T1/T2 cleared priority cards in the separate projection controls. Full release certification was not bypassed or tested; do not infer a certified false-positive release from this baseline.

Repair the consumer boundary so graph/display qualification uses the accepted source-bound requirement, current-action and route decision. Retain unknown access as unknown, distinguish restriction parsing from source completeness, and retain rejected/raw evidence outside qualified counts. Do not replace the accepted gate with another phrase classifier.

### S6-051-02: client family resolvers use title similarity as identity (P1)

`evidence_pack_v2._family_basis:50` hashes normalized title plus agency; `canonicalize_requirement_families:82` ignores structured solicitation identity. The paired synthetic results are:

| Input | Accepted triage families | Golden graph families | Required |
| --- | ---: | ---: | ---: |
| Two independent calls under one umbrella, same title | 2 | 1 | 2 |
| Two verified amendments, same structured identity, changed title | 1 | 2 | 1 |
| Two verified amendments, same title | 1 | 1 | 1 |

The graph representative also prefers an older row marked live over a newer closed row because `_canonical_quality:70` prioritizes actionability/contact richness rather than latest authoritative posting. It retains member IDs, not the full member status/evidence history on the canonical row. Raw input still exists; this is consumer lineage loss, not source-file deletion.

Related actual seams were traced, not separately replayed: `notice_join.dedupe_by_title_agency:71` (used by L1 store), and `market_map_projection:1525` / `:1907` use `_family(title):1720`, stripping qualifiers/numerals and sorting word sets. Route all these consumers through verified family identity and an explicit representative decision, preserving original native order and full history. Treat missing identity as unresolved/separate, never title-merge it. Preserve NETSCOUT's separately owned representative-URL patch and both amendment histories during root integration.

### S6-051-03: compact diagnostic loses the latest no-term member (P2; S3-DIAG-006)

`requirement_support.family_diagnostic:202` constructs rows only from matched spans. OLD requested-work / NEW “Deliver only projectors.” correctly produces no candidates and an OLD -> NEW supersession disposition, but the compact family table contains no NEW. Replacing NEW with explicit term negation makes NEW visible. Both receipts report `superseded_revisions: 0` despite a recorded supersession: the counter is derived from a later positive-only consolidation rather than the complete earlier census.

Keep term/field evidence rows as one diagnostic dimension, and join them to complete member status, representative ID, suppression target/reason, source identity and history. Count suppression from the same full-census decision; do not treat superseded positive terms as current unique opportunities. `S3-ROLE-007` remains an accepted missing-label limitation; this baseline does not invent or claim coverage of unsupported semantic labels.

### S6-051-04: non-C1 copy promotes context into intent (P1; S3-SURFACE-005 / N-DHS-12)

The synthetic historical capability-text award with a September 14 PoP end was selected on September 12 and emitted an actual Signal Board `best_fit.account` of **“ACTIVE AWARD CORRIDOR · DISPLACEMENT”** plus an “active displacement award corridor” explanation. Moving only its end before the as-of date excluded it. No current product refresh, funded follow-on or displacement evidence was supplied.

`facts._buyer_map_facts:934` emits “DISPLACEMENT WINDOW” when the supplied expiry-window flag is true. `facts._funded_demand_facts:912` labels a term appearing in a document “FUNDED-DEMAND SIGNAL,” albeit with an explicit dollar-line limitation. `ui.server._search_summary:875` called one generic legislative document “funding proof.” These are distinct from the already-repaired C1 wording. All four C1 source-context cases and its native-rebind control passed.

Use source-stated award dates, historical obligations and document mentions as bounded research facts. Current funding, refresh, live response, direct access and displacement each need their own supporting evidence. Keep honest questions and source links; avoid converting contextual numbers into addressable pipeline. `golden_press/render.py` displacement labels were located as another consumer seam, but no rendered release reproduction is claimed.

### S6-051-05: research/HOLD children default to live bid (P2; N-DHS-06)

`from_assess._assess_derived_unit:485` emits a live-bid motion ID for live-ledger subjects, and `_buying_motion:728` defaults that subject kind to `LIVE_BID`. The native draft mapper gave both the synthetic market-research subject and bid-now control a `live_bid` kind. Both remained HOLD; no seller-ready promotion was demonstrated. Preserve lifecycle, original screen decision, material fit judgment and recommended action separately. Existing immutable IDs/history must not be rewritten to fix display language; use a versioned new projection/contract where necessary.

## Preserved controls

- Zero/all-held graphs produced no qualified priority references; raw discarded rows remained available in slot5 and did not fill priorities. Mixed qualified input preserved Q-B then Q-A. Forecast-only default priority was explicitly linked to `future-forecasts`, and is excluded from a qualified-opportunity measurement. A complete empty LeadGen pack yielded zero seller-ready priorities.
- Native source coverage distinguished missing/failed (failed, total unknown), empty without census (partial), and reconciled known-empty (complete, total zero). Missing/unreadable attachment inventory stayed a named gap; a recognized, hash/count-reconciled empty inventory passed the bounded inventory check. This is no claim to attachment acquisition completeness.
- The 24 focused controls covered C1 claims/current rebind, reapproval versus acquisition clocks/IDs, unknown acquisition, schema1 history and schema2 downgrade/revalidation boundaries, thin evidence, bare-expiry WATCH, supported renewal T2, solicitation-only non-promotion, incomplete required coverage, and full-census family suppression. These are bounded controls, not a repeat of root's full review or cloud/local parity.

## NETSCOUT saved-source intake and prospective research-parent lane

`saved-fixtures/INTAKE.json` and `SAVED_SOURCE_HASHES.json` bind 16 original sources/projections to recorded source hashes. The coordinator's abbreviated research-output path did not exist; the canonical `.../2026-09-11/1/outputs/NETSCOUT_DHS_Comprehensive_Research.md` path and hash came from `OVERLAP_AND_STATE.json`. No source collection was repeated.

The historical `f0f7aec` bundle reconciles **25 notices, 25 discard dispositions, 23 parents, 18 HOLD children, 18 live_bid motion labels and zero seller-ready children**. Five additional fit assessments say no_fit; twenty were not fit-assessed. The cannabis analyzer's rejected cybersecurity boilerplate is retained as counterevidence, not positive capability proof. These saved outcomes do not establish present candidate behavior; the accepted497 native synthetic motion probe establishes the current bounded analogue.

N-DHS-12 fixture anchors retain: NCCS `70RTAC26RFI000004` closed March 6 (body/structured former cutoff differ, both expired); SAIC `70B04C20F00001359` September 14 PoP end and cumulative obligations, no demonstrated NETSCOUT refresh or funded follow-on; DHS aggregate budget remainder without IT allocation or September30 expiry authority; true KEV/publication chronology; empty approved Horizon over its limited fact bank. The old Horizon method note claims eleven extra forecasts screened even though those summaries supplied zero records. Corrected analyst narrative is saved evidence, not proof an ordinary rerun reproduces it. Later NCCS documents can extend program research without reopening the expired response; their acquisition is Step7.

The separately versioned ResearchSubject/native Assess v3 is owned by NETSCOUT/root. Per `RESEARCH_SUBJECT_CONSUMER_ADDENDUM.json`, final composite readers must preserve facts, targets, route limits, unknown continuation, questions and next research ask even with no child. Research parents get a separately identified population; they cannot fill qualified recommendations or seller-action counts. Published schema fields are **not yet assumed here**. Final tests must use its published contract, preserve v1/v2 read and anti-relabel boundaries, and avoid fabricated live_bid, current window, eligibility, freshness or permission. No schema/draft code was imported.

## Smallest coherent repair and acceptance

First connect existing native consumers to the accepted requirement/action/route decisions and full structured family census. Then adjust compact diagnostics and unsupported copy using those explicit states. Preserve the eight external slots, parent assessment, Market Map, targets, holds and source lineage. Keep ordinary raw diagnostics available. A new dashboard, quota, silent backfill, lower relevance threshold or parallel schema implementation would not repair these seams.

`ACCEPTANCE_PLAN.json` predeclares paired tests, exact consumer coverage, three future measurement outputs and equal-effort policy. Coordinator should publish one exact Step6 candidate and package these inputs/commands plus canonical plans and residual register for a single CoS-routed Fable review before Step7. Root owns later composite integration with NETSCOUT and the source work. The new tests should assert both positive controls and refusal of unsupported claims through native JSON/UI/report consumers; a helper-only green result cannot close a downstream mismatch.

All Step1 R1-R7 and canonical Step3/4/5 residuals are retained verbatim in `PRIOR_FOLLOWUPS.preserved.json`. Root's assigned strict acquisition P1 is **closed on accepted497b7f3**; broader source authenticity, continuous immutability, fresh acquisition, finite grammar/JSON and checker-authority limits remain. Fable's absent authentic23/25 replay, subset failures/skips/collection errors, different runtime and missing canonical plan paths remain disclosed. No benchmark winner, total-market recall, identical-search parity, recovered lead, certified release or production merge is claimed.
