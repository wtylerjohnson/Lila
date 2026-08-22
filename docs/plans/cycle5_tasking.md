# Cycle 5 tasking · two lanes, one scoreboard (drafted 2026-07-12, QB lane)

Base for both lanes: 1c1fc64 (post Cycle 4 merge + T5 seam consumption).
Suite at base: 825 passed / 1 skipped. Scoreboard:
docs/reviews/cycle3-review-triage.md (owner-routed; update statuses there,
never in a side channel). Frozen interfaces are unchanged from
docs/CONTRACT_SURFACES.md; T5's current_run_identity vocabulary is now
consumed by release_state and is load-bearing in both lanes.

Operator gates are unchanged: no pointer activation, no gate edits, no
metered or LLM press without William's word. All Cycle 5 work below is
offline-verifiable.

## Codex lane · Tranche 1 (PRESS BLOCKERS, land first, isolated diffs)

The NETSCOUT refresh press is gated on these two (triage doc: "Nothing here
blocks legacy report production EXCEPT items 1-4"; 3 and 4 are closed).

- Item 2: as_of dormancy. Deadline COMPARISONS keep the documented shared
  UTC cutoff; the no-as_of DEFAULT for report assembly must stop shifting a
  calendar day during US evening hours for pointer-less builds. Contract
  surface (dated doctrine): isolated, labeled diff with tests that pin an
  evening-hours build at both defaults.
- Item 1: sales-gate redaction garbling third-party news headlines/URLs kept
  in the gated model. The teaser surface is client-visible; redaction must
  remove candidate identity without rewriting unrelated copy. Leak tests
  stay string-physical.

## Codex lane · Tranche 2 (dormant legs + quality, before any cutover)

- Item 5: malformed scope designator returns INVALID before pointer
  existence; pointer-less clients must resolve ABSENT (dormancy).
- Item 8: approved_at + approval sha inside the immutable run identity means
  every re-approval rewrites the pointer and re-blocks release freshness;
  compose with the diff-aware re-approval cadence before cutover.
- Item 18: assess-approve endpoint returns 200 then swallows
  _refresh_assess_ledger failures; surface the failure (job log + response
  note), never a silent INVALID lane.
- Items 11-16, 19-21: horizon-factory guards (record_pull isolation, absent
  vs error coverage status, stored-sweep re-log, regulations_gov dead buyer
  plumbing, federal_register reserved 'errors' key), views rendering nits
  (empty QA block, unreachable monitor head), coverage-lane undercount.
- Items 9/10: DO NOT self-decide. Deliver a one-page adjudication memo for
  William: preserve frozen verdict_totals/monitor_notices semantics in
  ledger mode, or amend CONTRACT_SURFACES in a labeled joint diff he
  approves. Both options costed, with the exact count deltas per client.

## Codex lane · Tranche 3 (after William's fresh scope-bound sweep exists)

- Regenerate NETSCOUT and Osprey cutover packets WITHOUT the diagnostic
  scope override, against the new sweep. Offline, non-authorizing, same
  packet contract.
- Capture the first production change-digest baselines (scope-bound sweep
  satisfies T6's refusal). Zero motion expected on first capture.

## Claude lane (QB)

- Verify-then-merge every Codex tranche: tip SHA, isolated-worktree suite,
  lane diff, no data/state writes, packet hashes when applicable; then the
  adversarial review workflow over the merged range, triage to the
  scoreboard, fix Claude-routed survivors same-session.
- Press-week support: pre-press checklist (Control Room restart picked up
  1c1fc64+, fresh sweep carries explicit search_scope with the L19
  designator, approval diff panel shows the evidence delta), then babysit
  the split press artifacts (release_state reasons, QA stamp behavior, R2
  teaming_watch flag state) through the near-free verification press; flip
  --compose-split to default after two clean presses (small labeled diff).
- thinklogical: read-only pipeline state readout and exact next-click list
  for William (sweep exists; no review/qualify artifacts yet).
- Scope Partnering Resolve Phase 2 (auto-resolved partnering profile +
  client questions) as isolated diffs behind tests; BUILD ONLY after the
  press week ships and William green-lights the SAM re-probe it needs.
- Housekeeping: commit the accumulated data/entities/unresolved.log traffic
  with the next substantive commit; archive (never ship) the stale Press A
  NETSCOUT html on the Desktop once William confirms.

## William's decision list (only he can)

1. Control Room restart (picks up carry-forward approve, env-unified store,
   change-digest endpoint, release legs).
2. Fresh DHS filter-first sweep press for NETSCOUT, then re-approval via the
   diff panel, then ONE split press and the verification press.
3. Release clicks per client; recorded_future flag rulings; stale Desktop
   Press A html disposal.
4. Items 9/10 adjudication (memo from Codex Tranche 2).
5. Cutover decisions per client from the regenerated packets (pointer
   activation is his click alone).
6. Standing side items: OpenAI arbiter .env, Carahsoft reseller sentence in
   data/reference/vehicles.json, GitHub remote for the same-disk backup.

Done means, per lane and per tranche: full suite green, report lint clean,
scoreboard statuses updated, docs extended in the same diff that changes
behavior.
