# Candidate Review v1 Gate 1 review record

Recorded: 2026-07-22

## Decision

**GO.** Chunk 1 is complete and the isolated integration branch is ready for
Chunk 2. No release-critical contract defect remains in the reviewed scope.

This decision is based on passing contract and regression suites plus a final
read-only adversarial review. Claude Code was also invoked twice for the
required read-only gate, but its local CLI returned no decision payload: the
first invocation entered a permission loop, and the corrected invocation was
aborted after a local TLS event-export failure. Neither invocation changed a
file. This tool failure is recorded separately from the code decision and
should be retried at Gate 2 after the local Claude transport is healthy.

## Reviewed scope

- `agents/candidate_review_v1/__init__.py`
- `agents/candidate_review_v1/contracts.py`
- `agents/candidate_review_v1/projection.py`
- `tests/test_candidate_review_v1_contracts.py`
- `tests/test_candidate_review_v1_projection.py`
- `tests/test_candidate_review_v1_reference_contract.py`
- `tests/fixtures/candidate_review_v1/`
- `docs/reviews/candidate-review-v1-baseline.md`

No legacy renderer, composer, Command Center, client-content, or source-mesh
file was changed by Chunk 1.

## Contract coverage frozen in Chunk 1

- Exact client, run, scope, profile, and evidence-snapshot binding.
- Official-source and lifecycle rules for current SAM.gov notices.
- Explicit evidence spans for recompetes, events, attendance, and award ends.
- Typed dates, precision, status, and non-synthetic sorting dates.
- Typed money meanings with deterministic display values and evidence-kind
  compatibility.
- Current-notice candidates and research corridors with exact procurement
  family and evidence membership.
- Human-owned analyst treatment, pipeline value, and win probability.
- Sparse-valid content budgets expressed as maximums rather than quotas.
- Exact eight-section order, required ticker and scoreboard surfaces, one
  bottom Refresh action, contextual Undo, and forbidden legacy controls.
- Editable text and image state, card order, soft deletion, undo, and clean
  standalone export projection.
- Golden content and interaction contracts for Mark43, iMerit, and Riverbed.

## Verification evidence

- Candidate Review contract/projection/reference suite: **33 passed in 0.20s**.
- Combined related regression suite: **410 passed in 2.75s**.
- Generated schema: `CandidateReviewDocument` with **49 definitions**.
- `git diff --check`: passed.
- Latest independent adversarial audit: **GO** after testing cross-client
  binding, money-display forgery, notice-source identity, lifecycle and date
  mismatches, corridor membership, ticker evidence closure, and export state.
- Source checkout status and commit still match the recorded baseline; all
  Candidate Review additions exist only in the isolated worktree on
  `codex/candidate-review-v1` at rollback commit
  `1299e6920a660fe928e9fc824c12a9ac5be86629`.

## Required follow-up

- Retry the Claude Code read-only gate at Gate 2. The failure was operational,
  not a finding against the Chunk 1 contracts.
- Do not modify the locked contracts silently. Any change discovered during
  Chunk 2 must add a regression test and be recorded in the decision log.
