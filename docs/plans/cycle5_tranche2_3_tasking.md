# Cycle 5 · Codex Tranches 2-3 (refreshed 2026-07-12, QB lane)

Mandated base: `ae454ee5c1206d56e1649c47d2ce2bf73cca531a`. Suite at base:
850 passed / 1 skipped. Scoreboard: docs/reviews/cycle3-review-triage.md
(update statuses there). Report back in the Tranche 1 handoff format; every
claim verified mechanically before merge.

## Already closed, do not redo

Your UI assessment was reconciled and largely landed in the QB lane:
keyword edits now own the SAM screen (effective_query_terms; curated
usaspending vendor and web credential lanes deliberately kept), Target is
physically gated behind an explicit journaled operator door
(target_gate_status + /api/review/target-approve + /api/run 409s), artifact
families collapse behind their newest build, stale QA passes read
"superseded", agency badges spell out, type floor 10px, contrast raised,
rails collapse at laptop widths, keyboard parity landed, and the contact
graph strips phone-contaminated names and placeholder phones at derivation.
Your item 1 and item 3 are closed by those; items 2, 4, 5, 6 and the IA
reshape stay in the QB lane (in flight; A3 next).

## Hard rules (unchanged, plus one new)

No pointer activation, no gate edits, no metered or LLM press. NEW and
binding: pointer creation is operator-only. If you touch any refresh path,
it goes through tools/assess_refresh.py::refresh_current_assess_run_if_active
(creation-free); calling materialize_current_assess_run from a runner or
hook is a contract violation (CONTRACT_SURFACES, 2026-07-12). A current
pointer EXISTS for netscout/agency_dhs; nothing you run may replace or
remove it.

Do not touch (QB lane, in flight): ui/index.html, the dashboard sections of
ui/server.py (stage builder, client detail state, review/target endpoints),
agents/review.py, run_*.py runners, agents/reports/release.py,
agents/reports/compose_cache.py, tools/assess_refresh.py.

## Tranche 2 · review-debt burn-down (your lane, isolated diffs per item)

Scoreboard items, priority order:

- Item 22 (NEW, CONFIRMED live, fix FIRST): agents/assess/parity.py:905 —
  the hermetic LILA_ENTITIES_DIR override leaks into current-pointer
  validation, so any packet built while a pointer is current reads a false
  "review or reference inputs have changed" hold and NOT ELIGIBLE. Scope the
  override to the strict projection build only; the pointer validation must
  see the real entities dir. Repro and workaround are in the scoreboard
  addendum. This gates Tranche 3.
- Item 5: malformed scope designator returns INVALID before pointer
  existence; pointer-less clients must resolve ABSENT (dormancy).
- Item 8: approved_at + approval sha inside the immutable run identity mean
  every re-approval rewrites the pointer and re-trips release freshness;
  compose with the diff-aware re-approval cadence. Now LIVE, not dormant:
  netscout has a current pointer.
- Item 18: assess-approve returns 200 then swallows refresh failures; the
  server console half is done (QB lane); land the response-note half from
  your endpoint code without changing the response shape beyond an additive
  note field.
- Items 11-16, 19-21: horizon-factory guards (record_pull isolation, absent
  vs error coverage status, stored-sweep re-log, regulations_gov dead buyer
  plumbing, federal_register reserved 'errors' key), views nits (empty QA
  block, unreachable monitor head), coverage-lane undercount, census-copy
  dormancy (item 19).
- Items 9/10: adjudication memo ONLY (verdict_totals 'discard' repurposing,
  monitor_notices narrowing): both options costed with exact per-client
  count deltas, one page, for William. No code until he rules.

## Tranche 2b · sales-preview language (proposal first)

Your finding stands: the sales teaser's lock language undersells. While you
are in agents/reports/views.py for items 15/16: draft the replacement copy
set (verified signal counts, time horizons, source coverage, methodology;
names/contacts/plays reserved for the engagement) as a SHORT proposal doc
with before/after strings. William approves the exact strings; then land it
isolated with the full-model leak tests unchanged. Client-facing copy ships
nothing without his sign-off.

## Tranche 3 · cutover evidence (offline, zero spend; AFTER item 22)

- Regenerate the NETSCOUT (agency_dhs, scope-bound sweep of 2026-07-12) and
  Osprey packets WITHOUT any diagnostic scope override. Expect NETSCOUT to
  read eligible with pointer CURRENT and can_release true; if it does not,
  report why rather than adjusting anything.
- Capture first production change-digest baselines (the scope-bound sweep
  satisfies T6's refusal). Zero motion expected on first capture; write only
  digest state, never pointers.
- Hashes for every artifact in the report-back.

Done means, per tranche: full suite green, report lint clean, scoreboard
statuses updated, docs extended in the same diff that changes behavior, and
the handoff names exact SHAs.
