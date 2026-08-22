# Candidate Review v1 Chunk 2 checkpoint

Recorded: 2026-07-22

## Decision

**GO TO CHUNK 3.** Chunk 2 is complete. The isolated integration branch now
contains a deterministic, evidence-first candidate and corridor engine that
builds the system-owned **Candidate opportunities for review** projection
without assigning pursue, no-bid, disqualified, pipeline-value, or
win-probability treatment.

Gate 2 is intentionally still pending. The controlling playbook defines Gate
2 as the joint independent review of Chunks 2 and 3, so that review will occur
after the federal calendar and event-research lane is complete.

## Changed files

- `agents/candidate_review_v1/candidate_engine.py`
- `agents/candidate_review_v1/__init__.py`
- `agents/candidate_review_v1/contracts.py`
- `tests/test_candidate_review_v1_engine.py`

No legacy renderer, composer, Command Center, client-content, or source-mesh
file was changed by Chunk 2.

## Implemented behavior

- Accepts only explicit federal-buying anchors. Events, vehicles, legislation,
  regulation, news, and general web pages may support a candidate but cannot
  create one by themselves.
- Enforces exact client, run, scope, profile, snapshot, source, and timestamp
  binding before candidate construction.
- Canonicalizes exact source identities while retaining a complete alias map;
  semantically conflicting aliases fail closed.
- Maps active, recently verified official SAM.gov notices to current-notice
  candidates and maps other allowed anchors to explicit research corridors.
- Deduplicates at exact-record, procurement/amendment-family, and strategic-
  corridor levels while preserving each source record, URL, and assertion.
- Merges a procurement family only when the buyer and decision boundary stay
  materially the same. Buyer, eligibility, lifecycle, program, access route,
  analyst action, and ambiguous deadline differences remain separate.
- Permits only a provably later effective amendment to normalize a family
  deadline; ambiguous or equally dated conflicts fail closed.
- Applies ranking and diversity compression only to visible report order. The
  full ranked inventory and every deferred candidate remain available for
  audit and analyst review.
- Hydrates the locked `CandidateReviewDocument` contract directly from the
  build result.
- Validates date and money meanings, globally unique money IDs, exact evidence
  closure, and award-end/recompete language.
- Rejects dispositive copy containing pursue, no-bid, or disqualification
  language. Low fit, NAICS, set-aside, value, and incumbent concerns remain
  cautions and rank signals only.

## Adversarial findings closed before checkpoint

The independent audit initially identified three release holes. Each received
a production fix and regression test:

1. Plain-language pursue/no-bid/disqualified copy was rejected, not only the
   most explicit recommendation phrases.
2. Definitive award-end-to-recompete claims such as “recompete expected” or
   “expiry signals an upcoming recompete” were rejected unless explicitly
   supported by official recompete evidence.
3. Duplicate money IDs across separate candidates were rejected globally.

Additional tests cover news-only seeds, cross-client evidence, identity-alias
conflicts, conflicting dates and money, current-notice source and freshness,
SAM family identity, deadline ambiguity, corridor distinctness, traceability,
diversity compression, sparse inventories, and the Mark43, iMerit, and
Riverbed reference expectations.

## Verification evidence

- Candidate engine suite: **78 passed**.
- Combined Candidate Review contract, projection, reference, and engine suite:
  **111 passed in 0.36 seconds**.
- Combined related regression suite: **488 passed in 3.00 seconds**.
- Public API/schema import smoke: passed.
- `git diff --check`: passed.
- Final independent adversarial audit: **GO** after 78/78 focused tests and
  direct probes of forbidden disposition language, unsupported recompete
  certainty, duplicate money IDs, conflicting exact-identity aliases, and
  ambiguous family deadlines.
- Source checkout commit, status, and tracked-patch SHA-256 still match the
  baseline record. The source checkout remains untouched.

## Next step

Implement Chunk 3 in the isolated worktree, then run the required joint Claude
Gate 2 review of the candidate engine and federal calendar/event-research lane.
