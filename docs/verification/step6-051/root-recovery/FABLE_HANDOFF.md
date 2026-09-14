# FABLE-STEP6-051: native surfaces and root recovery

Review the exact published candidate on `codex/step6-root-recovery`, including the
full Step6 delta from accepted `497b7f3b1bdef6a58081918fd0e4d62b66fa7d76`.
Read ../REVIEW.md and ../FUTURE_MEASUREMENT_CONTRACT.json, the locked weekend
plan, LOCAL_VERIFICATION.json and PRIOR_FOLLOWUPS.json. The root recovery extends
the preserved coordinator drafts 23bfb920/d767922 and patch commit 53b0b0b.
Do not reuse those draft results or inspect a different branch as this candidate.

The complete offline suite ran against `d2fc01a609f530382c4534cfb59e1d84fe51f5e8`:
5844 passed, 40 skipped, 20 warnings, 26 subtests. The publication child only adds
this review package. Verify every non-doc file against LOCAL_VERIFICATION.json.
Runtime code is unchanged since d78e93c; d2fc01a corrects one obsolete test that
treated any published contact as a contracting officer. All remaining runtime
and test hashes are bound. Local Python is 3.14.5. Report your actual environment,
missing fixtures, failures and skips; 31 ignored local baseline fixtures are
listed in FIXTURE_STAGING.json and are not supplied as cloud parity evidence.

The first full run is retained: seven failures comprised that outdated expectation
and six headless Chrome launch failures in the restricted shell; 74 additional
browser skips occurred there. The final full run used approved browser execution
outside that shell and retained the same offline/strict flags. Earlier browser
reliability residuals remain distinct; this does not establish their root cause.

ORIGINAL_REVIEW_EVIDENCE.tar.gz retains the original operator report, exact probe
scripts, failing inputs/outputs and attempt errors on the earlier drafts. Extract
into an owned evidence directory without overwriting those attempts. In particular,
initial R2 summary serialization failed after products were written; those outputs
are preserved and the later driver has only the recorded serialization repair.
RECOVERY_PROOF.tar.gz contains the new attempts, source hashes, logs and receipts.
Archive hashes are in LOCAL_VERIFICATION.json. The probe accepts NEW_ABSOLUTE_RUN,
ABSOLUTE_CHECKOUT and EXACT_SHA arguments. Its supplied SHA label is not proof;
independently check loaded module hashes and inspect each result, not only exit 0.

Challenge the full Step6 native source/family/consumer contract and these repairs:

1. LILA-051-R1: saved minimal or full companion T1/T2 cannot revive stale,
   missing-context, modified-parent or different-Assessment notices. Current full
   companions must retain positive controls. Current reviewed research keeps its
   exact authorized ask/targets as research; saved history remains visible.
   Change source inputs during projection and require a withheld final return.
2. LILA-051-R2/S6-LOCAL-001: inject contacts, amounts, buyer components, access,
   incumbent and route fields only into the constructor pack. They must not gain
   original-source authority in the graph, Market Map or external fallback.
   Preserve original mapped/raw/flat primary, secondary and additional contacts,
   job titles only when supplied, phone-only POCs, small-business POC, and zero
   published value. Source roles and enrichment identity remain distinct.
3. LILA-051-R3/S3-DIAG-006: retain selected N1 in its native position but show
   newer closed/no-term N2, its title/link/status and complete supplied lineage.
   Suppress stale qualification without backfill, loss of history or reranking.
4. S6-LOCAL-002: independent full solicitation IDs do not collapse under triage;
   genuine amendment lineage still groups. Check native source census/store
   siblings, compact diagnostics and unknown/zero-qualified controls.
5. S3-SURFACE-005: source-bound non-C1 action/recommendation copy may not invent
   funding, displacement or eligibility from capability mentions or award context.
   Source acquisition remains independent of assessment/review/generation clocks;
   unknown clocks remain explicit even when saved companion summaries claim dates.
6. Recheck mutable projection/reference inputs during a warm context. Paths remain
   those of the existing canonical manifest; do not claim a new root path policy
   or continuous filesystem/source authentication.

Focused commands (plus your independent adversarial probes):

```sh
PYTHONDONTWRITEBYTECODE=1 LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest tests/test_native_notice_bridge.py tests/test_native_surface_contract.py tests/test_external_notice_authority.py tests/test_step6_material_source.py tests/test_triage_prefilter.py tests/test_assess_source_clock.py tests/test_assessment_output_retention.py tests/test_evidence_route.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 LILA_SUITE_OFFLINE=1 LILA_SUITE_STRICT=1 python -m pytest tests/ -q -rs -p no:cacheprovider
```

Keep original Step1 R1-R7 and canonical Step3/4/5 residuals. This review is not
NETSCOUT schema3/composite verification, Step7 collection, source authenticity,
live acquisition, lead recovery, seller readiness, frozen-case rescore or operating
merge. All eight external slots and historical assessment/rejection records remain.

CoS must check live cloud inventory and reuse an active exact-candidate review or
launch ONE Fable. Keep shepherd paused. Export the exact tested SHA, original
report/transcript, reproducible probes and complete residual register to shared
`handoffs/FABLE-STEP6-051/`; notify root and coordinator on launch and completion.
Root owns acceptance during recovery; Step7 stays pending until that acceptance.
Standing weekend auto-passthrough applies. Do not request renewed Tyler approval.
