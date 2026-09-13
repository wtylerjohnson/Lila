# REPAIR-STEP5-FIXED-REVIEW-050

**CHANGES_REQUIRED — two open P2 findings on exact candidate `55bf3aa800fd7a3dfdecd8d20ccc6abd2591a28a`.** The 37 focused tests pass, but six new adversarial cases expose two contract defects. Coordinator should repair these before the combined exact-SHA CoS/Fable handoff. Neither finding is downgraded to a residual.

Reviewed the existing `codex/step5-evidence-states` checkout. Its exact parent is imported root `dbc2458de359e25077cffadf2b7889e350ab6802`. HEAD and the snapshotted reviewed bytes were checked before/after executions and remained unchanged. Product code was not edited. The original 32-case acceptance plan is byte-identical to the preserved baseline; all 32 cases map to passing focused tests. This coverage does not replace the additional adversarial findings below.

## S5-REVIEW-050-01 — P2: unsupported opposing proposals disappear from disagreement reporting

**Input:** In `fixtures/unsupported_fit_opposition`, the original notice says the statement of work is unavailable. Reviewer 1 proposes `fit=false`; reviewer 2 proposes `fit=true`. Neither gets an admitted substantive-fit receipt. Additional fixtures propose grade0 versus grade3, and `fit=false` versus `fit=unknown`, against the same insufficient source.

**Actual:** In all three cases, both validated classifications correctly remain `unknown`, and raw reviews are retained. However, `disagreements=[]` and `initial_complete_decision_agreement=1.0`; there is no separate raw-proposal disagreement output. Source-support screening has erased the original difference from the reported disagreement/initial-agreement metrics. No false positive credit was observed in these repros.

**Expected:** Preserve original predicate/value or grade disagreements independently of validated-state agreement. Held claims must stay held, but their opposing proposals must not be reported as complete initial agreement. This is the accepted G5 guard: shared support checking must not eliminate reviewer disagreement or serve as adjudication. Same supported decisions from different admissible evidence/rationale may still agree substantively.

**Cause and source:** `tools/benchmark_v5/scorer.py:82-85` builds the comparison only from validated states/action/classification and `supported_proposals`; unsupported proposed values never enter that signature. `scorer.py:97-99` then labels the derived result as disagreements and initial complete decision agreement. `tools/benchmark_v5/support.py:127-134` correctly keeps unsupported proposals out of supported values, but the scorer supplies no independent raw-proposal comparison.

**Minimal recommendation:** Keep validated-state comparison and counts, add a separately named original-proposal disagreement/coverage metric, and retain both in the result. Compare semantic predicate/value proposals without requiring identical evidence IDs, claim IDs or prose. Do not promote an unsupported assertion or invent adjudication to resolve the difference. Add all three repros plus the existing different-evidence/same-decision control.

**Evidence:** `PROBE_RESULTS.json` entries `unsupported_fit_opposition`, `unsupported_grade_opposition`, `unsupported_negative_vs_unknown`; each fixture preserves original reviews, source records, registry and `review_result.json`. Exact invocation/output: `COMMANDS.jsonl`, `adversarial_probes.stdout.log`, `probes.py`.

## S5-REVIEW-050-02 — P2: ordinary dictionary equality admits noncanonical receipt types

**Input:** Start from a valid original notice with frozen integer span `[0,282]`. In separately authored synthetic admission snapshots, replace the admitted receipt's context span with `[0.0,282.0]` or `[false,282]`; retain the correctly typed frozen card. Recompute the checker context digest, checker-record digest and receipt digest, explicitly admit that replacement, and point the unchanged claim to its exact receipt. A third case changes only the bound checker record's domain from boolean `synthetic=true` to number `synthetic=1`, with its exact hashes admitted.

**Actual:** All three malformed cases score successfully as `relevant`. Float/boolean span receipt contexts compare equal to the frozen integer span; numeric checker domain compares equal to boolean true. Control cases using a string checker domain or a numeric top-level receipt domain are rejected. This is inconsistent structural validation inside the same support contract.

**Expected:** Refuse these malformed checker records/receipt contexts before producing a comparable score. G4 explicitly requires strict types, no boolean/numeric coercions, and exact canonical passage-span encoding. Admission authenticates the selected digest; it must not bypass the schema or exact frozen-context check.

**Cause and source:** `tools/benchmark_v5/support.py:89-92` checks the checker record through Python dictionary equality, where `1 == True`. `tools/benchmark_v5/scorer.py:56-62` regenerates a valid context but compares the received nested context through the same equality, where `0.0 == 0 == False`. `support.py:125-126` and `support.py:148-149` repeat that comparison. `custody.py:75-78` validates integer spans on the original card, but this validation does not validate the admitted receipt's separate evidence-span representation.

**Minimal recommendation:** Validate checker-record and receipt-context field types explicitly, including boolean domains and integer-only span offsets; compare canonical serialized context or its digest after schema validation rather than coercive dictionary equality. Apply the same rule to all admitted receipts, not only reviewer-selected receipts. Retain the existing positive canonical-span, Unicode-codepoint and invalid UTF-8-offset controls.

**Evidence:** `PROBE_RESULTS.json` entry `check_record_numeric_domain`; `RECEIPT_TYPE_RESULTS.json` entries `receipt_context_float_span`, `receipt_context_bool_span`, and the refusing `receipt_numeric_domain_control`. Every fixture retains original and replacement receipts/check records, the external admission snapshot, unchanged source/card bytes, raw proposals and result. Exact invocations/outputs are in `COMMANDS.jsonl`, `adversarial_probes.stdout.log`, `receipt_type_probes.stdout.log`, `probes.py` and `receipt_type_probes.py`.

These are structural-validation repros with explicit synthetic admission, not a claim that hashes detect a dishonest checker, authenticate government bytes or establish semantic truth. No live-domain bypass or changed substantive fact was demonstrated.

## Other bounded results

| Guard | Review result |
| --- | --- |
| G1 negative versus unknown | Passing controls: supported fit exclusion survives unknown clocks/evidence gaps; evidence insufficiency and grade0 alone stay unknown; discovery/non-substantive/undated material supports neither polarity. |
| G2 conflict aggregation | Passing controls: opposing fit support conflicts before classification; receipt order does not choose a winner; explicit revocation retains the old result; an independently resolved route exclusion can decide despite a fit conflict. |
| G3 action/route scope | Passing controls: different action/offering/routes/continuation-universe bindings refuse; direct-only exclusion with partner uncertainty stays unknown; same-subject continuation positive and response/broader-action alternatives pass the focused tests. Substantive quantifier correctness remains the disclosed checker dependency. |
| G4 admission/identity/types | Original subject mismatch, invalid Unicode span, unfrozen issuer, empty registry, duplicate JSON keys and stale/bad authority controls pass. **Type-validation defect S5-REVIEW-050-02 remains open.** |
| G5 roles/proposals | Existing role/provenance independence and raw-review preservation checks pass. **Original-disagreement defect S5-REVIEW-050-01 remains open.** |
| Fixed20 | A 21-row mixture retains 13 duplicate slots, one known retrieved family, one injected slot, one unresolved-provenance slot, one unresolved identity and one confirmed-negative slot. Three eligible families receive credit; rank21 supplies no replacement. Empty slots, no winner and withheld novelty remain explicit. |
| Portable CLI | `prepare`, `score`, `--help` and `demo` pass from a clean export of the exact committed `tools/benchmark_v5` tree, with checkout PYTHONPATH removed and user site disabled. Attempting to overwrite a result exits2 and preserves its digest. This is local offline export proof, not remote publication. |

30 additional probe records comprise 29 behavioral checks (23 expectations met, six repros grouped into the two findings) and one explicitly scoped trust-boundary observation. The observation shows extra original JSON fields can survive into neutral source copies when the fixture falsely attests `blind_content_reviewed=true`. Honest custodian review remains a disclosed prerequisite; this was not counted as a mechanical sanitization pass or a new guard defect. Original-source authentication, cross-notice identity adapters and general semantic entailment remain outside the declared version.

Focused tests ran once under Python3.14.5 using the standard library. The coordinator-owned combined full suite was not duplicated. Root integration metadata matches the imported parent and retained producer receipt hash `6058f131da87ce8dc403f194589e7df52eff161157b4555dfb832ef1de7f7a7c`; Step5 adds its separate contract sections without changing parent product implementation. Root source-clock P1 remains open for combined review acceptance; no duplicate root P1 review was performed. Step6 stays pending.

`VERIFICATION.json` binds commands, logs, reviewed files, probe/source receipts and preservation hashes. All 518 preserved baseline files remain byte-identical. No frozen case was read or modified, and no live/source/model call, new worker/worktree, product patch, push, Fable dispatch or production operation was performed. Operator checkpoints after one completion handoff; coordinator owns repair and subsequent review.
