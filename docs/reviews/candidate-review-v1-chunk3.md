# Candidate Review v1 Chunk 3 checkpoint

Recorded: 2026-07-22

## Decision

**GO TO GATE 2 REVIEW.** Chunk 3 is complete in the isolated integration
worktree. The system now has a deterministic, evidence-bound federal
opportunity calendar and event/conference research lane. It does not make live
network or model calls, and it does not infer attendance, dates, event status,
or opportunity disposition from discovery snippets.

The calendar is ready for the locked Candidate Review renderer in Chunk 5.
This checkpoint does not render the replacement section yet; it freezes the
data, provenance, lifecycle, and projection contract that the renderer will
consume.

## Changed files

- `agents/candidate_review_v1/event_research.py`
- `agents/candidate_review_v1/calendar_engine.py`
- `agents/candidate_review_v1/contracts.py`
- `agents/candidate_review_v1/candidate_engine.py`
- `agents/candidate_review_v1/projection.py`
- `agents/candidate_review_v1/__init__.py`
- `tests/test_candidate_review_v1_event_research.py`
- `tests/test_candidate_review_v1_calendar.py`
- `tests/test_candidate_review_v1_contracts.py`
- `tests/test_candidate_review_v1_engine.py`
- `tests/test_candidate_review_v1_projection.py`
- `tests/test_candidate_review_v1_reference_contract.py`

No legacy renderer, composer, Command Center, or client-content file was
changed by Chunk 3.

## Implemented behavior

- Builds a deterministic query manifest across all seven locked research
  families and four official-source classes.
- Binds the manifest, attempts, leads, accepted evidence, events, coverage
  receipt, and calendar projection to one client, run, scope, research-frame
  revision, and as-of date.
- Uses an injected discovery provider only. Unit tests and replay paths make no
  live federal-source, search, or model calls.
- Records returned-zero, failed, partial, stale, not-run, scope-excluded, and
  not-used lanes separately. “Comprehensive” means the required query/source
  census completed; it never claims the entire internet was searched.
- Enforces the locked horizons: 12 months for confirmed events, 18 months for
  officially proven flagship events, and 24 months for procurement research
  clocks.
- Accepts conferences and industry events only from authoritative agency or
  explicitly trusted organizer hosts with an exact official-event assertion,
  event identity, date, and edition match.
- Binds agenda, attendance, registration, and venue query credit to the exact
  role page or exact attendance assertion for that event.
- Requires every researched conference or industry event to retain at least
  one query, lead, evidence, and source-class attribution. Coverage is
  recomputed from those persistent attributions.
- Preserves cancellation, postponement, completed, scheduled, and date-TBD
  lifecycle states without creating synthetic dates.
- Preserves day, range, month, quarter, and fiscal-year precision. Coarse
  periods remain active through their actual period end while displaying no
  invented exact day.
- Normalizes semantic duplicates only for the same event edition. Recurring
  editions remain separate, while conflicting exact external identities fail
  closed.
- Produces chronological active and deferred inventories, applies a maximum
  only to the visible projection, and keeps the complete accepted audit
  universe.
- Hydrates the locked `CandidateReviewDocument` only when the candidate,
  evidence, event, coverage, and calendar snapshots match exactly.
- Keeps deleted candidate opportunities out of the published calendar while
  preserving internal recovery and audit state.

## Gate 2 findings closed before checkpoint

The two independent read-only adversarial lanes found and verified fixes for
the following integrity classes:

1. Candidate result snapshots could diverge from their ranked audit inventory
   or carry foreign evidence.
2. Candidate and calendar typed dates could be present only in metadata or
   could borrow the wrong semantic role from another date on the same page.
3. Calendar candidate snapshots were not independently client-bound.
4. A displayed researched event could survive after its discovery attribution
   was removed and the receipt recomputed.
5. An unrelated official page on the right host could receive query credit for
   another event.
6. An unrelated agenda, attendance, registration, or venue page could receive
   role-query credit instead of the exact event role page.
7. Ordinary attendance implications could appear without structured official
   attendance evidence.
8. Month, quarter, and fiscal-year milestones became historical on the first
   day of the period.
9. Shared-hosting roots could be trusted too broadly, and malformed numeric
   date suffixes could pass lexeme checks.
10. A calendar result could exceed the downstream document’s 32-event budget.

Every class now has a regression test. The final frozen-tree adversarial
matrix returned **GO** with no remaining finite finding.

## Verification evidence

- Complete Candidate Review v1 focused suite: **236 passed in 0.75 seconds**.
- Related candidate, assessment, research, report, refresh, and UI regression
  suite: **637 passed, 1 skipped in 3.64 seconds**.
- Final independent adversarial checks: **155/155** passed, comprising date
  semantics **43/43**, attendance proof **56/56**, role-query attribution
  **5/5**, source-class and role authority **11/11**, exact/shared-host trust
  **26/26**, and merge/deletion/coverage checks **14/14**. A second lane’s
  consolidated candidate/result matrix also passed **7/7**.
- Ruff: passed.
- `git diff --check`: passed.
- The preserved source checkout still matches its recorded commit, status, and
  tracked-patch SHA-256 exactly.

## Repository-wide test note

The unfiltered repository suite is not self-contained in this isolated
worktree. Collection stops because ignored fixture
`data/raw/sample_sam_response.json` is absent from `tests/test_contacts.py` and
`tests/test_ingest.py`. A second run excluding those two modules reached
**2319 passed and 77 skipped**, with **10 failures and 8 setup errors** in
legacy asset, cached-client-data, Chrome/PDF, Insignary replay, and JavaScript
editor tests whose required ignored data/assets or environment are not present
in the isolated worktree. None touches `agents/candidate_review_v1`; the
bounded related regression suite above is green.

## Next step

Complete the required read-only Claude Code Gate 2 decision. The local Claude
CLI is installed but currently reports `loggedIn: false`, so the code review is
GO while the playbook’s named reviewer decision remains operationally pending.
